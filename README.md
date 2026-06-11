# Find Fortune Route

面向中国 A 股市场的股票策略研究与辅助决策系统。项目定位为研究、分析、提醒和复盘工具，不做自动下单。

## What Is Included

- FastAPI backend with SQLAlchemy models for market data, strategies, alerts, watchlists, research items, and user trade samples.
- Celery worker and beat scheduler for market data sync, research ingestion, strategy signal generation, alert checks, and feedback learning jobs.
- PostgreSQL with TimescaleDB and pgvector via Docker, plus Redis for cache and task queue.
- AkShare-first market data adapter with deterministic demo fallback when upstream data is unavailable.
- Strategy engine covering multi-factor, reversal, trend breakout, low-volatility quality, money-flow anomaly, and event-driven starter strategies.
- Next.js dashboard for market overview, watchlist tracking, strategy recommendations, alerts, research, and backtest summaries.

## Quick Start

```bash
cp .env.example .env
docker compose up --build
```

Then open:

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000/docs

## Development

Backend:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Safety

All strategy outputs are decision-support signals. The system records user-confirmed buy/sell decisions and realized outcomes as learning samples, but it does not place orders or claim guaranteed returns.
