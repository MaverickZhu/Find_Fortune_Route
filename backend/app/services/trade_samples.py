from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.domain import (
    Alert,
    AlertStatus,
    MarketQuote,
    PortfolioPosition,
    PositionStatus,
    Strategy,
    StrategySignal,
    TradeAction,
    UserTradeSample,
    WatchlistItem,
)
from app.services.data_quality import DataQualityService
from app.services.market_rules import MarketRuleService
from app.services.portfolio import PortfolioService


class TradeSampleService:
    def record_alert_decision(
        self,
        db: Session,
        alert: Alert,
        action: str,
        quantity: int | None = None,
        notes: str | None = None,
    ) -> UserTradeSample:
        quote = (
            db.execute(
                select(MarketQuote)
                .where(MarketQuote.symbol == alert.symbol)
                .order_by(MarketQuote.observed_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        inherited_strategy_code = self._strategy_code_from_alert(db, alert)
        signal_query = select(StrategySignal).where(StrategySignal.symbol == alert.symbol)
        if inherited_strategy_code:
            signal_query = signal_query.where(StrategySignal.strategy_code == inherited_strategy_code)
        signal = (
            db.execute(
                signal_query.order_by(StrategySignal.generated_at.desc()).limit(1)
            )
            .scalars()
            .first()
        )
        if signal is None and not inherited_strategy_code:
            signal = (
                db.execute(
                    select(StrategySignal)
                    .where(StrategySignal.symbol == alert.symbol)
                    .order_by(StrategySignal.generated_at.desc())
                    .limit(1)
                )
                .scalars()
                .first()
            )
        strategy_code = inherited_strategy_code or (signal.strategy_code if signal else None)
        quote_payload = self._quote_payload(alert.symbol, quote)
        quality_level, quality_message = DataQualityService().assess_quote(quote_payload)
        market_rules = MarketRuleService().evaluate_quote(quote_payload, db=db)
        sample = UserTradeSample(
            symbol=alert.symbol,
            action=TradeAction(action),
            alert_id=alert.id,
            decision_price=float(quote_payload["last_price"]),
            quantity=quantity,
            decision_at=datetime.utcnow(),
            strategy_code=strategy_code,
            status="recorded",
            notes=notes,
            features={
                "alert": {
                    "id": alert.id,
                    "type": alert.alert_type,
                    "message": alert.message,
                    "payload": alert.payload,
                    "triggered_at": alert.triggered_at.isoformat() if alert.triggered_at else None,
                },
                "quote": quote_payload,
                "signal": self._signal_payload(signal),
                "data_quality": {
                    "level": quality_level.value,
                    "message": quality_message,
                },
                "market_rules": market_rules,
            },
        )
        db.add(sample)
        alert.status = AlertStatus.dismissed
        db.flush()
        portfolio_event = PortfolioService().apply_trade_sample(db, sample)
        cleanup_event = self.cleanup_watchlist_after_trade(db, sample, portfolio_event)
        sample.features = {**sample.features, "portfolio_event": portfolio_event, "watchlist_cleanup": cleanup_event}
        db.commit()
        db.refresh(sample)
        return sample

    def apply_manual_trade(self, db: Session, sample: UserTradeSample) -> UserTradeSample:
        portfolio_event = PortfolioService().apply_trade_sample(db, sample)
        cleanup_event = self.cleanup_watchlist_after_trade(db, sample, portfolio_event)
        sample.features = {
            **(sample.features or {}),
            "portfolio_event": portfolio_event,
            "watchlist_cleanup": cleanup_event,
        }
        db.commit()
        db.refresh(sample)
        return sample

    def cleanup_watchlist_after_trade(
        self,
        db: Session,
        sample: UserTradeSample,
        portfolio_event: dict[str, Any] | None,
    ) -> dict[str, Any]:
        event = (portfolio_event or {}).get("event")
        if sample.action == TradeAction.buy and event in {"position_opened", "position_added"}:
            strategy_code = sample.strategy_code or ((portfolio_event or {}).get("position") or {}).get("strategy_code")
            deleted_ids = self._remove_watchlist_tracking(db, sample.symbol)
            return {
                "event": "removed_after_buy",
                "reason": "已确认买入并进入模拟持仓，已同步移出策略观测。",
                "strategy_code": strategy_code,
                "watchlist_ids": deleted_ids,
            }

        if sample.action != TradeAction.sell or (portfolio_event or {}).get("event") != "position_closed":
            return {"event": "not_applicable"}

        strategy_code = sample.strategy_code or ((portfolio_event or {}).get("position") or {}).get("strategy_code")
        deleted_ids = self._remove_watchlist_tracking(db, sample.symbol, strategy_code)
        return {
            "event": "removed_after_sell",
            "reason": "最终卖出并完成模拟交易，已同步移出自选追踪；后续若策略再次命中，将由实时观测重新推荐。",
            "strategy_code": strategy_code,
            "watchlist_ids": deleted_ids,
        }

    def _remove_watchlist_tracking(self, db: Session, symbol: str, strategy_code: str | None = None) -> list[int]:
        watch_query = select(WatchlistItem).where(WatchlistItem.symbol == symbol)
        if strategy_code:
            watch_query = watch_query.where(WatchlistItem.strategy_code == strategy_code)
        watch_items = db.execute(watch_query).scalars().all()
        deleted_ids = [item.id for item in watch_items]
        for item in watch_items:
            db.delete(item)
        db.execute(delete(Alert).where(Alert.symbol == symbol, Alert.status != AlertStatus.dismissed))
        return deleted_ids

    def _strategy_code_from_alert(self, db: Session, alert: Alert) -> str | None:
        payload = alert.payload if isinstance(alert.payload, dict) else {}
        signal_payload = payload.get("signal") if isinstance(payload.get("signal"), dict) else {}
        for value in [payload.get("strategy_code"), signal_payload.get("strategy_code")]:
            if isinstance(value, str) and value:
                return value
        watchlist_id = payload.get("watchlist_id")
        watchlist_item = db.get(WatchlistItem, watchlist_id) if isinstance(watchlist_id, int) else None
        if watchlist_item and watchlist_item.strategy_code:
            return watchlist_item.strategy_code
        latest_watch = (
            db.execute(
                select(WatchlistItem)
                .where(WatchlistItem.symbol == alert.symbol, WatchlistItem.strategy_code.is_not(None))
                .order_by(WatchlistItem.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return latest_watch.strategy_code if latest_watch else None

    def summary(self, db: Session) -> dict[str, Any]:
        samples = (
            db.execute(select(UserTradeSample).order_by(UserTradeSample.decision_at.desc()).limit(80))
            .scalars()
            .all()
        )
        counts = {"buy": 0, "sell": 0, "ignore": 0, "watch": 0}
        realized: list[float] = []
        for sample in samples:
            counts[sample.action.value] = counts.get(sample.action.value, 0) + 1
            if sample.realized_return_pct is not None:
                realized.append(sample.realized_return_pct)
        return {
            "total": len(samples),
            "counts": counts,
            "avg_realized_return_pct": round(sum(realized) / len(realized), 2) if realized else None,
            "recent": [self.serialize(sample) for sample in samples[:8]],
        }

    def strategy_history_drawdowns(self, db: Session) -> list[dict[str, Any]]:
        strategy_names = {
            item.code: item.name
            for item in db.execute(select(Strategy)).scalars().all()
        }
        positions = (
            db.execute(
                select(PortfolioPosition)
                .where(
                    PortfolioPosition.status == PositionStatus.closed,
                    PortfolioPosition.strategy_code.is_not(None),
                    PortfolioPosition.realized_return_pct.is_not(None),
                    PortfolioPosition.exit_at.is_not(None),
                )
                .order_by(PortfolioPosition.exit_at.asc())
            )
            .scalars()
            .all()
        )
        grouped: dict[str, list[PortfolioPosition]] = {}
        for position in positions:
            if position.strategy_code:
                grouped.setdefault(position.strategy_code, []).append(position)

        runs = [
            self._history_drawdown_run(code, rows, strategy_names.get(code, code))
            for code, rows in grouped.items()
        ]
        return sorted(
            runs,
            key=lambda item: (
                item["metrics"].get("sample_count", 0),
                item["end_date"] or "",
            ),
            reverse=True,
        )

    def _history_drawdown_run(
        self,
        strategy_code: str,
        positions: list[PortfolioPosition],
        strategy_name: str,
    ) -> dict[str, Any]:
        equity = 1.0
        peak = 1.0
        equity_curve: list[dict[str, Any]] = []
        drawdown_curve: list[dict[str, Any]] = []
        monthly_start: dict[str, float] = {}
        monthly_end: dict[str, float] = {}
        realized_returns: list[float] = []
        for position in positions:
            realized = float(position.realized_return_pct or 0)
            realized_returns.append(realized)
            equity *= 1 + realized / 100
            peak = max(peak, equity)
            exit_at = position.exit_at or position.entry_at or datetime.utcnow()
            decision_date = exit_at.date().isoformat()
            month = exit_at.strftime("%Y-%m")
            monthly_start.setdefault(month, equity / (1 + realized / 100) if realized != -100 else equity)
            monthly_end[month] = equity
            equity_curve.append({"date": decision_date, "value": round(equity, 4)})
            drawdown_curve.append({"date": decision_date, "value": round((equity / peak - 1) * 100, 2)})

        total_return = round((equity - 1) * 100, 2)
        avg_return = round(sum(realized_returns) / len(realized_returns), 2) if realized_returns else None
        max_drawdown = min((item["value"] for item in drawdown_curve), default=0)
        wins = sum(1 for item in realized_returns if item > 0)
        monthly_returns = [
            {
                "date": month,
                "return_pct": round((monthly_end[month] / start - 1) * 100, 2) if start else 0,
            }
            for month, start in sorted(monthly_start.items())
        ][-12:]
        diagnostics = {
            "data_status": "real_user_trade_samples",
            "source": "portfolio_positions",
            "sample_count": len(positions),
            "realized_count": len(realized_returns),
            "selected_symbols_avg": 1,
            "last_sample_at": (positions[-1].exit_at or positions[-1].entry_at).isoformat() if positions else None,
            "source_breakdown": self._position_source_breakdown(positions),
        }
        metrics = {
            "total_return_pct": total_return,
            "annual_return_pct": total_return,
            "avg_realized_return_pct": avg_return,
            "max_drawdown_pct": round(max_drawdown, 2),
            "sharpe": self._sample_sharpe(realized_returns),
            "calmar": round(total_return / max(0.1, abs(max_drawdown)), 2) if realized_returns else 0,
            "win_rate_pct": round(wins / len(realized_returns) * 100, 2) if realized_returns else 0,
            "turnover_pct": None,
            "alpha_pct": None,
            "benchmark_return_pct": None,
            "fee_adjusted": False,
            "sample_count": len(positions),
        }
        return {
            "id": None,
            "strategy_code": strategy_code,
            "strategy_name": strategy_name,
            "stock_pool": sorted({position.symbol for position in positions}),
            "start_date": (positions[0].exit_at or positions[0].entry_at) if positions else None,
            "end_date": (positions[-1].exit_at or positions[-1].entry_at) if positions else None,
            "metrics": metrics,
            "assumptions": {
                "data_source": "真实虚拟持仓闭环 portfolio_positions",
                "validation": "仅统计已完成卖出且有已实现收益的真实虚拟持仓；未绑定策略、未卖出持仓、无效卖出和模拟/测试回测记录不会进入策略历史回撤分析。",
                "cost_model": "按用户实际确认价计算，暂不额外扣减费用。",
                "constraints": ["样本量较小时不做参数定论", "未绑定策略样本不纳入策略回撤分析", "未平仓持仓不纳入已实现回撤"],
            },
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
            "monthly_returns": monthly_returns,
            "regime_breakdown": self._sample_regime_breakdown(realized_returns),
            "risk_flags": self._sample_risk_flags(realized_returns, max_drawdown),
            "diagnostics": diagnostics,
        }

    def _position_source_breakdown(self, positions: list[PortfolioPosition]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for position in positions:
            meta = position.meta or {}
            sources: list[str] = []
            for key in ("entry_features", "exit_features"):
                features = meta.get(key) if isinstance(meta, dict) else None
                if not isinstance(features, dict):
                    continue
                quote = features.get("quote")
                source = quote.get("source") if isinstance(quote, dict) else None
                source = source or features.get("source") or "manual"
                sources.append(str(source))
            source_key = "+".join(sorted(set(sources))) if sources else "manual"
            counts[source_key] = counts.get(source_key, 0) + 1
        return counts

    def _sample_sharpe(self, returns: list[float]) -> float:
        if len(returns) < 2:
            return 0
        mean = sum(returns) / len(returns)
        variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
        return round(mean / (variance ** 0.5), 2) if variance > 0 else 0

    def _sample_regime_breakdown(self, returns: list[float]) -> list[dict[str, Any]]:
        if not returns:
            return []
        positive = [item for item in returns if item > 0]
        negative = [item for item in returns if item <= 0]
        return [
            {
                "regime": "盈利样本",
                "return_pct": round(sum(positive) / len(positive), 2) if positive else 0,
                "win_rate_pct": 100 if positive else 0,
            },
            {
                "regime": "亏损/持平样本",
                "return_pct": round(sum(negative) / len(negative), 2) if negative else 0,
                "win_rate_pct": 0,
            },
        ]

    def _sample_risk_flags(self, returns: list[float], max_drawdown: float) -> list[str]:
        flags = ["真实用户样本口径"]
        if len(returns) < 5:
            flags.append("样本量偏少")
        if max_drawdown <= -10:
            flags.append("历史样本回撤超过 10%")
        if any(item <= -8 for item in returns):
            flags.append("存在单笔较大亏损")
        return flags

    def serialize(self, sample: UserTradeSample) -> dict[str, Any]:
        return {
            "id": sample.id,
            "symbol": sample.symbol,
            "action": sample.action.value,
            "alert_id": sample.alert_id,
            "decision_price": sample.decision_price,
            "quantity": sample.quantity,
            "decision_at": sample.decision_at,
            "strategy_code": sample.strategy_code,
            "realized_return_pct": sample.realized_return_pct,
            "status": sample.status,
            "notes": sample.notes,
            "features": sample.features,
        }

    def _quote_payload(self, symbol: str, quote: MarketQuote | None) -> dict[str, Any]:
        if quote is None:
            return {
                "symbol": symbol,
                "name": "",
                "observed_at": datetime.utcnow(),
                "last_price": 0,
                "change_pct": 0,
                "volume": 0,
                "amount": 0,
                "source": "missing",
                "quality": "missing",
            }
        return {
            "symbol": quote.symbol,
            "name": quote.name,
            "observed_at": quote.observed_at.isoformat(),
            "last_price": quote.last_price,
            "change_pct": quote.change_pct,
            "volume": quote.volume,
            "amount": quote.amount,
            "source": quote.source,
            "quality": quote.quality,
        }

    def _signal_payload(self, signal: StrategySignal | None) -> dict[str, Any] | None:
        if signal is None:
            return None
        return {
            "id": signal.id,
            "strategy_code": signal.strategy_code,
            "action": signal.action.value,
            "score": signal.score,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "evidence": signal.evidence,
            "generated_at": signal.generated_at.isoformat(),
        }
