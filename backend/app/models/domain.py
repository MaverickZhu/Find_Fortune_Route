import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SignalAction(str, enum.Enum):
    watch = "watch"
    buy = "buy"
    sell = "sell"
    reduce = "reduce"
    hold = "hold"


class AlertStatus(str, enum.Enum):
    open = "open"
    triggered = "triggered"
    dismissed = "dismissed"


class TradeAction(str, enum.Enum):
    buy = "buy"
    sell = "sell"
    ignore = "ignore"
    watch = "watch"


class PositionStatus(str, enum.Enum):
    open = "open"
    closed = "closed"


class DataQualityLevel(str, enum.Enum):
    ok = "ok"
    stale = "stale"
    degraded = "degraded"
    missing = "missing"
    invalid = "invalid"


class Stock(Base):
    __tablename__ = "stocks"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    exchange: Mapped[str] = mapped_column(String(16), default="A_SHARE")
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MarketQuote(Base):
    __tablename__ = "market_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    last_price: Mapped[float] = mapped_column(Float)
    change_pct: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(32), default="akshare")
    quality: Mapped[str] = mapped_column(String(32), default="ok")

    __table_args__ = (Index("ix_market_quotes_symbol_time", "symbol", "observed_at"),)


class DailyBar(Base):
    __tablename__ = "daily_bars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    trade_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    amount: Mapped[float] = mapped_column(Float, default=0)
    turnover_rate: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(32), default="akshare")

    __table_args__ = (UniqueConstraint("symbol", "trade_date", name="uq_daily_bar_symbol_date"),)


class DataSource(Base):
    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    reliability: Mapped[float] = mapped_column(Float, default=0.5)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DataQualityLog(Base):
    __tablename__ = "data_quality_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(String(64), index=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(16), index=True, nullable=True)
    level: Mapped[DataQualityLevel] = mapped_column(Enum(DataQualityLevel), default=DataQualityLevel.ok, index=True)
    message: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MarketGuardrailState(Base):
    __tablename__ = "market_guardrail_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    status: Mapped[str] = mapped_column(String(32), default="healthy", index=True)
    mode: Mapped[str] = mapped_column(String(32), default="normal")
    selected_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_ok_count: Mapped[int] = mapped_column(Integer, default=0)
    source_fail_count: Mapped[int] = mapped_column(Integer, default=0)
    stale_symbol_count: Mapped[int] = mapped_column(Integer, default=0)
    max_deviation_pct: Mapped[float] = mapped_column(Float, default=0)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class MarketCalendar(Base):
    __tablename__ = "market_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(16), default="CN_A", index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, default=True)
    session: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(64), default="estimated")
    note: Mapped[str | None] = mapped_column(String(256), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("market", "trade_date", name="uq_market_calendar_date"),)


class StockStatus(Base):
    __tablename__ = "stock_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    board: Mapped[str] = mapped_column(String(32), default="A股")
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    is_new_stock: Mapped[bool] = mapped_column(Boolean, default=False)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    limit_up_down_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="estimated")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class InstitutionalHoldingSnapshot(Base):
    __tablename__ = "institutional_holding_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    report_date: Mapped[date] = mapped_column(Date, index=True)
    source: Mapped[str] = mapped_column(String(64), default="akshare_sina_circulate_holder")
    institution_count: Mapped[int] = mapped_column(Integer, default=0)
    fund_count: Mapped[int] = mapped_column(Integer, default=0)
    big_holder_count: Mapped[int] = mapped_column(Integer, default=0)
    top_holder_count: Mapped[int] = mapped_column(Integer, default=0)
    institution_holding_pct: Mapped[float] = mapped_column(Float, default=0)
    fund_holding_pct: Mapped[float] = mapped_column(Float, default=0)
    northbound_holding_pct: Mapped[float] = mapped_column(Float, default=0)
    top10_holding_pct: Mapped[float] = mapped_column(Float, default=0)
    institutional_change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    crowding_score: Mapped[float] = mapped_column(Float, default=0)
    data_status: Mapped[str] = mapped_column(String(32), default="real_shareholder_ratio")
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (UniqueConstraint("symbol", "report_date", "source", name="uq_institutional_holding_snapshot"),)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class StrategyLibraryEntry(Base):
    __tablename__ = "strategy_library_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32), default="v1.0.0")
    name: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    source: Mapped[str] = mapped_column(String(128), default="system_seed")
    thesis: Mapped[str] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    performance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    learning_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("code", "version", name="uq_strategy_library_code_version"),)


class StrategySignal(Base):
    __tablename__ = "strategy_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_code: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    action: Mapped[SignalAction] = mapped_column(Enum(SignalAction), default=SignalAction.watch)
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class StrategyObservation(Base):
    __tablename__ = "strategy_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_code: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_sample_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(32), default="watch")
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="observing", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SectorLinkageEvent(Base):
    __tablename__ = "sector_linkage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    sector: Mapped[str] = mapped_column(String(128), index=True)
    sector_type: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(16), default="", index=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    scan_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    last_price: Mapped[float] = mapped_column(Float)
    trigger_move_pct: Mapped[float] = mapped_column(Float, default=0)
    change_pct: Mapped[float] = mapped_column(Float, default=0)
    crowding_score: Mapped[float] = mapped_column(Float, default=0)
    volume_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    trigger_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    followup_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="observing", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_sector_linkage_event_trade_sector", "trade_date", "sector"),
        Index("ix_sector_linkage_event_symbol_time", "symbol", "triggered_at"),
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    target_buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_sell: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    strategy_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("symbol", name="uq_watchlist_symbol"),)


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    alert_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[AlertStatus] = mapped_column(Enum(AlertStatus), default=AlertStatus.open, index=True)
    triggered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResearchItem(Base):
    __tablename__ = "research_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    source: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    summary: Mapped[str] = mapped_column(Text)
    credibility: Mapped[float] = mapped_column(Float, default=0.5)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_code: Mapped[str] = mapped_column(String(64), index=True)
    stock_pool: Mapped[list[str]] = mapped_column(JSON, default=list)
    start_date: Mapped[datetime] = mapped_column(DateTime)
    end_date: Mapped[datetime] = mapped_column(DateTime)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UserTradeSample(Base):
    __tablename__ = "user_trade_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[TradeAction] = mapped_column(Enum(TradeAction))
    alert_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    decision_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    strategy_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="recorded", index=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PortfolioPosition(Base):
    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    strategy_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    entry_price: Mapped[float] = mapped_column(Float)
    entry_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[PositionStatus] = mapped_column(Enum(PositionStatus), default=PositionStatus.open, index=True)
    entry_sample_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    exit_sample_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
