import DashboardClient from "@/components/dashboard";
import { Dashboard, getDashboard } from "@/lib/api";

const emptyDashboard: Dashboard = {
  market_overview: {
    stocks: 0,
    quote_symbols: 0,
    up_symbols: 0,
    down_symbols: 0,
    total_amount: 0,
    signal_count: 0,
    avg_signal_score: 0,
    latest_observed_at: null,
    board_distribution: {}
  },
  market_quotes: [],
  signals: [],
  alerts: [],
  watchlist: [],
  strategies: [],
  research: [],
  backtests: [],
  data_quality: {
    window_hours: 24,
    counts: {},
    latest_quote_quality: {},
    sources: [],
    recent_issues: []
  },
  market_rules: {
    trade_calendar_status: "estimated",
    trade_date: "",
    is_trading_day: false,
    session_state: "unknown",
    trade_time: false,
    near_limit_count: 0,
    limit_up_count: 0,
    limit_down_count: 0,
    blocked_buy_count: 0,
    blocked_sell_count: 0,
    rule_notes: []
  },
  trade_samples: {
    total: 0,
    counts: {},
    avg_realized_return_pct: null,
    recent: []
  },
  portfolio: {
    open_count: 0,
    closed_count: 0,
    avg_realized_return_pct: null,
    total_floating_pnl: null,
    total_market_value: null,
    open_positions: [],
    recent_closed: [],
    trade_history: [],
    strategy_trade_summary: []
  },
  strategy_library: {
    total: 0,
    status_counts: {},
    category_counts: {},
    entries: [],
    comparison: []
  },
  weekly_analysis: {
    generated_at: null,
    week_start: null,
    week_end: null,
    benchmark: {
      name: "A股覆盖股票等权基准",
      return_pct: null,
      median_return_pct: null,
      up_count: 0,
      down_count: 0,
      up_ratio_pct: null,
      sample_count: 0,
      total_amount: null,
      amount_change_pct: null,
      regime: "数据不足",
      interpretation: "等待本周真实日 K 数据。"
    },
    strategy_reviews: [],
    summary: {
      outperform_count: 0,
      underperform_count: 0,
      watch_count: 0,
      overall_assessment: "等待完整交易周样本。",
      market_regime: "数据不足"
    },
    methodology: {}
  },
  sector_linkage: {
    active: false,
    window: "09:30-10:00",
    sudden_window_minutes: 1,
    sudden_threshold_pct: 1.2,
    market_excess_threshold_pct: 0.8,
    amount_surge_ratio_threshold: 1.8,
    market_amount_ratio_threshold: 2.5,
    intraday_volume_intensity_threshold: 2,
    generated_at: null,
    trade_date: null,
    trigger_count: 0,
    sector_count: 0,
    groups: [],
    history: {
      total_today: 0,
      positive_today: 0,
      avg_candidate_return_pct: null,
      items: []
    },
    message: "等待开盘前 30 分钟板块联动扫描。"
  },
  readiness: {
    score: 0,
    status: "not_ready",
    passed: 0,
    warnings: 0,
    failed: 0,
    blockers: [],
    checks: [],
    recommendation: "等待后端准备度检查。",
    generated_at: ""
  },
  guardrails: {
    status: "unknown",
    mode: "observe_only",
    selected_source: null,
    source_ok_count: 0,
    source_fail_count: 0,
    stale_symbol_count: 0,
    max_deviation_pct: 0,
    reasons: ["等待真实行情保护阈值评估。"],
    observed_at: null
  }
};

export default async function Home() {
  let dashboard = emptyDashboard;
  try {
    dashboard = await getDashboard();
  } catch {
    dashboard = emptyDashboard;
  }
  return <DashboardClient initial={dashboard} />;
}
