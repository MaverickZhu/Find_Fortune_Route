export type MarketQuote = {
  symbol: string;
  name: string;
  observed_at: string;
  last_price: number;
  change_pct: number;
  volume: number;
  amount: number;
  source: string;
  quality: string;
};

export type StrategySignal = {
  id: number;
  strategy_code: string;
  symbol: string;
  generated_at: string;
  action: string;
  score: number;
  confidence: number;
  reason: string;
  evidence: Record<string, unknown>;
};

export type StrategyObservationCandidate = {
  symbol: string;
  name: string;
  strategy_code: string;
  strategy_name: string;
  rank: number;
  score: number;
  confidence: number;
  action: string;
  reason: string;
  last_price: number;
  change_pct: number;
  amount: number;
  quote_source: string;
  quote_quality: string;
  observed_at: string;
  data_status: string;
  factors: Record<string, unknown>;
};

export type StrategyObservationGroup = {
  strategy_code: string;
  strategy_name: string;
  category: string;
  description: string;
  min_score: number;
  candidate_count: number;
  generated_at?: string | null;
  items: StrategyObservationCandidate[];
  message: string;
};

export type SectorLinkageTrigger = {
  symbol: string;
  name: string;
  last_price: number;
  change_pct: number;
  trigger_type?: string;
  trigger_move_pct?: number;
  sudden_window_minutes?: number | null;
  sudden_threshold_pct?: number | null;
  market_median_move_pct?: number | null;
  market_excess_move_pct?: number | null;
  market_excess_threshold_pct?: number | null;
  baseline_price?: number | null;
  baseline_at?: string | null;
  amount_delta?: number | null;
  previous_amount_delta?: number | null;
  amount_surge_ratio?: number | null;
  volume_delta?: number | null;
  previous_volume_delta?: number | null;
  volume_surge_ratio?: number | null;
  intraday_amount_intensity?: number | null;
  intraday_volume_intensity?: number | null;
  market_median_amount_delta?: number | null;
  market_median_volume_delta?: number | null;
  market_amount_ratio?: number | null;
  market_volume_ratio?: number | null;
  volume_confirmed?: boolean;
  amount: number;
  crowding_score: number;
  institution_holding_pct: number;
  institution_count: number;
  fund_count: number;
  report_date: string;
  quote_source: string;
  observed_at: string;
};

export type SectorLinkageCandidate = {
  symbol: string;
  name: string;
  strategy_code: string;
  action: string;
  score: number;
  confidence: number;
  reason: string;
  last_price: number;
  change_pct: number;
  amount: number;
  quote_source: string;
  observed_at: string;
  data_status: string;
};

export type SectorLinkageGroup = {
  sector: string;
  sector_type: string;
  direction: "up" | "down" | string;
  sector_strength: number;
  trigger_count: number;
  trigger_types?: string[];
  trigger_symbols: SectorLinkageTrigger[];
  candidate_count: number;
  items: SectorLinkageCandidate[];
  message: string;
};

export type SectorLinkageHistoryItem = {
  id: number;
  trade_date: string;
  sector: string;
  sector_type: string;
  symbol: string;
  name: string;
  trigger_type: string;
  direction: string;
  triggered_at: string;
  last_price: number;
  trigger_move_pct: number;
  change_pct: number;
  crowding_score: number;
  volume_confirmed: boolean;
  candidate_count: number;
  candidates: Array<Record<string, unknown>>;
  followup_metrics: {
    trigger_current_price?: number | null;
    trigger_return_pct?: number | null;
    candidate_count?: number;
    measured_candidate_count?: number;
    avg_candidate_return_pct?: number | null;
    positive_candidate_count?: number;
    positive_candidate_rate_pct?: number | null;
    best_candidate?: Record<string, unknown> | null;
    worst_candidate?: Record<string, unknown> | null;
    candidate_returns?: Array<Record<string, unknown>>;
    elapsed_minutes?: number;
    updated_at?: string;
  };
  status: string;
};

export type SectorLinkageHistory = {
  total_today: number;
  positive_today: number;
  avg_candidate_return_pct?: number | null;
  items: SectorLinkageHistoryItem[];
};

