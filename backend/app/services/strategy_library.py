from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain import BacktestRun, PortfolioPosition, PositionStatus, Strategy, StrategyLibraryEntry, UserTradeSample
from app.services.strategy_observation import StrategyObservationService


class StrategyLibraryService:
    def sync_from_current_state(self, db: Session) -> dict[str, Any]:
        strategies = db.execute(select(Strategy).order_by(Strategy.id.asc())).scalars().all()
        existing = {
            (entry.code, entry.version): entry
            for entry in db.execute(select(StrategyLibraryEntry)).scalars().all()
        }
        created = 0
        updated = 0
        for strategy in strategies:
            key = (strategy.code, "v1.0.0")
            entry = existing.get(key)
            payload = self._build_entry_payload(db, strategy)
            if entry is None:
                db.add(StrategyLibraryEntry(**payload))
                created += 1
            else:
                for field, value in payload.items():
                    setattr(entry, field, value)
                updated += 1
        db.commit()
        return {"created": created, "updated": updated, "total": created + updated}

    def summary(self, db: Session) -> dict[str, Any]:
        observation_summary = StrategyObservationService().summary(db)
        entries = (
            db.execute(
                select(StrategyLibraryEntry)
                .order_by(StrategyLibraryEntry.status.asc(), StrategyLibraryEntry.updated_at.desc())
            )
            .scalars()
            .all()
        )
        status_counts = {
            row[0]: row[1]
            for row in db.execute(
                select(StrategyLibraryEntry.status, func.count(StrategyLibraryEntry.id)).group_by(StrategyLibraryEntry.status)
            ).all()
        }
        category_counts = {
            row[0]: row[1]
            for row in db.execute(
                select(StrategyLibraryEntry.category, func.count(StrategyLibraryEntry.id)).group_by(StrategyLibraryEntry.category)
            ).all()
        }
        serialized = [
            self.serialize(entry, observation_summary.get("by_strategy", {}).get(entry.code, {}))
            for entry in entries
        ]
        return {
            "total": len(entries),
            "status_counts": status_counts,
            "category_counts": category_counts,
            "entries": serialized,
            "comparison": self._comparison(serialized),
            "observation_pool": observation_summary,
        }

    def serialize(self, entry: StrategyLibraryEntry, observation_metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        observation_metrics = observation_metrics or {}
        return {
            "id": entry.id,
            "code": entry.code,
            "version": entry.version,
            "name": entry.name,
            "category": entry.category,
            "status": entry.status,
            "source": entry.source,
            "thesis": entry.thesis,
            "parameters": entry.parameters,
            "risk_rules": entry.risk_rules,
            "performance": entry.performance,
            "learning_metrics": entry.learning_metrics,
            "observation_metrics": observation_metrics,
            "display_profile": self._display_profile(entry, observation_metrics),
            "tags": entry.tags,
            "created_at": entry.created_at.isoformat(),
            "updated_at": entry.updated_at.isoformat(),
        }

    def _build_entry_payload(self, db: Session, strategy: Strategy) -> dict[str, Any]:
        performance = self._latest_performance(db, strategy.code)
        learning = self._learning_metrics(db, strategy.code)
        status = self._status_from_evidence(performance, learning, strategy.enabled)
        return {
            "code": strategy.code,
            "version": "v1.0.0",
            "name": strategy.name,
            "category": strategy.category,
            "status": status,
            "source": "system_strategy_definition",
            "thesis": strategy.description,
            "parameters": strategy.parameters,
            "risk_rules": strategy.risk_rules,
            "performance": performance,
            "learning_metrics": learning,
            "tags": self._tags(strategy, status),
            "updated_at": datetime.utcnow(),
        }

    def _latest_performance(self, db: Session, code: str) -> dict[str, Any]:
        run = (
            db.execute(
                select(BacktestRun)
                .where(BacktestRun.strategy_code == code)
                .order_by(BacktestRun.created_at.desc())
                .limit(1)
            )
            .scalar_one_or_none()
        )
        if run is None:
            return {
                "backtest_count": 0,
                "annual_return_pct": None,
                "max_drawdown_pct": None,
                "sharpe": None,
                "win_rate_pct": None,
                "alpha_pct": None,
                "last_backtest_at": None,
            }
        metrics = run.metrics or {}
        count = db.execute(select(func.count(BacktestRun.id)).where(BacktestRun.strategy_code == code)).scalar_one()
        return {
            "backtest_count": count,
            "annual_return_pct": metrics.get("annual_return_pct"),
            "benchmark_return_pct": metrics.get("benchmark_return_pct"),
            "alpha_pct": metrics.get("alpha_pct"),
            "max_drawdown_pct": metrics.get("max_drawdown_pct"),
            "sharpe": metrics.get("sharpe"),
            "calmar": metrics.get("calmar"),
            "win_rate_pct": metrics.get("win_rate_pct"),
            "turnover_pct": metrics.get("turnover_pct"),
            "last_backtest_at": run.created_at.isoformat(),
        }

    def _learning_metrics(self, db: Session, code: str) -> dict[str, Any]:
        positions = (
            db.execute(select(PortfolioPosition).where(PortfolioPosition.strategy_code == code))
            .scalars()
            .all()
        )
        active_sample_ids = {
            position.entry_sample_id
            for position in positions
            if position.status == PositionStatus.open and position.entry_sample_id
        }
        for position in positions:
            if position.status == PositionStatus.open and isinstance(position.meta, dict):
                add_sample_id = position.meta.get("last_add_sample_id")
                if isinstance(add_sample_id, int):
                    active_sample_ids.add(add_sample_id)
        realized_sample_ids = {
            position.exit_sample_id
            for position in positions
            if position.status == PositionStatus.closed and position.exit_sample_id
        }
        valid_sample_ids = active_sample_ids | realized_sample_ids
        samples = (
            db.execute(
                select(UserTradeSample)
                .where(UserTradeSample.strategy_code == code, UserTradeSample.id.in_(valid_sample_ids))
                .order_by(UserTradeSample.decision_at.desc())
            )
            .scalars()
            .all()
        ) if valid_sample_ids else []
        action_counts: dict[str, int] = defaultdict(int)
        realized = [
            position.realized_return_pct
            for position in positions
            if position.status == PositionStatus.closed and position.realized_return_pct is not None
        ]
        for sample in samples:
            action = sample.action.value if hasattr(sample.action, "value") else str(sample.action)
            action_counts[action] += 1
        return {
            "sample_count": len(samples),
            "active_observation_count": len(active_sample_ids),
            "closed_trade_count": len(realized_sample_ids),
            "action_counts": dict(action_counts),
            "realized_count": len(realized),
            "avg_realized_return_pct": round(sum(realized) / len(realized), 2) if realized else None,
            "positive_realized_rate_pct": round(sum(1 for item in realized if item > 0) / len(realized) * 100, 2) if realized else None,
            "last_sample_at": samples[0].decision_at.isoformat() if samples else None,
        }

    def _status_from_evidence(self, performance: dict[str, Any], learning: dict[str, Any], enabled: bool) -> str:
        if not enabled:
            return "archived"
        if performance.get("backtest_count", 0) == 0:
            return "candidate"
        drawdown = abs(float(performance.get("max_drawdown_pct") or 0))
        sharpe = float(performance.get("sharpe") or 0)
        samples = int(learning.get("sample_count") or 0)
        if int(performance.get("backtest_count") or 0) == 0:
            return "candidate"
        if sharpe >= 1 and drawdown <= 12 and samples >= 1:
            return "active"
        if sharpe >= 0.8 and drawdown <= 16:
            return "validated"
        return "candidate"

    def _tags(self, strategy: Strategy, status: str) -> list[str]:
        tags = [strategy.category, status]
        for key in strategy.parameters.keys():
            tags.append(str(key))
        return tags[:6]

    def _comparison(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for entry in entries:
            perf = entry["performance"]
            learning = entry["learning_metrics"]
            rows.append(
                {
                    "code": entry["code"],
                    "name": entry["name"],
                    "status": entry["status"],
                    "annual_return_pct": perf.get("annual_return_pct"),
                    "max_drawdown_pct": perf.get("max_drawdown_pct"),
                    "sharpe": perf.get("sharpe"),
                    "sample_count": learning.get("sample_count"),
                    "avg_realized_return_pct": learning.get("avg_realized_return_pct"),
                    "observed_count": entry.get("observation_metrics", {}).get("total"),
                    "avg_observed_return_pct": entry.get("observation_metrics", {}).get("avg_observed_return_pct"),
                    "applicability": entry.get("observation_metrics", {}).get("applicability"),
                }
            )
        return rows

    def _display_profile(self, entry: StrategyLibraryEntry, observation_metrics: dict[str, Any]) -> dict[str, Any]:
        strategy_notes = {
            "multi_factor_alpha": {
                "market_fit": "适合结构性行情，用多维因子寻找综合得分靠前的股票。",
                "decision_use": "作为备选股票池入口，优先结合估值、行业和风险约束再确认。",
                "risk_focus": "警惕因子拥挤、行情风格切换和单一行业暴露。",
            },
            "trend_breakout": {
                "market_fit": "适合放量上行、趋势延续和强势板块扩散阶段。",
                "decision_use": "用于发现强势观察标的，买点需结合涨跌停约束和回撤承受度。",
                "risk_focus": "高位追涨、短线波动放大和趋势失效是主要风险。",
            },
            "mean_reversion": {
                "market_fit": "适合震荡市或短线超跌修复，不适合单边下跌行情。",
                "decision_use": "用于寻找风险释放后的观察候选，必须等待止跌确认。",
                "risk_focus": "避免把趋势性下跌误判为反转机会。",
            },
            "low_vol_quality": {
                "market_fit": "适合风险偏好下降或防守阶段，偏向稳健质量标的。",
                "decision_use": "用于降低组合波动，适合与进攻型策略搭配。",
                "risk_focus": "收益弹性可能不足，强势行情中容易跑输。",
            },
            "money_flow_anomaly": {
                "market_fit": "适合资金活跃、成交额快速放大的交易期。",
                "decision_use": "用于捕捉资金关注度变化，需确认不是一次性脉冲。",
                "risk_focus": "资金异动可能来自短期事件，持续性需要后续验证。",
            },
            "event_driven_watch": {
                "market_fit": "适合政策、公告、财报和行业事件密集阶段。",
                "decision_use": "用于事件线索整理，必须经过人工确认和行情验证。",
                "risk_focus": "信息噪声、预期兑现和事件反转风险较高。",
            },
            "close_daily_multi_factor": {
                "market_fit": "适合收盘后复盘和次日候选准备。",
                "decision_use": "用于盘后生成观察清单，不直接替代盘中买卖判断。",
                "risk_focus": "隔夜消息、跳空和流动性变化会影响次日可执行性。",
            },
        }
        base = strategy_notes.get(
            entry.code,
            {
                "market_fit": "适合在特定市场条件下辅助筛选观察标的。",
                "decision_use": "用于辅助分析，不直接构成买卖结论。",
                "risk_focus": "需结合行情质量、仓位管理和人工确认。",
            },
        )
        applicability = observation_metrics.get("applicability") or "样本不足"
        return {
            **base,
            "applicability": applicability,
            "evidence_label": self._evidence_label(entry, observation_metrics),
        }

    def _evidence_label(self, entry: StrategyLibraryEntry, observation_metrics: dict[str, Any]) -> str:
        user_samples = int((entry.learning_metrics or {}).get("sample_count") or 0)
        observed = int(observation_metrics.get("total") or 0)
        backtests = int((entry.performance or {}).get("backtest_count") or 0)
        if user_samples >= 5:
            return "真实样本优先"
        if observed >= 10:
            return "观测池验证中"
        if backtests > 0:
            return "回测证据"
        return "候选待验证"
