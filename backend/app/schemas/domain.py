from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class MarketQuoteOut(BaseModel):
    symbol: str
    name: str
    observed_at: datetime
    last_price: float
    change_pct: float
    volume: float
    amount: float
    source: str
    quality: str

    model_config = {"from_attributes": True}


class StrategySignalOut(BaseModel):
    id: int
    strategy_code: str
    symbol: str
    generated_at: datetime
    action: str
    score: float
    confidence: float
    reason: str
    evidence: dict[str, Any]

    model_config = {"from_attributes": True}


class StrategyPickRequest(BaseModel):
    strategy_codes: list[str] = Field(default_factory=list)
    min_score: float = Field(default=65, ge=0, le=100)
    limit: int = Field(default=5, ge=1, le=10)
    require_real_daily_factor: bool = False


class StrategyPickItem(BaseModel):
    symbol: str
    name: str
    strategy_code: str
    action: str
    score: float
    confidence: float
    reason: str
    last_price: float
    change_pct: float
    amount: float
    data_status: str
    quote_source: str
    quote_quality: str
    observed_at: datetime | None = None
    generated_at: datetime
    factors: dict[str, Any] = Field(default_factory=dict)


class StrategyPickResponse(BaseModel):
    selected_strategy_codes: list[str]
    min_score: float
    limit: int
    generated_at: datetime | None = None
    universe_size: int
    candidate_count: int
    items: list[StrategyPickItem]
    message: str


class StrategyObservationCandidate(BaseModel):
    symbol: str
    name: str
    strategy_code: str
    strategy_name: str
    rank: int
    score: float
    confidence: float
    action: str
    reason: str
    last_price: float
    change_pct: float
    amount: float
    quote_source: str
    quote_quality: str
    observed_at: datetime
    data_status: str
    factors: dict[str, Any] = Field(default_factory=dict)


class StrategyObservationGroup(BaseModel):
    strategy_code: str
    strategy_name: str
    category: str
    description: str
    min_score: float
    candidate_count: int
    generated_at: datetime | None = None
    items: list[StrategyObservationCandidate]
    message: str


class WatchlistCreate(BaseModel):
    symbol: str = Field(min_length=6, max_length=16)
    name: str = ""
    target_buy: float | None = None
    target_sell: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_code: str | None = None


class WatchlistUpdate(BaseModel):
    name: str | None = None
    target_buy: float | None = None
    target_sell: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy_code: str | None = None


class WatchlistOut(WatchlistCreate):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertOut(BaseModel):
    id: int
    symbol: str
    alert_type: str
    message: str
    status: str
    triggered_at: datetime | None
    payload: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class TradeSampleCreate(BaseModel):
    symbol: str
    action: str
    decision_price: float
    alert_id: int | None = None
    quantity: int | None = None
    strategy_code: str | None = None
    realized_return_pct: float | None = None
    status: str = "recorded"
    notes: str | None = None
    features: dict[str, Any] = Field(default_factory=dict)


class AlertDecisionCreate(BaseModel):
    action: str
    quantity: int | None = None
    notes: str | None = None


class DashboardOut(BaseModel):
    market_overview: dict[str, Any]
    market_quotes: list[MarketQuoteOut]
    strategy_observations: list[StrategyObservationGroup]
    signals: list[StrategySignalOut]
    alerts: list[AlertOut]
    watchlist: list[WatchlistOut]
    strategies: list[dict[str, Any]]
    research: list[dict[str, Any]]
    backtests: list[dict[str, Any]]
    data_quality: dict[str, Any]
    market_rules: dict[str, Any]
    trade_samples: dict[str, Any]
    portfolio: dict[str, Any]
    strategy_library: dict[str, Any]
    weekly_analysis: dict[str, Any]
    sector_linkage: dict[str, Any]
    readiness: dict[str, Any]
    guardrails: dict[str, Any]
