from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.domain import DataQualityLevel, DataQualityLog, DataSource, MarketQuote


DEFAULT_SOURCES = [
    {
        "code": "sina_finance",
        "name": "新浪财经",
        "category": "market_quote",
        "priority": 10,
        "reliability": 0.82,
        "meta": {"role": "primary", "usage": "首选实时行情、分时与日线 K 线；策略信号默认以该源为主"},
    },
    {
        "code": "tencent_quote",
        "name": "腾讯财经行情",
        "category": "market_quote",
        "priority": 20,
        "reliability": 0.68,
        "meta": {"role": "secondary", "usage": "次选 A 股实时快照，用于交叉校验和新浪失败兜底"},
    },
    {
        "code": "akshare_eastmoney",
        "name": "AkShare 东方财富行情",
        "category": "market_quote",
        "priority": 30,
        "reliability": 0.52,
        "meta": {"role": "backup", "usage": "备用行情、日线与资金流入口；实时行情失败时才尝试"},
    },
    {
        "code": "eastmoney_web",
        "name": "东方财富网页数据",
        "category": "market_quote_research",
        "priority": 40,
        "reliability": 0.5,
        "meta": {"role": "fundamental_backup", "usage": "基本面、行业概念、资金流、龙虎榜、财报研报参考；实时行情仅兜底"},
    },
    {
        "code": "demo",
        "name": "本地演示数据",
        "category": "fallback",
        "priority": 999,
        "reliability": 0.1,
        "enabled": False,
        "meta": {"usage": "开发兜底，不可用于真实决策"},
    },
]


class DataQualityService:
    def seed_sources(self, db: Session) -> None:
        existing = {source.code: source for source in db.execute(select(DataSource)).scalars().all()}
        for source in DEFAULT_SOURCES:
            current = existing.get(source["code"])
            if current is None:
                db.add(DataSource(**source))
            else:
                current.name = source["name"]
                current.category = source["category"]
                current.priority = source["priority"]
                current.reliability = source["reliability"]
                current.enabled = bool(source.get("enabled", True))
                current.meta = source["meta"]
        db.commit()

    def record_quote_batch(self, db: Session, quotes: list[dict[str, Any]], source_code: str) -> list[DataQualityLog]:
        source = db.execute(select(DataSource).where(DataSource.code == source_code)).scalar_one_or_none()
        logs: list[DataQualityLog] = []
        now = datetime.utcnow()
        if source:
            source.last_success_at = now
            source.last_error = None

        if not quotes:
            log = DataQualityLog(
                source_code=source_code,
                dataset="market_quotes",
                level=DataQualityLevel.missing,
                message="行情批次为空。",
                payload={},
            )
            db.add(log)
            logs.append(log)
        for quote in quotes:
            level, message = self.assess_quote(quote)
            log = DataQualityLog(
                source_code=str(quote.get("source") or source_code),
                dataset="market_quotes",
                symbol=str(quote.get("symbol", "")),
                level=level,
                message=message,
                payload={
                    "quality": quote.get("quality"),
                    "last_price": quote.get("last_price"),
                    "change_pct": quote.get("change_pct"),
                    "amount": quote.get("amount"),
                    "source_errors": quote.get("source_errors"),
                },
            )
            db.add(log)
            logs.append(log)
        db.commit()
        return logs

    def assess_quote(self, quote: dict[str, Any]) -> tuple[DataQualityLevel, str]:
        quality = str(quote.get("quality") or "")
        last_price = float(quote.get("last_price") or 0)
        amount = float(quote.get("amount") or 0)
        if quality.startswith("demo") or "fallback" in quality:
            return DataQualityLevel.degraded, "使用演示或降级行情，不能用于真实交易判断。"
        if last_price <= 0:
            return DataQualityLevel.invalid, "最新价缺失或小于等于 0。"
        if amount <= 0:
            return DataQualityLevel.invalid, "成交额缺失或小于等于 0。"
        return DataQualityLevel.ok, "行情字段通过基础质量检查。"

    def summary(self, db: Session) -> dict[str, Any]:
        since = datetime.utcnow() - timedelta(hours=24)
        logs = (
            db.execute(
                select(DataQualityLog)
                .where(DataQualityLog.observed_at >= since)
                .order_by(DataQualityLog.observed_at.desc())
                .limit(200)
            )
            .scalars()
            .all()
        )
        latest_quote_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        latest_quotes = (
            db.execute(
                select(MarketQuote).join(
                    latest_quote_times,
                    and_(
                        MarketQuote.symbol == latest_quote_times.c.symbol,
                        MarketQuote.observed_at == latest_quote_times.c.observed_at,
                    ),
                )
            )
            .scalars()
            .all()
        )
        sources = db.execute(select(DataSource).order_by(DataSource.priority.asc())).scalars().all()
        counts = {level.value: 0 for level in DataQualityLevel}
        for log in logs:
            counts[log.level.value] = counts.get(log.level.value, 0) + 1
        return {
            "window_hours": 24,
            "counts": counts,
            "latest_quote_quality": self._latest_quote_quality(latest_quotes),
            "sources": [
                {
                    "code": source.code,
                    "name": source.name,
                    "category": source.category,
                    "priority": source.priority,
                    "reliability": source.reliability,
                    "enabled": source.enabled,
                    "last_success_at": source.last_success_at,
                    "last_error": source.last_error,
                    "meta": source.meta,
                }
                for source in sources
            ],
            "recent_issues": [
                {
                    "source_code": log.source_code,
                    "dataset": log.dataset,
                    "symbol": log.symbol,
                    "level": log.level.value,
                    "message": log.message,
                    "observed_at": log.observed_at,
                }
                for log in logs[:12]
                if log.level != DataQualityLevel.ok
            ],
        }

    def _latest_quote_quality(self, quotes: list[MarketQuote]) -> dict[str, int]:
        result = {"ok": 0, "degraded": 0, "invalid": 0}
        seen: set[str] = set()
        for quote in quotes:
            if quote.symbol in seen:
                continue
            seen.add(quote.symbol)
            level, _ = self.assess_quote(
                {
                    "quality": quote.quality,
                    "last_price": quote.last_price,
                    "amount": quote.amount,
                }
            )
            if level == DataQualityLevel.ok:
                result["ok"] += 1
            elif level == DataQualityLevel.invalid:
                result["invalid"] += 1
            else:
                result["degraded"] += 1
        return result
