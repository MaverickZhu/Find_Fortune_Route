# Find Fortune Route Architecture

## System Shape

Find Fortune Route uses a research-first architecture:

1. Collect market, fundamental, macro, news, and research data.
2. Normalize raw data into queryable time-series and knowledge records.
3. Generate strategy signals with transparent evidence.
4. Track user-selected watchlist targets and alert thresholds.
5. Record user decisions and realized returns as learning samples.
6. Promote strategy updates only after backtesting and manual review.

## Services

- `backend`: FastAPI API, SQLAlchemy models, strategy services, alert logic.
- `worker`: Celery worker for background collection and signal jobs.
- `beat`: Celery scheduler.
- `db`: PostgreSQL with TimescaleDB and pgvector extensions.
- `redis`: Celery broker, result backend, and future low-latency alert cache.
- `frontend`: Next.js dashboard.

## Data Sources

The first implementation uses AkShare as the primary adapter. The adapter currently uses Eastmoney-backed A-share spot quotes through AkShare and falls back to deterministic demo quotes when upstream data fails. Additional adapters should expose the same normalized quote/bar shape.

## Strategy Governance

Strategies are stored as metadata plus generated signals. New research should first become a research item or candidate strategy, then be backtested with A-share constraints before it is enabled for recommendation.

## A-Share Backtest Rules

Backtests should model T+1, limit up/down, suspensions, 100-share lots, stamp tax on sells, commissions, slippage, and survivorship-bias-aware stock pools.
