from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import MarketGuardrailState


class MarketGuardrailService:
    max_cross_source_deviation_pct = 0.3
    max_single_quote_change_pct = 9.7
    min_ok_sources = 2
    primary_quote_source = "sina_finance"

    def evaluate_sync(
        self,
        db: Session,
        *,
        selected_source: str | None,
        source_status: list[dict[str, Any]],
        audits: dict[str, dict[str, Any]],
        selected_quotes: list[dict[str, Any]],
    ) -> MarketGuardrailState:
        reasons: list[str] = []
        ok_sources = [item for item in source_status if item["status"] == "ok"]
        failed_sources = [item for item in source_status if item["status"] != "ok"]
        stale_audits = [item for item in audits.values() if item["level"] != "ok"]
        if selected_source == self.primary_quote_source:
            stale_audits = [item for item in stale_audits if int(item.get("sources") or 0) > 1]
        max_deviation = max([item["max_deviation_pct"] for item in audits.values()] or [0])
        blocking_audits = stale_audits
        if selected_source == self.primary_quote_source and max_deviation <= 1.0:
            blocking_audits = []
        jump_quotes = [quote for quote in selected_quotes if abs(float(quote.get("change_pct") or 0)) >= self.max_single_quote_change_pct]
        required_ok_sources = 1 if selected_source == self.primary_quote_source else self.min_ok_sources

        if not selected_source or not selected_quotes:
            reasons.append("无可用主行情源。")
        if len(ok_sources) < required_ok_sources:
            reasons.append(f"可用真实行情源不足 {required_ok_sources} 个。")
        if blocking_audits:
            reasons.append(f"{len(blocking_audits)} 个标的跨源一致性不足。")
        if blocking_audits and max_deviation > self.max_cross_source_deviation_pct:
            reasons.append(f"最大跨源价差 {round(max_deviation, 4)}%，超过 {self.max_cross_source_deviation_pct}%。")
        jump_warnings = []
        if stale_audits and not blocking_audits:
            jump_warnings.append(f"{len(stale_audits)} 个标的跨源一致性不足，新浪主源模式下仅记录为校验提示。")
        if jump_quotes:
            jump_warnings.append(f"{len(jump_quotes)} 个标的接近涨跌停或出现异常跳价。")

        status = "healthy"
        mode = "normal"
        if reasons:
            status = "guarded"
            mode = "observe_only"
        if not selected_source or not selected_quotes:
            status = "blocked"
            mode = "data_blocked"

        state = MarketGuardrailState(
            observed_at=datetime.utcnow(),
            status=status,
            mode=mode,
            selected_source=selected_source,
            source_ok_count=len(ok_sources),
            source_fail_count=len(failed_sources),
            stale_symbol_count=len(stale_audits),
            max_deviation_pct=round(max_deviation, 4),
            reasons=reasons,
            payload={
                "source_status": source_status,
                "stale_symbols": [item["symbol"] for item in stale_audits],
                "jump_symbols": [quote["symbol"] for quote in jump_quotes],
                "warnings": jump_warnings,
            },
        )
        db.add(state)
        db.commit()
        db.refresh(state)
        return state

    def latest(self, db: Session) -> dict[str, Any]:
        state = (
            db.execute(select(MarketGuardrailState).order_by(MarketGuardrailState.observed_at.desc()).limit(1))
            .scalar_one_or_none()
        )
        if state is None:
            return {
                "status": "unknown",
                "mode": "observe_only",
                "selected_source": None,
                "source_ok_count": 0,
                "source_fail_count": 0,
                "stale_symbol_count": 0,
                "max_deviation_pct": 0,
                "reasons": ["尚未完成真实行情保护阈值评估。"],
                "observed_at": None,
            }
        return self.serialize(state)

    def recent_unhealthy_count(self, db: Session, minutes: int = 30) -> int:
        since = datetime.utcnow() - timedelta(minutes=minutes)
        states = (
            db.execute(
                select(MarketGuardrailState)
                .where(MarketGuardrailState.observed_at >= since, MarketGuardrailState.status != "healthy")
            )
            .scalars()
            .all()
        )
        return len(states)

    def serialize(self, state: MarketGuardrailState) -> dict[str, Any]:
        return {
            "id": state.id,
            "status": state.status,
            "mode": state.mode,
            "selected_source": state.selected_source,
            "source_ok_count": state.source_ok_count,
            "source_fail_count": state.source_fail_count,
            "stale_symbol_count": state.stale_symbol_count,
            "max_deviation_pct": state.max_deviation_pct,
            "reasons": state.reasons,
            "payload": state.payload,
            "observed_at": state.observed_at.isoformat(),
        }
