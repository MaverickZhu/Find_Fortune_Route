from datetime import datetime

from app.models.domain import MarketQuote, SignalAction
from app.services.strategy_engine import StrategyEngine


def test_strategy_engine_scores_high_liquidity_positive_quote_as_buy_candidate() -> None:
    quote = MarketQuote(
        symbol="000001",
        name="平安银行",
        observed_at=datetime.utcnow(),
        last_price=12.3,
        change_pct=3.2,
        volume=10_000_000,
        amount=500_000_000,
        quality="ok",
    )

    engine = StrategyEngine()
    score = engine._score_quote(quote)
    action = engine._action_from_score(score, quote.change_pct)

    assert score >= 75
    assert action == SignalAction.buy
