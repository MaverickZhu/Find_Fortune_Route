from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.domain import (
    MarketQuote,
    PortfolioPosition,
    PositionStatus,
    SignalAction,
    Strategy,
    StrategyObservation,
    StrategySignal,
    TradeAction,
    UserTradeSample,
)


class StrategyObservationService:
    daily_top_limit = 3
    min_signal_score = 60

    def sync(self, db: Session) -> dict[str, int]:
        created_top3 = self.sync_daily_top3(db)
        created_user = self.sync_user_samples(db)
        updated = self.refresh_returns(db)
        db.commit()
        return {"daily_top3": created_top3, "user_trade": created_user, "updated_returns": updated}

    def sync_daily_top3(self, db: Session, trade_date: date | None = None) -> int:
        trade_date = trade_date or self._latest_signal_trade_date(db)
        if trade_date is None:
            return 0

        enabled_codes = {
            row[0]
            for row in db.execute(select(Strategy.code).where(Strategy.enabled.is_(True))).all()
        }
        candidate_rows = (
            db.execute(
                select(StrategySignal)
                .where(
                    StrategySignal.strategy_code.in_(enabled_codes),
                    StrategySignal.score >= self.min_signal_score,
                    StrategySignal.action.in_([SignalAction.buy, SignalAction.watch, SignalAction.hold]),
                )
                .order_by(
                    StrategySignal.strategy_code.asc(),
                    StrategySignal.generated_at.desc(),
                    StrategySignal.score.desc(),
                    StrategySignal.confidence.desc(),
                )
            )
            .scalars()
            .all()
        )
        latest_dates: dict[str, date] = {}
        for row in candidate_rows:
            latest_dates.setdefault(row.strategy_code, row.generated_at.date())
        rows = [row for row in candidate_rows if row.generated_at.date() == latest_dates.get(row.strategy_code)]
        quote_map = self._latest_quotes(db, [row.symbol for row in rows])
        existing = self._existing_keys(db, None, "daily_top3")
        grouped: dict[str, list[StrategySignal]] = defaultdict(list)
        for row in rows:
            grouped[row.strategy_code].append(row)

        created = 0
        for code, signals in grouped.items():
            for signal in signals[: self.daily_top_limit]:
                signal_trade_date = signal.generated_at.date()
                key = (signal.strategy_code, signal.symbol, signal_trade_date, "daily_top3")
                if key in existing:
                    continue
                quote = quote_map.get(signal.symbol)
                entry_price = self._entry_price(signal, quote)
                if entry_price <= 0:
                    continue
                db.add(
                    StrategyObservation(
                        strategy_code=signal.strategy_code,
                        symbol=signal.symbol,
                        name=quote.name if quote else signal.symbol,
                        trade_date=signal_trade_date,
                        source_type="daily_top3",
                        signal_id=signal.id,
                        action=signal.action.value if hasattr(signal.action, "value") else str(signal.action),
                        score=signal.score,
                        confidence=signal.confidence,
                        entry_price=entry_price,
                        current_price=quote.last_price if quote else entry_price,
                        current_return_pct=0,
                        status="observing",
                        reason=signal.reason,
                        evidence={"signal": signal.evidence or {}, "selection": "daily_strategy_top3"},
                        observed_at=signal.generated_at,
                    )
                )
                existing.add(key)
                created += 1
        return created

    def sync_user_samples(self, db: Session) -> int:
        active_sample_ids, realized_sample_ids = self._valid_user_trade_sample_ids(db)
        valid_sample_ids = active_sample_ids | realized_sample_ids
        self._purge_stale_user_trade_observations(db, valid_sample_ids)
        if not valid_sample_ids:
            return 0

        samples = (
            db.execute(
                select(UserTradeSample)
                .where(
                    UserTradeSample.id.in_(valid_sample_ids),
                    UserTradeSample.strategy_code.is_not(None),
                    UserTradeSample.action.in_([TradeAction.buy, TradeAction.sell]),
                )
                .order_by(UserTradeSample.decision_at.desc())
                .limit(1000)
            )
            .scalars()
            .all()
        )
        quote_map = self._latest_quotes(db, [sample.symbol for sample in samples])
        existing_sample_ids = {
            row[0]
            for row in db.execute(
                select(StrategyObservation.user_sample_id).where(
                    StrategyObservation.source_type == "user_trade",
                    StrategyObservation.user_sample_id.is_not(None),
                )
            ).all()
        }
        created = 0
        for sample in samples:
            if sample.id in existing_sample_ids or not sample.strategy_code:
                continue
            quote = quote_map.get(sample.symbol)
            current_price = quote.last_price if quote else sample.decision_price
            realized = sample.realized_return_pct if sample.id in realized_sample_ids else None
            current_return = realized if realized is not None else round((current_price / sample.decision_price - 1) * 100, 2)
            status = "realized" if sample.id in realized_sample_ids else "observing"
            trade_date = sample.decision_at.date()
            quote_payload = sample.features.get("quote", {}) if isinstance(sample.features, dict) else {}
            db.add(
                StrategyObservation(
                    strategy_code=sample.strategy_code,
                    symbol=sample.symbol,
                    name=str(quote_payload.get("name") or (quote.name if quote else sample.symbol)),
                    trade_date=trade_date,
                    source_type="user_trade",
                    user_sample_id=sample.id,
                    action=sample.action.value if hasattr(sample.action, "value") else str(sample.action),
                    entry_price=sample.decision_price,
                    current_price=current_price,
                    current_return_pct=current_return,
                    horizon_days=max(0, (datetime.utcnow().date() - trade_date).days),
                    status=status,
                    reason=sample.notes or ("用户真实完成交易样本。" if status == "realized" else "用户真实持仓观察样本。"),
                    evidence={"sample_status": sample.status, "sample_id": sample.id, "features": sample.features or {}},
                    observed_at=sample.decision_at,
                )
            )
            existing_sample_ids.add(sample.id)
            created += 1
        return created

    def _valid_user_trade_sample_ids(self, db: Session) -> tuple[set[int], set[int]]:
        positions = db.execute(select(PortfolioPosition)).scalars().all()
        active_sample_ids: set[int] = set()
        realized_sample_ids: set[int] = set()
        for position in positions:
            if position.status == PositionStatus.open and position.entry_sample_id:
                active_sample_ids.add(position.entry_sample_id)
            if position.status == PositionStatus.open and isinstance(position.meta, dict):
                add_sample_id = position.meta.get("last_add_sample_id")
                if isinstance(add_sample_id, int):
                    active_sample_ids.add(add_sample_id)
            if position.status == PositionStatus.closed and position.exit_sample_id:
                realized_sample_ids.add(position.exit_sample_id)
        return active_sample_ids, realized_sample_ids

    def _purge_stale_user_trade_observations(self, db: Session, valid_sample_ids: set[int]) -> None:
        query = delete(StrategyObservation).where(StrategyObservation.source_type == "user_trade")
        if valid_sample_ids:
            query = query.where(
                StrategyObservation.user_sample_id.is_(None)
                | StrategyObservation.user_sample_id.not_in(valid_sample_ids)
            )
        db.execute(query)

    def refresh_returns(self, db: Session) -> int:
        rows = (
            db.execute(
                select(StrategyObservation)
                .where(StrategyObservation.status == "observing")
                .order_by(StrategyObservation.observed_at.desc())
                .limit(3000)
            )
            .scalars()
            .all()
        )
        quote_map = self._latest_quotes(db, [row.symbol for row in rows])
        updated = 0
        today = datetime.utcnow().date()
        for row in rows:
            quote = quote_map.get(row.symbol)
            if not quote or row.entry_price <= 0:
                continue
            row.current_price = quote.last_price
            row.current_return_pct = round((quote.last_price / row.entry_price - 1) * 100, 2)
            row.horizon_days = max(0, (today - row.trade_date).days)
            row.updated_at = datetime.utcnow()
            updated += 1
        return updated

    def summary(self, db: Session) -> dict[str, Any]:
        rows = db.execute(select(StrategyObservation).order_by(StrategyObservation.observed_at.desc()).limit(2000)).scalars().all()
        by_strategy: dict[str, dict[str, Any]] = {}
        for row in rows:
            bucket = by_strategy.setdefault(
                row.strategy_code,
                {
                    "total": 0,
                    "user_trade_count": 0,
                    "daily_top3_count": 0,
                    "observing_count": 0,
                    "realized_count": 0,
                    "returns": [],
                    "recent": [],
                },
            )
            bucket["total"] += 1
            if row.source_type == "user_trade":
                bucket["user_trade_count"] += 1
            if row.source_type == "daily_top3":
                bucket["daily_top3_count"] += 1
            if row.status == "observing":
                bucket["observing_count"] += 1
            if row.status == "realized":
                bucket["realized_count"] += 1
            if row.current_return_pct is not None:
                bucket["returns"].append(row.current_return_pct)
            if len(bucket["recent"]) < 5:
                bucket["recent"].append(self.serialize(row))

        return {
            "total": len(rows),
            "by_strategy": {code: self._finalize_bucket(bucket) for code, bucket in by_strategy.items()},
        }

    def serialize(self, row: StrategyObservation) -> dict[str, Any]:
        return {
            "id": row.id,
            "strategy_code": row.strategy_code,
            "symbol": row.symbol,
            "name": row.name,
            "trade_date": row.trade_date.isoformat(),
            "source_type": row.source_type,
            "action": row.action,
            "score": row.score,
            "confidence": row.confidence,
            "entry_price": row.entry_price,
            "current_price": row.current_price,
            "current_return_pct": row.current_return_pct,
            "horizon_days": row.horizon_days,
            "status": row.status,
            "reason": row.reason,
        }

    def _finalize_bucket(self, bucket: dict[str, Any]) -> dict[str, Any]:
        returns = bucket.pop("returns", [])
        bucket["avg_observed_return_pct"] = round(sum(returns) / len(returns), 2) if returns else None
        bucket["positive_rate_pct"] = round(sum(1 for item in returns if item > 0) / len(returns) * 100, 2) if returns else None
        bucket["applicability"] = self._applicability(bucket)
        return bucket

    def _applicability(self, bucket: dict[str, Any]) -> str:
        count = int(bucket.get("total") or 0)
        avg_return = bucket.get("avg_observed_return_pct")
        positive_rate = bucket.get("positive_rate_pct")
        if count < 5:
            return "样本不足"
        if avg_return is not None and avg_return > 2 and (positive_rate or 0) >= 55:
            return "当前适用"
        if avg_return is not None and avg_return < -2:
            return "需要降权"
        return "继续观察"

    def _latest_signal_trade_date(self, db: Session) -> date | None:
        latest = db.execute(select(func.max(StrategySignal.generated_at))).scalar_one_or_none()
        return latest.date() if latest else None

    def _existing_keys(self, db: Session, trade_date: date | None, source_type: str) -> set[tuple[str, str, date, str]]:
        query = select(
            StrategyObservation.strategy_code,
            StrategyObservation.symbol,
            StrategyObservation.trade_date,
            StrategyObservation.source_type,
        ).where(StrategyObservation.source_type == source_type)
        if trade_date is not None:
            query = query.where(StrategyObservation.trade_date == trade_date)
        rows = db.execute(query).all()
        return {(row[0], row[1], row[2], row[3]) for row in rows}

    def _entry_price(self, signal: StrategySignal, quote: MarketQuote | None) -> float:
        evidence = signal.evidence or {}
        price = evidence.get("last_price")
        if isinstance(price, (int, float)) and price > 0:
            return float(price)
        return float(quote.last_price) if quote else 0

    def _latest_quotes(self, db: Session, symbols: list[str]) -> dict[str, MarketQuote]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        if not unique_symbols:
            return {}
        latest = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.symbol.in_(unique_symbols), MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote).join(
                    latest,
                    and_(MarketQuote.symbol == latest.c.symbol, MarketQuote.observed_at == latest.c.observed_at),
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: row for row in rows}
