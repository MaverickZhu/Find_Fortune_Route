"use client";

import { Activity, BarChart3, Bell, BookOpenText, BrainCircuit, CandlestickChart, Check, LineChart, ListChecks, Pencil, Plus, RefreshCw, Settings2, ShieldAlert, Star, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { addWatchlistItem, backfillActiveDaily, DailyBar, Dashboard, deleteWatchlistItem, dismissAlert, evaluateAlerts, generateSignals, getDashboard, getStockDetail, IntradayPoint, MarketQuote, pickStrategyStocks, PortfolioPosition, recordAlertDecision, recordTradeSample, SectorLinkageHistoryItem, SectorLinkageTrigger, StockDetail, StrategyDefinition, StrategyPickItem, StrategyPickResponse, syncActiveMarket, syncAllAShares, syncRealMarket, updateWatchlistItem, WatchlistItem } from "@/lib/api";

const actionLabel: Record<string, string> = {
  buy: "买入观察",
  sell: "卖出观察",
  reduce: "减仓观察",
  hold: "继续持有",
  watch: "重点观察"
};

export default function DashboardClient({ initial }: { initial: Dashboard }) {
  const [data, setData] = useState(initial);
  const [loading, setLoading] = useState(false);
  const [watchSaving, setWatchSaving] = useState(false);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [stockDetail, setStockDetail] = useState<StockDetail | null>(null);
  const [selectedLinkageEvent, setSelectedLinkageEvent] = useState<SectorLinkageHistoryItem | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [activeStrategy, setActiveStrategy] = useState<string | null>(null);
  const [activeBacktest, setActiveBacktest] = useState<Dashboard["backtests"][number] | null>(null);
  const [backtestFilter, setBacktestFilter] = useState("all");
  const [selectedPickStrategies, setSelectedPickStrategies] = useState<string[]>(["multi_factor_alpha", "trend_breakout", "money_flow_anomaly"]);
  const [pickLoading, setPickLoading] = useState(false);
  const [pickResult, setPickResult] = useState<StrategyPickResponse | null>(null);
  const [readinessExpanded, setReadinessExpanded] = useState(false);
  const [editingWatchId, setEditingWatchId] = useState<number | null>(null);
  const [watchNotice, setWatchNotice] = useState<string | null>(null);
  const [watchForm, setWatchForm] = useState({
    symbol: "",
    target_buy: "",
    target_sell: "",
    stop_loss: "",
    take_profit: "",
    strategy_code: "multi_factor_alpha"
  });

  async function reloadDashboard(failureMessage = "数据刷新失败，已保留当前页面数据。") {
    try {
      setData(await getDashboard());
      return true;
    } catch {
      setWatchNotice(failureMessage);
      return false;
    }
  }

  async function refresh() {
    setLoading(true);
    try {
      if (data.market_rules?.trade_time) {
        await syncActiveMarket();
      } else {
        await syncRealMarket();
      }
      await backfillActiveDaily();
      await generateSignals();
      await reloadDashboard();
    } catch {
      setWatchNotice("刷新失败，可能是本地后端正在重启或行情源短暂不可用。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (initial.market_quotes.length === 0) {
      refresh();
    }
  }, []);

  useEffect(() => {
    if (!data.market_rules?.trade_time) return;
    const timer = window.setInterval(async () => {
      try {
        await syncActiveMarket();
        await reloadDashboard();
      } catch {
        // Keep the current dashboard visible if one live refresh fails.
      }
    }, 15000);
    return () => window.clearInterval(timer);
  }, [data.market_rules?.trade_time]);

  function strategyDisplayName(code?: string | null) {
    if (!code) return "未绑定策略";
    return data.strategies.find((strategy) => strategy.code === code)?.name ?? code;
  }

  function findTracked(symbol: string) {
    return data.watchlist.find((item) => item.symbol === symbol);
  }

  function notifyAlreadyTracked(symbol: string, requestedStrategy?: string | null) {
    const tracked = findTracked(symbol);
    if (!tracked) return false;
    const currentStrategy = strategyDisplayName(tracked.strategy_code);
    const requested = requestedStrategy && requestedStrategy !== tracked.strategy_code
      ? `；本次选择策略为 ${strategyDisplayName(requestedStrategy)}，可在自选追踪中编辑切换`
      : "";
    setWatchNotice(`${symbol} 已在自选追踪中，当前绑定 ${currentStrategy}${requested}。`);
    return true;
  }

  async function openStock(symbol: string, linkageEvent: SectorLinkageHistoryItem | null = null) {
    setSelectedSymbol(symbol);
    setSelectedLinkageEvent(linkageEvent);
    setDetailLoading(true);
    setDetailError(null);
    setStockDetail(null);
    try {
      setStockDetail(await getStockDetail(symbol));
    } catch {
      setDetailError("个股详情加载失败，可能是本地后端重启或外部行情源短暂不可用，请稍后重试。");
    } finally {
      setDetailLoading(false);
    }
  }

  function closeStock() {
    setSelectedSymbol(null);
    setStockDetail(null);
    setDetailError(null);
    setSelectedLinkageEvent(null);
  }

  async function saveWatchlist(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const symbol = watchForm.symbol.trim();
    if (!symbol) return;
    if (!editingWatchId && notifyAlreadyTracked(symbol, watchForm.strategy_code)) return;
    const quote = data.market_quotes.find((item) => item.symbol === symbol);
    setWatchSaving(true);
    try {
      const payload = {
        name: quote?.name ?? "",
        strategy_code: watchForm.strategy_code,
        target_buy: numberOrNull(watchForm.target_buy),
        target_sell: numberOrNull(watchForm.target_sell),
        stop_loss: numberOrNull(watchForm.stop_loss),
        take_profit: numberOrNull(watchForm.take_profit)
      };
      if (editingWatchId) {
        await updateWatchlistItem(editingWatchId, payload);
      } else {
        await addWatchlistItem({ symbol, ...payload });
      }
      await evaluateAlerts();
      await reloadDashboard();
      setWatchNotice(null);
      setEditingWatchId(null);
      setWatchForm((current) => ({ ...current, symbol: "", target_buy: "", target_sell: "", stop_loss: "", take_profit: "" }));
    } catch (error) {
      setWatchNotice(error instanceof Error ? error.message : "添加追踪失败，请稍后重试。");
    } finally {
      setWatchSaving(false);
    }
  }

  function editWatchlistItem(item: WatchlistItem) {
    setEditingWatchId(item.id);
    setWatchForm({
      symbol: item.symbol,
      strategy_code: item.strategy_code ?? "multi_factor_alpha",
      target_buy: item.target_buy?.toString() ?? "",
      target_sell: item.target_sell?.toString() ?? "",
      stop_loss: item.stop_loss?.toString() ?? "",
      take_profit: item.take_profit?.toString() ?? ""
    });
  }

  function resetWatchForm() {
    setEditingWatchId(null);
    setWatchNotice(null);
    setWatchForm({ symbol: "", target_buy: "", target_sell: "", stop_loss: "", take_profit: "", strategy_code: "multi_factor_alpha" });
  }

  async function removeWatchlistItem(id: number) {
    try {
      await deleteWatchlistItem(id);
      await reloadDashboard();
      if (editingWatchId === id) resetWatchForm();
    } catch (error) {
      setWatchNotice(error instanceof Error ? error.message : "删除追踪失败，请稍后重试。");
    }
  }

  async function acknowledgeAlert(id: number) {
    try {
      await dismissAlert(id);
      await reloadDashboard();
    } catch {
      setWatchNotice("提醒处理失败，请稍后重试。");
    }
  }

  async function decideAlert(id: number, action: "buy" | "sell" | "ignore" | "watch") {
    try {
      await recordAlertDecision(id, action, undefined, action === "buy" || action === "sell" ? 100 : undefined);
      await reloadDashboard();
    } catch {
      setWatchNotice("决策记录失败，请稍后重试。");
    }
  }

  function useSignalAsWatch(signal: Dashboard["signals"][number]) {
    if (notifyAlreadyTracked(signal.symbol, signal.strategy_code)) return;
    const quote = data.market_quotes.find((item) => item.symbol === signal.symbol);
    const price = quote?.last_price ?? Number(signal.evidence.last_price ?? 0);
    setWatchForm({
      symbol: signal.symbol,
      strategy_code: signal.strategy_code,
      target_buy: price ? (price * 0.985).toFixed(2) : "",
      target_sell: price ? (price * 1.06).toFixed(2) : "",
      stop_loss: price ? (price * 0.94).toFixed(2) : "",
      take_profit: price ? (price * 1.12).toFixed(2) : ""
    });
    setWatchNotice(null);
    setEditingWatchId(null);
  }

  function usePickAsWatch(item: StrategyPickItem) {
    if (notifyAlreadyTracked(item.symbol, item.strategy_code)) return;
    const price = item.last_price;
    setWatchForm({
      symbol: item.symbol,
      strategy_code: item.strategy_code,
      target_buy: price ? (price * 0.985).toFixed(2) : "",
      target_sell: price ? (price * 1.06).toFixed(2) : "",
      stop_loss: price ? (price * 0.94).toFixed(2) : "",
      take_profit: price ? (price * 1.12).toFixed(2) : ""
    });
    setWatchNotice(null);
    setEditingWatchId(null);
  }

  async function addDetailToWatch(detail: StockDetail) {
    const price = detail.quote.last_price;
    const strategyCode = detail.signals[0]?.strategy_code ?? "multi_factor_alpha";
    if (notifyAlreadyTracked(detail.symbol, strategyCode)) return;
    try {
      await addWatchlistItem({
        symbol: detail.symbol,
        name: detail.name || detail.quote.name || detail.symbol,
        strategy_code: strategyCode,
        target_buy: price ? Number((price * 0.985).toFixed(2)) : null,
        target_sell: price ? Number((price * 1.06).toFixed(2)) : null,
        stop_loss: price ? Number((price * 0.94).toFixed(2)) : null,
        take_profit: price ? Number((price * 1.12).toFixed(2)) : null
      });
      await evaluateAlerts();
      await reloadDashboard();
      setWatchNotice(null);
    } catch (error) {
      setWatchNotice(error instanceof Error ? error.message : "添加追踪失败，请稍后重试。");
    }
  }

  async function manualSellPosition(
    position: PortfolioPosition,
    payload: { price: number; quantity: number; notes?: string }
  ) {
    await recordTradeSample({
      symbol: position.symbol,
      action: "sell",
      decision_price: payload.price,
      quantity: payload.quantity,
      strategy_code: position.strategy_code,
      notes: payload.notes ?? "用户在持仓详情中主动确认卖出。",
      features: {
        source: "manual_position_detail",
        position_id: position.id,
        entry_price: position.entry_price,
        entry_at: position.entry_at,
        current_price: position.current_price,
        floating_return_pct: position.floating_return_pct
      }
    });
    await reloadDashboard();
  }

  function togglePickStrategy(code: string) {
    setSelectedPickStrategies((current) =>
      current.includes(code) ? current.filter((item) => item !== code) : [...current, code]
    );
  }

  async function runStrategyPicker() {
    if (selectedPickStrategies.length === 0) return;
    setPickLoading(true);
    try {
      if (data.market_rules?.trade_time) {
        await syncAllAShares();
      }
      await generateSignals();
      const result = await pickStrategyStocks({
        strategy_codes: selectedPickStrategies,
        min_score: 65,
        limit: selectedPickStrategies.includes("institutional_crowding") ? 10 : 5,
        require_real_daily_factor: false
      });
      setPickResult(result);
      await reloadDashboard();
    } catch {
      setWatchNotice("策略选股刷新失败，请稍后重试。");
    } finally {
      setPickLoading(false);
    }
  }

  const stats = useMemo(() => {
    const quotes = data.market_quotes;
    const overview = data.market_overview;
    const up = overview?.up_symbols ?? quotes.filter((item) => item.change_pct > 0).length;
    const totalAmount = overview?.total_amount ?? quotes.reduce((sum, item) => sum + item.amount, 0);
    const avgScore = overview?.avg_signal_score ?? (data.signals.length
      ? data.signals.reduce((sum, item) => sum + item.score, 0) / data.signals.length
      : 0);
    const total = overview?.quote_symbols ?? quotes.length;
    const signalCount = overview?.signal_count ?? data.signals.length;
    return { up, total, totalAmount, avgScore, signalCount };
  }, [data]);
  const strategies = data.strategies ?? [];
  const dataQuality = data.data_quality ?? {
    window_hours: 24,
    counts: {},
    latest_quote_quality: {},
    sources: [],
    recent_issues: []
  };
  const marketRules = data.market_rules ?? {
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
  };
  const tradeSamples = data.trade_samples ?? {
    total: 0,
    counts: {},
    avg_realized_return_pct: null,
    recent: []
  };
  const portfolio = data.portfolio ?? {
    open_count: 0,
    closed_count: 0,
    avg_realized_return_pct: null,
    total_floating_pnl: null,
    total_market_value: null,
    open_positions: [],
    recent_closed: [],
    trade_history: [],
    strategy_trade_summary: []
  };
  const strategyLibrary = data.strategy_library ?? {
    total: 0,
    status_counts: {},
    category_counts: {},
    entries: [],
    comparison: []
  };
  const weeklyAnalysis = data.weekly_analysis ?? {
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
  };
  const readiness = data.readiness ?? {
    score: 0,
    status: "not_ready",
    passed: 0,
    warnings: 0,
    failed: 0,
    blockers: [],
    checks: [],
    recommendation: "等待后端准备度检查。",
    generated_at: ""
  };
  const guardrails = data.guardrails ?? {
    status: "unknown",
    mode: "observe_only",
    selected_source: null,
    source_ok_count: 0,
    source_fail_count: 0,
    stale_symbol_count: 0,
    max_deviation_pct: 0,
    reasons: ["等待真实行情保护阈值评估。"],
    observed_at: null
  };
  const filteredBacktests = data.backtests.filter((run) => backtestFilter === "all" || run.strategy_code === backtestFilter);
  const pickableStrategies = strategies.filter((strategy) => strategy.code !== "close_daily_multi_factor");
  const strategyObservationGroups = data.strategy_observations ?? [];
  const strategyObservationCount = strategyObservationGroups.reduce((sum, group) => sum + group.items.length, 0);
  const activeStrategyObservationCount = strategyObservationGroups.filter((group) => group.items.length > 0).length;
  const sectorLinkage = data.sector_linkage ?? {
    active: false,
    window: "09:30-10:00",
    trigger_count: 0,
    sector_count: 0,
    groups: [],
    history: {
      total_today: 0,
      positive_today: 0,
      avg_candidate_return_pct: null,
      items: []
    },
    sudden_window_minutes: 1,
    sudden_threshold_pct: 1.2,
    market_excess_threshold_pct: 0.8,
    amount_surge_ratio_threshold: 1.8,
    market_amount_ratio_threshold: 2.5,
    intraday_volume_intensity_threshold: 2,
    message: "等待开盘前 30 分钟板块联动扫描。",
  };
  const researchItems = useMemo(() => dedupeResearchItems(data.research), [data.research]);
  const selectedPositionSymbol = stockDetail?.symbol ?? selectedSymbol;
  const selectedOpenPosition = selectedPositionSymbol
    ? portfolio.open_positions.find((item) => item.symbol === selectedPositionSymbol) ?? null
    : null;

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">A 股策略研究与辅助决策</p>
          <h1>Find Fortune Route</h1>
        </div>
        <div className="topbarActions">
          <span className={marketRules.trade_time ? "sessionPill live" : "sessionPill"}>{marketRules.trade_time ? "交易中" : marketRules.session_state}</span>
          <span className="sessionPill">主源 {guardrails.selected_source ?? "-"}</span>
          <button className="iconButton" onClick={refresh} disabled={loading} title="刷新采集与信号">
            <RefreshCw size={18} className={loading ? "spin" : ""} />
            <span>刷新</span>
          </button>
        </div>
      </header>

      <section className="metricGrid">
        <Metric icon={<CandlestickChart />} label="监测标的" value={`${stats.total}`} sub={`上涨 ${stats.up}`} />
        <Metric icon={<Activity />} label="成交额样本" value={`${(stats.totalAmount / 100000000).toFixed(1)} 亿`} sub="最近行情批次" />
        <Metric icon={<BrainCircuit />} label="策略平均分" value={stats.avgScore.toFixed(1)} sub={`信号 ${stats.signalCount}`} />
        <Metric icon={<ShieldAlert />} label="数据/规则风险" value={`${(dataQuality.latest_quote_quality.degraded ?? 0) + marketRules.near_limit_count}`} sub={`提醒 ${data.alerts.length}`} />
      </section>

      <section className="opsBand">
        <section className="opsPanel">
          <div className="panelTitle">
            <ShieldAlert />
            <h2>数据质量</h2>
          </div>
          <div className="opsStats">
            <SummaryCell label="真实/通过" value={`${dataQuality.latest_quote_quality.ok ?? 0}`} />
            <SummaryCell label="降级数据" value={`${dataQuality.latest_quote_quality.degraded ?? 0}`} tone={(dataQuality.latest_quote_quality.degraded ?? 0) > 0 ? "negative" : undefined} />
            <SummaryCell label="异常数据" value={`${dataQuality.latest_quote_quality.invalid ?? 0}`} tone={(dataQuality.latest_quote_quality.invalid ?? 0) > 0 ? "negative" : undefined} />
          </div>
          <div className="sourceList">
            {dataQuality.sources.slice(0, 4).map((source) => (
              <div key={source.code}>
                <strong>{source.name}{source.meta?.role ? ` · ${sourceRoleLabel(source.meta.role)}` : ""}</strong>
                <span>可靠度 {(source.reliability * 100).toFixed(0)}% · {source.meta?.usage ?? source.category}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="opsPanel">
          <div className="panelTitle">
            <CandlestickChart />
            <h2>A 股交易规则</h2>
          </div>
          <div className="opsStats">
            <SummaryCell label="交易日" value={marketRules.is_trading_day ? "是" : "否"} />
            <SummaryCell label="交易时段" value={marketRules.trade_time ? "是" : marketRules.session_state} />
            <SummaryCell label="近涨跌停" value={`${marketRules.near_limit_count}`} tone={marketRules.near_limit_count > 0 ? "negative" : undefined} />
          </div>
          <ul className="compactList">
            {marketRules.rule_notes.slice(0, 3).map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      </section>

      <section className="readinessBand">
        <div className="readinessHead">
          <div>
            <h2>真实数据接入准备度</h2>
            <p>{readiness.recommendation}</p>
          </div>
          <div className="readinessActions">
            <div className={readiness.status === "ready" ? "readinessScore ready" : "readinessScore"}>
              <strong>{readiness.score}</strong>
              <span>{readiness.status === "ready" ? "可联调" : "未就绪"}</span>
            </div>
            <button className="secondaryButton compactButton" type="button" onClick={() => setReadinessExpanded((value) => !value)}>
              <span>{readinessExpanded ? "收起检查" : "展开检查"}</span>
            </button>
          </div>
        </div>
        <div className="readinessStats">
          <SummaryCell label="通过" value={`${readiness.passed}`} />
          <SummaryCell label="警告" value={`${readiness.warnings}`} />
          <SummaryCell label="失败" value={`${readiness.failed}`} tone={readiness.failed > 0 ? "negative" : undefined} />
          <SummaryCell label="阻塞项" value={`${readiness.blockers.length}`} tone={readiness.blockers.length > 0 ? "negative" : undefined} />
        </div>
        {readinessExpanded && (
          <div className="readinessChecks">
            {readiness.checks.map((check) => (
              <article className={`readinessCheck ${check.status}`} key={check.key}>
                <strong>{check.label}</strong>
                <span>{readinessStatusLabel(check.status)}{check.blocking ? " · 阻塞" : ""}</span>
                <p>{check.message}</p>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className={guardrails.status === "healthy" ? "guardrailBand healthy" : "guardrailBand"}>
        <div>
          <h2>真实行情保护阈值</h2>
          <p>{guardrails.reasons.length ? guardrails.reasons.join("；") : "行情源与跨源一致性正常，策略信号可按常规口径生成。"}</p>
        </div>
        <div className="guardrailStats">
          <SummaryCell label="状态" value={guardrailStatusLabel(guardrails.status)} tone={guardrails.status === "healthy" ? undefined : "negative"} />
          <SummaryCell label="模式" value={guardrails.mode} />
          <SummaryCell label="主行情源" value={guardrails.selected_source ?? "-"} />
          <SummaryCell label="可用/失败源" value={`${guardrails.source_ok_count}/${guardrails.source_fail_count}`} />
          <SummaryCell label="一致性风险" value={`${guardrails.stale_symbol_count}`} tone={guardrails.stale_symbol_count > 0 ? "negative" : undefined} />
          <SummaryCell label="最大价差" value={`${guardrails.max_deviation_pct}%`} />
        </div>
      </section>

      <section className="workspace">
        <Panel title="实时观测" icon={<LineChart />} className="observationPanel">
          <div className="observationBrief">
            <div>
              <strong>策略实时观测池</strong>
              <span>按每个策略的核心因子筛选当前最匹配股票，每个策略最多展示前 10 只，不再以成交额榜作为主入口。</span>
            </div>
            <mark>{activeStrategyObservationCount} 个策略有候选 · 共 {strategyObservationCount} 只</mark>
          </div>
          <div className="marketSnapshot">
            <SummaryCell label="上涨/下跌" value={`${data.market_overview.up_symbols}/${data.market_overview.down_symbols}`} />
            <SummaryCell label="覆盖股票" value={`${data.market_overview.quote_symbols}`} />
            <SummaryCell label="最新批次" value={data.market_overview.latest_observed_at ? formatShortDateTime(data.market_overview.latest_observed_at) : "-"} />
            <SummaryCell label="策略候选" value={`${strategyObservationCount}`} />
          </div>
          <div className="miniBoardGrid">
            {Object.entries(data.market_overview.board_distribution ?? {}).slice(0, 5).map(([board, count]) => (
              <span key={board}>{board} {count}</span>
            ))}
          </div>
          <div className="sectorLinkageBox">
            <div className="sectorLinkageHead">
              <div>
                <strong>抱团股板块联动提示</strong>
                <span>{sectorLinkage.message}</span>
              </div>
              <mark>
                {sectorLinkage.window} · {sectorLinkage.sudden_window_minutes ?? 1}分钟 {formatOptionalPct(sectorLinkage.sudden_threshold_pct)}
                {" "} / 超额 {formatOptionalPct(sectorLinkage.market_excess_threshold_pct)}
                {" "} / 相对放量 {formatRatio(sectorLinkage.amount_surge_ratio_threshold)}
                {" "} / 分时强度 {formatRatio(sectorLinkage.intraday_volume_intensity_threshold)} · 触发 {sectorLinkage.trigger_count}
              </mark>
            </div>
            {sectorLinkage.groups.length > 0 && (
              <div className="sectorLinkageGrid">
                {sectorLinkage.groups.slice(0, 10).map((group) => (
                  <article className="sectorLinkageCard" key={group.sector}>
                    <div className="sectorLinkageTitle">
                      <div>
                        <strong>{group.sector}</strong>
                        <span>{group.direction === "up" ? "抱团股上行触发" : "抱团股下行触发"} · {(group.trigger_types ?? []).map(triggerTypeLabel).join("、") || group.sector_type}</span>
                      </div>
                      <b>{group.items.length}/{Math.min(10, group.candidate_count)}</b>
                    </div>
                    <div className="sectorTriggerRow">
                      {group.trigger_symbols.slice(0, 3).map((trigger) => (
                        <span key={trigger.symbol} className={trigger.change_pct >= 0 ? "positive" : "negative"}>
                          {trigger.name || trigger.symbol} {triggerTypeLabel(trigger.trigger_type)} {formatOptionalPct(trigger.trigger_move_pct ?? trigger.change_pct)}
                          {trigger.volume_confirmed ? ` · ${formatVolumeEvidence(trigger)}` : ""}
                          {" "}· 抱团 {trigger.crowding_score.toFixed(1)}
                        </span>
                      ))}
                    </div>
                    <div className="sectorCandidateMiniList">
                      {group.items.slice(0, 10).map((item, index) => (
                        <button key={`${group.sector}-${item.symbol}`} type="button" onClick={() => openStock(item.symbol)}>
                          <span>{index + 1}. {item.name || item.symbol}</span>
                          <small>{item.strategy_code} · {actionLabel[item.action] ?? item.action}</small>
                          <b className={item.change_pct >= 0 ? "positive" : "negative"}>{item.change_pct.toFixed(2)}%</b>
                        </button>
                      ))}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </div>
          <div className="sectorHistoryBox">
            <div className="sectorHistoryHead">
              <div>
                <strong>板块联动历史验证</strong>
                <span>保存当日触发记录，并用候选股后续走势验证提示质量。</span>
              </div>
              <mark>今日 {sectorLinkage.history?.total_today ?? 0} 次 · 正向 {sectorLinkage.history?.positive_today ?? 0} 次 · 均值 {formatOptionalPct(sectorLinkage.history?.avg_candidate_return_pct)}</mark>
            </div>
            {(sectorLinkage.history?.items ?? []).length > 0 ? (
              <div className="sectorHistoryList">
                {(sectorLinkage.history?.items ?? []).slice(0, 6).map((event) => (
                  <article key={event.id} className="sectorHistoryRow">
                    <button type="button" onClick={() => openStock(event.symbol, event)} className="sectorHistoryTrigger">
                      <strong>{event.sector}</strong>
                      <span>{event.name || event.symbol} · {triggerTypeLabel(event.trigger_type)} · {formatShortDateTime(event.triggered_at)}</span>
                    </button>
                    <div className="sectorHistoryCandidates">
                      {candidateReturnItems(event).slice(0, 4).map((candidate) => (
                        <button key={`${event.id}-${String(candidate.symbol)}`} type="button" onClick={() => openStock(String(candidate.symbol || event.symbol), event)}>
                          <span>{String(candidate.name || candidate.symbol || "-")}</span>
                          <b className={returnTone(metricNumber(candidate.return_pct, 0))}>{formatOptionalPct(candidate.return_pct)}</b>
                        </button>
                      ))}
                      {candidateReturnItems(event).length === 0 && (
                        <span>候选股走势等待后续行情验证</span>
                      )}
                    </div>
                    <div className="sectorHistoryMetrics">
                      <span className={returnTone(metricNumber(event.followup_metrics?.avg_candidate_return_pct, 0))}>
                        候选 {formatOptionalPct(event.followup_metrics?.avg_candidate_return_pct)}
                      </span>
                      <small>正向 {formatOptionalPct(event.followup_metrics?.positive_candidate_rate_pct)} · 已测 {event.followup_metrics?.measured_candidate_count ?? 0} 只</small>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="strategyObservationEmpty">暂无已保存的板块联动触发记录。</div>
            )}
          </div>
          <div className="strategyObservationGrid">
            {strategyObservationGroups.length === 0 && (
              <div className="strategyObservationEmpty">策略候选池暂未生成，请刷新行情与策略信号后查看。</div>
            )}
            {strategyObservationGroups.map((group) => (
              <article className="strategyObservationCard" key={group.strategy_code}>
                <div className="strategyObservationHead">
                  <div>
                    <strong>{group.strategy_name}</strong>
                    <span>{group.description}</span>
                  </div>
                  <mark>{group.items.length}/{Math.min(10, Math.max(group.candidate_count, group.items.length))}</mark>
                </div>
                <div className="strategyObservationMeta">
                  <span>阈值 {group.min_score}</span>
                  <span>候选 {group.candidate_count}</span>
                  <span>{group.generated_at ? formatShortDateTime(group.generated_at) : "待生成"}</span>
                </div>
                {group.items.length > 0 ? (
                  <div className="strategyCandidateList">
                    {group.items.map((item) => (
                      <button className="strategyCandidateRow" key={`${group.strategy_code}-${item.symbol}`} onClick={() => openStock(item.symbol)} type="button">
                        <div className="quoteIdentity">
                          <span className="quoteRank">{item.rank}</span>
                          <span className={item.quote_quality === "ok" ? "statusDot ok" : "statusDot warn"} />
                          <div>
                            <strong>{item.name || item.symbol}</strong>
                            <span>{item.symbol} · {boardForSymbol(item.symbol)} · {actionLabel[item.action] ?? item.action}</span>
                            <small>{item.reason}</small>
                          </div>
                        </div>
                        <div className="numberBlock">
                          <b>{item.last_price.toFixed(2)}</b>
                          <span className={item.change_pct >= 0 ? "positive" : "negative"}>{item.change_pct.toFixed(2)}%</span>
                          <small>分数 {item.score.toFixed(1)}</small>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="strategyObservationEmpty">{group.message}</div>
                )}
              </article>
            ))}
          </div>
        </Panel>

        <Panel title="策略推荐" icon={<BrainCircuit />}>
          <div className="signalList">
            {data.signals.slice(0, 8).map((signal) => (
              <article className="signalItem" key={signal.id}>
                <div className="signalHead">
                  <span>{signal.symbol}</span>
                  <mark>{actionLabel[signal.action] ?? signal.action}</mark>
                </div>
                <div className="scoreBar">
                  <span style={{ width: `${Math.min(100, signal.score)}%` }} />
                </div>
                <p>{signal.reason}</p>
                <small>{signal.strategy_code} · {signalDataStatus(signal)} · 置信度 {(signal.confidence * 100).toFixed(0)}%</small>
                <div className="actionRow">
                  <button onClick={() => setActiveStrategy(signal.strategy_code)} title="查看策略参数">
                    <Settings2 size={15} />
                    <span>策略</span>
                  </button>
                  <button onClick={() => useSignalAsWatch(signal)} title="填入自选追踪">
                    <Star size={15} />
                    <span>追踪</span>
                  </button>
                </div>
              </article>
            ))}
          </div>
        </Panel>

        <Panel title="自选股与提醒" icon={<Star />}>
          <div className="taskQueueHead">
            <SummaryCell label="自选追踪" value={`${data.watchlist.length}`} />
            <SummaryCell label="待确认提醒" value={`${data.alerts.length}`} tone={data.alerts.length > 0 ? "negative" : undefined} />
          </div>
          {watchNotice && (
            <div className="inlineNotice">
              <span>{watchNotice}</span>
              <button type="button" onClick={() => setWatchNotice(null)} title="关闭提示">
                <X size={14} />
              </button>
            </div>
          )}
          <form className="watchForm" onSubmit={saveWatchlist}>
            <div className="formGrid">
              <label>
                <span>股票代码</span>
                <input value={watchForm.symbol} onChange={(event) => setWatchForm({ ...watchForm, symbol: event.target.value })} placeholder="000001" minLength={6} maxLength={16} disabled={editingWatchId !== null} />
              </label>
              <label>
                <span>策略</span>
                <select value={watchForm.strategy_code} onChange={(event) => setWatchForm({ ...watchForm, strategy_code: event.target.value })}>
                  {strategies.map((strategy) => (
                    <option value={strategy.code} key={strategy.code}>{strategy.name}</option>
                  ))}
                </select>
              </label>
              <label>
                <span>买入观察</span>
                <input value={watchForm.target_buy} onChange={(event) => setWatchForm({ ...watchForm, target_buy: event.target.value })} inputMode="decimal" placeholder="价格" />
              </label>
              <label>
                <span>卖出观察</span>
                <input value={watchForm.target_sell} onChange={(event) => setWatchForm({ ...watchForm, target_sell: event.target.value })} inputMode="decimal" placeholder="价格" />
              </label>
              <label>
                <span>止损</span>
                <input value={watchForm.stop_loss} onChange={(event) => setWatchForm({ ...watchForm, stop_loss: event.target.value })} inputMode="decimal" placeholder="价格" />
              </label>
              <label>
                <span>止盈</span>
                <input value={watchForm.take_profit} onChange={(event) => setWatchForm({ ...watchForm, take_profit: event.target.value })} inputMode="decimal" placeholder="价格" />
              </label>
            </div>
            <button className="primaryButton" disabled={watchSaving || !watchForm.symbol.trim()}>
              <Plus size={16} />
              <span>{watchSaving ? "保存中" : editingWatchId ? "保存修改" : "添加追踪"}</span>
            </button>
            {editingWatchId && (
              <button className="secondaryButton" type="button" onClick={resetWatchForm}>
                <X size={16} />
                <span>取消编辑</span>
              </button>
            )}
          </form>
          <div className="taskSection">
            <div className="taskSectionHead">
              <strong>自选追踪</strong>
              <span>{data.watchlist.length} 项</span>
            </div>
          {data.watchlist.length === 0 ? (
            <div className="empty">尚未添加自选股。可从策略信号点击追踪，也可以手动输入观察价。</div>
          ) : (
            data.watchlist.map((item) => (
              <div className="watchItem" key={item.id}>
                <button className="watchRow watchAction" onClick={() => openStock(item.symbol)}>
                  <div>
                    <strong>{item.name || item.symbol}</strong>
                    <span>{item.strategy_code ?? "未绑定策略"}</span>
                  </div>
                  <span>买 {item.target_buy ?? "-"} / 卖 {item.target_sell ?? "-"}</span>
                </button>
                <div className="miniActionRow">
                  <button onClick={() => editWatchlistItem(item)} title="编辑追踪参数">
                    <Pencil size={14} />
                    <span>编辑</span>
                  </button>
                  <button onClick={() => removeWatchlistItem(item.id)} title="删除追踪">
                    <Trash2 size={14} />
                    <span>删除</span>
                  </button>
                </div>
              </div>
            ))
          )}
          </div>
          <div className="taskSection">
            <div className="taskSectionHead">
              <strong>待确认决策</strong>
              <span>{data.alerts.length} 项</span>
            </div>
            <div className="alertList">
              {data.alerts.length === 0 ? (
                <div className="empty">暂无触发提醒。系统会持续检查价格、策略和风险事件。</div>
              ) : data.alerts.map((alert) => (
                <div className="alertItem" key={alert.id}>
                  <Bell size={16} />
                  <span>{alert.message}</span>
                  <button onClick={() => acknowledgeAlert(alert.id)} title="确认提醒">
                    <Check size={14} />
                    <span>确认</span>
                  </button>
                  <div className="decisionRow">
                    <button onClick={() => decideAlert(alert.id, "buy")}>买入</button>
                    <button onClick={() => decideAlert(alert.id, "sell")}>卖出</button>
                    <button onClick={() => decideAlert(alert.id, "ignore")}>忽略</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </Panel>

        <Panel title="市场研究" icon={<BookOpenText />}>
          {researchItems.length === 0 ? (
            <div className="empty">暂无研究条目。可通过研究采集任务补充财经新闻、研报和策略知识。</div>
          ) : researchItems.map((item, index) => (
            <article className="researchItem" key={`${item.source}-${item.title}-${index}`}>
              <div className="researchHead">
                <div className="researchTitleWrap">
                  <h3 tabIndex={0}>{item.title}</h3>
                  <div className="researchPopover" role="tooltip">
                    <strong>{item.title}</strong>
                    <p>{researchDetailText(item)}</p>
                    <div className="researchPopoverMeta">
                      <span>{item.source}</span>
                      <span>可信度 {(item.credibility * 100).toFixed(0)}%</span>
                      <span>{item.published_at ? formatShortDateTime(item.published_at) : "发布时间待识别"}</span>
                    </div>
                    {item.url && <a href={item.url} target="_blank" rel="noreferrer">查看原文</a>}
                  </div>
                </div>
                <mark>{item.source}</mark>
              </div>
              <p>{compactResearchSummary(item)}</p>
              <div className="researchMeta">
                <span>可信度 {(item.credibility * 100).toFixed(0)}%</span>
                <span>{item.is_summary_complete ? "摘要完整" : "标题快讯"}</span>
                <span>{item.tags.length} 个标签</span>
              </div>
              <div className="tagRow">
                {item.tags.map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </div>
            </article>
          ))}
        </Panel>
      </section>

      <section className="learningBand">
        <div>
          <h2>用户决策样本</h2>
          <p>提醒后的买入、卖出、忽略会沉淀为策略学习样本。</p>
          <div className="learningFlow">
            <span>研究</span>
            <span>信号</span>
            <span>决策</span>
            <span>收益</span>
            <span>学习</span>
          </div>
        </div>
        <div className="learningStats">
          <SummaryCell label="样本总数" value={`${tradeSamples.total}`} />
          <SummaryCell label="买入/卖出" value={`${tradeSamples.counts.buy ?? 0}/${tradeSamples.counts.sell ?? 0}`} />
          <SummaryCell label="忽略/观察" value={`${tradeSamples.counts.ignore ?? 0}/${tradeSamples.counts.watch ?? 0}`} />
          <SummaryCell label="平均实际收益" value={tradeSamples.avg_realized_return_pct == null ? "-" : `${tradeSamples.avg_realized_return_pct}%`} />
        </div>
        <div className="samplePanel">
          <div className="samplePanelHead">
            <h3>最近样本</h3>
            <span>{tradeSamples.recent.length} 条近期记录</span>
          </div>
          {tradeSamples.recent.length === 0 ? (
            <div className="empty">暂无决策样本。触发提醒后可记录买入、卖出或忽略。</div>
          ) : (
            <div className="sampleList">
              {tradeSamples.recent.map((sample) => (
                <article className="sampleItem" key={sample.id}>
                  <div>
                    <strong>{sample.symbol}</strong>
                    <span>{formatShortDateTime(sample.decision_at)}</span>
                  </div>
                  <mark className={`decisionMark ${sample.action}`}>{decisionLabel(sample.action)}</mark>
                  <span>{sample.decision_price.toFixed(2)} · {sample.strategy_code ?? "未绑定策略"}</span>
                </article>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="portfolioBand">
        <div>
          <h2>虚拟持仓与收益跟踪</h2>
          <p>买入决策会建立虚拟持仓，卖出决策会匹配持仓并计算收益率。</p>
        </div>
        <div className="learningStats">
          <SummaryCell label="当前持仓" value={`${portfolio.open_count}`} />
          <SummaryCell label="已完成交易" value={`${portfolio.closed_count}`} />
          <SummaryCell label="平均收益" value={portfolio.avg_realized_return_pct == null ? "-" : `${portfolio.avg_realized_return_pct}%`} />
          <SummaryCell
            label="总浮盈"
            value={formatMoney(portfolio.total_floating_pnl)}
            tone={portfolio.total_floating_pnl == null ? undefined : portfolio.total_floating_pnl >= 0 ? "positive" : "negative"}
          />
        </div>
        <div className="positionGrid">
          <section className="openPositionsPanel">
            <div className="positionPanelHead">
              <h3>当前持仓</h3>
              <span>{portfolio.open_positions.length}/{portfolio.open_count} 只</span>
            </div>
            {portfolio.open_positions.length === 0 ? (
              <div className="empty">暂无虚拟持仓。</div>
            ) : (
              <>
              {portfolio.open_positions.length > 6 && (
                <div className="positionScrollHint">列表支持滚动查看全部持仓，点击任一股票可打开详情与主动卖出。</div>
              )}
              <div className="openPositionList">
                {portfolio.open_positions.map((item) => {
                  const dividendTotal = cashDividendTotal(item);
                  return (
                  <button className="positionItem positionAction positionItemRich" key={item.id} onClick={() => openStock(item.symbol)}>
                    <div className="positionHead">
                      <div>
                        <strong>{item.name || item.symbol}</strong>
                        <span>{item.symbol} · {item.strategy_code ?? "未绑定策略"}</span>
                        {dividendTotal != null && (
                          <span className="dividendAdjustTag">除权除息已调整 · 分红权益 {formatMoney(dividendTotal)}</span>
                        )}
                      </div>
                      <mark className={`positionReturnMark ${returnTone(item.floating_return_pct)}`}>
                        {formatReturnPct(item.floating_return_pct)}
                      </mark>
                    </div>
                    <div className="positionPriceRow">
                      <b>{formatOptionalPrice(item.current_price)}</b>
                      <span>成本 {item.entry_price.toFixed(2)}</span>
                      <span className={returnTone(item.floating_pnl)}>{formatMoney(item.floating_pnl)}</span>
                    </div>
                    <div className="positionMetaRow">
                      <span>{item.quantity} 股</span>
                      <span>{holdingDaysLabel(item.holding_days)}</span>
                      <span>{item.quote_source ?? "行情源 -"}</span>
                    </div>
                  </button>
                  );
                })}
              </div>
              </>
            )}
          </section>
          <section className="closedPositionsPanel">
            <h3>最近完成</h3>
            {portfolio.recent_closed.length === 0 ? (
              <div className="empty">暂无完成交易。</div>
            ) : (
              <div className="closedPositionList">
                {portfolio.recent_closed.map((item) => (
                  <article className="positionItem closedPositionItem" key={item.id}>
                    <strong>{item.name || item.symbol}<small>{item.symbol}</small></strong>
                    <span className={(item.realized_return_pct ?? 0) >= 0 ? "positive" : "negative"}>
                      {item.realized_return_pct ?? 0}% · {item.entry_price.toFixed(2)} → {item.exit_price?.toFixed(2) ?? "-"}
                    </span>
                    <small>{item.strategy_code ?? "未绑定策略"} · {holdingDaysLabel(item.holding_days)} · {formatShortDateTime(item.exit_at ?? item.entry_at)}</small>
                  </article>
                ))}
              </div>
            )}
          </section>
          <section className="tradeHistoryPanel">
            <div className="tradeHistoryHead">
              <div>
                <h3>历史交易</h3>
                <span>按策略汇总已闭环交易，用于观察策略真实执行表现。</span>
              </div>
              <mark>{portfolio.trade_history.length} 笔历史交易</mark>
            </div>
            {portfolio.strategy_trade_summary.length === 0 ? (
              <div className="empty">暂无历史策略交易。完成卖出后，这里会沉淀策略胜率、平均收益和持仓周期。</div>
            ) : (
              <>
                <div className="strategyTradeGrid">
                  {portfolio.strategy_trade_summary.map((item) => (
                    <article className="strategyTradeCard" key={item.strategy_code}>
                      <div>
                        <strong>{strategyDisplayName(item.strategy_code)}</strong>
                        <span>{item.trade_count} 笔 · {item.avg_holding_days == null ? "周期 -" : `均持 ${item.avg_holding_days} 天`}</span>
                      </div>
                      <div className="strategyTradeMetrics">
                        <span><small>胜率</small><b>{item.win_rate_pct == null ? "-" : `${item.win_rate_pct}%`}</b></span>
                        <span><small>平均收益</small><b className={returnTone(item.avg_return_pct)}>{formatReturnPct(item.avg_return_pct)}</b></span>
                        <span><small>累计盈亏</small><b className={returnTone(item.total_realized_pnl)}>{formatMoney(item.total_realized_pnl)}</b></span>
                      </div>
                    </article>
                  ))}
                </div>
                <div className="tradeHistoryList">
                  <div className="tradeHistoryRow tradeHistoryHeader">
                    <span>股票</span>
                    <span>策略</span>
                    <span>买入/卖出</span>
                    <span>收益</span>
                    <span>周期</span>
                    <span>完成时间</span>
                  </div>
                  {portfolio.trade_history.map((item) => (
                    <div className="tradeHistoryRow" key={item.id}>
                      <strong>{item.name || item.symbol}<small>{item.symbol}</small></strong>
                      <span>{strategyDisplayName(item.strategy_code)}</span>
                      <span>{item.entry_price.toFixed(2)} → {item.exit_price?.toFixed(2) ?? "-"}</span>
                      <mark className={`positionReturnMark ${returnTone(item.realized_return_pct)}`}>
                        {formatReturnPct(item.realized_return_pct)}
                      </mark>
                      <span>{holdingDaysLabel(item.holding_days).replace("持仓 ", "")}</span>
                      <span>{formatShortDateTime(item.exit_at ?? item.entry_at)}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        </div>
      </section>

      <section className="weeklyAnalysisBand">
        <div className="sectionHead">
          <div>
            <h2>周形势分析</h2>
            <p>按最近可用交易周，对比市场基准、策略实际收益与周回测表现。</p>
          </div>
          <mark>{weeklyAnalysis.week_start ?? "-"} 至 {weeklyAnalysis.week_end ?? "-"}</mark>
        </div>
        <div className="weeklyOverview">
          <article className="weeklyMarketCard">
            <div className="weeklyCardHead">
              <span className="eyebrow">本周市场形势</span>
              <strong>{weeklyAnalysis.benchmark.regime}</strong>
            </div>
            <p>{weeklyAnalysis.benchmark.interpretation}</p>
            <div className="weeklyMetricGrid">
              <SummaryCell
                label="市场等权收益"
                value={formatReturnPct(weeklyAnalysis.benchmark.return_pct)}
                tone={summaryTone(weeklyAnalysis.benchmark.return_pct)}
              />
              <SummaryCell label="上涨占比" value={formatOptionalPct(weeklyAnalysis.benchmark.up_ratio_pct)} />
              <SummaryCell label="样本覆盖" value={`${weeklyAnalysis.benchmark.sample_count}`} />
              <SummaryCell
                label="成交额变化"
                value={formatReturnPct(weeklyAnalysis.benchmark.amount_change_pct)}
                tone={summaryTone(weeklyAnalysis.benchmark.amount_change_pct)}
              />
            </div>
          </article>
          <article className="weeklyMarketCard">
            <div className="weeklyCardHead">
              <span className="eyebrow">策略运行结论</span>
              <strong>{weeklyAnalysis.summary.market_regime ?? "待评估"}</strong>
            </div>
            <p>{weeklyAnalysis.summary.overall_assessment}</p>
            <div className="weeklyMetricGrid three">
              <SummaryCell label="优于市场" value={`${weeklyAnalysis.summary.outperform_count}`} tone="positive" />
              <SummaryCell label="需复盘" value={`${weeklyAnalysis.summary.underperform_count}`} tone={weeklyAnalysis.summary.underperform_count > 0 ? "negative" : undefined} />
              <SummaryCell label="继续观察" value={`${weeklyAnalysis.summary.watch_count}`} />
            </div>
          </article>
        </div>
        {weeklyAnalysis.strategy_reviews.length === 0 ? (
          <div className="empty">暂无周策略分析。补齐本周真实日 K、回测或交易样本后会自动生成。</div>
        ) : (
          <div className="weeklyStrategyGrid">
            {weeklyAnalysis.strategy_reviews.map((item) => (
              <article className="weeklyStrategyCard" key={item.strategy_code}>
                <div className="weeklyStrategyHead">
                  <div>
                    <strong>{item.strategy_name}</strong>
                    <span>{item.strategy_code}</span>
                  </div>
                  <mark className={weeklyStatusClass(item.optimization_signal)}>{item.status}</mark>
                </div>
                <div className="weeklyStrategyMetrics">
                  <span>
                    <small>实际收益</small>
                    <b className={returnTone(item.actual_return_pct)}>{formatReturnPct(item.actual_return_pct)}</b>
                  </span>
                  <span>
                    <small>周回测</small>
                    <b className={returnTone(item.backtest_return_pct)}>{formatReturnPct(item.backtest_return_pct)}</b>
                  </span>
                  <span>
                    <small>超额</small>
                    <b className={returnTone(item.excess_vs_market_pct)}>{formatReturnPct(item.excess_vs_market_pct)}</b>
                  </span>
                  <span>
                    <small>样本/胜率</small>
                    <b>{item.actual_trade_count} / {formatOptionalPct(item.win_rate_pct)}</b>
                  </span>
                </div>
                <p>{item.diagnosis}</p>
                <div className="weeklySuggestionList">
                  {item.suggestions.slice(0, 3).map((suggestion) => (
                    <span key={suggestion}>{suggestion}</span>
                  ))}
                </div>
              </article>
            ))}
          </div>
        )}
        <div className="weeklyMethodology">
          {Object.entries(weeklyAnalysis.methodology).map(([key, value]) => (
            <span key={key}>{value}</span>
          ))}
        </div>
      </section>

      <section className="strategyPickerBand">
        <div className="sectionHead">
          <div>
            <h2>策略选股</h2>
            <p>选择一个或多个策略后，从当前真实行情覆盖股票池里筛出评分最高的候选；机构抱团策略可展示前 10 只。</p>
          </div>
          <button className="primaryButton inlineButton" onClick={runStrategyPicker} disabled={pickLoading || selectedPickStrategies.length === 0}>
            <ListChecks size={16} />
            <span>{pickLoading ? "筛选中" : "运行选股"}</span>
          </button>
        </div>
        <div className="strategyOptionGrid">
          {pickableStrategies.map((strategy) => (
            <label className={selectedPickStrategies.includes(strategy.code) ? "strategyOption active" : "strategyOption"} key={strategy.code}>
              <input type="checkbox" checked={selectedPickStrategies.includes(strategy.code)} onChange={() => togglePickStrategy(strategy.code)} />
              <span>
                <strong>{strategy.name}</strong>
                <small>{strategy.category} · 阈值 65</small>
              </span>
            </label>
          ))}
        </div>
        {pickResult ? (
          <>
            <div className="pickerStats">
              <SummaryCell label="候选股票" value={`${pickResult.items.length}`} />
              <SummaryCell label="达标样本" value={`${pickResult.candidate_count}`} />
              <SummaryCell label="覆盖股票池" value={`${pickResult.universe_size}`} />
              <SummaryCell label="最低阈值" value={`${pickResult.min_score}`} />
            </div>
            <div className="pickerMeta">
              <span>{pickResult.message}</span>
              <span>股票池 {pickResult.universe_size} · 候选 {pickResult.candidate_count} · {pickResult.generated_at ? formatDateTime(pickResult.generated_at) : "等待信号"}</span>
            </div>
            {pickResult.items.length === 0 ? (
              <div className="empty">{pickResult.message}</div>
            ) : (
              <div className="pickResultGrid">
                {pickResult.items.map((item) => (
                  <article className="pickCard" key={`${item.strategy_code}-${item.symbol}`}>
                    <div className="pickCardHead">
                      <div>
                        <strong>{item.name || item.symbol}</strong>
                        <span>{item.symbol} · {boardForSymbol(item.symbol)} · {item.strategy_code}</span>
                      </div>
                      <mark className={`recommendMark ${pickRecommendation(item).className}`}>
                        {pickRecommendation(item).label} {item.score.toFixed(1)}
                      </mark>
                    </div>
                    <div className="priceLine">
                      <b>{item.last_price.toFixed(2)}</b>
                      <span className={item.change_pct >= 0 ? "positive" : "negative"}>{item.change_pct.toFixed(2)}%</span>
                      <small>{(item.amount / 100000000).toFixed(1)} 亿</small>
                    </div>
                    <div className="scoreBar">
                      <span style={{ width: `${Math.min(100, item.score)}%` }} />
                    </div>
                    <div className="factorPills">
                      {pickFactorPills(item).map((pill) => (
                        <span key={pill}>{pill}</span>
                      ))}
                    </div>
                    <p>{item.reason}</p>
                    <small>{pickDataStatusLabel(item.data_status)} · {item.quote_source} · 置信度 {(item.confidence * 100).toFixed(0)}%</small>
                    <div className="actionRow">
                      <button onClick={() => openStock(item.symbol)} title="查看个股走势与要素">
                        <LineChart size={15} />
                        <span>个股</span>
                      </button>
                      <button onClick={() => usePickAsWatch(item)} title="填入自选追踪">
                        <Star size={15} />
                        <span>追踪</span>
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="empty">尚未运行策略选股。开盘后可先刷新实时行情，再运行选股。</div>
        )}
      </section>

      <section className="strategyLibraryBand">
        <div className="sectionHead">
          <div>
            <h2>策略库</h2>
            <p>保留策略历史版本、回测证据和用户样本表现，为后续自学习更新提供基线。</p>
          </div>
          <div className="libraryStats">
            <SummaryCell label="策略版本" value={`${strategyLibrary.total}`} />
            <SummaryCell label="已验证" value={`${(strategyLibrary.status_counts.validated ?? 0) + (strategyLibrary.status_counts.active ?? 0)}`} />
            <SummaryCell label="候选" value={`${strategyLibrary.status_counts.candidate ?? 0}`} />
            <SummaryCell label="观测池" value={`${strategyLibrary.observation_pool?.total ?? 0}`} />
          </div>
        </div>
        <div className="libraryGrid">
          {strategyLibrary.entries.map((entry) => (
            <article className="libraryCard" key={`${entry.code}-${entry.version}`}>
              <div className="libraryCardHead">
                <div>
                  <strong>{entry.name}</strong>
                  <span>{entry.code} · {entry.version}</span>
                </div>
                <mark>{strategyStatusLabel(entry.status)}</mark>
              </div>
              <p>{entry.display_profile?.market_fit ?? entry.thesis}</p>
              <div className="strategyUseBox">
                <span>{entry.display_profile?.decision_use ?? "用于辅助分析，不直接构成买卖结论。"}</span>
                <span>{entry.display_profile?.risk_focus ?? "需结合行情质量、仓位管理和人工确认。"}</span>
              </div>
              <div className="maturityBar" aria-label="策略成熟度">
                <span style={{ width: `${strategyMaturityScore(entry)}%` }} />
              </div>
              <div className="evidenceRow">
                <span>{entry.display_profile?.evidence_label ?? "候选待验证"}</span>
                <span>{entry.observation_metrics?.applicability ?? "样本不足"}</span>
                <span>回测 {formatCount(entry.performance.backtest_count)}</span>
                <span>用户 {formatCount(entry.learning_metrics.sample_count)}</span>
                <span>观测 {entry.observation_metrics?.total ?? 0}</span>
              </div>
              <div className="libraryMetrics">
                <SummaryCell label="观测收益" value={formatOptionalPct(entry.observation_metrics?.avg_observed_return_pct)} tone={metricNumber(entry.observation_metrics?.avg_observed_return_pct, 0) >= 0 ? "positive" : "negative"} />
                <SummaryCell label="正样本率" value={formatOptionalPct(entry.observation_metrics?.positive_rate_pct)} />
                <SummaryCell label="真实样本" value={`${entry.learning_metrics.sample_count ?? 0}`} />
                <SummaryCell label="Top3样本" value={`${entry.observation_metrics?.daily_top3_count ?? 0}`} />
              </div>
              {(entry.observation_metrics?.recent?.length ?? 0) > 0 && (
                <div className="observationMiniList">
                  {entry.observation_metrics?.recent?.slice(0, 3).map((item, index) => (
                    <span key={`${entry.code}-${item.symbol}-${item.trade_date}-${item.source_type}-${index}`}>
                      {item.symbol} {formatOptionalPct(item.current_return_pct)}
                    </span>
                  ))}
                </div>
              )}
              <div className="tagRow">
                {entry.tags.slice(0, 4).map((tag) => (
                  <span key={tag}>{tag}</span>
                ))}
              </div>
            </article>
          ))}
        </div>
        <div className="comparisonTable">
          <div className="comparisonHeader">
            <span>策略</span>
            <span>状态</span>
            <span>年化</span>
            <span>最大回撤</span>
            <span>Sharpe</span>
            <span>用户样本</span>
            <span>样本收益</span>
            <span>观测池</span>
            <span>适用度</span>
          </div>
          {strategyLibrary.comparison.map((row) => (
            <div className="comparisonRow" key={row.code}>
              <strong>{row.name}</strong>
              <span>{strategyStatusLabel(row.status)}</span>
              <span className={metricNumber(row.annual_return_pct, 0) >= 0 ? "positive" : "negative"}>{formatOptionalPct(row.annual_return_pct)}</span>
              <span className="negative">{formatOptionalPct(row.max_drawdown_pct)}</span>
              <span>{formatOptional(row.sharpe)}</span>
              <span>{row.sample_count ?? 0}</span>
              <span>{formatOptionalPct(row.avg_realized_return_pct)}</span>
              <span>{row.observed_count ?? 0}</span>
              <span>{row.applicability ?? "样本不足"}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="backtestBand">
        <div className="sectionHead">
          <div>
            <h2>策略历史回撤分析</h2>
            <p>仅使用截至目前的真实用户交易闭环样本，测试/模拟回测数据不再进入该面板。</p>
          </div>
          <select value={backtestFilter} onChange={(event) => setBacktestFilter(event.target.value)} aria-label="筛选回撤策略">
            <option value="all">全部策略</option>
            {Array.from(new Map(data.backtests.map((run) => [run.strategy_code, run.strategy_name ?? run.strategy_code])).entries()).map(([code, name]) => (
              <option value={code} key={code}>{name} · {code}</option>
            ))}
          </select>
        </div>
        <div className="backtestGrid">
          {filteredBacktests.length === 0 ? (
            <div className="empty">暂无真实用户闭环样本。完成真实买入与卖出后，这里会自动生成策略收益曲线和回撤分析。</div>
          ) : filteredBacktests.map((run, index) => {
            const maxDrawdown = metricNumber(run.metrics.max_drawdown_pct);
            const totalReturn = metricNumber(run.metrics.total_return_pct ?? run.metrics.annual_return_pct);
            return (
            <article className="backtestCard" key={`${run.strategy_code}-${index}`}>
              <div className="backtestCardHead">
                <div>
                  <h3>{run.strategy_name ?? run.strategy_code}</h3>
                  <span>{run.strategy_code} · {String(run.diagnostics?.data_status ?? run.diagnostics?.source ?? "real_data")}</span>
                </div>
                <mark>{riskLevelLabel(run.risk_flags ?? [])}</mark>
              </div>
              <MiniEquityChart points={run.equity_curve ?? []} />
              <dl>
                <div><dt>累计收益</dt><dd className={totalReturn >= 0 ? "positive" : "negative"}>{totalReturn}%</dd></div>
                <div><dt>最大回撤</dt><dd className="negative">{maxDrawdown}%</dd></div>
                <div><dt>样本Sharpe</dt><dd>{metricNumber(run.metrics.sharpe)}</dd></div>
                <div><dt>胜率</dt><dd>{metricNumber(run.metrics.win_rate_pct)}%</dd></div>
                <div><dt>平均收益</dt><dd>{metricNumber(run.metrics.avg_realized_return_pct)}%</dd></div>
                <div><dt>样本数</dt><dd>{metricNumber(run.metrics.sample_count)}</dd></div>
              </dl>
              <div className="backtestEvidence">
                <span>样本 {metricNumber(run.diagnostics?.sample_count)}</span>
                <span>{String(run.diagnostics?.source ?? "portfolio_positions")}</span>
                <span>{run.assumptions?.cost_model ? String(run.assumptions.cost_model) : "按用户确认价"}</span>
              </div>
              <button className="textButton" onClick={() => setActiveBacktest(run)}>
                <BarChart3 size={15} />
                <span>查看完整分析</span>
              </button>
            </article>
            );
          })}
        </div>
      </section>

      {(selectedSymbol || stockDetail) && (
        <StockDetailModal
          detail={stockDetail}
          loading={detailLoading}
          error={detailError}
          fallbackSymbol={selectedSymbol}
          holdingPosition={selectedOpenPosition}
          isTracked={stockDetail ? data.watchlist.some((item) => item.symbol === stockDetail.symbol) : false}
          linkageEvent={selectedLinkageEvent}
          onAddWatch={addDetailToWatch}
          onManualSell={manualSellPosition}
          onClose={closeStock}
        />
      )}

      {activeStrategy && (
        <StrategyModal
          strategy={strategies.find((item) => item.code === activeStrategy) ?? null}
          onClose={() => setActiveStrategy(null)}
        />
      )}

      {activeBacktest && (
        <BacktestModal run={activeBacktest} onClose={() => setActiveBacktest(null)} />
      )}
    </main>
  );
}

function numberOrNull(value: string): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) && value.trim() !== "" ? parsed : null;
}

function dedupeResearchItems(items: Dashboard["research"]): Dashboard["research"] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = normalizeResearchKey(item.title);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function normalizeResearchKey(value: string): string {
  return value.toLowerCase().replace(/[^\w\u4e00-\u9fff]+/g, "").slice(0, 80);
}

function compactResearchSummary(item: Dashboard["research"][number]): string {
  const summary = item.summary?.trim() || item.title;
  if (normalizeResearchKey(summary) === normalizeResearchKey(item.title)) {
    return item.is_summary_complete ? summary : "鼠标移到标题可查看来源、标签与原文入口。";
  }
  return summary.length > 92 ? `${summary.slice(0, 92)}...` : summary;
}

function researchDetailText(item: Dashboard["research"][number]): string {
  const summary = item.summary?.trim();
  if (summary && normalizeResearchKey(summary) !== normalizeResearchKey(item.title)) {
    return summary;
  }
  return "当前来源只提供标题级快讯，系统已保留来源、标签和原文链接；后续采集任务会继续尝试补齐正文摘要。";
}

function metricNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function formatOptional(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "-";
}

function formatCount(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "0";
}

function formatOptionalPct(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value}%` : "-";
}

function triggerTypeLabel(value?: string): string {
  const labels: Record<string, string> = {
    sudden_rise: "1分钟拉升",
    sudden_drop: "1分钟跳水",
    opening_rise: "开盘上行",
    opening_drop: "开盘下行",
  };
  return value ? labels[value] ?? value : "波动";
}

function formatAmountYi(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "成交额 -";
  return `${(value / 100000000).toFixed(1)} 亿`;
}

function formatRatio(value?: number | null): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}x` : "-";
}

function formatVolumeEvidence(trigger: SectorLinkageTrigger): string {
  const strongestRatio = [
    trigger.volume_surge_ratio,
    trigger.amount_surge_ratio,
    trigger.intraday_volume_intensity,
    trigger.intraday_amount_intensity,
    trigger.market_volume_ratio,
    trigger.market_amount_ratio,
  ].filter((value): value is number => typeof value === "number" && Number.isFinite(value))
    .sort((a, b) => b - a)[0];
  const amountText = formatPlainMoney(trigger.amount_delta);
  return `量能强度 ${formatRatio(strongestRatio)}${amountText !== "-" ? ` / 额 ${amountText}` : ""}`;
}

function formatPlainMoney(value?: number | null): string {
  if (value == null || !Number.isFinite(value) || value <= 0) return "-";
  if (value >= 100000000) return `${(value / 100000000).toFixed(2)}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(1)}万`;
  return value.toFixed(0);
}

function formatMoney(value?: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  const absValue = Math.abs(value);
  const prefix = value > 0 ? "+" : value < 0 ? "-" : "";
  if (absValue >= 100000000) return `${prefix}${(absValue / 100000000).toFixed(2)} 亿`;
  if (absValue >= 10000) return `${prefix}${(absValue / 10000).toFixed(2)} 万`;
  return `${prefix}${absValue.toFixed(2)}`;
}

function formatDateTime(value: string): string {
  return parseAppDate(value).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" });
}

function formatShortDateTime(value: string): string {
  return parseAppDate(value).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" });
}

function formatCnTime(value: string): string {
  if (!value) return "";
  return parseAppDate(value).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai" });
}

function parseAppDate(value: string): Date {
  if (!value) return new Date(NaN);
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value);
  return new Date(hasTimezone ? value : `${value}Z`);
}

function candidateReturnItems(event: SectorLinkageHistoryItem): Array<Record<string, unknown>> {
  const followupItems = event.followup_metrics?.candidate_returns;
  if (Array.isArray(followupItems) && followupItems.length > 0) return followupItems;
  return event.candidates ?? [];
}

function closestIntradayIndex(points: IntradayPoint[], triggeredAt: string): number {
  if (!triggeredAt || points.length === 0) return -1;
  const target = minutesOfDay(formatCnTime(triggeredAt));
  if (target == null) return -1;
  let bestIndex = -1;
  let bestDistance = Number.POSITIVE_INFINITY;
  points.forEach((point, index) => {
    const current = minutesOfDay(point.time);
    if (current == null) return;
    const distance = Math.abs(current - target);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestDistance <= 45 ? bestIndex : -1;
}

function minutesOfDay(value: string): number | null {
  const match = value.match(/(\d{1,2}):(\d{2})/);
  if (!match) return null;
  return Number(match[1]) * 60 + Number(match[2]);
}

function formatOptionalNumber(value: unknown, suffix = "", digits = 2): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : "暂无";
}

function factorNumber(factors: Record<string, unknown>, key: string): number | null {
  const value = factors[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function pickFactorPills(item: StrategyPickItem): string[] {
  const factors = item.factors ?? {};
  const institutional = typeof factors.institutional === "object" && factors.institutional !== null
    ? factors.institutional as Record<string, unknown>
    : null;
  if (item.strategy_code === "institutional_crowding" && institutional) {
    return [
      `抱团 ${formatFactorValue(recordNumber(institutional, "crowding_score"), 1)}`,
      `机构 ${formatOptionalFactorPct(recordNumber(institutional, "institution_holding_pct"))}`,
      `机构数 ${formatFactorValue(recordNumber(institutional, "institution_count"), 0)}`,
      `基金数 ${formatFactorValue(recordNumber(institutional, "fund_count"), 0)}`,
      `北向 ${formatOptionalFactorPct(recordNumber(institutional, "northbound_holding_pct"))}`
    ].filter((pill) => !pill.includes("暂无"));
  }
  const pills = [
    `20日 ${formatSignedPct(factorNumber(factors, "return_20d_pct"))}`,
    `MA20 ${formatSignedPct(factorNumber(factors, "distance_ma20_pct"))}`,
    `量比 ${formatFactorValue(factorNumber(factors, "live_amount_ratio"), 2)}`,
    `波动 ${formatOptionalFactorPct(factorNumber(factors, "volatility_20d_pct"))}`
  ];
  return pills.filter((pill) => !pill.includes("暂无"));
}

function formatSignedPct(value: number | null): string {
  if (value == null) return "暂无";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function formatOptionalFactorPct(value: number | null): string {
  return value == null ? "暂无" : `${value.toFixed(1)}%`;
}

function formatFactorValue(value: number | null, digits = 1): string {
  return value == null ? "暂无" : value.toFixed(digits);
}

function recordNumber(record: Record<string, unknown>, key: string): number | null {
  const value = record[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function cashDividendTotal(position?: PortfolioPosition | null): number | null {
  const value = position?.meta?.cash_dividend_total;
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }
  return null;
}

function formatOptionalPrice(value?: number | null): string {
  return value == null ? "-" : value.toFixed(2);
}

function formatReturnPct(value?: number | null): string {
  return value == null ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function returnTone(value?: number | null): string {
  if (value == null) return "neutral";
  return value >= 0 ? "positive" : "negative";
}

function summaryTone(value?: number | null): "positive" | "negative" | undefined {
  if (value == null) return undefined;
  return value >= 0 ? "positive" : "negative";
}

function weeklyStatusClass(signal: string): string {
  if (signal === "keep") return "weeklyStatus keep";
  if (signal === "review") return "weeklyStatus review";
  return "weeklyStatus watch";
}

function holdingDaysLabel(days?: number | null): string {
  return days == null ? "持仓 -" : `持仓 ${days} 天`;
}

function pickRecommendation(item: StrategyPickItem): { label: string; className: string } {
  if (item.score >= 72 && item.confidence >= 0.7) return { label: "强观察", className: "strong" };
  if (item.score >= 65) return { label: "观察", className: "watch" };
  return { label: "暂缓", className: "hold" };
}

function strategyMaturityScore(entry: Dashboard["strategy_library"]["entries"][number]): number {
  const statusScore = entry.status === "active" ? 35 : entry.status === "validated" ? 28 : entry.status === "candidate" ? 14 : 6;
  const backtestScore = Math.min(30, Number(entry.performance.backtest_count ?? 0) * 10);
  const sampleScore = Math.min(25, Number(entry.learning_metrics.sample_count ?? 0) * 3);
  const returnScore = typeof entry.performance.sharpe === "number" && entry.performance.sharpe > 0 ? 10 : 0;
  return Math.min(100, statusScore + backtestScore + sampleScore + returnScore);
}

function riskLevelLabel(flags: string[]): string {
  if (flags.length >= 2) return "高风险";
  if (flags.length === 1) return "需观察";
  return "常规";
}

function strategyStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    active: "线上使用",
    validated: "已验证",
    candidate: "候选",
    archived: "归档"
  };
  return labels[status] ?? status;
}

function readinessStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pass: "通过",
    warn: "警告",
    fail: "失败"
  };
  return labels[status] ?? status;
}

function guardrailStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    healthy: "健康",
    guarded: "保护中",
    blocked: "已阻断",
    unknown: "未知"
  };
  return labels[status] ?? status;
}

function signalDataStatus(signal: Dashboard["signals"][number]): string {
  const status = signal.evidence?.data_status;
  if (status === "real_daily_factor") return "真实日K因子";
  if (status === "live_quote_only") return "实时行情观察";
  return "真实行情";
}

function pickDataStatusLabel(status: string): string {
  if (status === "real_daily_factor") return "真实日K因子";
  if (status === "live_quote_only") return "实时行情观察";
  return "真实行情";
}

function sourceRoleLabel(role: string): string {
  const labels: Record<string, string> = {
    primary: "主源",
    secondary: "次选",
    backup: "备用",
    fundamental_backup: "基本面备用"
  };
  return labels[role] ?? role;
}

function stockStatusLabel(status: StockDetail["stock_status"]): string {
  if (status.is_suspended) return "停牌";
  if (status.is_st) return "ST/风险警示";
  if (status.is_new_stock) return "新股特殊期";
  return "正常";
}

function boardForSymbol(symbol: string): string {
  if (symbol.startsWith("300") || symbol.startsWith("301")) return "创业板";
  if (symbol.startsWith("688") || symbol.startsWith("689")) return "科创板";
  if (symbol.startsWith("8") || symbol.startsWith("4")) return "北交所";
  if (symbol.startsWith("600") || symbol.startsWith("601") || symbol.startsWith("603") || symbol.startsWith("605")) return "上海主板";
  if (symbol.startsWith("000") || symbol.startsWith("001") || symbol.startsWith("002") || symbol.startsWith("003")) return "深圳主板";
  return "A股";
}

function decisionLabel(action: string): string {
  const labels: Record<string, string> = {
    buy: "买入",
    sell: "卖出",
    ignore: "忽略",
    watch: "继续观察"
  };
  return labels[action] ?? action;
}

function Metric({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: string; sub: string }) {
  return (
    <article className="metric">
      <div className="metricIcon">{icon}</div>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{sub}</small>
    </article>
  );
}

function Panel({ title, icon, children, className = "" }: { title: string; icon: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <section className={className ? `panel ${className}` : "panel"}>
      <div className="panelTitle">
        {icon}
        <h2>{title}</h2>
      </div>
      {children}
    </section>
  );
}

function quoteSortLabel(sort: "amount" | "up" | "down" | "signal"): string {
  const labels = {
    amount: "成交额",
    up: "涨幅",
    down: "跌幅",
    signal: "策略命中"
  };
  return labels[sort];
}

function quoteSortDescription(sort: "amount" | "up" | "down" | "signal"): string {
  const descriptions = {
    amount: "按真实行情成交额排序，优先观察资金参与度高的股票。",
    up: "按当日涨幅排序，优先观察强势异动股票。",
    down: "按当日跌幅排序，优先观察风险释放和反转候选。",
    signal: "优先展示已命中策略信号的股票，再按成交额排序。"
  };
  return descriptions[sort];
}

function quoteReasonLabel(quote: MarketQuote, sort: "amount" | "up" | "down" | "signal", signalHit: boolean): string {
  const amountText = `成交额 ${formatAmountYi(quote.amount)}`;
  if (sort === "signal" && signalHit) return `策略命中 · ${amountText}`;
  if (signalHit) return `策略命中 · ${quoteSortLabel(sort)}榜`;
  if (sort === "up") return `涨幅靠前 · ${amountText}`;
  if (sort === "down") return `跌幅靠前 · ${amountText}`;
  return `高成交额 · ${quote.change_pct >= 0 ? "上涨" : "下跌"} ${quote.change_pct.toFixed(2)}%`;
}

function StrategyModal({ strategy, onClose }: { strategy: StrategyDefinition | null; onClose: () => void }) {
  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="smallModal">
        <header className="modalHead">
          <div>
            <span className="eyebrow">策略推荐及设定</span>
            <h2>{strategy ? strategy.name : "策略详情"}</h2>
          </div>
          <button className="closeButton" onClick={onClose} title="关闭">
            <X size={20} />
          </button>
        </header>
        {strategy ? (
          <>
            <p className="modalCopy">{strategy.description}</p>
            <div className="detailGrid compact">
              <section className="detailBlock">
                <h3>策略参数</h3>
                <KeyValueList data={strategy.parameters} />
              </section>
              <section className="detailBlock">
                <h3>风控规则</h3>
                <KeyValueList data={strategy.risk_rules} />
              </section>
            </div>
            <div className="noticeBox">
              候选策略进入线上推荐前，应经过离线回测、样本外验证和人工确认；当前页面展示的是研究辅助信号。
            </div>
          </>
        ) : (
          <div className="modalLoading">未找到该策略。</div>
        )}
      </section>
    </div>
  );
}

function BacktestModal({ run, onClose }: { run: Dashboard["backtests"][number]; onClose: () => void }) {
  const riskFlags = run.risk_flags ?? [];
  const diagnostics = run.diagnostics ?? {};
  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="backtestModal">
        <header className="modalHead">
          <div>
            <span className="eyebrow">策略历史回撤分析</span>
            <h2>{run.strategy_name ?? run.strategy_code}</h2>
            <p>{run.strategy_code}</p>
          </div>
          <button className="closeButton" onClick={onClose} title="关闭">
            <X size={20} />
          </button>
        </header>
        <div className="backtestDetailGrid">
          <SummaryCell
            label="累计收益"
            value={`${metricNumber(run.metrics.total_return_pct ?? run.metrics.annual_return_pct)}%`}
            tone={metricNumber(run.metrics.total_return_pct ?? run.metrics.annual_return_pct) >= 0 ? "positive" : "negative"}
          />
          <SummaryCell label="平均单笔" value={`${metricNumber(run.metrics.avg_realized_return_pct)}%`} tone={metricNumber(run.metrics.avg_realized_return_pct) >= 0 ? "positive" : "negative"} />
          <SummaryCell label="最大回撤" value={`${metricNumber(run.metrics.max_drawdown_pct)}%`} tone="negative" />
          <SummaryCell label="Sharpe / Calmar" value={`${metricNumber(run.metrics.sharpe)} / ${metricNumber(run.metrics.calmar)}`} />
          <SummaryCell label="胜率" value={`${metricNumber(run.metrics.win_rate_pct)}%`} />
          <SummaryCell label="样本数" value={`${metricNumber(run.metrics.sample_count)}`} />
          <SummaryCell label="数据口径" value={String(run.diagnostics?.source ?? "portfolio_positions")} />
          <SummaryCell label="费用口径" value={String(run.assumptions?.cost_model ?? "按用户确认价")} />
        </div>
        <div className="chartGrid">
          <ChartPanel title="资金曲线">
            <BacktestLineChart points={run.equity_curve ?? []} mode="equity" />
          </ChartPanel>
          <ChartPanel title="回撤曲线">
            <BacktestLineChart points={run.drawdown_curve ?? []} mode="drawdown" />
          </ChartPanel>
        </div>
        <div className="detailGrid compact">
          <section className="detailBlock">
            <h3>月度收益</h3>
            <div className="monthlyReturnGrid">
              {(run.monthly_returns ?? []).slice(-12).map((item) => (
                <div key={item.date} className={item.return_pct >= 0 ? "returnCell positiveBg" : "returnCell negativeBg"}>
                  <span>{item.date.slice(5)}</span>
                  <strong>{item.return_pct}%</strong>
                </div>
              ))}
            </div>
          </section>
          <section className="detailBlock">
            <h3>市场状态表现</h3>
            <div className="regimeList">
              {(run.regime_breakdown ?? []).map((item) => (
                <div key={item.regime}>
                  <span>{item.regime}</span>
                  <strong className={item.return_pct >= 0 ? "positive" : "negative"}>{item.return_pct}%</strong>
                  <small>胜率 {item.win_rate_pct}%</small>
                </div>
              ))}
            </div>
          </section>
        </div>
        <div className="detailGrid compact">
          <section className="detailBlock">
            <h3>交易约束</h3>
            <KeyValueList data={run.assumptions} />
          </section>
          <section className="detailBlock">
            <h3>样本与诊断</h3>
            <KeyValueList data={diagnostics} />
            <div className="tagRow">
              {run.stock_pool.slice(0, 12).map((symbol) => (
                <span key={symbol}>{symbol}</span>
              ))}
            </div>
          </section>
        </div>
        {riskFlags.length > 0 && (
          <ul className="riskList">
            {riskFlags.map((flag) => (
              <li key={flag}>{flag}</li>
            ))}
          </ul>
        )}
        <div className="noticeBox">
          当前面板只使用真实用户确认买卖形成的闭环样本；未绑定策略、未卖出持仓和模拟/测试回测记录不会进入策略历史回撤分析。
        </div>
      </section>
    </div>
  );
}

function KeyValueList({ data }: { data: Record<string, unknown> }) {
  return (
    <dl className="keyValueList">
      {Object.entries(data).map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{formatValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${String(item)}`)
      .join(" / ");
  }
  return String(value);
}

function StockDetailModal({
  detail,
  loading,
  error,
  fallbackSymbol,
  holdingPosition,
  isTracked,
  linkageEvent,
  onAddWatch,
  onManualSell,
  onClose
}: {
  detail: StockDetail | null;
  loading: boolean;
  error: string | null;
  fallbackSymbol: string | null;
  holdingPosition: PortfolioPosition | null;
  isTracked: boolean;
  linkageEvent: SectorLinkageHistoryItem | null;
  onAddWatch: (detail: StockDetail) => Promise<void>;
  onManualSell: (position: PortfolioPosition, payload: { price: number; quantity: number; notes?: string }) => Promise<void>;
  onClose: () => void;
}) {
  const quote = detail?.quote;
  const latestSignal = detail?.signals[0];
  const fundamentals = detail?.fundamentals;
  const [watchAdding, setWatchAdding] = useState(false);
  const [sellPrice, setSellPrice] = useState("");
  const [sellQuantity, setSellQuantity] = useState("");
  const [sellNotes, setSellNotes] = useState("");
  const [sellSaving, setSellSaving] = useState(false);
  const [sellMessage, setSellMessage] = useState<string | null>(null);
  const floatingReturnPct = quote && holdingPosition
    ? ((quote.last_price / holdingPosition.entry_price) - 1) * 100
    : null;

  useEffect(() => {
    if (!holdingPosition) {
      setSellPrice("");
      setSellQuantity("");
      setSellNotes("");
      setSellMessage(null);
      return;
    }
    const defaultPrice = quote?.last_price ?? holdingPosition.current_price ?? holdingPosition.entry_price;
    setSellPrice(defaultPrice ? defaultPrice.toFixed(2) : "");
    setSellQuantity(String(holdingPosition.quantity));
    setSellNotes("");
    setSellMessage(null);
  }, [holdingPosition?.id, quote?.last_price]);

  async function addCurrentToWatch() {
    if (!detail || watchAdding || isTracked) return;
    setWatchAdding(true);
    try {
      await onAddWatch(detail);
    } finally {
      setWatchAdding(false);
    }
  }

  async function submitManualSell(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!holdingPosition || sellSaving) return;
    const price = Number(sellPrice);
    const quantity = Number.parseInt(sellQuantity, 10);
    if (!Number.isFinite(price) || price <= 0) {
      setSellMessage("请填写有效卖出价格。");
      return;
    }
    if (!Number.isFinite(quantity) || quantity <= 0) {
      setSellMessage("请填写有效卖出数量。");
      return;
    }
    if (quantity > holdingPosition.quantity) {
      setSellMessage(`卖出数量不能超过当前持仓 ${holdingPosition.quantity} 股。`);
      return;
    }
    setSellSaving(true);
    try {
      await onManualSell(holdingPosition, {
        price,
        quantity,
        notes: sellNotes.trim() || "用户在持仓详情中主动确认卖出。"
      });
      setSellMessage("已记录主动卖出，并同步更新持仓收益与策略样本。");
    } catch (error) {
      setSellMessage(error instanceof Error ? error.message : "主动卖出记录失败，请稍后重试。");
    } finally {
      setSellSaving(false);
    }
  }

  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true">
      <section className="stockModal">
        <header className="modalHead">
          <div>
            <span className="eyebrow">个股实时详情</span>
            <h2>{detail ? `${detail.name || detail.symbol} ${detail.symbol}` : fallbackSymbol}</h2>
          </div>
          <button className="closeButton" onClick={onClose} title="关闭">
            <X size={20} />
          </button>
        </header>

        {error && <div className="noticeBox">{error}</div>}
        {loading ? (
          <div className="modalLoading">正在读取分时、K线与策略信号...</div>
        ) : detail && quote ? (
          <>
            <div className="decisionStrip">
              <div>
                <span className="eyebrow">决策快照</span>
                <strong>{latestSignal ? (actionLabel[latestSignal.action] ?? latestSignal.action) : "等待策略信号"}</strong>
              </div>
              <div>
                <span>最新价</span>
                <b className={quote.change_pct >= 0 ? "positive" : "negative"}>{quote.last_price.toFixed(2)}</b>
              </div>
              <div>
                <span>日内涨跌</span>
                <b className={quote.change_pct >= 0 ? "positive" : "negative"}>{quote.change_pct.toFixed(2)}%</b>
              </div>
              <div>
                <span>持仓浮盈</span>
                <b className={(floatingReturnPct ?? 0) >= 0 ? "positive" : "negative"}>{floatingReturnPct == null ? "-" : `${floatingReturnPct.toFixed(2)}%`}</b>
              </div>
              <div>
                <span>策略分</span>
                <b>{latestSignal ? latestSignal.score.toFixed(1) : "-"}</b>
              </div>
              <div>
                <span>数据状态</span>
                <b>{detail.data_quality.level}</b>
              </div>
            </div>

            <div className="stockActionBar">
              <div>
                <strong>{isTracked ? "已加入追踪" : "看好该股？"}</strong>
                <span>{isTracked ? "该标的已经在自选股与提醒列表中。" : "按当前价格自动生成买入、目标、止损和止盈参考线。"}</span>
              </div>
              <button className="primaryButton inlineButton" type="button" onClick={addCurrentToWatch} disabled={watchAdding || isTracked}>
                <Star size={15} />
                <span>{isTracked ? "已追踪" : watchAdding ? "加入中" : "加入追踪"}</span>
              </button>
            </div>

            <div className="stockSummary">
              <SummaryCell label="最新价" value={quote.last_price.toFixed(2)} strong tone={quote.change_pct >= 0 ? "positive" : "negative"} />
              <SummaryCell label="涨跌幅" value={`${quote.change_pct.toFixed(2)}%`} tone={quote.change_pct >= 0 ? "positive" : "negative"} />
              <SummaryCell label="成交额" value={`${(quote.amount / 100000000).toFixed(2)} 亿`} />
              <SummaryCell label="换手率" value={formatOptionalNumber(fundamentals?.turnover_rate, "%")} />
              <SummaryCell label="交易板块" value={detail.stock_status.board} />
              <SummaryCell label="数据质量" value={detail.data_quality.level} tone={detail.data_quality.level === "ok" ? undefined : "negative"} />
            </div>

            {linkageEvent && (
              <div className="linkageEventNote">
                <div>
                  <span className="eyebrow">板块联动触发</span>
                  <strong>{linkageEvent.sector} · {triggerTypeLabel(linkageEvent.trigger_type)}</strong>
                  <span>{formatShortDateTime(linkageEvent.triggered_at)} 触发，候选均值 {formatOptionalPct(linkageEvent.followup_metrics?.avg_candidate_return_pct)}，正向率 {formatOptionalPct(linkageEvent.followup_metrics?.positive_candidate_rate_pct)}。</span>
                </div>
                <mark>图中已标记触发区域</mark>
              </div>
            )}

            {holdingPosition && (
              <div className="holdingInsight">
                <div>
                  <span className="eyebrow">当前持仓买入位置</span>
                  <h3>{holdingPosition.name || detail.name || holdingPosition.symbol}</h3>
                </div>
                <div className="holdingStats">
                  <SummaryCell label="买入价/成本" value={holdingPosition.entry_price.toFixed(2)} strong />
                  <SummaryCell label="持仓数量" value={`${holdingPosition.quantity} 股`} />
                  <SummaryCell label="买入时间" value={formatShortDateTime(holdingPosition.entry_at)} />
                  <SummaryCell
                    label="浮动收益"
                    value={floatingReturnPct == null ? "-" : `${floatingReturnPct.toFixed(2)}%`}
                    tone={(floatingReturnPct ?? 0) >= 0 ? "positive" : "negative"}
                  />
                  <SummaryCell label="绑定策略" value={holdingPosition.strategy_code ?? "未绑定"} />
                </div>
                {cashDividendTotal(holdingPosition) != null && (
                  <div className="holdingAdjustmentNote">
                    已按除权除息同步调整持仓股数与成本，累计分红权益 {formatMoney(cashDividendTotal(holdingPosition))} 已计入浮动收益口径。
                  </div>
                )}
                <form className="manualSellBox" onSubmit={submitManualSell}>
                  <div>
                    <strong>主动卖出确认</strong>
                    <span>错过系统提醒或需要人工止损时，可按实际成交价记录卖出。</span>
                  </div>
                  <label>
                    <span>卖出价</span>
                    <input value={sellPrice} onChange={(event) => setSellPrice(event.target.value)} inputMode="decimal" />
                  </label>
                  <label>
                    <span>数量</span>
                    <input value={sellQuantity} onChange={(event) => setSellQuantity(event.target.value)} inputMode="numeric" />
                  </label>
                  <label className="manualSellNote">
                    <span>备注</span>
                    <input value={sellNotes} onChange={(event) => setSellNotes(event.target.value)} placeholder="如：功能调整错过卖点，手动止损" />
                  </label>
                  <button className="dangerButton" type="submit" disabled={sellSaving}>
                    <Check size={15} />
                    <span>{sellSaving ? "记录中" : "确认卖出"}</span>
                  </button>
                  {sellMessage && <p>{sellMessage}</p>}
                </form>
              </div>
            )}

            <div className="chartGrid">
              <ChartPanel title="分时走势">
                <IntradayChart points={detail.intraday} quote={quote} holdingPosition={holdingPosition} linkageEvent={linkageEvent} />
              </ChartPanel>
              <ChartPanel title="日 K 走势">
                <KLineChart bars={detail.daily_bars} holdingPosition={holdingPosition} />
              </ChartPanel>
            </div>

            <div className="detailGrid">
              <section className="detailBlock">
                <h3>基本要素</h3>
                <dl className="factorGrid">
                  <Factor label="行业" value={detail.fundamentals.industry || "暂无"} />
                  <Factor label="地区" value={detail.fundamentals.region || "暂无"} />
                  <Factor label="总市值" value={formatOptionalNumber(detail.fundamentals.market_cap, " 亿")} />
                  <Factor label="流通市值" value={formatOptionalNumber(detail.fundamentals.circulating_market_cap, " 亿")} />
                  <Factor label="PE TTM" value={formatOptionalNumber(detail.fundamentals.pe_ttm, "")} />
                  <Factor label="PB" value={formatOptionalNumber(detail.fundamentals.pb, "")} />
                  <Factor label="ROE" value={formatOptionalNumber(detail.fundamentals.roe, "%")} />
                  <Factor label="60日高点" value={formatOptionalNumber(detail.fundamentals.high_60d, "")} />
                  <Factor label="60日低点" value={formatOptionalNumber(detail.fundamentals.low_60d, "")} />
                  <Factor label="来源" value={detail.fundamentals.data_source || "暂无"} />
                </dl>
                {(detail.fundamentals.concepts ?? []).length > 0 && (
                  <div className="tagRow compactTags">
                    {(detail.fundamentals.concepts ?? []).slice(0, 6).map((concept) => (
                      <span key={concept}>{concept}</span>
                    ))}
                  </div>
                )}
              </section>

              <section className="detailBlock">
                <h3>交易规则校验</h3>
                <dl className="factorGrid">
                  <Factor label="交易单位" value={`${detail.market_rules.lot_size} 股`} />
                  <Factor label="最小报价" value={detail.market_rules.price_tick.toFixed(2)} />
                  <Factor label="涨跌幅限制" value={`${detail.market_rules.limit_up_down_pct ?? "-"}%`} />
                  <Factor label="T+1" value={detail.market_rules.t_plus_one ? "是" : "否"} />
                  <Factor label="股票状态" value={stockStatusLabel(detail.stock_status)} />
                  <Factor label="交易时段" value={detail.market_rules.is_trade_time ? "是" : "否"} />
                </dl>
                <ul className="riskList">
                  {[...detail.market_rules.notes, ...detail.market_rules.warnings, detail.data_quality.message].map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </section>

              <section className="detailBlock wideBlock">
                <h3>策略与风险</h3>
                <div className="strategyRiskGrid">
                  {latestSignal ? (
                    <div className="detailSignal">
                      <div className="signalHead">
                        <span>{latestSignal.strategy_code}</span>
                        <mark>{actionLabel[latestSignal.action] ?? latestSignal.action}</mark>
                      </div>
                      <div className="scoreBar">
                        <span style={{ width: `${Math.min(100, latestSignal.score)}%` }} />
                      </div>
                      <p>{latestSignal.reason}</p>
                      <small>策略分 {latestSignal.score.toFixed(1)} · 置信度 {(latestSignal.confidence * 100).toFixed(0)}% · {signalDataStatus(latestSignal)}</small>
                    </div>
                  ) : (
                    <div className="empty">暂无该股策略信号。</div>
                  )}
                  <div className="riskPanel">
                    <strong>风险提示</strong>
                    <ul className="riskList compactRiskList">
                      {detail.risk_notes.map((note) => (
                        <li key={note}>{note}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </section>
            </div>
          </>
        ) : (
          <div className="modalLoading">暂无该股详情。</div>
        )}
      </section>
    </div>
  );
}

function SummaryCell({ label, value, strong = false, tone }: { label: string; value: string; strong?: boolean; tone?: "positive" | "negative" }) {
  return (
    <div className="summaryCell">
      <span>{label}</span>
      <strong className={tone}>{strong ? value : value}</strong>
    </div>
  );
}

function ChartPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="chartPanel">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function MiniEquityChart({ points }: { points: Array<{ date: string; value: number }> }) {
  return (
    <div className="miniBacktestChart">
      <BacktestLineChart points={points} mode="equity" compact />
    </div>
  );
}

function BacktestLineChart({
  points,
  mode,
  compact = false
}: {
  points: Array<{ date: string; value: number }>;
  mode: "equity" | "drawdown";
  compact?: boolean;
}) {
  const width = compact ? 360 : 620;
  const height = compact ? 92 : 250;
  const pad = compact ? 10 : 26;
  const values = points.length ? points.map((point) => point.value) : [0, 1];
  const min = mode === "drawdown" ? Math.min(...values, -1) : Math.min(...values);
  const max = mode === "drawdown" ? Math.max(...values, 0) : Math.max(...values);
  const range = Math.max(0.01, max - min);
  const x = (idx: number) => pad + (idx / Math.max(1, points.length - 1)) * (width - pad * 2);
  const y = (value: number) => height - pad - ((value - min) / range) * (height - pad * 2);
  const line = points.map((point, idx) => `${x(idx)},${y(point.value)}`).join(" ");
  const latest = points.at(-1);
  return (
    <svg className={compact ? "chartSvg mini" : "chartSvg"} viewBox={`0 0 ${width} ${height}`} role="img">
      <line x1={pad} y1={mode === "drawdown" ? y(0) : height - pad} x2={width - pad} y2={mode === "drawdown" ? y(0) : height - pad} className="axisLine" />
      {line && <polyline points={line} className={mode === "drawdown" ? "backtestLine drawdown" : "backtestLine equity"} />}
      {!compact && (
        <>
          <text x={pad} y={18}>{mode === "drawdown" ? `${max.toFixed(1)}%` : max.toFixed(2)}</text>
          <text x={pad} y={height - 8}>{mode === "drawdown" ? `${min.toFixed(1)}%` : min.toFixed(2)}</text>
          <text x={width - 112} y={height - 8}>{latest?.date ?? ""}</text>
        </>
      )}
    </svg>
  );
}

function IntradayChart({
  points,
  quote,
  holdingPosition,
  linkageEvent
}: {
  points: IntradayPoint[];
  quote: MarketQuote;
  holdingPosition?: PortfolioPosition | null;
  linkageEvent?: SectorLinkageHistoryItem | null;
}) {
  if (points.length === 0) {
    return <div className="chartEmpty">暂无真实分时数据</div>;
  }
  const width = 620;
  const height = 250;
  const pad = 24;
  const positionPrice = holdingPosition?.entry_price;
  const prices = points.flatMap((point) => [point.price, point.avg_price]);
  if (positionPrice && positionPrice > 0) prices.push(positionPrice);
  const min = Math.min(...prices, quote.last_price);
  const max = Math.max(...prices, quote.last_price);
  const range = Math.max(0.01, max - min);
  const x = (idx: number) => pad + (idx / Math.max(1, points.length - 1)) * (width - pad * 2);
  const y = (value: number) => height - pad - ((value - min) / range) * (height - pad * 2);
  const line = points.map((point, idx) => `${x(idx)},${y(point.price)}`).join(" ");
  const avg = points.map((point, idx) => `${x(idx)},${y(point.avg_price)}`).join(" ");
  const triggerIndex = linkageEvent ? closestIntradayIndex(points, linkageEvent.triggered_at) : -1;
  const triggerX = triggerIndex >= 0 ? x(triggerIndex) : null;
  const triggerPrice = triggerIndex >= 0 ? points[triggerIndex].price : linkageEvent?.last_price;
  const triggerY = triggerPrice ? y(triggerPrice) : null;
  const windowHalfWidth = triggerIndex >= 0 ? Math.max(6, (width - pad * 2) / Math.max(1, points.length - 1) * 3) : 0;
  return (
    <svg className="chartSvg" viewBox={`0 0 ${width} ${height}`} role="img">
      <line x1={pad} y1={height / 2} x2={width - pad} y2={height / 2} className="axisLine" />
      {triggerX !== null && (
        <>
          <rect
            x={Math.max(pad, triggerX - windowHalfWidth)}
            y={pad}
            width={Math.min(width - pad, triggerX + windowHalfWidth) - Math.max(pad, triggerX - windowHalfWidth)}
            height={height - pad * 2}
            className="triggerBand"
          />
          <line x1={triggerX} y1={pad} x2={triggerX} y2={height - pad} className="triggerLine" />
          {triggerY !== null && <circle cx={triggerX} cy={triggerY} r={4} className="triggerDot" />}
          <text x={Math.min(width - 132, triggerX + 8)} y={Math.max(16, (triggerY ?? pad) - 8)} className="triggerText">触发 {formatCnTime(linkageEvent?.triggered_at ?? "")}</text>
        </>
      )}
      {positionPrice && positionPrice > 0 && (
        <>
          <line x1={pad} y1={y(positionPrice)} x2={width - pad} y2={y(positionPrice)} className="entryLine" />
          <text x={width - 124} y={Math.max(16, y(positionPrice) - 6)} className="entryText">买入 {positionPrice.toFixed(2)}</text>
        </>
      )}
      <polyline points={avg} className="avgLine" />
      <polyline points={line} className={quote.change_pct >= 0 ? "priceLine up" : "priceLine down"} />
      <text x={pad} y={18}>{max.toFixed(2)}</text>
      <text x={pad} y={height - 8}>{min.toFixed(2)}</text>
      <text x={width - 76} y={height - 8}>{points.at(-1)?.time}</text>
    </svg>
  );
}

function KLineChart({ bars, holdingPosition }: { bars: DailyBar[]; holdingPosition?: PortfolioPosition | null }) {
  if (bars.length === 0) {
    return <div className="chartEmpty">暂无真实日 K 数据</div>;
  }
  const width = 620;
  const height = 250;
  const pad = 24;
  const visible = bars.slice(-48);
  const positionPrice = holdingPosition?.entry_price;
  const entryDate = holdingPosition?.entry_at.slice(0, 10);
  const entryIndex = entryDate ? visible.findIndex((bar) => bar.trade_date >= entryDate) : -1;
  const prices = visible.flatMap((bar) => [bar.high, bar.low]);
  if (positionPrice && positionPrice > 0) prices.push(positionPrice);
  if (prices.length === 0) {
    return <div className="chartEmpty">暂无真实日 K 数据</div>;
  }
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = Math.max(0.01, max - min);
  const step = (width - pad * 2) / Math.max(1, visible.length);
  const candleWidth = Math.max(4, step * 0.56);
  const y = (value: number) => height - pad - ((value - min) / range) * (height - pad * 2);
  const entryX = entryIndex >= 0 ? pad + entryIndex * step + step / 2 : null;
  return (
    <svg className="chartSvg" viewBox={`0 0 ${width} ${height}`} role="img">
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} className="axisLine" />
      {positionPrice && positionPrice > 0 && (
        <>
          <line x1={pad} y1={y(positionPrice)} x2={width - pad} y2={y(positionPrice)} className="entryLine" />
          <text x={width - 126} y={Math.max(16, y(positionPrice) - 6)} className="entryText">买入 {positionPrice.toFixed(2)}</text>
        </>
      )}
      {entryX !== null && (
        <line x1={entryX} y1={pad} x2={entryX} y2={height - pad} className="entryDateLine" />
      )}
      {visible.map((bar, idx) => {
        const cx = pad + idx * step + step / 2;
        const rising = bar.close >= bar.open;
        const top = y(Math.max(bar.open, bar.close));
        const bodyHeight = Math.max(2, Math.abs(y(bar.open) - y(bar.close)));
        return (
          <g key={`${bar.trade_date}-${idx}`} className={rising ? "candle rising" : "candle falling"}>
            <line x1={cx} y1={y(bar.high)} x2={cx} y2={y(bar.low)} />
            <rect x={cx - candleWidth / 2} y={top} width={candleWidth} height={bodyHeight} />
          </g>
        );
      })}
      {entryX !== null && positionPrice && positionPrice > 0 && (
        <circle cx={entryX} cy={y(positionPrice)} r="4" className="entryDot" />
      )}
      <text x={pad} y={18}>{max.toFixed(2)}</text>
      <text x={pad} y={height - 8}>{min.toFixed(2)}</text>
    </svg>
  );
}

function Factor({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}
