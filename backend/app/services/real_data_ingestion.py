from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.domain import DataQualityLevel, DataQualityLog, DataSource, MarketQuote, Stock, WatchlistItem
from app.services.data_quality import DataQualityService
from app.services.market_calendar import MarketCalendarService
from app.services.market_guardrails import MarketGuardrailService
from app.services.market_data import MarketDataProvider
from app.services.stock_status import StockStatusService


class RealDataIngestionService:
    primary_order = ["sina_finance", "tencent_quote", "akshare_eastmoney", "eastmoney_web"]

    def sync_readonly(
        self,
        db: Session,
        symbols: list[str],
        source_codes: list[str] | None = None,
    ) -> dict[str, Any]:
        quality = DataQualityService()
        quality.seed_sources(db)
        active_source_codes = source_codes or ["sina_finance", "tencent_quote"]
        snapshots = MarketDataProvider().fetch_realtime_quotes_by_source(symbols, source_codes=active_source_codes)
        selected_source, selected_quotes = self._select_primary_snapshot(snapshots)
        if source_codes is None and not selected_quotes:
            backup_snapshots = MarketDataProvider().fetch_realtime_quotes_by_source(
                symbols,
                source_codes=["akshare_eastmoney", "eastmoney_web"],
            )
            snapshots.update(backup_snapshots)
            selected_source, selected_quotes = self._select_primary_snapshot(snapshots)
        source_status = self._update_source_status(db, snapshots)
        audits = self._audit_cross_source(snapshots)
        guardrail_state = MarketGuardrailService().evaluate_sync(
            db,
            selected_source=selected_source,
            source_status=source_status,
            audits=audits,
            selected_quotes=selected_quotes,
        )

        persisted = 0
        if selected_quotes:
            for quote in selected_quotes:
                quote["quality"] = self._quote_quality(quote, guardrail_state.status)
                quote["source_audit"] = audits.get(quote["symbol"], {})
                if quote["quality"] != "ok":
                    continue
                db.add(MarketQuote(**{key: quote[key] for key in ["symbol", "name", "observed_at", "last_price", "change_pct", "volume", "amount", "source", "quality"]}))
                persisted += 1
            db.commit()
            StockStatusService().upsert_from_quote_batch(db, [quote for quote in selected_quotes if quote.get("quality") == "ok"])

        quality_logs = quality.record_quote_batch(db, selected_quotes, source_code=selected_source or "unknown")
        audit_logs = self._record_audit_logs(db, audits, snapshots)
        return {
            "mode": "readonly_real_data",
            "selected_source": selected_source,
            "quotes": persisted,
            "quality_logs": len(quality_logs),
            "audit_logs": len(audit_logs),
            "source_status": source_status,
            "cross_source": {
                "symbols": len(audits),
                "warnings": sum(1 for item in audits.values() if item["level"] != "ok"),
                "max_deviation_pct": max([item["max_deviation_pct"] for item in audits.values()] or [0]),
            },
            "guardrail": MarketGuardrailService().serialize(guardrail_state),
        }

    def sync_all_a_shares(
        self,
        db: Session,
        limit: int | None = None,
        chunk_size: int = 200,
    ) -> dict[str, Any]:
        provider = MarketDataProvider()
        universe = provider.fetch_a_share_universe(limit=limit)
        stored_universe = self._stored_universe(db, limit=limit)
        if len(stored_universe) > len(universe) and len(universe) < 1000:
            universe = stored_universe
        self._upsert_universe(db, universe)

        source_codes = ["sina_finance", "tencent_quote"]
        backup_source_codes = ["akshare_eastmoney", "eastmoney_web"]
        chunk_size = max(20, min(chunk_size, 300))
        chunks = [universe[index : index + chunk_size] for index in range(0, len(universe), chunk_size)]

        summaries: list[dict[str, Any]] = []
        total_quotes = 0
        total_quality_logs = 0
        total_audit_logs = 0
        backup_chunks = 0
        selected_source_counts: dict[str, int] = {}
        for index, chunk in enumerate(chunks, start=1):
            symbols = [item["symbol"] for item in chunk]
            summary = self.sync_readonly(db, symbols, source_codes=source_codes)
            if summary["quotes"] == 0:
                backup_chunks += 1
                summary = self.sync_readonly(db, symbols, source_codes=backup_source_codes)
            selected = summary.get("selected_source") or "none"
            selected_source_counts[selected] = selected_source_counts.get(selected, 0) + 1
            total_quotes += int(summary["quotes"])
            total_quality_logs += int(summary["quality_logs"])
            total_audit_logs += int(summary["audit_logs"])
            summaries.append(
                {
                    "chunk": index,
                    "symbols": len(symbols),
                    "selected_source": selected,
                    "quotes": summary["quotes"],
                    "warnings": summary["cross_source"]["warnings"],
                    "guardrail_mode": summary["guardrail"]["mode"],
                }
            )

        return {
            "mode": "readonly_real_data_all_a_shares",
            "universe": len(universe),
            "chunks": len(chunks),
            "chunk_size": chunk_size,
            "quotes": total_quotes,
            "quality_logs": total_quality_logs,
            "audit_logs": total_audit_logs,
            "backup_chunks": backup_chunks,
            "selected_source_counts": selected_source_counts,
            "latest_chunks": summaries[-5:],
            "guardrail": MarketGuardrailService().latest(db),
        }

    def sync_active_market(self, db: Session, max_symbols: int = 360) -> dict[str, Any]:
        calendar = MarketCalendarService().today_status(db)
        symbols = self.active_symbols(db, max_symbols=max_symbols)
        summary = self.sync_readonly(db, symbols, source_codes=["sina_finance", "tencent_quote"])
        summary["mode"] = "readonly_real_data_active_pool"
        summary["active_symbols"] = len(symbols)
        summary["session_state"] = calendar["session_state"]
        summary["trade_time"] = calendar["is_trade_time"]
        return summary

    def _upsert_universe(self, db: Session, universe: list[dict[str, str]]) -> None:
        for item in universe:
            db.merge(
                Stock(
                    symbol=item["symbol"],
                    name=item["name"],
                    exchange=item.get("exchange", "A_SHARE"),
                )
            )
        db.commit()

    def _stored_universe(self, db: Session, limit: int | None = None) -> list[dict[str, str]]:
        statement = select(Stock).order_by(Stock.symbol.asc())
        if limit:
            statement = statement.limit(limit)
        rows = db.execute(statement).scalars().all()
        return [
            {
                "symbol": row.symbol,
                "name": row.name,
                "exchange": row.exchange or "A_SHARE",
            }
            for row in rows
        ]

    def active_symbols(self, db: Session, max_symbols: int = 360) -> list[str]:
        symbols: list[str] = []
        seen: set[str] = set()

        def add(symbol: str | None) -> None:
            value = str(symbol or "").strip().zfill(6)
            if len(value) == 6 and value not in seen:
                seen.add(value)
                symbols.append(value)

        for symbol in get_settings().stock_pool:
            add(symbol)
        for symbol in db.execute(select(WatchlistItem.symbol).order_by(WatchlistItem.created_at.desc())).scalars().all():
            add(symbol)

        latest_quote_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        active_rows = (
            db.execute(
                select(MarketQuote.symbol)
                .join(
                    latest_quote_times,
                    and_(
                        MarketQuote.symbol == latest_quote_times.c.symbol,
                        MarketQuote.observed_at == latest_quote_times.c.observed_at,
                    ),
                )
                .where(MarketQuote.quality == "ok", MarketQuote.amount > 0)
                .order_by(MarketQuote.amount.desc())
                .limit(max_symbols)
            )
            .scalars()
            .all()
        )
        for symbol in active_rows:
            add(symbol)
            if len(symbols) >= max_symbols:
                break
        return symbols

    def _select_primary_snapshot(self, snapshots: dict[str, dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
        for source in self.primary_order:
            snapshot = snapshots.get(source)
            if snapshot and snapshot["status"] == "ok" and self._has_persistable_quotes(snapshot["quotes"]):
                return source, snapshot["quotes"]
        return None, []

    def _has_persistable_quotes(self, quotes: list[dict[str, Any]]) -> bool:
        return any(
            float(quote.get("last_price") or 0) > 0
            and float(quote.get("amount") or 0) > 0
            and float(quote.get("volume") or 0) > 0
            for quote in quotes
        )

    def _quote_quality(self, quote: dict[str, Any], guardrail_status: str) -> str:
        if guardrail_status == "blocked":
            return "stale"
        if float(quote.get("last_price") or 0) <= 0:
            return "invalid"
        if float(quote.get("amount") or 0) <= 0 or float(quote.get("volume") or 0) <= 0:
            return "invalid"
        return "ok"

    def _update_source_status(self, db: Session, snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        now = datetime.utcnow()
        for code, snapshot in snapshots.items():
            source = db.execute(select(DataSource).where(DataSource.code == code)).scalar_one_or_none()
            if source:
                if snapshot["status"] == "ok":
                    source.last_success_at = now
                    source.last_error = None
                else:
                    source.last_error = snapshot["error"]
            rows.append(
                {
                    "code": code,
                    "status": snapshot["status"],
                    "latency_ms": snapshot["latency_ms"],
                    "rows": len(snapshot["quotes"]),
                    "error": snapshot["error"],
                }
            )
        db.commit()
        return rows

    def _audit_cross_source(self, snapshots: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        prices: dict[str, list[dict[str, Any]]] = {}
        for code, snapshot in snapshots.items():
            if snapshot["status"] != "ok":
                continue
            for quote in snapshot["quotes"]:
                prices.setdefault(quote["symbol"], []).append(
                    {"source": code, "price": float(quote["last_price"]), "name": quote["name"]}
                )

        audits: dict[str, dict[str, Any]] = {}
        for symbol, rows in prices.items():
            values = [row["price"] for row in rows if row["price"] > 0]
            if not values:
                continue
            min_price = min(values)
            max_price = max(values)
            mean_price = sum(values) / len(values)
            deviation_pct = ((max_price - min_price) / mean_price * 100) if mean_price else 0
            level = "ok" if len(values) >= 2 and deviation_pct <= 0.3 else "stale"
            if len(values) == 1:
                level = "stale"
            audits[symbol] = {
                "symbol": symbol,
                "name": rows[0]["name"],
                "level": level,
                "sources": len(values),
                "min_price": round(min_price, 4),
                "max_price": round(max_price, 4),
                "max_deviation_pct": round(deviation_pct, 4),
                "prices": rows,
            }
        return audits

    def _record_audit_logs(
        self,
        db: Session,
        audits: dict[str, dict[str, Any]],
        snapshots: dict[str, dict[str, Any]],
    ) -> list[DataQualityLog]:
        logs: list[DataQualityLog] = []
        for code, snapshot in snapshots.items():
            if snapshot["status"] == "fail":
                log = DataQualityLog(
                    source_code=code,
                    dataset="source_probe",
                    level=DataQualityLevel.degraded,
                    message=f"真实行情节点失败：{snapshot['error']}",
                    payload={"latency_ms": snapshot["latency_ms"]},
                )
                db.add(log)
                logs.append(log)
        for audit in audits.values():
            if audit["level"] == "ok":
                continue
            if audit["sources"] == 1 and (audit["prices"] or [{}])[0].get("source") == "sina_finance":
                continue
            log = DataQualityLog(
                source_code="cross_source",
                dataset="quote_consistency",
                symbol=audit["symbol"],
                level=DataQualityLevel.stale,
                message=f"跨源一致性不足：{audit['sources']} 个来源，最大价差 {audit['max_deviation_pct']}%。",
                payload=audit,
            )
            db.add(log)
            logs.append(log)
        db.commit()
        return logs
