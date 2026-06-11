from dataclasses import dataclass
from datetime import datetime
from statistics import pstdev

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.domain import (
    DailyBar,
    InstitutionalHoldingSnapshot,
    MarketQuote,
    PortfolioPosition,
    PositionStatus,
    SignalAction,
    StockStatus,
    Strategy,
    StrategySignal,
    TradeAction,
    UserTradeSample,
)
from app.services.institutional_holdings import InstitutionalHoldingService
from app.services.market_guardrails import MarketGuardrailService


@dataclass(frozen=True)
class StrategyDefinition:
    code: str
    name: str
    category: str
    description: str
    parameters: dict
    risk_rules: dict


STARTER_STRATEGIES = [
    StrategyDefinition(
        "multi_factor_alpha",
        "多因子选股",
        "multi_factor",
        "综合趋势、成交额、波动、估值质量和资金流代理指标生成评分。",
        {"rebalance": "daily", "min_score": 65},
        {"max_position_pct": 0.15, "stop_loss_pct": -8, "take_profit_pct": 18},
    ),
    StrategyDefinition(
        "mean_reversion",
        "均值回归/反转",
        "reversal",
        "捕捉短期超跌后的修复机会，适合震荡市和高流动性标的。",
        {"lookback_days": 20, "oversold_threshold": -5},
        {"max_holding_days": 10, "stop_loss_pct": -5},
    ),
    StrategyDefinition(
        "trend_breakout",
        "趋势突破",
        "trend",
        "识别放量上涨和突破信号，适合趋势市场。",
        {"volume_multiplier": 1.5, "breakout_score": 70},
        {"trailing_stop_pct": -6},
    ),
    StrategyDefinition(
        "low_vol_quality",
        "低波动质量",
        "quality",
        "偏向波动较低、走势平稳且基本面稳健的标的。",
        {"max_change_abs_pct": 3, "min_liquidity_amount": 50_000_000},
        {"max_position_pct": 0.2},
    ),
    StrategyDefinition(
        "money_flow_anomaly",
        "资金流异动",
        "flow",
        "用成交额和涨跌幅代理资金关注度，发现异常活跃标的。",
        {"amount_percentile": 0.8},
        {"cooldown_days": 3},
    ),
    StrategyDefinition(
        "event_driven_watch",
        "事件驱动观察",
        "event",
        "结合公告、新闻和研究条目触发观察信号。",
        {"research_tags": ["政策", "财报", "行业"]},
        {"manual_review_required": True},
    ),
    StrategyDefinition(
        "close_daily_multi_factor",
        "收盘日线多因子",
        "daily_close",
        "收盘后基于历史日线计算动量、趋势、波动、成交额和回撤风险，生成次日观察信号。",
        {"lookback_days": 120, "momentum_windows": [5, 20], "ma_windows": [5, 20, 60]},
        {"next_day_execution": True, "t_plus_one": True, "observe_only_when_guarded": True},
    ),
    StrategyDefinition(
        "institutional_crowding",
        "机构大资金抱团",
        "institutional",
        "基于十大流通股东中的机构、基金、北向和大资金持股占流通股比例构建抱团指数，并结合趋势与风控参数给出观察标的。",
        {"rebalance": "quarterly_snapshot_daily_rank", "top_n": 10, "min_crowding_score": 60},
        {"avoid_overheated_intraday_pct": 9.5, "stop_loss_pct": -6, "requires_shareholder_snapshot": True},
    ),
]


