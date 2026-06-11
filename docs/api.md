# API Notes

Base URL: `http://localhost:8000/api`

## Bootstrap

`POST /bootstrap`

Seeds starter strategies, fetches market quotes, creates initial signals, seed research, and demo backtests.

## Dashboard

`GET /dashboard`

Returns market quotes, latest signals, alerts, watchlist items, research notes, and backtest summaries.

## Watchlist

`POST /watchlist`

```json
{
  "symbol": "600519",
  "name": "贵州茅台",
  "target_buy": 1500,
  "target_sell": 1800,
  "stop_loss": 1450,
  "take_profit": 1850,
  "strategy_code": "multi_factor_alpha"
}
```

## Trade Samples

`POST /trades`

Records the user decision and realized outcome used by the learning loop.

```json
{
  "symbol": "600519",
  "action": "buy",
  "decision_price": 1512.3,
  "strategy_code": "multi_factor_alpha",
  "notes": "User confirmed after strategy alert"
}
```
