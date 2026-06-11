from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.alerts import AlertService
from app.services.close_daily_runner import CloseDailyStrategyRunner
from app.services.institutional_holdings import InstitutionalHoldingService
from app.services.market_calendar import MarketCalendarService
from app.services.real_data_ingestion import RealDataIngestionService
from app.services.research import ResearchService
from app.services.sector_linkage import SectorLinkageService
from app.services.strategy_engine import StrategyEngine
from app.services.strategy_observation import StrategyObservationService
from app.workers.celery_app import celery_app


@celery_app.task
def sync_market_quotes() -> int:
    db = SessionLocal()
    try:
        calendar = MarketCalendarService().today_status(db)
        if calendar["is_trade_time"]:
            result = RealDataIngestionService().sync_active_market(db)
        else:
            result = RealDataIngestionService().sync_readonly(db, get_settings().stock_pool)
        return int(result["quotes"])
    finally:
        db.close()


@celery_app.task
def generate_strategy_signals() -> int:
    db = SessionLocal()
    try:
        engine = StrategyEngine()
        engine.seed_strategies(db)
        return len(engine.generate_signals(db))
    finally:
        db.close()


@celery_app.task
def evaluate_alerts() -> int:
    db = SessionLocal()
    try:
        return len(AlertService().evaluate_watchlist(db))
    finally:
        db.close()


@celery_app.task
def ingest_research() -> int:
    db = SessionLocal()
    try:
        return ResearchService().collect_real_research(db)
    finally:
        db.close()


@celery_app.task
def run_close_daily_strategy() -> int:
    db = SessionLocal()
    try:
        result = CloseDailyStrategyRunner().run(db, get_settings().stock_pool)
        return int(result["signals"])
    finally:
        db.close()


@celery_app.task
def sync_strategy_observations() -> int:
    db = SessionLocal()
    try:
        result = StrategyObservationService().sync(db)
        return int(result["daily_top3"] + result["user_trade"] + result["updated_returns"])
    finally:
        db.close()


@celery_app.task
def sync_institutional_holdings() -> int:
    db = SessionLocal()
    try:
        result = InstitutionalHoldingService().sync_latest(db, max_symbols=200)
        return int(result["created"] + result["updated"])
    finally:
        db.close()


@celery_app.task
def scan_opening_sector_linkage() -> int:
    db = SessionLocal()
    try:
        result = SectorLinkageService().opening_scan(db, create_alerts=True)
        return int(result.get("sector_count") or 0)
    finally:
        db.close()