class StrategyEngine:
    quote_driven_strategy_codes = {
        "multi_factor_alpha",
        "mean_reversion",
        "trend_breakout",
        "low_vol_quality",
        "money_flow_anomaly",
        "event_driven_watch",
        "institutional_crowding",
    }

    def seed_strategies(self, db: Session) -> None:
        existing = {row[0] for row in db.execute(select(Strategy.code)).all()}
        for item in STARTER_STRATEGIES:
            if item.code not in existing:
                db.add(
                    Strategy(
                        code=item.code,
                        name=item.name,
                        category=item.category,
                        description=item.description,
                        parameters=item.parameters,
                        risk_rules=item.risk_rules,
                    )
                )
        db.commit()

    def generate_signals(self, db: Session, limit: int = 60) -> list[StrategySignal]:
        latest_quote_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        latest_quotes = (
            db.execute(
                select(MarketQuote)
                .join(
                    latest_quote_times,
                    and_(
                        MarketQuote.symbol == latest_quote_times.c.symbol,
                        MarketQuote.observed_at == latest_quote_times.c.observed_at,
                    ),
                )
                .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
                .order_by(MarketQuote.amount.desc(), func.abs(MarketQuote.change_pct).desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        guardrail = MarketGuardrailService().latest(db)
        symbols = [quote.symbol for quote in latest_quotes]
        panels = self._daily_panels(db, symbols)
        statuses = self._stock_statuses(db, symbols)
        institutional_metrics = InstitutionalHoldingService().latest_metrics(db, symbols)
        generated: list[StrategySignal] = []
        generated_at = datetime.utcnow()
        db.execute(delete(StrategySignal).where(StrategySignal.strategy_code.in_(self.quote_driven_strategy_codes)))
        for quote in latest_quotes:
            status = statuses.get(quote.symbol)
            factors = self._factors_for_quote(quote, panels.get(quote.symbol, []), institutional_metrics.get(quote.symbol))
            strategy_code = self._strategy_for_quote(quote, factors)
            if strategy_code == "institutional_crowding":
                scored = self._score_for_strategy(strategy_code, quote, factors, status)
                score = float(scored["score"])
                action = scored["action"]
                reason = str(scored["reason"])
            else:
                score = self._score_quote(quote, factors, status)
                action = self._action_from_score(score, quote.change_pct, factors, status)
                reason = self._reason(score, quote, factors, status)
            evidence_note = "信号用于辅助决策，不代表确定买卖建议。"
            if guardrail["status"] == "blocked":
                action = SignalAction.watch
                score = min(score, 60)
                reason = "真实行情源被阻断，信号降级为观察。"
                evidence_note = "真实行情保护阈值阻断，暂停买卖类信号升级。"
            signal = StrategySignal(
                strategy_code=strategy_code,
                symbol=quote.symbol,
                generated_at=generated_at,
                action=action,
                score=score,
                confidence=self._confidence(score, factors),
                reason=reason,
                evidence={
                    "last_price": quote.last_price,
                    "change_pct": quote.change_pct,
                    "amount": quote.amount,
                    "data_quality": quote.quality,
                    "data_status": factors["data_status"],
                    "factors": factors,
                    "stock_status": {
                        "board": status.board if status else None,
                        "is_st": bool(status.is_st) if status else False,
                        "is_suspended": bool(status.is_suspended) if status else False,
                    },
                    "guardrail": guardrail,
                    "note": evidence_note,
                },
            )
            db.add(signal)
            generated.append(signal)
        db.commit()
        return generated

    def pick_stocks(
        self,
        db: Session,
        strategy_codes: list[str] | None = None,
        min_score: float = 65,
        limit: int = 5,
        require_real_daily_factor: bool = False,
    ) -> dict:
        self.seed_strategies(db)
        enabled_codes = {
            row[0]
            for row in db.execute(select(Strategy.code).where(Strategy.enabled.is_(True))).all()
        }
        selected_codes = [code for code in (strategy_codes or []) if code in enabled_codes]
        if not selected_codes:
            selected_codes = [
                item.code
                for item in STARTER_STRATEGIES
                if item.code in enabled_codes and item.code in self.quote_driven_strategy_codes
            ]

        latest_generated_at = db.execute(
            select(func.max(StrategySignal.generated_at)).where(StrategySignal.strategy_code.in_(selected_codes))
        ).scalar_one_or_none()
        if latest_generated_at is None:
            self.generate_signals(db, limit=6000)
            latest_generated_at = db.execute(
                select(func.max(StrategySignal.generated_at)).where(StrategySignal.strategy_code.in_(selected_codes))
            ).scalar_one_or_none()

        universe_size = int(
            db.execute(select(func.count(func.distinct(MarketQuote.symbol))).where(MarketQuote.quality == "ok")).scalar_one() or 0
        )
        if latest_generated_at is None:
            return self._empty_pick_response(selected_codes, min_score, limit, universe_size)

        latest_quote_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        latest_quotes = (
            db.execute(
                select(MarketQuote)
                .join(
                    latest_quote_times,
                    and_(
                        MarketQuote.symbol == latest_quote_times.c.symbol,
                        MarketQuote.observed_at == latest_quote_times.c.observed_at,
                    ),
                )
                .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            )
            .scalars()
            .all()
        )
        quote_map = {quote.symbol: quote for quote in latest_quotes}
        status_map = self._stock_statuses(db, list(quote_map))
        institutional_metrics = InstitutionalHoldingService().latest_metrics(db, list(quote_map))
        rows = (
            db.execute(
                select(StrategySignal)
                .where(
                    StrategySignal.strategy_code.in_(selected_codes),
                    StrategySignal.generated_at == latest_generated_at,
                    StrategySignal.score >= min_score,
                    StrategySignal.action.in_([SignalAction.buy, SignalAction.watch, SignalAction.hold]),
                )
                .order_by(StrategySignal.score.desc(), StrategySignal.confidence.desc())
                .limit(300)
            )
            .scalars()
            .all()
        )

        excluded_symbols = self.excluded_simulated_symbols(db)
        candidates: list[dict] = []
        seen_symbols: set[str] = set()
        for signal in rows:
            if signal.symbol in excluded_symbols:
                continue
            quote = quote_map.get(signal.symbol)
            status = status_map.get(signal.symbol)
            if not quote or (status and status.is_suspended):
                continue
            evidence = signal.evidence or {}
            data_status = str(evidence.get("data_status") or "")
            if require_real_daily_factor and data_status != "real_daily_factor":
                continue
            if signal.symbol in seen_symbols:
                continue
            seen_symbols.add(signal.symbol)
            factors = evidence.get("factors") if isinstance(evidence.get("factors"), dict) else {}
            if signal.strategy_code == "institutional_crowding" and not factors.get("institutional"):
                factors = {**factors, "institutional": institutional_metrics.get(signal.symbol)}
            candidates.append(
                {
                    "symbol": signal.symbol,
                    "name": quote.name or signal.symbol,
                    "strategy_code": signal.strategy_code,
                    "action": signal.action.value if hasattr(signal.action, "value") else str(signal.action),
                    "score": signal.score,
                    "confidence": signal.confidence,
                    "reason": signal.reason,
                    "last_price": quote.last_price,
                    "change_pct": quote.change_pct,
                    "amount": quote.amount,
                    "data_status": data_status or "real_quote_signal",
                    "quote_source": quote.source,
                    "quote_quality": quote.quality,
                    "observed_at": quote.observed_at,
                    "generated_at": signal.generated_at,
                    "factors": factors,
                }
            )

        items = sorted(candidates, key=lambda item: (item["score"], item["confidence"], item["amount"]), reverse=True)[:limit]
        message = (
            f"已从 {universe_size} 只真实行情覆盖股票中筛出 {len(items)} 只候选。"
            if items
            else "以上策略暂无推荐。"
        )
        return {
            "selected_strategy_codes": selected_codes,
            "min_score": min_score,
            "limit": limit,
            "generated_at": latest_generated_at,
            "universe_size": universe_size,
            "candidate_count": len(candidates),
            "items": items,
            "message": message,
        }

    def strategy_observation_groups(
        self,
        db: Session,
        limit_per_strategy: int = 10,
        min_score: float = 55,
        universe_limit: int = 6000,
    ) -> list[dict]:
        self.seed_strategies(db)
        latest_quote_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        latest_quotes = (
            db.execute(
                select(MarketQuote)
                .join(
                    latest_quote_times,
                    and_(
                        MarketQuote.symbol == latest_quote_times.c.symbol,
                        MarketQuote.observed_at == latest_quote_times.c.observed_at,
                    ),
                )
                .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0)
                .order_by(MarketQuote.amount.desc(), func.abs(MarketQuote.change_pct).desc())
                .limit(universe_limit)
            )
            .scalars()
            .all()
        )
        strategy_defs = [
            item
            for item in STARTER_STRATEGIES
            if item.code in self.quote_driven_strategy_codes or item.code == "close_daily_multi_factor"
        ]
        symbols = [quote.symbol for quote in latest_quotes]
        panels = self._daily_panels(db, symbols)
        statuses = self._stock_statuses(db, symbols)
        institutional_metrics = InstitutionalHoldingService().latest_metrics(db, symbols)
        excluded_symbols = self.excluded_simulated_symbols(db)
        generated_at = datetime.utcnow() if latest_quotes else None
        grouped: dict[str, list[dict]] = {item.code: [] for item in strategy_defs}

        for quote in latest_quotes:
            if quote.symbol in excluded_symbols:
                continue
            status = statuses.get(quote.symbol)
            if status and status.is_suspended:
                continue
            factors = self._factors_for_quote(quote, panels.get(quote.symbol, []), institutional_metrics.get(quote.symbol))
            for definition in strategy_defs:
                scored = self._score_for_strategy(definition.code, quote, factors, status)
                if scored["score"] < min_score:
                    continue
                grouped[definition.code].append(
                    {
                        "symbol": quote.symbol,
                        "name": quote.name or quote.symbol,
                        "strategy_code": definition.code,
                        "strategy_name": definition.name,
                        "rank": 0,
                        "score": scored["score"],
                        "confidence": self._strategy_confidence(scored["score"], factors, definition.code),
                        "action": scored["action"].value if hasattr(scored["action"], "value") else str(scored["action"]),
                        "reason": scored["reason"],
                        "last_price": quote.last_price,
                        "change_pct": quote.change_pct,
                        "amount": quote.amount,
                        "quote_source": quote.source,
                        "quote_quality": quote.quality,
                        "observed_at": quote.observed_at,
                        "data_status": factors["data_status"],
                        "factors": factors,
                    }
                )

        groups: list[dict] = []
        for definition in strategy_defs:
            rows = sorted(
                grouped[definition.code],
                key=lambda item: (item["score"], item["confidence"], item["amount"]),
                reverse=True,
            )
            items = rows[:limit_per_strategy]
            for index, item in enumerate(items, start=1):
                item["rank"] = index
            groups.append(
                {
                    "strategy_code": definition.code,
                    "strategy_name": definition.name,
                    "category": definition.category,
                    "description": self._strategy_observation_description(definition.code),
                    "min_score": min_score,
                    "candidate_count": len(rows),
                    "generated_at": generated_at,
                    "items": items,
                    "message": f"当前符合策略条件 {len(rows)} 只，展示前 {len(items)} 只。"
                    if items
                    else "当前没有股票达到该策略的基础评分阈值。",
                }
            )
        return groups

    def _empty_pick_response(self, strategy_codes: list[str], min_score: float, limit: int, universe_size: int) -> dict:
        return {
            "selected_strategy_codes": strategy_codes,
            "min_score": min_score,
            "limit": limit,
            "generated_at": None,
            "universe_size": universe_size,
            "candidate_count": 0,
            "items": [],
            "message": "以上策略暂无推荐。",
        }

    def excluded_simulated_symbols(self, db: Session) -> set[str]:
        today = datetime.utcnow().date()
        open_symbols = {
            row[0]
            for row in db.execute(
                select(PortfolioPosition.symbol).where(PortfolioPosition.status == PositionStatus.open)
            ).all()
        }
        traded_today = {
            row[0]
            for row in db.execute(
                select(UserTradeSample.symbol).where(
                    UserTradeSample.action.in_([TradeAction.buy, TradeAction.sell]),
                    func.date(UserTradeSample.decision_at) == today,
                )
            ).all()
        }
        return open_symbols | traded_today

    def _daily_panels(self, db: Session, symbols: list[str]) -> dict[str, list[DailyBar]]:
        if not symbols:
            return {}
        rows = (
            db.execute(
                select(DailyBar)
                .where(DailyBar.symbol.in_(symbols))
                .order_by(DailyBar.symbol.asc(), DailyBar.trade_date.asc())
            )
            .scalars()
            .all()
        )
        panels: dict[str, list[DailyBar]] = {}
        for row in rows:
            panels.setdefault(row.symbol, []).append(row)
        return panels

    def _stock_statuses(self, db: Session, symbols: list[str]) -> dict[str, StockStatus]:
        if not symbols:
            return {}
        rows = db.execute(select(StockStatus).where(StockStatus.symbol.in_(symbols))).scalars().all()
        return {row.symbol: row for row in rows}

    def _factors_for_quote(self, quote: MarketQuote, bars: list[DailyBar], institutional: dict | None = None) -> dict:
        closes = [bar.close for bar in bars if bar.close > 0]
        amounts = [bar.amount for bar in bars if bar.amount > 0]
        returns = [(closes[idx] / closes[idx - 1] - 1) * 100 for idx in range(1, len(closes)) if closes[idx - 1] > 0]
        ma5 = self._mean(closes[-5:])
        ma20 = self._mean(closes[-20:])
        ma60 = self._mean(closes[-60:]) if len(closes) >= 60 else ma20
        high60 = max(closes[-60:]) if len(closes) >= 60 else max(closes or [quote.last_price])
        amount20 = self._mean(amounts[-20:])
        live_amount_ratio = quote.amount / amount20 if amount20 > 0 else None
        ret5 = self._window_return(closes, 5)
        ret20 = self._window_return(closes, 20)
        volatility20 = pstdev(returns[-20:]) if len(returns[-20:]) >= 2 else None
        return {
            "data_status": "real_daily_factor" if len(closes) >= 30 else "live_quote_only",
            "bar_count": len(closes),
            "return_1d_pct": round(quote.change_pct, 3),
            "return_5d_pct": ret5,
            "return_20d_pct": ret20,
            "ma5": round(ma5, 3) if ma5 else None,
            "ma20": round(ma20, 3) if ma20 else None,
            "ma60": round(ma60, 3) if ma60 else None,
            "distance_ma20_pct": round((quote.last_price / ma20 - 1) * 100, 3) if ma20 else None,
            "drawdown_60d_pct": round((quote.last_price / high60 - 1) * 100, 3) if high60 else None,
            "volatility_20d_pct": round(volatility20, 3) if volatility20 is not None else None,
            "amount_20d_avg": round(amount20, 2) if amount20 else None,
            "live_amount_ratio": round(live_amount_ratio, 3) if live_amount_ratio is not None else None,
            "institutional": institutional,
        }

    def _score_quote(self, quote: MarketQuote, factors: dict, status: StockStatus | None) -> float:
        liquidity = min(12, quote.amount / 300_000_000)
        momentum_base = float(factors.get("return_20d_pct") or quote.change_pct)
        momentum = max(-12, min(12, momentum_base * 0.25 + quote.change_pct * 0.8))
        stability = max(0, 8 - abs(quote.change_pct) * 0.8)
        trend = 0
        if factors.get("data_status") == "real_daily_factor":
            ma5 = factors.get("ma5") or 0
            ma20 = factors.get("ma20") or 0
            ma60 = factors.get("ma60") or 0
            trend += 7 if ma5 >= ma20 >= ma60 else -7
            trend += max(-4, min(4, float(factors.get("distance_ma20_pct") or 0) * 0.14))
            trend += min(4, max(0, (float(factors.get("live_amount_ratio") or 1) - 1) * 1.4))
            trend -= min(8, max(0, float(factors.get("volatility_20d_pct") or 0) * 0.55))
        else:
            trend -= 12
        quality = 8 if quote.quality == "ok" else 2
        risk_penalty = 0
        if status and status.is_st:
            risk_penalty += 12
        if status and status.is_suspended:
            risk_penalty += 100
        score = 25 + liquidity + momentum + stability + trend + quality - risk_penalty
        return round(max(0, min(92, score)), 2)

    def _score_for_strategy(self, code: str, quote: MarketQuote, factors: dict, status: StockStatus | None) -> dict:
        if quote.quality != "ok" or (status and status.is_st):
            return {"score": 0.0, "action": SignalAction.watch, "reason": "风险警示或数据质量不足，暂不进入策略候选。"}
        if factors.get("data_status") != "real_daily_factor":
            base = 35 + min(10, quote.amount / 500_000_000) + max(-8, min(8, quote.change_pct))
            return {
                "score": round(max(0, min(58, base)), 2),
                "action": SignalAction.watch,
                "reason": "本地日 K 样本不足，仅按实时行情保留低置信观察。",
            }

        ret5 = float(factors.get("return_5d_pct") or 0)
        ret20 = float(factors.get("return_20d_pct") or 0)
        distance_ma20 = float(factors.get("distance_ma20_pct") or 0)
        drawdown = float(factors.get("drawdown_60d_pct") or 0)
        volatility = float(factors.get("volatility_20d_pct") or 0)
        amount_ratio = float(factors.get("live_amount_ratio") or 1)
        ma5 = float(factors.get("ma5") or 0)
        ma20 = float(factors.get("ma20") or 0)
        ma60 = float(factors.get("ma60") or 0)
        liquidity = min(10, quote.amount / 400_000_000)

        if code == "trend_breakout":
            score = 44 + max(0, ret20) * 1.1 + max(0, ret5) * 1.4 + max(0, amount_ratio - 1) * 8 + liquidity
            score += 8 if ma5 >= ma20 >= ma60 else -8
            score -= max(0, quote.change_pct - 8) * 2
            return {
                "score": round(max(0, min(96, score)), 2),
                "action": SignalAction.buy if score >= 72 and quote.change_pct < 9.5 else SignalAction.watch,
                "reason": "趋势、均线排列和成交活跃度共同靠前，适合作为突破策略观察标的。",
            }
        if code == "mean_reversion":
            oversold = abs(min(0, ret5)) * 2 + abs(min(0, drawdown)) * 0.9 + abs(min(0, distance_ma20)) * 1.2
            rebound = max(0, quote.change_pct) * 1.5
            score = 38 + oversold + rebound + liquidity - max(0, volatility - 6) * 1.5
            return {
                "score": round(max(0, min(94, score)), 2),
                "action": SignalAction.watch,
                "reason": "短期回撤和均线偏离较明显，若出现止跌修复可纳入反转观察。",
            }
        if code == "low_vol_quality":
            score = 48 + max(0, 8 - volatility) * 3 + max(0, 3 - abs(quote.change_pct)) * 2 + liquidity
            score += 7 if ma5 >= ma20 else 0
            score += 5 if drawdown > -12 else -5
            return {
                "score": round(max(0, min(92, score)), 2),
                "action": SignalAction.hold if score >= 70 else SignalAction.watch,
                "reason": "波动率、回撤和趋势稳定性较好，偏向稳健持有与低波动观察。",
            }
        if code == "money_flow_anomaly":
            score = 40 + max(0, amount_ratio - 1) * 18 + min(12, quote.amount / 300_000_000) + abs(quote.change_pct) * 1.2
            score -= max(0, quote.change_pct - 9) * 3
            return {
                "score": round(max(0, min(95, score)), 2),
                "action": SignalAction.watch,
                "reason": "实时成交额相对历史均值显著放大，资金关注度异常，适合事件与资金流联动观察。",
            }
        if code == "institutional_crowding":
            institutional = factors.get("institutional") if isinstance(factors.get("institutional"), dict) else {}
            crowding = float(institutional.get("crowding_score") or 0)
            institution_pct = float(institutional.get("institution_holding_pct") or 0)
            institution_count = int(institutional.get("institution_count") or 0)
            fund_count = int(institutional.get("fund_count") or 0)
            change_pct = float(institutional.get("institutional_change_pct") or 0)
            if crowding <= 0:
                return {
                    "score": 0.0,
                    "action": SignalAction.watch,
                    "reason": "暂无机构/基金/北向持仓快照，需先同步机构抱团数据。",
                }
            trend_bonus = 0
            trend_bonus += 6 if ma5 >= ma20 >= ma60 else -5
            trend_bonus += max(-5, min(8, ret20 * 0.22))
            trend_bonus += max(-4, min(4, change_pct * 0.8))
            risk_penalty = max(0, quote.change_pct - 8) * 2 + max(0, volatility - 7) * 0.8
            score = crowding * 0.72 + trend_bonus + liquidity - risk_penalty
            action = SignalAction.buy if score >= 72 and 0 <= quote.change_pct < 9.5 else SignalAction.watch
            if score < 55 or quote.change_pct < -6:
                action = SignalAction.reduce
            return {
                "score": round(max(0, min(96, score)), 2),
                "action": action,
                "reason": f"机构抱团指数 {crowding:.1f}，机构持股约 {institution_pct:.1f}%、机构家数 {institution_count}、基金家数 {fund_count}，结合趋势与不过热规则给出观察。",
            }
        if code == "event_driven_watch":
            event_move = max(abs(quote.change_pct) - 2.5, 0) * 4 + max(amount_ratio - 1.2, 0) * 12
            score = 36 + event_move + liquidity
            return {
                "score": round(max(0, min(90, score)), 2),
                "action": SignalAction.watch,
                "reason": "价格波动和成交放量显示事件驱动特征，需要结合公告、新闻和行业信息复核。",
            }
        if code == "close_daily_multi_factor":
            score = self._score_quote(quote, factors, status) + max(0, ret20) * 0.35 - max(0, volatility - 7)
            return {
                "score": round(max(0, min(94, score)), 2),
                "action": SignalAction.watch,
                "reason": "收盘日线多因子综合评分靠前，适合进入次日策略观测池。",
            }

        score = self._score_quote(quote, factors, status)
        return {
            "score": score,
            "action": self._action_from_score(score, quote.change_pct, factors, status),
            "reason": "趋势、流动性、波动和日线因子综合评分靠前，适合作为多因子候选。",
        }

    def _strategy_confidence(self, score: float, factors: dict, code: str) -> float:
        base = self._confidence(score, factors)
        if code in {"event_driven_watch", "money_flow_anomaly"}:
            return round(max(0.35, min(0.88, base - 0.04)), 2)
        return base

    def _strategy_observation_description(self, code: str) -> str:
        descriptions = {
            "multi_factor_alpha": "综合趋势、流动性、波动和成交活跃度，不把成交额作为唯一依据。",
            "mean_reversion": "寻找短期超跌、回撤较深但仍有流动性的修复候选。",
            "trend_breakout": "优先观察中短期趋势向上、均线配合且放量的突破候选。",
            "low_vol_quality": "偏向走势稳定、回撤较浅、波动受控的稳健候选。",
            "money_flow_anomaly": "观察成交额相对历史均值异常放大的资金关注标的。",
            "institutional_crowding": "按机构、基金、北向和大资金在十大流通股东中的占比与家数计算抱团指数，展示排名靠前标的。",
            "event_driven_watch": "捕捉涨跌幅与成交额共同异动的事件线索，需结合新闻公告复核。",
            "close_daily_multi_factor": "基于日线多因子在收盘后形成次日观察候选。",
        }
        return descriptions.get(code, "按该策略的核心因子筛选当前最匹配的候选股。")

    def _action_from_score(self, score: float, change_pct: float, factors: dict, status: StockStatus | None) -> SignalAction:
        if status and status.is_suspended:
            return SignalAction.watch
        if factors.get("data_status") != "real_daily_factor":
            return SignalAction.watch if score >= 60 else SignalAction.hold
        if score >= 72 and 0 < change_pct < 9.5:
            return SignalAction.buy
        if score <= 40 or change_pct < -6:
            return SignalAction.reduce
        if change_pct > 7:
            return SignalAction.watch
        return SignalAction.hold

    def _strategy_for_quote(self, quote: MarketQuote, factors: dict) -> str:
        if factors.get("data_status") == "real_daily_factor":
            ret5 = float(factors.get("return_5d_pct") or 0)
            ret20 = float(factors.get("return_20d_pct") or 0)
            ma5 = float(factors.get("ma5") or 0)
            ma20 = float(factors.get("ma20") or 0)
            drawdown = float(factors.get("drawdown_60d_pct") or 0)
            live_amount_ratio = float(factors.get("live_amount_ratio") or 1)
            institutional = factors.get("institutional") if isinstance(factors.get("institutional"), dict) else {}
            if float(institutional.get("crowding_score") or 0) >= 68:
                return "institutional_crowding"
            if ret5 < -3 and drawdown < -5:
                return "mean_reversion"
            if ret20 > 8 and ma5 >= ma20:
                return "trend_breakout"
            if live_amount_ratio >= 1.6 or quote.amount > 800_000_000:
                return "money_flow_anomaly"
            return "multi_factor_alpha"
        if quote.change_pct < -4:
            return "mean_reversion"
        if quote.change_pct > 4:
            return "trend_breakout"
        if quote.amount > 300_000_000:
            return "money_flow_anomaly"
        return "multi_factor_alpha"

    def _reason(self, score: float, quote: MarketQuote, factors: dict, status: StockStatus | None) -> str:
        if quote.quality != "ok":
            return "当前使用演示或降级数据，建议先刷新真实行情后再评估。"
        if status and status.is_suspended:
            return "该标的被识别为停牌或零成交，策略仅保留观察记录，不生成交易提示。"
        if factors.get("data_status") != "real_daily_factor":
            return "该股本地真实日 K 样本不足，暂按实时行情低置信观察，待补齐历史数据后再升级推荐。"
        action = self._action_from_score(score, quote.change_pct, factors, status)
        if action == SignalAction.buy:
            return "真实日 K 因子与实时成交活跃度评分较高，进入买入观察候选。"
        if action == SignalAction.reduce:
            return "真实日 K 因子显示短线风险或趋势弱化，建议降低仓位或等待修复。"
        if action == SignalAction.watch:
            return "真实日 K 因子较强但短线波动或涨跌幅偏高，保留为重点观察。"
        return "真实日 K 因子综合评分中性，适合继续观察并结合基本面与市场状态判断。"

    def _confidence(self, score: float, factors: dict) -> float:
        if factors.get("data_status") != "real_daily_factor":
            return min(0.55, max(0.3, score / 160))
        bar_count = int(factors.get("bar_count") or 0)
        history_bonus = min(0.2, bar_count / 600)
        return round(min(0.92, max(0.45, score / 115 + history_bonus)), 2)

    def _window_return(self, closes: list[float], window: int) -> float:
        if len(closes) <= window or closes[-window - 1] <= 0:
            return 0
        return round((closes[-1] / closes[-window - 1] - 1) * 100, 3)

    def _mean(self, values: list[float]) -> float:
        return sum(values) / max(1, len(values))