export type SectorLinkage = {
  active: boolean;
  forced?: boolean;
  window: string;
  generated_at?: string | null;
  trade_date?: string | null;
  trigger_threshold_pct?: number;
  sudden_window_minutes?: number;
  sudden_threshold_pct?: number;
  market_excess_threshold_pct?: number;
  amount_surge_ratio_threshold?: number;
  market_amount_ratio_threshold?: number;
  intraday_volume_intensity_threshold?: number;
  min_crowding_score?: number;
  min_candidate_score?: number;
  trigger_count: number;
  sector_count: number;
  groups: SectorLinkageGroup[];
  history?: SectorLinkageHistory;
  message: string;
};

export type WatchlistItem = {
  id: number;
  symbol: string;
  name: string;
  target_buy?: number | null;
  target_sell?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  strategy_code?: string | null;
  created_at: string;
};

export type AlertItem = {
  id: number;
  symbol: string;
  alert_type: string;
  message: string;
  status: string;
  triggered_at?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type Dashboard = {
  market_overview: {
    stocks: number;
    quote_symbols: number;
    up_symbols: number;
    down_symbols: number;
    total_amount: number;
    signal_count: number;
    avg_signal_score: number;
    latest_observed_at?: string | null;
    board_distribution?: Record<string, number>;
  };
  market_quotes: MarketQuote[];
  strategy_observations?: StrategyObservationGroup[];
  signals: StrategySignal[];
  alerts: AlertItem[];
  watchlist: WatchlistItem[];
  strategies: StrategyDefinition[];
  research: Array<{
    title: string;
    source: string;
    url?: string | null;
    summary: string;
    is_summary_complete?: boolean;
    credibility: number;
    tags: string[];
    published_at?: string | null;
    collected_at?: string | null;
  }>;
  backtests: BacktestRun[];
  data_quality: DataQualitySummary;
  market_rules: MarketRuleSummary;
  trade_samples: TradeSampleSummary;
  portfolio: PortfolioSummary;
  strategy_library: StrategyLibrarySummary;
  weekly_analysis: WeeklyAnalysis;
  sector_linkage: SectorLinkage;
  readiness: ReadinessSummary;
  guardrails: GuardrailSummary;
};

export type BacktestPoint = {
  date: string;
  value: number;
};

export type MonthlyReturn = {
  date: string;
  return_pct: number;
};

export type RegimeBreakdown = {
  regime: string;
  return_pct: number;
  win_rate_pct: number;
};

export type BacktestRun = {
  id?: number;
  strategy_code: string;
  strategy_name?: string;
  stock_pool: string[];
  start_date?: string;
  end_date?: string;
  metrics: Record<string, number | boolean | string | Record<string, unknown>>;
  assumptions: Record<string, number | boolean | string | string[] | Record<string, unknown>>;
  equity_curve?: BacktestPoint[];
  drawdown_curve?: BacktestPoint[];
  monthly_returns?: MonthlyReturn[];
  regime_breakdown?: RegimeBreakdown[];
  risk_flags?: string[];
  diagnostics?: Record<string, number | string | boolean | Record<string, unknown>>;
};

export type StrategyDefinition = {
  code: string;
  name: string;
  category: string;
  description: string;
  parameters: Record<string, string | number | boolean | string[]>;
  risk_rules: Record<string, string | number | boolean | string[]>;
};

export type StrategyLibraryEntry = {
  id: number;
  code: string;
  version: string;
  name: string;
  category: string;
  status: string;
  source: string;
  thesis: string;
  parameters: Record<string, unknown>;
  risk_rules: Record<string, unknown>;
  performance: Record<string, number | string | null>;
  learning_metrics: Record<string, number | string | null | Record<string, number>>;
  observation_metrics?: {
    total?: number;
    user_trade_count?: number;
    daily_top3_count?: number;
    observing_count?: number;
    realized_count?: number;
    avg_observed_return_pct?: number | null;
    positive_rate_pct?: number | null;
    applicability?: string;
    recent?: Array<{
      symbol: string;
      name: string;
      trade_date: string;
      source_type: string;
      current_return_pct?: number | null;
      score?: number | null;
      status: string;
    }>;
  };
  display_profile?: {
    market_fit: string;
    decision_use: string;
    risk_focus: string;
    applicability: string;
    evidence_label: string;
  };
  tags: string[];
  created_at: string;
  updated_at: string;
};

export type StrategyLibrarySummary = {
  total: number;
  status_counts: Record<string, number>;
  category_counts: Record<string, number>;
  entries: StrategyLibraryEntry[];
  comparison: Array<{
    code: string;
    name: string;
    status: string;
    annual_return_pct?: number | null;
    max_drawdown_pct?: number | null;
    sharpe?: number | null;
    sample_count?: number | null;
    avg_realized_return_pct?: number | null;
    observed_count?: number | null;
    avg_observed_return_pct?: number | null;
    applicability?: string | null;
  }>;
  observation_pool?: {
    total: number;
    by_strategy: Record<string, unknown>;
  };
};

export type StrategyPickItem = {
  symbol: string;
  name: string;
  strategy_code: string;
  action: string;
  score: number;
  confidence: number;
  reason: string;
  last_price: number;
  change_pct: number;
  amount: number;
  data_status: string;
  quote_source: string;
  quote_quality: string;
  observed_at?: string | null;
  generated_at: string;
  factors: Record<string, unknown>;
};

export type StrategyPickResponse = {
  selected_strategy_codes: string[];
  min_score: number;
  limit: number;
  generated_at?: string | null;
  universe_size: number;
  candidate_count: number;
  items: StrategyPickItem[];
  message: string;
};

export type ReadinessCheck = {
  key: string;
  label: string;
  status: "pass" | "warn" | "fail" | string;
  message: string;
  blocking: boolean;
};

export type ReadinessSummary = {
  score: number;
  status: string;
  passed: number;
  warnings: number;
  failed: number;
  blockers: string[];
  checks: ReadinessCheck[];
  recommendation: string;
  generated_at: string;
};

export type GuardrailSummary = {
  id?: number;
  status: string;
  mode: string;
  selected_source?: string | null;
  source_ok_count: number;
  source_fail_count: number;
  stale_symbol_count: number;
  max_deviation_pct: number;
  reasons: string[];
  payload?: Record<string, unknown>;
  observed_at?: string | null;
};

export type WatchlistCreate = {
  symbol: string;
  name?: string;
  target_buy?: number | null;
  target_sell?: number | null;
  stop_loss?: number | null;
  take_profit?: number | null;
  strategy_code?: string | null;
};

export type IntradayPoint = {
  date?: string;
  time: string;
  price: number;
  avg_price: number;
  volume: number;
};

export type DailyBar = {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  amount: number;
  turnover_rate: number;
};

export type StockDetail = {
  symbol: string;
  name: string;
  quote: MarketQuote & { source?: string };
  fundamentals: {
    industry: string;
    region?: string;
    concepts?: string[];
    market_cap: number | null;
    circulating_market_cap?: number | null;
    pe_ttm: number | null;
    pb: number | null;
    roe: number | null;
    turnover_rate: number | null;
    high_60d: number;
    low_60d: number;
    data_quality: string;
    data_source?: string;
    errors?: string[];
  };
  intraday: IntradayPoint[];
  daily_bars: DailyBar[];
  signals: StrategySignal[];
  risk_notes: string[];
  market_rules: MarketRuleEvaluation;
  data_quality: { level: string; message: string };
  stock_status: StockStatus;
};

export type DataQualitySummary = {
  window_hours: number;
  counts: Record<string, number>;
  latest_quote_quality: Record<string, number>;
  sources: Array<{
    code: string;
    name: string;
    category: string;
    priority: number;
    reliability: number;
    enabled: boolean;
    last_success_at?: string | null;
    last_error?: string | null;
    meta?: {
      role?: string;
      usage?: string;
    };
  }>;
  recent_issues: Array<{
    source_code: string;
    dataset: string;
    symbol?: string | null;
    level: string;
    message: string;
    observed_at: string;
  }>;
};

export type MarketRuleSummary = {
  trade_calendar_status: string;
  trade_date: string;
  is_trading_day: boolean;
  session_state: string;
  trade_time: boolean;
  near_limit_count: number;
  limit_up_count: number;
  limit_down_count: number;
  blocked_buy_count: number;
  blocked_sell_count: number;
  rule_notes: string[];
};

export type StockStatus = {
  symbol: string;
  name: string;
  board: string;
  is_st: boolean;
  is_suspended: boolean;
  is_new_stock: boolean;
  listing_date?: string | null;
  limit_up_down_pct?: number | null;
  source: string;
  updated_at: string;
};

export type MarketRuleEvaluation = {
  symbol: string;
  board: string;
  lot_size: number;
  price_tick: number;
  limit_up_down_pct?: number | null;
  t_plus_one: boolean;
  is_trading_day: boolean;
  is_trade_time: boolean;
  calendar?: Record<string, unknown> | null;
  near_limit: boolean;
  at_limit_up: boolean;
  at_limit_down: boolean;
  can_buy_hint: boolean;
  can_sell_hint: boolean;
  rounded_price: number;
  rounded_lot_quantity: number;
  notes: string[];
  warnings: string[];
};

export type TradeSample = {
  id: number;
  symbol: string;
  action: string;
  alert_id?: number | null;
  decision_price: number;
  quantity?: number | null;
  decision_at: string;
  strategy_code?: string | null;
  realized_return_pct?: number | null;
  status: string;
  notes?: string | null;
  features: Record<string, unknown>;
};

export type TradeSampleCreate = {
  symbol: string;
  action: "buy" | "sell" | "ignore" | "watch";
  decision_price: number;
  alert_id?: number | null;
  quantity?: number | null;
  strategy_code?: string | null;
  realized_return_pct?: number | null;
  status?: string;
  notes?: string | null;
  features?: Record<string, unknown>;
};

export type TradeSampleSummary = {
  total: number;
  counts: Record<string, number>;
  avg_realized_return_pct?: number | null;
  recent: TradeSample[];
};

export type PortfolioPosition = {
  id: number;
  symbol: string;
  name: string;
  strategy_code?: string | null;
  quantity: number;
  entry_price: number;
  entry_at: string;
  exit_price?: number | null;
  exit_at?: string | null;
  realized_return_pct?: number | null;
  status: string;
  entry_sample_id?: number | null;
  exit_sample_id?: number | null;
  current_price?: number | null;
  current_change_pct?: number | null;
  current_value?: number | null;
  floating_pnl?: number | null;
  floating_return_pct?: number | null;
  holding_days?: number | null;
  latest_quote_at?: string | null;
  quote_source?: string | null;
  meta: Record<string, unknown>;
};

export type StrategyTradeSummary = {
  strategy_code: string;
  trade_count: number;
  win_count: number;
  win_rate_pct?: number | null;
  avg_return_pct?: number | null;
  best_return_pct?: number | null;
  worst_return_pct?: number | null;
  total_realized_pnl?: number | null;
  avg_holding_days?: number | null;
  latest_exit_at?: string | null;
};

export type PortfolioSummary = {
  open_count: number;
  closed_count: number;
  avg_realized_return_pct?: number | null;
  total_floating_pnl?: number | null;
  total_market_value?: number | null;
  open_positions: PortfolioPosition[];
  recent_closed: PortfolioPosition[];
  trade_history: PortfolioPosition[];
  strategy_trade_summary: StrategyTradeSummary[];
};

export type WeeklyStrategyReview = {
  strategy_code: string;
  strategy_name: string;
  actual_return_pct?: number | null;
  actual_trade_count: number;
  win_rate_pct?: number | null;
  open_position_count: number;
  open_floating_return_pct?: number | null;
  backtest_return_pct?: number | null;
  backtest_max_drawdown_pct?: number | null;
  backtest_sample_points: number;
  benchmark_return_pct?: number | null;
  excess_vs_market_pct?: number | null;
  status: string;
  optimization_signal: "keep" | "review" | "watch" | string;
  diagnosis: string;
  suggestions: string[];
};

export type WeeklyAnalysis = {
  generated_at?: string | null;
  week_start?: string | null;
  week_end?: string | null;
  benchmark: {
    name: string;
    return_pct?: number | null;
    median_return_pct?: number | null;
    up_count: number;
    down_count: number;
    up_ratio_pct?: number | null;
    sample_count: number;
    total_amount?: number | null;
    amount_change_pct?: number | null;
    regime: string;
    interpretation: string;
  };
  strategy_reviews: WeeklyStrategyReview[];
  summary: {
    outperform_count: number;
    underperform_count: number;
    watch_count: number;
    overall_assessment: string;
    market_regime?: string | null;
  };
  methodology: Record<string, string>;
};

function apiBaseUrl(): string {
  if (typeof window === "undefined") {
    return process.env.INTERNAL_API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  }
  return process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
}

async function fetchJson<T>(url: string, init: RequestInit = {}, timeoutMs = 12000): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, { ...init, signal: controller.signal });
      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error(text || `API request failed with ${response.status}`);
      }
      return response.json();
    } catch (error) {
      lastError = error;
      if (attempt === 1) break;
      await new Promise((resolve) => setTimeout(resolve, 400));
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastError instanceof Error ? lastError : new Error("API request failed");
}

