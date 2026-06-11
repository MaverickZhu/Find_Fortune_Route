from __future__ import annotations

from datetime import datetime, timedelta
from statistics import pstdev
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import DailyBar, MarketQuote, SignalAction, StrategySignal
from app.services.market_data import MarketDataProvider
from app.services.market_guardrails import MarketGuardrailService
from app.services.strategy_engine import StrategyEngine


class CloseDailyStrategyRunner:
    strategy_code = "close_daily_multi_factor"

    def run(self, db: Session, symbols: list[str], lookback_days: int = 180) -> dict[str, Any]:
        StrategyEngine().seed_strategies(db)
        bars_written = self.backfill_daily_bars(db, symbols, lookback_days)
        signals = self.generate_close_signals(db, symbols)
        return {
            "strategy_code": self.strategy_code,
            "symbols": len(symbols),
            "bars_written": bars_written,
            "signals": len(signals),
            "trade_date": self._latest_trade_date(db, symbols),
            "actions": self._action_counts(signals),
        }

    def backfill_daily_bars(self, db: Session, symbols: list[str], lookback_days: int = 180) -> int:
        provider = MarketDataProvider()
        end = datetime.utcnow()
        start = end - timedelta(days=max(lookback_days + 40, 220))
        written = 0
        for symbol in symbols:
            df = provider.fetch_daily_bars(symbol, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            rows = self._rows_from_frame(df)
            if not rows:
                rows = self._fallback_rows(db, provider, symbol)
            rows = self._merge_latest_close_snapshot(db, symbol, rows)
            for row in rows[-lookback_days:]:
                trade_date = pd.to_datetime(row["trade_date"]).to_pydatetime().replace(hour=0, minute=0, second=0, microsecond=0)
                existing = db.execute(
                    select(DailyBar).where(DailyBar.symbol == symbol, DailyBar.trade_date == trade_date)
                ).scalar_one_or_none()
                payload = {
                    "symbol": symbol,
                    "trade_date": trade_date,
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume") or 0),
                    "amount": float(row.get("amount") or 0),
                    "turnover_rate": float(row.get("turnover_rate") or 0),
                    "source": str(row.get("source") or "unknown_daily"),
                }
                if existing:
                    for key, value in payload.items():
                        setattr(existing, key, value)
                else:
                    db.add(DailyBar(**payload))
                written += 1
            db.commit()
        return written

    def generate_close_signals(self, db: Session, symbols: list[str]) -> list[StrategySignal]:
        guardrail = MarketGuardrailService().latest(db)
        generated: list[StrategySignal] = []
        for symbol in symbols:
            bars = (
                db.execute(select(DailyBar).where(DailyBar.symbol == symbol).order_by(DailyBar.trade_date.asc()))
                .scalars()
                .all()
            )
            if len(bars) < 20:
                continue
            factors = self._factors(bars)
            score = self._score(factors)
            action = self._action(score, factors)
            reason = self._reason(action, factors)
            note = "收盘后日线策略，仅用于次日观察与研究，不代表确定买卖建议。"
            if guardrail["status"] == "blocked":
                score = min(score, 60)
                action = SignalAction.watch
                reason = "真实行情源被阻断，收盘策略信号降级为观察。"
                note = "真实行情保护阈值阻断，暂停买卖类信号升级。"
            signal = StrategySignal(
                strategy_code=self.strategy_code,
                symbol=symbol,
                generated_at=datetime.utcnow(),
                action=action,
                score=score,
                confidence=min(0.9, max(0.35, score / 100)),
                reason=reason,
                evidence={
                    "factors": factors,
                    "guardrail": guardrail,
                    "note": note,
                },
            )
            db.add(signal)
            generated.append(signal)
        db.commit()
        return generated

    def _rows_from_frame(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if df.empty:
            return []
        rows = []
        for _, row in df.iterrows():
            rows.append(
                {
                    "trade_date": row.get("trade_date"),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume", 0),
                    "amount": row.get("amount", 0),
                    "turnover_rate": row.get("turnover_rate", 0),
                    "source": str(row.get("source") or "unknown_daily"),
                }
            )
        return rows

    def _fallback_rows(self, db: Session, provider: MarketDataProvider, symbol: str) -> list[dict[str, Any]]:
        return []

    def _merge_latest_close_snapshot(self, db: Session, symbol: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        quote = db.execute(
            select(MarketQuote).where(MarketQuote.symbol == symbol).order_by(MarketQuote.observed_at.desc()).limit(1)
        ).scalar_one_or_none()
        if quote is None or quote.quality != "ok":
            return rows
        quote_date = quote.observed_at.date()
        latest_row_date = None
        if rows:
            latest_row_date = pd.to_datetime(rows[-1]["trade_date"]).date()
        if latest_row_date is not None and quote_date <= latest_row_date:
            return rows
        previous_close = float(rows[-1]["close"]) if rows else quote.last_price
        rows.append(
            {
                "trade_date": quote_date.isoformat(),
                "open": previous_close,
                "high": max(previous_close, quote.last_price),
                "low": min(previous_close, quote.last_price),
                "close": quote.last_price,
                "volume": quote.volume,
                "amount": quote.amount,
                "turnover_rate": 0,
                "source": f"{quote.source}_close_snapshot",
            }
        )
        return rows

    def _factors(self, bars: list[DailyBar]) -> dict[str, Any]:
        closes = [bar.close for bar in bars]
        amounts = [bar.amount for bar in bars]
        latest = bars[-1]
        returns = [(closes[idx] / closes[idx - 1] - 1) * 100 for idx in range(1, len(closes)) if closes[idx - 1] > 0]
        ma5 = self._mean(closes[-5:])
        ma20 = self._mean(closes[-20:])
        ma60 = self._mean(closes[-60:]) if len(closes) >= 60 else ma20
        high60 = max(closes[-60:]) if len(closes) >= 60 else max(closes)
        return {
            "trade_date": latest.trade_date.date().isoformat(),
            "close": round(latest.close, 3),
            "return_1d_pct": round(returns[-1], 3) if returns else 0,
            "return_5d_pct": self._window_return(closes, 5),
            "return_20d_pct": self._window_return(closes, 20),
            "ma5": round(ma5, 3),
            "ma20": round(ma20, 3),
            "ma60": round(ma60, 3),
            "distance_ma20_pct": round((latest.close / ma20 - 1) * 100, 3) if ma20 else 0,
            "volatility_20d_pct": round(pstdev(returns[-20:]), 3) if len(returns) >= 2 else 0,
            "amount_20d_avg": round(self._mean(amounts[-20:]), 2),
            "turnover_rate": round(latest.turnover_rate, 3),
            "drawdown_60d_pct": round((latest.close / high60 - 1) * 100, 3) if high60 else 0,
            "bar_count": len(bars),
            "source": latest.source,
        }

    def _score(self, factors: dict[str, Any]) -> float:
        score = 50.0
        score += max(-12, min(18, factors["return_20d_pct"] * 0.9))
        score += max(-8, min(10, factors["return_5d_pct"] * 1.1))
        score += 10 if factors["ma5"] >= factors["ma20"] >= factors["ma60"] else -6
        score += min(12, factors["amount_20d_avg"] / 200_000_000)
        score -= min(10, factors["volatility_20d_pct"] * 0.9)
        score += max(-8, min(4, factors["drawdown_60d_pct"] * 0.25))
        return round(max(0, min(100, score)), 2)

    def _action(self, score: float, factors: dict[str, Any]) -> SignalAction:
        if score >= 72 and factors["return_20d_pct"] > 0 and factors["ma5"] >= factors["ma20"]:
            return SignalAction.buy
        if score <= 38 or factors["drawdown_60d_pct"] < -18:
            return SignalAction.reduce
        if score >= 62:
            return SignalAction.watch
        return SignalAction.hold

    def _reason(self, action: SignalAction, factors: dict[str, Any]) -> str:
        if action == SignalAction.buy:
            return "收盘后日线因子显示趋势与动量较强，进入次日买入观察。"
        if action == SignalAction.reduce:
            return "收盘后日线因子显示回撤或弱势风险偏高，建议降低仓位或等待修复。"
        if action == SignalAction.watch:
            return "收盘后综合评分较好，但仍需结合次日开盘、成交量与市场状态确认。"
        return "收盘后综合评分中性，暂以持有/观察为主。"

    def _window_return(self, closes: list[float], window: int) -> float:
        if len(closes) <= window or closes[-window - 1] <= 0:
            return 0
        return round((closes[-1] / closes[-window - 1] - 1) * 100, 3)

    def _mean(self, values: list[float]) -> float:
        return sum(values) / max(1, len(values))

    def _latest_trade_date(self, db: Session, symbols: list[str]) -> str | None:
        latest = (
            db.execute(select(DailyBar).where(DailyBar.symbol.in_(symbols)).order_by(DailyBar.trade_date.desc()).limit(1))
            .scalar_one_or_none()
        )
        return latest.trade_date.date().isoformat() if latest else None

    def _action_counts(self, signals: list[StrategySignal]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for signal in signals:
            action = signal.action.value if hasattr(signal.action, "value") else str(signal.action)
            counts[action] = counts.get(action, 0) + 1
        return counts
