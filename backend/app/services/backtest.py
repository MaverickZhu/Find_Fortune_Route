import math
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain import BacktestRun, DailyBar, MarketQuote
from app.services.close_daily_runner import CloseDailyStrategyRunner


class BacktestService:
    def create_real_backtests(self, db: Session, symbols: list[str] | None = None, limit: int = 80) -> list[BacktestRun]:
        symbols = symbols or self._liquid_symbols(db, limit)
        if not symbols:
            return []
        CloseDailyStrategyRunner().backfill_daily_bars(db, symbols, lookback_days=180)
        panels = self._load_bar_panels(db, symbols)
        if len(panels) < 5:
            return []
        start = min(bar.trade_date for rows in panels.values() for bar in rows)
        end = max(bar.trade_date for rows in panels.values() for bar in rows)
        strategies = ["multi_factor_alpha", "mean_reversion", "trend_breakout"]
        runs: list[BacktestRun] = []
        for strategy in strategies:
            analysis = self._simulate_strategy(strategy, panels)
            if not analysis["equity_curve"]:
                continue
            metrics = self._metrics_from_analysis(analysis)
            metrics["analysis"] = analysis
            run = BacktestRun(
                strategy_code=strategy,
                stock_pool=symbols,
                start_date=start,
                end_date=end,
                metrics=metrics,
                assumptions=self._real_assumptions(start, end, len(symbols)),
            )
            db.add(run)
            runs.append(run)
        db.commit()
        return runs

    def latest_analyses(self, db: Session, limit: int = 12) -> list[dict[str, Any]]:
        runs = db.execute(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)).scalars().all()
        return [self.serialize_run(run) for run in runs]

    def serialize_run(self, run: BacktestRun) -> dict[str, Any]:
        metrics = dict(run.metrics or {})
        analysis = metrics.get("analysis")
        if not isinstance(analysis, dict):
            analysis = {
                "equity_curve": [],
                "drawdown_curve": [],
                "monthly_returns": [],
                "regime_breakdown": [],
                "risk_flags": ["该回测缺少真实分析明细，请重新运行真实回测。"],
                "diagnostics": {"data_status": "missing_analysis"},
            }
        return {
            "id": run.id,
            "strategy_code": run.strategy_code,
            "stock_pool": run.stock_pool,
            "start_date": run.start_date,
            "end_date": run.end_date,
            "metrics": metrics,
            "assumptions": run.assumptions,
            "equity_curve": analysis.get("equity_curve", []),
            "drawdown_curve": analysis.get("drawdown_curve", []),
            "monthly_returns": analysis.get("monthly_returns", []),
            "regime_breakdown": analysis.get("regime_breakdown", []),
            "risk_flags": analysis.get("risk_flags", []),
            "diagnostics": analysis.get("diagnostics", {}),
        }

    def _liquid_symbols(self, db: Session, limit: int) -> list[str]:
        latest = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote)
                .join(latest, (MarketQuote.symbol == latest.c.symbol) & (MarketQuote.observed_at == latest.c.observed_at))
                .where(MarketQuote.quality == "ok")
                .where(MarketQuote.amount > 0)
                .order_by(MarketQuote.amount.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [row.symbol for row in rows]

    def _load_bar_panels(self, db: Session, symbols: list[str]) -> dict[str, list[DailyBar]]:
        panels: dict[str, list[DailyBar]] = {}
        for symbol in symbols:
            bars = (
                db.execute(select(DailyBar).where(DailyBar.symbol == symbol).order_by(DailyBar.trade_date.asc()))
                .scalars()
                .all()
            )
            if len(bars) >= 60:
                panels[symbol] = bars
        return panels

    def _simulate_strategy(self, strategy: str, panels: dict[str, list[DailyBar]]) -> dict[str, Any]:
        by_date: dict[datetime, dict[str, DailyBar]] = {}
        for symbol, bars in panels.items():
            for bar in bars:
                by_date.setdefault(bar.trade_date, {})[symbol] = bar
        dates = sorted(by_date)
        equity = 1.0
        peak = 1.0
        equity_curve: list[dict[str, Any]] = []
        drawdown_curve: list[dict[str, Any]] = []
        monthly: dict[str, float] = {}
        selected_counts: list[int] = []
        daily_returns: list[float] = []
        previous_selection: list[str] = []
        prev_equity_by_month: dict[str, float] = {}
        for idx, day in enumerate(dates):
            if idx < 25:
                continue
            selection = self._select_symbols(strategy, panels, day)
            if not selection:
                continue
            returns = []
            for symbol in selection:
                bars = panels[symbol]
                position = next((i for i, bar in enumerate(bars) if bar.trade_date == day), None)
                if position is None or position == 0:
                    continue
                prev_close = bars[position - 1].close
                if prev_close > 0:
                    returns.append((bars[position].close / prev_close - 1) - 0.0008)
            if not returns:
                continue
            day_return = sum(returns) / len(returns)
            equity *= 1 + day_return
            peak = max(peak, equity)
            daily_returns.append(day_return)
            selected_counts.append(len(selection))
            month = day.strftime("%Y-%m")
            prev_equity_by_month.setdefault(month, equity / (1 + day_return))
            monthly[month] = equity / prev_equity_by_month[month] - 1
            equity_curve.append({"date": day.date().isoformat(), "value": round(equity, 4)})
            drawdown_curve.append({"date": day.date().isoformat(), "value": round((equity / peak - 1) * 100, 2)})
            previous_selection = selection
        monthly_returns = [{"date": key, "return_pct": round(value * 100, 2)} for key, value in sorted(monthly.items())[-12:]]
        return {
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
            "monthly_returns": monthly_returns,
            "regime_breakdown": self._real_regime_breakdown(daily_returns),
            "risk_flags": self._real_risk_flags(equity_curve, drawdown_curve),
            "diagnostics": {
                "sample_count": len(daily_returns),
                "selected_symbols_avg": round(sum(selected_counts) / len(selected_counts), 2) if selected_counts else 0,
                "source": "daily_bars",
                "data_status": "real_daily_bars",
                "cost_model": "daily_return_minus_8bp",
                "last_selection_count": len(previous_selection),
            },
        }

    def _select_symbols(self, strategy: str, panels: dict[str, list[DailyBar]], day: datetime) -> list[str]:
        scored: list[tuple[float, str]] = []
        for symbol, bars in panels.items():
            idx = next((i for i, bar in enumerate(bars) if bar.trade_date == day), None)
            if idx is None or idx < 25:
                continue
            closes = [bar.close for bar in bars[: idx + 1]]
            amounts = [bar.amount for bar in bars[max(0, idx - 20) : idx + 1]]
            ret5 = closes[-1] / closes[-6] - 1 if len(closes) >= 6 and closes[-6] > 0 else 0
            ret20 = closes[-1] / closes[-21] - 1 if len(closes) >= 21 and closes[-21] > 0 else 0
            ma20 = sum(closes[-20:]) / 20
            liquidity = sum(amounts) / max(1, len(amounts))
            if strategy == "mean_reversion":
                score = -ret5 * 100 + min(10, liquidity / 500_000_000)
                if ret5 < -0.02:
                    scored.append((score, symbol))
            elif strategy == "trend_breakout":
                score = ret20 * 100 + (5 if closes[-1] > ma20 else -5) + min(8, liquidity / 800_000_000)
                if ret20 > 0.02 and closes[-1] > ma20:
                    scored.append((score, symbol))
            else:
                score = ret20 * 70 + ret5 * 30 + min(10, liquidity / 600_000_000)
                scored.append((score, symbol))
        return [symbol for _, symbol in sorted(scored, reverse=True)[:12]]

    def _metrics_from_analysis(self, analysis: dict[str, Any]) -> dict[str, Any]:
        equity_curve = analysis["equity_curve"]
        drawdown_curve = analysis["drawdown_curve"]
        if len(equity_curve) < 2:
            return {}
        total_return = equity_curve[-1]["value"] / equity_curve[0]["value"] - 1
        days = max(1, len(equity_curve))
        annual_return = (1 + total_return) ** (252 / days) - 1
        max_drawdown = min((point["value"] for point in drawdown_curve), default=0)
        monthly = analysis["monthly_returns"]
        wins = sum(1 for item in monthly if item["return_pct"] > 0)
        monthly_values = [item["return_pct"] / 100 for item in monthly]
        mean = sum(monthly_values) / max(1, len(monthly_values))
        variance = sum((item - mean) ** 2 for item in monthly_values) / max(1, len(monthly_values))
        sharpe = (mean / math.sqrt(variance) * math.sqrt(12)) if variance > 0 else 0
        return {
            "annual_return_pct": round(annual_return * 100, 2),
            "benchmark_return_pct": None,
            "alpha_pct": None,
            "max_drawdown_pct": round(max_drawdown, 2),
            "sharpe": round(sharpe, 2),
            "calmar": round((annual_return * 100) / max(0.1, abs(max_drawdown)), 2),
            "win_rate_pct": round(wins / max(1, len(monthly)) * 100, 2),
            "turnover_pct": None,
            "fee_adjusted": True,
        }

    def _real_assumptions(self, start: datetime, end: datetime, universe_size: int) -> dict[str, Any]:
        return {
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
            "universe_size": universe_size,
            "data_source": "新浪日线 K 线 + 本地 daily_bars",
            "validation": "真实日线快速回测，仍需复权、停牌、退市与完整费用模型增强",
            "constraints": ["T+1 近似", "100 股整数手待接入", "涨跌停过滤待增强", "日收益扣 8bp 成本"],
        }

    def _real_regime_breakdown(self, returns: list[float]) -> list[dict[str, Any]]:
        if not returns:
            return []
        thirds = max(1, len(returns) // 3)
        regimes = [("前段", returns[:thirds]), ("中段", returns[thirds : thirds * 2]), ("后段", returns[thirds * 2 :])]
        return [
            {
                "regime": name,
                "return_pct": round((math.prod([1 + item for item in rows]) - 1) * 100, 2) if rows else 0,
                "win_rate_pct": round(sum(1 for item in rows if item > 0) / max(1, len(rows)) * 100, 2),
            }
            for name, rows in regimes
        ]

    def _real_risk_flags(self, equity_curve: list[dict[str, Any]], drawdown_curve: list[dict[str, Any]]) -> list[str]:
        flags = ["真实日线快速回测已启用；复权、停牌、涨跌停、退市样本和行业暴露仍需增强。"]
        max_drawdown = min((point["value"] for point in drawdown_curve), default=0)
        if max_drawdown < -15:
            flags.append("最大回撤超过 15%，需要降低单票权重或增加市场状态过滤。")
        if len(equity_curve) < 60:
            flags.append("样本期偏短，不能作为策略上线准入依据。")
        return flags