export async function getDashboard(): Promise<Dashboard> {
  const baseUrl = apiBaseUrl();
  return fetchJson<Dashboard>(`${baseUrl}/api/dashboard`, { cache: "no-store" }, 15000);
}

export async function bootstrapDemo(): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/bootstrap`, { method: "POST", cache: "no-store" });
}

export async function syncRealMarket(): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/market/real-readonly-sync`, { method: "POST", cache: "no-store" });
}

export async function syncActiveMarket(maxSymbols = 360): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/market/sync-active?max_symbols=${maxSymbols}`, { method: "POST", cache: "no-store" });
}

export async function syncAllAShares(): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/market/sync-all-a-shares?chunk_size=200`, { method: "POST", cache: "no-store" });
}

export async function generateSignals(limit = 6000): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/strategies/signals?limit=${limit}`, { method: "POST", cache: "no-store" });
}

export async function pickStrategyStocks(payload: {
  strategy_codes: string[];
  min_score?: number;
  limit?: number;
  require_real_daily_factor?: boolean;
}): Promise<StrategyPickResponse> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/strategies/pick-stocks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Strategy stock picker API unavailable");
  }
  return response.json();
}

export async function backfillActiveDaily(maxSymbols = 120): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/strategies/backfill-active-daily?max_symbols=${maxSymbols}`, { method: "POST", cache: "no-store" });
}

