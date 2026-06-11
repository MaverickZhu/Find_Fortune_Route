from __future__ import annotations

from datetime import date, datetime
from typing import Any

import akshare as ak
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.domain import InstitutionalHoldingSnapshot, MarketQuote, Stock


class InstitutionalHoldingService:
    source = "akshare_sina_circulate_holder"

    institution_keywords = [
        "基金",
        "证券",
        "保险",
        "社保",
        "养老金",
        "信托",
        "资管",
        "资产管理",
        "银行",
        "券商",
        "qfii",
        "香港中央结算",
        "hkscc",
        "中央汇金",
        "证金",
        "投资",
        "limited",
        "公司",
    ]
    fund_keywords = ["基金", "etf", "交易型开放式", "混合型", "股票型", "指数证券投资"]
    northbound_keywords = ["香港中央结算", "hkscc"]
    natural_keywords = ["自然人", "个人"]

    def sync_latest(
        self,
        db: Session,
        max_symbols: int = 120,
        symbols: list[str] | None = None,
    ) -> dict[str, Any]:
        universe = symbols or self._active_universe(db, max_symbols)
        name_map = self._name_map(db, universe)
        created = 0
        updated = 0
        failed = 0
        latest_report_date: date | None = None
        for symbol in universe:
            try:
                snapshot = self.fetch_symbol_snapshot(symbol)
            except Exception:
                failed += 1
                continue
            if snapshot is None:
                failed += 1
                continue
            if not snapshot.get("name"):
                snapshot["name"] = name_map.get(snapshot["symbol"], "")
            existing = (
                db.execute(
                    select(InstitutionalHoldingSnapshot).where(
                        InstitutionalHoldingSnapshot.symbol == snapshot["symbol"],
                        InstitutionalHoldingSnapshot.report_date == snapshot["report_date"],
                        InstitutionalHoldingSnapshot.source == self.source,
                    )
                )
                .scalars()
                .first()
            )
            if existing is None:
                db.add(InstitutionalHoldingSnapshot(**snapshot))
                created += 1
            else:
                for key, value in snapshot.items():
                    setattr(existing, key, value)
                updated += 1
            latest_report_date = max(latest_report_date or snapshot["report_date"], snapshot["report_date"])
        db.commit()
        return {
            "requested": len(universe),
            "created": created,
            "updated": updated,
            "failed": failed,
            "latest_report_date": latest_report_date.isoformat() if latest_report_date else None,
            "source": self.source,
        }

    def fetch_symbol_snapshot(self, symbol: str) -> dict[str, Any] | None:
        df = ak.stock_circulate_stock_holder(symbol=symbol)
        if df is None or df.empty:
            return None
        rows = df.copy()
        rows["截止日期"] = rows["截止日期"].astype(str)
        latest_date_text = str(rows["截止日期"].max())
        latest_rows = rows[rows["截止日期"] == latest_date_text].copy()
        if latest_rows.empty:
            return None
        report_date = datetime.strptime(latest_date_text[:10], "%Y-%m-%d").date()
        previous_dates = sorted({str(item) for item in rows["截止日期"].unique() if str(item) < latest_date_text})
        previous_rows = rows[rows["截止日期"] == previous_dates[-1]].copy() if previous_dates else None
        return self._snapshot_from_holder_rows(symbol, latest_rows, report_date, previous_rows)

    def latest_metrics(self, db: Session, symbols: list[str]) -> dict[str, dict[str, Any]]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        if not unique_symbols:
            return {}
        latest_dates = (
            select(
                InstitutionalHoldingSnapshot.symbol,
                func.max(InstitutionalHoldingSnapshot.report_date).label("report_date"),
            )
            .where(InstitutionalHoldingSnapshot.symbol.in_(unique_symbols))
            .group_by(InstitutionalHoldingSnapshot.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(InstitutionalHoldingSnapshot).join(
                    latest_dates,
                    and_(
                        InstitutionalHoldingSnapshot.symbol == latest_dates.c.symbol,
                        InstitutionalHoldingSnapshot.report_date == latest_dates.c.report_date,
                    ),
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: self.serialize(row) for row in rows}

    def top_candidates(self, db: Session, limit: int = 10) -> list[dict[str, Any]]:
        latest_dates = (
            select(
                InstitutionalHoldingSnapshot.symbol,
                func.max(InstitutionalHoldingSnapshot.report_date).label("report_date"),
            )
            .group_by(InstitutionalHoldingSnapshot.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(InstitutionalHoldingSnapshot)
                .join(
                    latest_dates,
                    and_(
                        InstitutionalHoldingSnapshot.symbol == latest_dates.c.symbol,
                        InstitutionalHoldingSnapshot.report_date == latest_dates.c.report_date,
                    ),
                )
                .order_by(InstitutionalHoldingSnapshot.crowding_score.desc(), InstitutionalHoldingSnapshot.institution_holding_pct.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [self.serialize(row) for row in rows]

    def _name_map(self, db: Session, symbols: list[str]) -> dict[str, str]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        if not unique_symbols:
            return {}
        quote_latest = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.symbol.in_(unique_symbols), MarketQuote.name != "")
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        quotes = (
            db.execute(
                select(MarketQuote).join(
                    quote_latest,
                    and_(MarketQuote.symbol == quote_latest.c.symbol, MarketQuote.observed_at == quote_latest.c.observed_at),
                )
            )
            .scalars()
            .all()
        )
        names = {row.symbol: row.name for row in quotes if row.name}
        for row in db.execute(select(Stock).where(Stock.symbol.in_(unique_symbols))).scalars().all():
            names.setdefault(row.symbol, row.name)
        return names

    def serialize(self, row: InstitutionalHoldingSnapshot) -> dict[str, Any]:
        return {
            "symbol": row.symbol,
            "name": row.name,
            "report_date": row.report_date.isoformat(),
            "institution_count": row.institution_count,
            "fund_count": row.fund_count,
            "big_holder_count": row.big_holder_count,
            "top_holder_count": row.top_holder_count,
            "institution_holding_pct": row.institution_holding_pct,
            "fund_holding_pct": row.fund_holding_pct,
            "northbound_holding_pct": row.northbound_holding_pct,
            "top10_holding_pct": row.top10_holding_pct,
            "institutional_change_pct": row.institutional_change_pct,
            "crowding_score": row.crowding_score,
            "data_status": row.data_status,
            "source": row.source,
            "observed_at": row.observed_at.isoformat(),
        }

    def _active_universe(self, db: Session, max_symbols: int) -> list[str]:
        latest_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0, MarketQuote.amount > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote.symbol)
                .join(
                    latest_times,
                    and_(MarketQuote.symbol == latest_times.c.symbol, MarketQuote.observed_at == latest_times.c.observed_at),
                )
                .order_by(MarketQuote.amount.desc())
                .limit(max_symbols)
            )
            .all()
        )
        symbols = [row[0] for row in rows]
        if symbols:
            return symbols
        return [
            row[0]
            for row in db.execute(select(Stock.symbol).order_by(Stock.symbol.asc()).limit(max_symbols)).all()
        ]

    def _snapshot_from_holder_rows(
        self,
        symbol: str,
        rows: Any,
        report_date: date,
        previous_rows: Any | None,
    ) -> dict[str, Any]:
        name = ""
        institution_pct = 0.0
        fund_pct = 0.0
        northbound_pct = 0.0
        top10_pct = 0.0
        institution_count = 0
        fund_count = 0
        big_holder_count = 0
        holder_payloads: list[dict[str, Any]] = []
        for _, row in rows.iterrows():
            holder_name = str(row.get("股东名称") or "")
            holder_type = str(row.get("股本性质") or row.get("股东性质") or "")
            holding_pct = self._to_float(row.get("占流通股比例") or row.get("占总流通股本持股比例"))
            holding_shares = self._to_float(row.get("持股数量") or row.get("持股数"))
            if not name:
                name = str(row.get("股票简称") or "")
            is_institution = self._is_institution(holder_name, holder_type)
            is_fund = self._contains_any(holder_name, self.fund_keywords) or self._contains_any(holder_type, self.fund_keywords)
            is_northbound = self._contains_any(holder_name, self.northbound_keywords)
            top10_pct += holding_pct
            if is_institution:
                institution_count += 1
                institution_pct += holding_pct
            if is_fund:
                fund_count += 1
                fund_pct += holding_pct
            if is_northbound:
                northbound_pct += holding_pct
            if holding_pct >= 3 or is_institution:
                big_holder_count += 1
            holder_payloads.append(
                {
                    "name": holder_name,
                    "type": holder_type,
                    "holding_pct": holding_pct,
                    "holding_shares": holding_shares,
                    "is_institution": is_institution,
                    "is_fund": is_fund,
                    "is_northbound": is_northbound,
                }
            )

        previous_institution_pct = self._institution_pct(previous_rows) if previous_rows is not None else None
        institutional_change = (
            round(institution_pct - previous_institution_pct, 3)
            if previous_institution_pct is not None
            else None
        )
        crowding_score = self._crowding_score(
            institution_pct=institution_pct,
            fund_pct=fund_pct,
            northbound_pct=northbound_pct,
            top10_pct=top10_pct,
            institution_count=institution_count,
            fund_count=fund_count,
            big_holder_count=big_holder_count,
            institutional_change_pct=institutional_change,
        )
        return {
            "symbol": symbol,
            "name": name,
            "report_date": report_date,
            "source": self.source,
            "institution_count": institution_count,
            "fund_count": fund_count,
            "big_holder_count": big_holder_count,
            "top_holder_count": int(len(rows)),
            "institution_holding_pct": round(institution_pct, 3),
            "fund_holding_pct": round(fund_pct, 3),
            "northbound_holding_pct": round(northbound_pct, 3),
            "top10_holding_pct": round(top10_pct, 3),
            "institutional_change_pct": institutional_change,
            "crowding_score": crowding_score,
            "data_status": "real_shareholder_ratio",
            "raw": {"holders": holder_payloads[:12], "method": "top_circulating_shareholders"},
            "observed_at": datetime.utcnow(),
        }

    def _institution_pct(self, rows: Any) -> float | None:
        if rows is None or rows.empty:
            return None
        total = 0.0
        for _, row in rows.iterrows():
            holder_name = str(row.get("股东名称") or "")
            holder_type = str(row.get("股本性质") or row.get("股东性质") or "")
            if self._is_institution(holder_name, holder_type):
                total += self._to_float(row.get("占流通股比例") or row.get("占总流通股本持股比例"))
        return total

    def _crowding_score(
        self,
        institution_pct: float,
        fund_pct: float,
        northbound_pct: float,
        top10_pct: float,
        institution_count: int,
        fund_count: int,
        big_holder_count: int,
        institutional_change_pct: float | None,
    ) -> float:
        ratio_score = min(32, institution_pct * 1.45)
        count_score = min(18, institution_count * 2.6)
        fund_score = min(16, fund_count * 3.0 + fund_pct * 0.55)
        northbound_score = min(10, northbound_pct * 1.5)
        concentration_score = min(14, top10_pct * 0.32)
        big_money_score = min(6, big_holder_count * 1.1)
        change_score = max(-6, min(8, (institutional_change_pct or 0) * 1.2))
        return round(max(0, min(98, ratio_score + count_score + fund_score + northbound_score + concentration_score + big_money_score + change_score)), 2)

    def _is_institution(self, holder_name: str, holder_type: str) -> bool:
        text = f"{holder_name} {holder_type}".lower()
        if self._contains_any(text, self.natural_keywords):
            return False
        return self._contains_any(text, self.institution_keywords)

    def _contains_any(self, text: str, keywords: list[str]) -> bool:
        lower = str(text).lower()
        return any(keyword.lower() in lower for keyword in keywords)

    def _to_float(self, value: Any) -> float:
        if value is None:
            return 0.0
        try:
            text = str(value).replace(",", "").replace("%", "").strip()
            if text in {"", "nan", "None", "不变"}:
                return 0.0
            return float(text)
        except Exception:
            return 0.0
