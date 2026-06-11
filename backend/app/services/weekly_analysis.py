from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.domain import DailyBar, MarketQuote, PortfolioPosition, PositionStatus, Strategy


class WeeklyAnalysisService:
    """Summarize the latest available trading week using local real market data."""

    def report(self, db: Session) -> dict[str, Any]:
        latest_trade_at = db.execute(select(func.max(DailyBar.trade_date))).scalar_one_or_none()
        latest_quote_at = db.execute(select(func.max(MarketQuote.observed_at))).scalar_one_or_none()
        anchor = latest_trade_at or latest_quote_at or datetime.utcnow()
        week_start = datetime.combine((anchor - timedelta(days=anchor.weekday())).date(), time.min)
        week_end = datetime.combine(anchor.date(), time.max)
        previous_start = week_start - timedelta(days=7)
        previous_end = week_start - timedelta(microseconds=1)

        strategies = db.execute(select(Strategy).where(Strategy.enabled.is_(True)).order_by(Strategy.id.asc())).scalars().all()
        strategy_names = {item.code: item.name for item in strategies}
        benchmark = self._market_benchmark(db, week_start, week_end, previous_start, previous_end)
        reviews = [
            self._strategy_review(db, strategy.code, strategy.name, week_start, week_end, benchmark)
            for strategy in strategies
        ]
        summary = self._summary(benchmark, reviews)
        return {
            "generated_at": datetime.utcnow().isoformat(),
            "week_start": week_start.date().isoformat(),
            "week_end": anchor.date().isoformat(),
            "benchmark": benchmark,
            "strategy_reviews": reviews,
            "strategy_names": strategy_names,
            "summary": summary,
            "methodology": {
                "market_proxy": "本地真实日 K 覆盖股票等权周收益，结合涨跌家数与成交额变化判断市场形势。",
                "actual_return": "以本周已完成虚拟交易的实际收益为主，未完成持仓单独统计浮动收益，不混入已实现收益。",
                "backtest": "读取最新真实日线回测 equity_curve，截取最近可用交易周区间计算周回测收益。",
                "review_rule": "策略收益或回测收益连续弱于市场时标记为需复盘；样本不足时只给观察结论。",
            },
        }

    def _market_benchmark(
        self,
        db: Session,
        week_start: datetime,
        week_end: datetime,
        previous_start: datetime,
        previous_end: datetime,
    ) -> dict[str, Any]:
        weekly_bars = (
            db.execute(
                select(DailyBar)
                .where(DailyBar.trade_date >= week_start, DailyBar.trade_date <= week_end)
                .order_by(DailyBar.symbol.asc(), DailyBar.trade_date.asc())
            )
            .scalars()
            .all()
        )
        previous_amount = db.execute(
            select(func.sum(DailyBar.amount)).where(DailyBar.trade_date >= previous_start, DailyBar.trade_date <= previous_end)
        ).scalar_one_or_none()

        by_symbol: dict[str, list[DailyBar]] = defaultdict(list)
        for bar in weekly_bars:
            by_symbol[bar.symbol].append(bar)
        returns: list[float] = []
        amount = 0.0
        for rows in by_symbol.values():
            amount += sum(item.amount or 0 for item in rows)
            if len(rows) < 2 or rows[0].close <= 0:
                continue
            returns.append((rows[-1].close / rows[0].close - 1) * 100)

        avg_return = round(sum(returns) / len(returns), 2) if returns else None
        median_return = round(statistics.median(returns), 2) if returns else None
        up_count = sum(1 for item in returns if item > 0)
        down_count = sum(1 for item in returns if item < 0)
        up_ratio = round(up_count / len(returns) * 100, 2) if returns else None
        amount_change = None
        if previous_amount and previous_amount > 0:
            amount_change = round((amount / float(previous_amount) - 1) * 100, 2)
        regime = self._market_regime(avg_return, up_ratio, amount_change)
        return {
            "name": "A股覆盖股票等权基准",
            "return_pct": avg_return,
            "median_return_pct": median_return,
            "up_count": up_count,
            "down_count": down_count,
            "up_ratio_pct": up_ratio,
            "sample_count": len(returns),
            "total_amount": round(amount, 2),
            "amount_change_pct": amount_change,
            "regime": regime,
            "interpretation": self._market_interpretation(regime, avg_return, up_ratio, amount_change),
        }

    def _strategy_review(
        self,
        db: Session,
        strategy_code: str,
        strategy_name: str,
        week_start: datetime,
        week_end: datetime,
        benchmark: dict[str, Any],
    ) -> dict[str, Any]:
        closed = (
            db.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.strategy_code == strategy_code,
                    PortfolioPosition.status == PositionStatus.closed,
                    PortfolioPosition.exit_at >= week_start,
                    PortfolioPosition.exit_at <= week_end,
                )
            )
            .scalars()
            .all()
        )
        open_rows = (
            db.execute(
                select(PortfolioPosition).where(
                    PortfolioPosition.strategy_code == strategy_code,
                    PortfolioPosition.status == PositionStatus.open,
                    PortfolioPosition.entry_at <= week_end,
                )
            )
            .scalars()
            .all()
        )
        actual_returns = [item.realized_return_pct for item in closed if item.realized_return_pct is not None]
        actual_return = round(sum(actual_returns) / len(actual_returns), 2) if actual_returns else None
        win_rate = round(sum(1 for item in actual_returns if item > 0) / len(actual_returns) * 100, 2) if actual_returns else None
        open_floating = self._open_floating_return(db, open_rows)
        backtest = self._weekly_backtest(db, strategy_code, week_start, week_end)
        benchmark_return = benchmark.get("return_pct")
        comparison_return = actual_return if actual_return is not None else backtest.get("return_pct")
        excess = round(comparison_return - benchmark_return, 2) if comparison_return is not None and benchmark_return is not None else None
        status, optimization_signal = self._review_status(actual_return, backtest.get("return_pct"), benchmark_return, len(actual_returns))
        return {
            "strategy_code": strategy_code,
            "strategy_name": strategy_name,
            "actual_return_pct": actual_return,
            "actual_trade_count": len(actual_returns),
            "win_rate_pct": win_rate,
            "open_position_count": len(open_rows),
            "open_floating_return_pct": open_floating,
            "backtest_return_pct": backtest.get("return_pct"),
            "backtest_max_drawdown_pct": backtest.get("max_drawdown_pct"),
            "backtest_sample_points": backtest.get("sample_points", 0),
            "benchmark_return_pct": benchmark_return,
            "excess_vs_market_pct": excess,
            "status": status,
            "optimization_signal": optimization_signal,
            "diagnosis": self._diagnosis(status, actual_return, backtest.get("return_pct"), benchmark_return, len(actual_returns)),
            "suggestions": self._suggestions(optimization_signal, benchmark.get("regime"), strategy_code),
        }

    def _open_floating_return(self, db: Session, positions: list[PortfolioPosition]) -> float | None:
        latest_quotes = self._latest_quotes(db, [item.symbol for item in positions])
        returns = []
        for position in positions:
            quote = latest_quotes.get(position.symbol)
            if quote and quote.last_price and position.entry_price:
                returns.append((quote.last_price / position.entry_price - 1) * 100)
        return round(sum(returns) / len(returns), 2) if returns else None

    def _latest_quotes(self, db: Session, symbols: list[str]) -> dict[str, MarketQuote]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        if not unique_symbols:
            return {}
        latest_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.symbol.in_(unique_symbols), MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote).join(
                    latest_times,
                    (MarketQuote.symbol == latest_times.c.symbol) & (MarketQuote.observed_at == latest_times.c.observed_at),
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: row for row in rows}

    def _weekly_backtest(self, db: Session, strategy_code: str, week_start: datetime, week_end: datetime) -> dict[str, Any]:
        rows = (
            db.execute(
                select(PortfolioPosition)
                .where(
                    PortfolioPosition.strategy_code == strategy_code,
                    PortfolioPosition.status == PositionStatus.closed,
                    PortfolioPosition.realized_return_pct.is_not(None),
                    PortfolioPosition.exit_at >= week_start,
                    PortfolioPosition.exit_at <= week_end,
                )
                .order_by(PortfolioPosition.exit_at.asc())
            )
            .scalars()
            .all()
        )
        if not rows:
            return {"return_pct": None, "max_drawdown_pct": None, "sample_points": 0}
        equity = 1.0
        peak = 1.0
        drawdowns: list[float] = []
        for row in rows:
            equity *= 1 + float(row.realized_return_pct or 0) / 100
            peak = max(peak, equity)
            drawdowns.append((equity / peak - 1) * 100)
        weekly_return = round((equity - 1) * 100, 2)
        max_drawdown = min(drawdowns, default=0)
        return {
            "return_pct": weekly_return,
            "max_drawdown_pct": round(max_drawdown, 2) if max_drawdown is not None else None,
            "sample_points": len(rows),
        }

    def _point_in_week(self, point: dict[str, Any], week_start: datetime, week_end: datetime) -> bool:
        try:
            point_date = datetime.fromisoformat(str(point.get("date")))
        except ValueError:
            return False
        return week_start.date() <= point_date.date() <= week_end.date()

    def _market_regime(self, avg_return: float | None, up_ratio: float | None, amount_change: float | None) -> str:
        if avg_return is None or up_ratio is None:
            return "数据不足"
        if avg_return >= 2 and up_ratio >= 60:
            return "普涨偏强"
        if avg_return <= -2 and up_ratio <= 40:
            return "普跌偏弱"
        if amount_change is not None and amount_change >= 20 and up_ratio < 50:
            return "放量分化"
        if up_ratio >= 55:
            return "温和修复"
        if up_ratio <= 45:
            return "震荡偏弱"
        return "震荡分化"

    def _market_interpretation(
        self,
        regime: str,
        avg_return: float | None,
        up_ratio: float | None,
        amount_change: float | None,
    ) -> str:
        if avg_return is None:
            return "本周可用日 K 样本不足，暂不能形成可靠市场基准。"
        amount_text = "成交额变化不足" if amount_change is None else f"成交额较上周{amount_change:+.2f}%"
        return f"{regime}：覆盖股票等权收益 {avg_return:+.2f}%，上涨占比 {up_ratio or 0:.1f}%，{amount_text}。"

    def _review_status(
        self,
        actual_return: float | None,
        backtest_return: float | None,
        benchmark_return: float | None,
        actual_count: int,
    ) -> tuple[str, str]:
        if benchmark_return is None:
            return "市场基准不足", "watch"
        if actual_count == 0 and backtest_return is None:
            return "样本不足", "watch"
        primary = actual_return if actual_return is not None else backtest_return
        if primary is None:
            return "样本不足", "watch"
        excess = primary - benchmark_return
        if actual_count > 0 and excess >= 1:
            return "优于市场", "keep"
        if actual_count > 0 and excess <= -1:
            return "弱于市场", "review"
        if actual_count == 0 and excess <= -2:
            return "回测弱于市场", "review"
        return "接近市场", "watch"

    def _diagnosis(
        self,
        status: str,
        actual_return: float | None,
        backtest_return: float | None,
        benchmark_return: float | None,
        actual_count: int,
    ) -> str:
        if status == "样本不足":
            return "本周没有足够的已完成用户交易样本，建议先看候选质量和未完成持仓浮盈，不宜直接调参。"
        if benchmark_return is None:
            return "市场基准样本不足，暂不做相对强弱判断。"
        actual_text = "无实盘样本" if actual_return is None else f"实际收益 {actual_return:+.2f}%"
        backtest_text = "回测不足" if backtest_return is None else f"周回测 {backtest_return:+.2f}%"
        return f"{actual_text}，{backtest_text}，市场基准 {benchmark_return:+.2f}%；结论为{status}。"

    def _suggestions(self, optimization_signal: str, regime: str, strategy_code: str) -> list[str]:
        if optimization_signal == "keep":
            return ["维持当前参数观察", "继续沉淀本策略用户交易样本", "关注下周是否仍有稳定超额收益"]
        if optimization_signal == "review":
            base = ["复盘入选股票的成交额与回撤暴露", "降低本策略推荐权重或提高最低分阈值", "对比本周市场风格，检查因子是否失效"]
            if regime in {"普跌偏弱", "震荡偏弱"}:
                base.append("弱势环境下优先加入止损、低波动和仓位约束")
            if strategy_code in {"trend_breakout", "money_flow_anomaly"}:
                base.append("检查追高信号在放量分化行情中的回撤风险")
            return base
        return ["继续观察，不急于调参", "优先补充真实交易样本", "等待下一个完整交易周再做参数判断"]

    def _summary(self, benchmark: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
        outperform = sum(1 for item in reviews if item["status"] == "优于市场")
        underperform = sum(1 for item in reviews if item["optimization_signal"] == "review")
        watch = len(reviews) - outperform - underperform
        if underperform > outperform:
            assessment = "本周策略整体需要复盘，优先检查弱于市场的策略阈值、止损和样本质量。"
        elif outperform > 0:
            assessment = "本周已有策略跑赢市场，可继续保持观察并扩大有效样本。"
        else:
            assessment = "本周策略与市场接近或样本不足，建议先积累完整交易周样本。"
        return {
            "outperform_count": outperform,
            "underperform_count": underperform,
            "watch_count": watch,
            "overall_assessment": assessment,
            "market_regime": benchmark.get("regime"),
        }