export async function getStockDetail(symbol: string): Promise<StockDetail> {
  const baseUrl = apiBaseUrl();
  return fetchJson<StockDetail>(`${baseUrl}/api/stocks/${encodeURIComponent(symbol)}/detail`, { cache: "no-store" }, 10000);
}

export async function addWatchlistItem(payload: WatchlistCreate): Promise<WatchlistItem> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "Watchlist API unavailable");
  }
  return response.json();
}

export async function updateWatchlistItem(id: number, payload: Omit<WatchlistCreate, "symbol">): Promise<WatchlistItem> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/watchlist/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Watchlist update API unavailable");
  }
  return response.json();
}

export async function deleteWatchlistItem(id: number): Promise<void> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/watchlist/${id}`, { method: "DELETE", cache: "no-store" });
  if (!response.ok) {
    throw new Error("Watchlist delete API unavailable");
  }
}

export async function evaluateAlerts(): Promise<void> {
  const baseUrl = apiBaseUrl();
  await fetch(`${baseUrl}/api/alerts/evaluate`, { method: "POST", cache: "no-store" });
}

export async function dismissAlert(id: number): Promise<void> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/alerts/${id}/dismiss`, { method: "POST", cache: "no-store" });
  if (!response.ok) {
    throw new Error("Alert dismiss API unavailable");
  }
}

export async function recordAlertDecision(id: number, action: "buy" | "sell" | "ignore" | "watch", notes?: string, quantity?: number): Promise<TradeSample> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/alerts/${id}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, notes, quantity }),
    cache: "no-store"
  });
  if (!response.ok) {
    throw new Error("Alert decision API unavailable");
  }
  return response.json();
}

export async function recordTradeSample(payload: TradeSampleCreate): Promise<{ id: number }> {
  const baseUrl = apiBaseUrl();
  const response = await fetch(`${baseUrl}/api/trades`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    cache: "no-store"
  });
  if (!response.ok) {
    const errorPayload = await response.json().catch(() => null);
    throw new Error(typeof errorPayload?.detail === "string" ? errorPayload.detail : "Trade sample API unavailable");
  }
  return response.json();
}
