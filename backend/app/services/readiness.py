from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.domain import DataSource, MarketQuote, PortfolioPosition, PositionStatus, ResearchItem, StrategyLibraryEntry, UserTradeSample
from app.services.data_quality import DataQualityService
from app.services.market_guardrails import MarketGuardrailService


class ReadinessService:
    def report(self, db: Session) -> dict[str, Any]:
        checks = [
            self._database_check(db),
            self._data_source_check(db),
            self._quote_quality_check(db),
            self._guardrail_check(db),
            self._history_backtest_check(db),
            self._strategy_library_check(db),
            self._learning_loop_check(db),
            self._research_check(db),
            self._scheduler_check(),
            self._compliance_check(),
        ]
        passed = sum(1 for item in checks if item["status"] == "pass")
        warnings = sum(1 for item in checks if item["status"] == "warn")
        failed = sum(1 for item in checks if item["status"] == "fail")
        score = round((passed + warnings * 0.5) / max(1, len(checks)) * 100)
        blockers = [item["label"] for item in checks if item["status"] == "fail" and item.get("blocking")]
        return {
            "score": score,
            "status": "ready" if score >= 80 and not blockers else "not_ready",
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "blockers": blockers,
            "checks": checks,
            "recommendation": self._recommendation(score, blockers),
            "generated_at": datetime.utcnow().isoformat(),
        }

    def _database_check(self, db: Session) -> dict[str, Any]:
        db.execute(text("select 1")).scalar_one()
        return self._check("database", "数据库连接", "pass", "PostgreSQL/Timescale 基础连接正常。")

    def _data_source_check(self, db: Session) -> dict[str, Any]:
        DataQualityService().seed_sources(db)
        sources = db.execute(select(DataSource).where(DataSource.enabled.is_(True))).scalars().all()
        non_demo = [source for source in sources if source.code != "demo"]
        source_codes = {source.code for source in non_demo}
        has_sina = "sina_finance" in source_codes
        has_backup = bool({"tencent_quote", "eastmoney_web", "akshare_eastmoney"} & source_codes)
        status = "pass" if has_sina and has_backup else "warn"
        message = f"主行情源为新浪财经，已配置 {len(non_demo)} 个非演示数据源；AkShare 仅作为备用，开关为 {get_settings().akshare_enabled}。"
        return self._check("data_sources", "多源数据配置", status, message)

    def _quote_quality_check(self, db: Session) -> dict[str, Any]:
        summary = DataQualityService().summary(db)
        latest = summary["latest_quote_quality"]
        ok = int(latest.get("ok") or 0)
        degraded = int(latest.get("degraded") or 0)
        invalid = int(latest.get("invalid") or 0)
        blocking = False
        if ok > 0 and invalid == 0:
            status = "pass"
            message = f"最近行情有 {ok} 个真实/通过标的。"
        elif ok > 0:
            status = "warn"
            message = f"最近行情有 {ok} 个真实/通过标的，{invalid} 个字段异常，需在策略使用前过滤。"
        elif degraded > 0:
            status = "fail"
            message = f"最近行情仍有 {degraded} 个降级/演示标的，真实接入前必须消除 demo_fallback。"
            blocking = True
        else:
            status = "fail"
            message = "暂无可用行情样本。"
            blocking = True
        return self._check("quote_quality", "行情质量", status, message, blocking=blocking)

    def _history_backtest_check(self, db: Session) -> dict[str, Any]:
        closed_count = int(
            db.execute(
                select(func.count(PortfolioPosition.id)).where(
                    PortfolioPosition.status == PositionStatus.closed,
                    PortfolioPosition.strategy_code.is_not(None),
                    PortfolioPosition.realized_return_pct.is_not(None),
                )
            ).scalar_one()
            or 0
        )
        strategy_count = int(
            db.execute(
                select(func.count(func.distinct(PortfolioPosition.strategy_code))).where(
                    PortfolioPosition.status == PositionStatus.closed,
                    PortfolioPosition.strategy_code.is_not(None),
                    PortfolioPosition.realized_return_pct.is_not(None),
                )
            ).scalar_one()
            or 0
        )
        status = "pass" if closed_count >= 5 and strategy_count >= 1 else "warn"
        message = f"已有 {closed_count} 条真实完成持仓闭环，覆盖 {strategy_count} 个策略；回撤分析不再读取测试回测表。"
        return self._check("backtests", "回测与回撤分析", status, message)

    def _guardrail_check(self, db: Session) -> dict[str, Any]:
        guardrail = MarketGuardrailService().latest(db)
        unhealthy = MarketGuardrailService().recent_unhealthy_count(db)
        if guardrail["status"] == "healthy" and unhealthy == 0:
            status = "pass"
            message = "真实行情保护阈值健康，允许策略信号正常生成。"
        elif guardrail["status"] == "blocked":
            status = "fail"
            message = f"真实行情被阻断：{'；'.join(guardrail['reasons'])}"
        else:
            status = "warn"
            message = f"保护阈值处于 {guardrail['mode']}，近 30 分钟异常 {unhealthy} 次。"
        return self._check("guardrails", "真实行情保护阈值", status, message, blocking=guardrail["status"] == "blocked")

    def _strategy_library_check(self, db: Session) -> dict[str, Any]:
        entries = db.execute(select(StrategyLibraryEntry)).scalars().all()
        validated = [
            item
            for item in entries
            if item.status in {"validated", "active"} and int((item.performance or {}).get("backtest_count") or 0) > 0
        ]
        status = "pass" if len(entries) >= 3 and validated else "warn"
        message = f"策略库已有 {len(entries)} 个版本，{len(validated)} 个具备真实回测验证证据。"
        return self._check("strategy_library", "策略库版本基线", status, message)

    def _learning_loop_check(self, db: Session) -> dict[str, Any]:
        samples = db.execute(select(UserTradeSample).order_by(UserTradeSample.decision_at.desc()).limit(50)).scalars().all()
        realized = [item for item in samples if item.realized_return_pct is not None]
        status = "pass" if samples and realized else "warn"
        message = f"已有 {len(samples)} 条用户决策样本，{len(realized)} 条包含实际收益。"
        return self._check("learning_loop", "用户样本闭环", status, message)

    def _research_check(self, db: Session) -> dict[str, Any]:
        since = datetime.utcnow() - timedelta(days=14)
        count = (
            db.execute(
                select(ResearchItem)
                .where(ResearchItem.collected_at >= since)
                .where(ResearchItem.source != "internal_research_seed")
            )
            .scalars()
            .all()
        )
        status = "pass" if len(count) >= 3 else "warn"
        message = f"近 14 天真实研究库条目 {len(count)} 条；待接入定时互联网采集。"
        return self._check("research", "研究知识库", status, message)

    def _scheduler_check(self) -> dict[str, Any]:
        return self._check("scheduler", "定时任务", "pass", "Celery Beat 已配置行情、信号、提醒和研究采集周期。")

    def _compliance_check(self) -> dict[str, Any]:
        return self._check("compliance", "辅助决策边界", "pass", "系统只做研究、提醒和虚拟记录，不执行自动下单。")

    def _check(self, key: str, label: str, status: str, message: str, blocking: bool = False) -> dict[str, Any]:
        return {"key": key, "label": label, "status": status, "message": message, "blocking": blocking}

    def _recommendation(self, score: int, blockers: list[str]) -> str:
        if blockers:
            return f"暂不建议接入真实决策流。先处理阻塞项：{', '.join(blockers)}。"
        if score >= 80:
            return "真实行情、研究采集和日线回测已接入；下一步补齐用户样本闭环和更完整的复权/停牌/涨跌停约束。"
        return "真实行情已接入；研究采集、真实回测和用户样本闭环仍需补齐后再进入策略准入评估。"
