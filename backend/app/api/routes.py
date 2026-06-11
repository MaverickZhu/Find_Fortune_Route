import re
from html import unescape
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import (
    Alert,
    AlertStatus,
    BacktestRun,
    DailyBar,
    DataQualityLog,
    MarketGuardrailState,
    MarketQuote,
    PortfolioPosition,
    ResearchItem,
    Stock,
    StockStatus,
    Strategy,
    StrategyLibraryEntry,
    StrategySignal,
    UserTradeSample,
    WatchlistItem,
)
from app.schemas.domain import (
    AlertDecisionCreate,
    DashboardOut,
    StrategyPickRequest,
    StrategyPickResponse,
    TradeSampleCreate,
    WatchlistCreate,
    WatchlistOut,
    WatchlistUpdate,
)
from app.services.alerts import AlertService
from app.services.close_daily_runner import CloseDailyStrategyRunner
from app.services.data_quality import DataQualityService
from app.services.institutional_holdings import InstitutionalHoldingService
from app.services.market_data import MarketDataProvider
from app.services.market_guardrails import MarketGuardrailService
from app.services.market_rules import MarketRuleService
from app.services.portfolio import PortfolioService
from app.services.readiness import ReadinessService
from app.services.real_data_ingestion import RealDataIngestionService
from app.services.research import ResearchService
from app.services.sector_linkage import SectorLinkageService
from app.services.source_probe import SourceProbeService
from app.services.stock_status import StockStatusService
from app.services.strategy_engine import StrategyEngine
from app.services.strategy_library import StrategyLibraryService
from app.services.strategy_observation import StrategyObservationService
from app.services.trade_samples import TradeSampleService
from app.services.weekly_analysis import WeeklyAnalysisService

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/bootstrap")
def bootstrap(db: Session = Depends(get_db)) -> dict[str, int]:
    settings = get_settings()
    quality = DataQualityService()
    engine = StrategyEngine()
    engine.seed_strategies(db)
    quality.seed_sources(db)

    market_sync = RealDataIngestionService().sync_readonly(db, settings.stock_pool)

    generated = engine.generate_signals(db, limit=6000)
    research_count = ResearchService().collect_real_research(db)
    alerts = AlertService().evaluate_watchlist(db)
    return {
        "quotes": market_sync["quotes"],
        "quality_logs": market_sync["quality_logs"] + market_sync["audit_logs"],
        "signals": len(generated),
        "research": research_count,
        "backtests": 0,
        "alerts": len(alerts),
    }


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(db: Session = Depends(get_db)) -> DashboardOut:
    strategy_engine = StrategyEngine()
    strategy_engine.seed_strategies(db)
    DataQualityService().seed_sources(db)
    latest_quote_times = (
        select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
        .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
        .group_by(MarketQuote.symbol)
        .subquery()
    )
    latest_quote_rows = (
        db.execute(
            select(MarketQuote)
            .join(
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
    quotes = sorted(
        [item for item in latest_quote_rows if item.source != "demo" and item.amount > 0],
        key=lambda item: (item.amount, abs(item.change_pct)),
        reverse=True,
    )[:40]
    visible_quotes = _refresh_visible_quotes(quotes)
    excluded_signal_symbols = strategy_engine.excluded_simulated_symbols(db)
    signal_rows_raw = (
        db.execute(select(StrategySignal).order_by(StrategySignal.generated_at.desc(), StrategySignal.score.desc()).limit(80))
        .scalars()
        .all()
    )
    signal_rows = [item for item in signal_rows_raw if item.symbol not in excluded_signal_symbols][:20]
    signals = [
        {
            "id": item.id,
            "strategy_code": item.strategy_code,
            "symbol": item.symbol,
            "generated_at": item.generated_at,
            "action": item.action.value if hasattr(item.action, "value") else item.action,
            "score": item.score,
            "confidence": item.confidence,
            "reason": item.reason,
            "evidence": item.evidence,
        }
        for item in signal_rows
    ]
    board_counts: dict[str, int] = {}
    for quote in latest_quote_rows:
        board = StockStatusService().infer(quote.symbol, quote.name)["board"]
        board_counts[board] = board_counts.get(board, 0) + 1
    signal_count, signal_avg = db.execute(
        select(func.count(func.distinct(StrategySignal.symbol)), func.avg(StrategySignal.score))
    ).one()
    alerts = (
        db.execute(
            select(Alert)
            .where(Alert.status == AlertStatus.triggered)
            .order_by(Alert.created_at.desc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    watchlist = db.execute(select(WatchlistItem).order_by(WatchlistItem.created_at.desc()).limit(20)).scalars().all()
    watchlist_payload = [
        {
            "id": item.id,
            "symbol": item.symbol,
            "name": item.name,
            "target_buy": item.target_buy,
            "target_sell": item.target_sell,
            "stop_loss": item.stop_loss,
            "take_profit": item.take_profit,
            "strategy_code": item.strategy_code,
            "created_at": item.created_at,
        }
        for item in watchlist
    ]
    strategies = db.execute(select(Strategy).where(Strategy.enabled.is_(True)).order_by(Strategy.id.asc())).scalars().all()
    research_items = db.execute(select(ResearchItem).order_by(ResearchItem.collected_at.desc()).limit(40)).scalars().all()
    trade_sample_service = TradeSampleService()
    return DashboardOut(
        market_overview={
            "stocks": db.execute(select(func.count(Stock.symbol))).scalar_one(),
            "quote_symbols": len({item.symbol for item in latest_quote_rows}),
            "up_symbols": sum(1 for item in latest_quote_rows if item.change_pct > 0),
            "down_symbols": sum(1 for item in latest_quote_rows if item.change_pct < 0),
            "total_amount": sum(item.amount for item in latest_quote_rows),
            "signal_count": int(signal_count or 0),
            "avg_signal_score": round(float(signal_avg or 0), 2),
            "latest_observed_at": max((item.observed_at for item in latest_quote_rows), default=None),
            "board_distribution": board_counts,
        },
        market_quotes=visible_quotes,
        strategy_observations=strategy_engine.strategy_observation_groups(db, limit_per_strategy=10, min_score=55),
        signals=signals,
        alerts=alerts,
        watchlist=watchlist_payload,
        strategies=[
            {
                "code": item.code,
                "name": item.name,
                "category": item.category,
                "description": item.description,
                "parameters": item.parameters,
                "risk_rules": item.risk_rules,
            }
            for item in strategies
        ],
        research=_serialize_research_items(research_items, limit=10),
        backtests=trade_sample_service.strategy_history_drawdowns(db),
        data_quality=DataQualityService().summary(db),
        market_rules=MarketRuleService().dashboard_summary(
            [
                {
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "last_price": item["last_price"],
                    "change_pct": item["change_pct"],
                }
                for item in visible_quotes
            ],
            db=db,
        ),
        trade_samples=trade_sample_service.summary(db),
        portfolio=PortfolioService().summary(db),
        strategy_library=StrategyLibraryService().summary(db),
        weekly_analysis=WeeklyAnalysisService().report(db),
        sector_linkage=SectorLinkageService().dashboard_snapshot(db),
        readiness=ReadinessService().report(db),
        guardrails=MarketGuardrailService().latest(db),
    )


def _refresh_visible_quotes(quotes: list[MarketQuote]) -> list[dict]:
    payloads = [_quote_to_payload(item) for item in quotes]
    symbols = [item["symbol"] for item in payloads]
    if not symbols:
        return payloads
    try:
        fresh_quotes = MarketDataProvider().fetch_realtime_quotes(symbols)
    except Exception:
        return payloads
    fresh_map = {
        item["symbol"]: item
        for item in fresh_quotes
        if item.get("quality") == "ok" and float(item.get("last_price") or 0) > 0
    }
    refreshed = []
    for payload in payloads:
        fresh = fresh_map.get(payload["symbol"])
        if fresh:
            refreshed.append(
                {
                    **payload,
                    "name": fresh.get("name") or payload["name"],
                    "observed_at": fresh.get("observed_at") or payload["observed_at"],
                    "last_price": float(fresh.get("last_price") or payload["last_price"]),
                    "change_pct": float(fresh.get("change_pct") or 0),
                    "volume": float(fresh.get("volume") or 0),
                    "amount": float(fresh.get("amount") or payload["amount"]),
                    "source": str(fresh.get("source") or payload["source"]),
                    "quality": str(fresh.get("quality") or payload["quality"]),
                }
            )
        else:
            refreshed.append(payload)
    return sorted(refreshed, key=lambda item: (item["amount"], abs(item["change_pct"])), reverse=True)


def _serialize_research_items(items: list[ResearchItem], limit: int = 10) -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    for item in items:
        key = _research_title_key(item.title)
        if not key or key in seen:
            continue
        seen.add(key)
        summary = item.summary or item.title
        rows.append(
            {
                "title": item.title,
                "source": item.source,
                "url": item.url,
                "summary": summary,
                "is_summary_complete": _research_title_key(summary) != key and len(summary.strip()) >= 36,
                "credibility": item.credibility,
                "tags": item.tags,
                "published_at": item.published_at,
                "collected_at": item.collected_at,
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _research_title_key(value: str) -> str:
    value = unescape(value or "")
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())
    return value[:80]


def _quote_to_payload(item: MarketQuote) -> dict:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "observed_at": item.observed_at,
        "last_price": item.last_price,
        "change_pct": item.change_pct,
        "volume": item.volume,
        "amount": item.amount,
        "source": item.source,
        "quality": item.quality,
    }


@router.get("/stocks/{symbol}/detail")
def stock_detail(symbol: str, db: Session = Depends(get_db)) -> dict:
    quote = db.execute(
        select(MarketQuote).where(MarketQuote.symbol == symbol).order_by(MarketQuote.observed_at.desc()).limit(1)
    ).scalar_one_or_none()
    signals = (
        db.execute(
            select(StrategySignal)
            .where(StrategySignal.symbol == symbol)
            .order_by(StrategySignal.generated_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    daily_rows = (
        db.execute(
            select(DailyBar)
            .where(DailyBar.symbol == symbol)
            .order_by(DailyBar.trade_date.desc())
            .limit(90)
        )
        .scalars()
        .all()
    )
    daily_payload = [
        {
            "trade_date": row.trade_date.date().isoformat(),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "amount": row.amount,
            "turnover_rate": row.turnover_rate,
            "source": row.source,
        }
        for row in reversed(daily_rows)
    ]
    quote_payload = None
    if quote:
        quote_payload = {
            "symbol": quote.symbol,
            "name": quote.name,
            "observed_at": quote.observed_at,
            "last_price": quote.last_price,
            "change_pct": quote.change_pct,
            "volume": quote.volume,
            "amount": quote.amount,
            "source": quote.source,
            "quality": quote.quality,
        }
    signal_payload = [
        {
            "id": item.id,
            "strategy_code": item.strategy_code,
            "symbol": item.symbol,
            "generated_at": item.generated_at,
            "action": item.action.value if hasattr(item.action, "value") else item.action,
            "score": item.score,
            "confidence": item.confidence,
            "reason": item.reason,
            "evidence": item.evidence,
        }
        for item in signals
    ]
    detail = MarketDataProvider().fetch_stock_detail(
        symbol,
        quote=quote_payload,
        signals=signal_payload,
        daily_bars=daily_payload or None,
    )
    detail["market_rules"] = MarketRuleService().evaluate_quote(detail["quote"], db=db)
    detail["stock_status"] = StockStatusService().serialize(
        StockStatusService().get_status(db, symbol, str(detail["quote"].get("name", "")))
    )
    detail["data_quality"] = {
        "level": DataQualityService().assess_quote(detail["quote"])[0].value,
        "message": DataQualityService().assess_quote(detail["quote"])[1],
    }
    return detail


@router.post("/market/sync")
def sync_market(db: Session = Depends(get_db)) -> dict[str, int]:
    result = RealDataIngestionService().sync_readonly(db, get_settings().stock_pool)
    return {"quotes": result["quotes"], "quality_logs": result["quality_logs"]}


@router.post("/market/real-readonly-sync")
def sync_real_market_readonly(db: Session = Depends(get_db)) -> dict:
    return RealDataIngestionService().sync_readonly(db, get_settings().stock_pool)


@router.post("/market/sync-active")
def sync_active_market(
    max_symbols: int = Query(default=360, ge=20, le=800),
    db: Session = Depends(get_db),
) -> dict:
    return RealDataIngestionService().sync_active_market(db, max_symbols=max_symbols)


@router.post("/market/sync-all-a-shares")
def sync_all_a_shares(
    limit: int | None = Query(default=None, ge=1, le=6000),
    chunk_size: int = Query(default=200, ge=20, le=300),
    db: Session = Depends(get_db),
) -> dict:
    return RealDataIngestionService().sync_all_a_shares(db, limit=limit, chunk_size=chunk_size)


@router.post("/market/reset-real-phase")
def reset_real_phase(db: Session = Depends(get_db)) -> dict[str, int]:
    tables = [
        Alert,
        BacktestRun,
        DailyBar,
        DataQualityLog,
        MarketGuardrailState,
        MarketQuote,
        PortfolioPosition,
        StrategySignal,
        UserTradeSample,
        WatchlistItem,
        ResearchItem,
        StrategyLibraryEntry,
        StockStatus,
        Stock,
    ]
    deleted: dict[str, int] = {}
    for model in tables:
        result = db.execute(delete(model))
        deleted[model.__tablename__] = int(result.rowcount or 0)
    db.commit()
    DataQualityService().seed_sources(db)
    return deleted


@router.post("/strategies/signals")
def generate_signals(limit: int = Query(default=500, ge=1, le=6000), db: Session = Depends(get_db)) -> dict[str, int]:
    StrategyEngine().seed_strategies(db)
    signals = StrategyEngine().generate_signals(db, limit=limit)
    return {"signals": len(signals)}


@router.post("/strategies/pick-stocks", response_model=StrategyPickResponse)
def pick_strategy_stocks(payload: StrategyPickRequest, db: Session = Depends(get_db)) -> dict:
    engine = StrategyEngine()
    engine.seed_strategies(db)
    latest_signal_at = db.execute(select(func.max(StrategySignal.generated_at))).scalar_one_or_none()
    latest_quote_at = db.execute(select(func.max(MarketQuote.observed_at))).scalar_one_or_none()
    if latest_signal_at is None or (latest_quote_at is not None and latest_signal_at < latest_quote_at):
        engine.generate_signals(db, limit=6000)
    return engine.pick_stocks(
        db,
        strategy_codes=payload.strategy_codes,
        min_score=payload.min_score,
        limit=payload.limit,
        require_real_daily_factor=payload.require_real_daily_factor,
    )


@router.post("/sector-linkage/scan")
def scan_sector_linkage(
    force: bool = Query(default=False),
    create_alerts: bool = Query(default=False),
    trigger_threshold_pct: float = Query(default=3.0, ge=0.5, le=30),
    sudden_window_minutes: int = Query(default=1, ge=1, le=30),
    sudden_threshold_pct: float = Query(default=1.2, ge=0.3, le=8),
    market_excess_threshold_pct: float = Query(default=0.8, ge=0.1, le=5),
    amount_surge_ratio_threshold: float = Query(default=1.8, ge=1, le=20),
    market_amount_ratio_threshold: float = Query(default=2.5, ge=0.1, le=50),
    intraday_volume_intensity_threshold: float = Query(default=2.0, ge=0.5, le=50),
    min_crowding_score: float = Query(default=80.0, ge=50, le=100),
    min_candidate_score: float = Query(default=65.0, ge=0, le=100),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return SectorLinkageService().opening_scan(
        db,
        force=force,
        create_alerts=create_alerts,
        trigger_threshold_pct=trigger_threshold_pct,
        sudden_window_minutes=sudden_window_minutes,
        sudden_threshold_pct=sudden_threshold_pct,
        market_excess_threshold_pct=market_excess_threshold_pct,
        amount_surge_ratio_threshold=amount_surge_ratio_threshold,
        market_amount_ratio_threshold=market_amount_ratio_threshold,
        intraday_volume_intensity_threshold=intraday_volume_intensity_threshold,
        min_crowding_score=min_crowding_score,
        min_candidate_score=min_candidate_score,
    )


@router.get("/sector-linkage/history")
def sector_linkage_history(limit: int = Query(default=30, ge=1, le=200), db: Session = Depends(get_db)) -> dict[str, Any]:
    return SectorLinkageService().history(db, limit=limit)


@router.post("/institutional-holdings/sync")
def sync_institutional_holdings(
    max_symbols: int = Query(default=120, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return InstitutionalHoldingService().sync_latest(db, max_symbols=max_symbols)


@router.get("/institutional-holdings/top")
def institutional_holding_top(limit: int = Query(default=10, ge=1, le=50), db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"items": InstitutionalHoldingService().top_candidates(db, limit=limit)}


@router.post("/strategies/backfill-active-daily")
def backfill_active_daily(
    max_symbols: int = Query(default=120, ge=5, le=500),
    lookback_days: int = Query(default=180, ge=60, le=500),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    symbols = RealDataIngestionService().active_symbols(db, max_symbols=max_symbols)
    bars = CloseDailyStrategyRunner().backfill_daily_bars(db, symbols, lookback_days=lookback_days)
    return {"symbols": len(symbols), "bars_written": bars}


@router.post("/strategies/run-close-daily")
def run_close_daily_strategy(db: Session = Depends(get_db)) -> dict:
    return CloseDailyStrategyRunner().run(db, get_settings().stock_pool)


@router.get("/strategy-library")
def strategy_library(db: Session = Depends(get_db)) -> dict:
    return StrategyLibraryService().summary(db)


@router.post("/research/collect")
def collect_research(db: Session = Depends(get_db)) -> dict[str, int]:
    return {"research": ResearchService().collect_real_research(db)}


@router.get("/readiness")
def readiness(db: Session = Depends(get_db)) -> dict:
    return ReadinessService().report(db)


@router.get("/market/guardrails")
def market_guardrails(db: Session = Depends(get_db)) -> dict:
    return MarketGuardrailService().latest(db)


@router.get("/data-sources/probe")
def probe_data_sources() -> dict:
    return SourceProbeService().probe()


@router.post("/strategy-library/sync")
def sync_strategy_library(db: Session = Depends(get_db)) -> dict:
    return StrategyLibraryService().sync_from_current_state(db)


@router.post("/strategy-observations/sync")
def sync_strategy_observations(db: Session = Depends(get_db)) -> dict:
    result = StrategyObservationService().sync(db)
    StrategyLibraryService().sync_from_current_state(db)
    return result


@router.post("/watchlist", response_model=WatchlistOut)
def add_watchlist_item(payload: WatchlistCreate, db: Session = Depends(get_db)) -> WatchlistItem:
    existing = db.execute(select(WatchlistItem).where(WatchlistItem.symbol == payload.symbol)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"{payload.symbol} 已在自选追踪中，请在自选追踪列表中编辑该股票的策略和提醒参数。",
        )
    item = WatchlistItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/watchlist/{item_id}", response_model=WatchlistOut)
def update_watchlist_item(item_id: int, payload: WatchlistUpdate, db: Session = Depends(get_db)) -> WatchlistItem:
    item = db.get(WatchlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(item, key, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/watchlist/{item_id}")
def delete_watchlist_item(item_id: int, db: Session = Depends(get_db)) -> dict[str, int]:
    item = db.get(WatchlistItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    symbol = item.symbol
    db.delete(item)
    db.execute(delete(Alert).where(Alert.symbol == symbol, Alert.status != AlertStatus.dismissed))
    db.commit()
    return {"deleted": item_id}


@router.get("/watchlist", response_model=list[WatchlistOut])
def list_watchlist(db: Session = Depends(get_db)) -> list[WatchlistItem]:
    return db.execute(select(WatchlistItem).order_by(WatchlistItem.created_at.desc())).scalars().all()


@router.post("/trades")
def record_trade_sample(payload: TradeSampleCreate, db: Session = Depends(get_db)) -> dict[str, int]:
    sample = UserTradeSample(**payload.model_dump())
    db.add(sample)
    db.flush()
    sample = TradeSampleService().apply_manual_trade(db, sample)
    return {"id": sample.id}


@router.get("/trades")
def list_trade_samples(db: Session = Depends(get_db)) -> dict:
    return TradeSampleService().summary(db)


@router.get("/portfolio")
def portfolio_summary(db: Session = Depends(get_db)) -> dict:
    return PortfolioService().summary(db)


@router.get("/backtests")
def list_backtests(db: Session = Depends(get_db)) -> dict:
    return {"runs": TradeSampleService().strategy_history_drawdowns(db), "mode": "real_portfolio_closed_positions"}


@router.post("/backtests/demo-run")
def run_legacy_real_backtests(db: Session = Depends(get_db)) -> dict:
    runs = TradeSampleService().strategy_history_drawdowns(db)
    StrategyLibraryService().sync_from_current_state(db)
    return {
        "backtests": len(runs),
        "mode": "real_portfolio_closed_positions",
        "message": "兼容接口已禁用旧式回测写库，仅返回真实已完成持仓回撤分析数量。",
    }


@router.post("/backtests/real-run")
def run_real_backtests(limit: int = Query(default=80, ge=10, le=200), db: Session = Depends(get_db)) -> dict[str, int]:
    runs = TradeSampleService().strategy_history_drawdowns(db)
    StrategyLibraryService().sync_from_current_state(db)
    return {"backtests": len(runs)}


@router.post("/alerts/{alert_id}/decision")
def record_alert_decision(alert_id: int, payload: AlertDecisionCreate, db: Session = Depends(get_db)) -> dict:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    if payload.action not in {"buy", "sell", "ignore", "watch"}:
        raise HTTPException(status_code=422, detail="Unsupported decision action")
    sample = TradeSampleService().record_alert_decision(
        db,
        alert=alert,
        action=payload.action,
        quantity=payload.quantity,
        notes=payload.notes,
    )
    return TradeSampleService().serialize(sample)


@router.post("/alerts/evaluate")
def evaluate_alerts(db: Session = Depends(get_db)) -> dict[str, int]:
    alerts = AlertService().evaluate_watchlist(db)
    return {"alerts": len(alerts)}


@router.post("/alerts/{alert_id}/dismiss")
def dismiss_alert(alert_id: int, db: Session = Depends(get_db)) -> dict[str, int]:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = AlertStatus.dismissed
    db.commit()
    return {"dismissed": alert_id}
