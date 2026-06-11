from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Alert, AlertStatus, MarketQuote, StrategySignal, WatchlistItem
from app.services.market_rules import MarketRuleService


class AlertService:
    def evaluate_watchlist(self, db: Session) -> list[Alert]:
        items = db.execute(select(WatchlistItem)).scalars().all()
        triggered: list[Alert] = []
        for item in items:
            quote = db.execute(
                select(MarketQuote)
                .where(MarketQuote.symbol == item.symbol)
                .order_by(MarketQuote.observed_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if quote is None:
                continue
            quote_payload = {
                "symbol": quote.symbol,
                "name": quote.name,
                "last_price": quote.last_price,
                "change_pct": quote.change_pct,
                "volume": quote.volume,
                "amount": quote.amount,
                "source": quote.source,
                "quality": quote.quality,
            }
            market_rules = MarketRuleService().evaluate_quote(quote_payload, db=db)
            signal = self._latest_signal(db, item.symbol, item.strategy_code)
            for alert_type, message in self._messages(item, quote):
                existing = db.execute(
                    select(Alert)
                    .where(
                        Alert.symbol == item.symbol,
                        Alert.alert_type == alert_type,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if existing:
                    continue
                alert = Alert(
                    symbol=item.symbol,
                    alert_type=alert_type,
                    message=message,
                    status=AlertStatus.triggered,
                    triggered_at=datetime.utcnow(),
                    payload={
                        "watchlist_id": item.id,
                        "strategy_code": item.strategy_code,
                        "price": quote.last_price,
                        "change_pct": quote.change_pct,
                        "quote_source": quote.source,
                        "quote_quality": quote.quality,
                        "observed_at": quote.observed_at.isoformat(),
                        "market_rules": market_rules,
                        "signal": self._signal_payload(signal),
                    },
                )
                db.add(alert)
                triggered.append(alert)
        db.commit()
        return triggered

    def _messages(self, item: WatchlistItem, quote: MarketQuote) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        if item.target_buy is not None and quote.last_price <= item.target_buy:
            messages.append(("target_buy", f"{item.symbol} 已到达买入观察价 {item.target_buy}，当前 {quote.last_price}。"))
        if item.target_sell is not None and quote.last_price >= item.target_sell:
            messages.append(("target_sell", f"{item.symbol} 已到达卖出观察价 {item.target_sell}，当前 {quote.last_price}。"))
        if item.stop_loss is not None and quote.last_price <= item.stop_loss:
            messages.append(("stop_loss", f"{item.symbol} 触发止损观察价 {item.stop_loss}，当前 {quote.last_price}。"))
        if item.take_profit is not None and quote.last_price >= item.take_profit:
            messages.append(("take_profit", f"{item.symbol} 触发止盈观察价 {item.take_profit}，当前 {quote.last_price}。"))
        return messages

    def _latest_signal(self, db: Session, symbol: str, strategy_code: str | None) -> StrategySignal | None:
        query = select(StrategySignal).where(StrategySignal.symbol == symbol)
        if strategy_code:
            query = query.where(StrategySignal.strategy_code == strategy_code)
        return db.execute(query.order_by(StrategySignal.generated_at.desc()).limit(1)).scalar_one_or_none()

    def _signal_payload(self, signal: StrategySignal | None) -> dict | None:
        if signal is None:
            return None
        return {
            "id": signal.id,
            "strategy_code": signal.strategy_code,
            "action": signal.action.value if hasattr(signal.action, "value") else signal.action,
            "score": signal.score,
            "confidence": signal.confidence,
            "reason": signal.reason,
            "data_status": (signal.evidence or {}).get("data_status"),
            "generated_at": signal.generated_at.isoformat(),
        }
