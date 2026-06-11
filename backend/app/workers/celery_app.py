from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()
celery_app = Celery("find_fortune_route", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.timezone = "Asia/Shanghai"
celery_app.conf.imports = ("app.workers.tasks",)
celery_app.conf.beat_schedule = {
    "sync-market-quotes-every-30s": {
        "task": "app.workers.tasks.sync_market_quotes",
        "schedule": 30.0,
    },
    "generate-strategy-signals-every-2m": {
        "task": "app.workers.tasks.generate_strategy_signals",
        "schedule": 120.0,
    },
    "evaluate-alerts-every-30s": {
        "task": "app.workers.tasks.evaluate_alerts",
        "schedule": 30.0,
    },
    "scan-opening-sector-linkage-every-minute": {
        "task": "app.workers.tasks.scan_opening_sector_linkage",
        "schedule": 60.0,
    },
    "ingest-research-hourly": {
        "task": "app.workers.tasks.ingest_research",
        "schedule": 3600.0,
    },
    "run-close-daily-strategy-after-close": {
        "task": "app.workers.tasks.run_close_daily_strategy",
        "schedule": crontab(hour=15, minute=45),
    },
    "sync-institutional-holdings-after-close": {
        "task": "app.workers.tasks.sync_institutional_holdings",
        "schedule": crontab(hour=15, minute=50),
    },
    "sync-strategy-observations-after-close": {
        "task": "app.workers.tasks.sync_strategy_observations",
        "schedule": crontab(hour=15, minute=55),
    },
}
