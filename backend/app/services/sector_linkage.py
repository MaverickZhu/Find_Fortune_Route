from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha1
from typing import Any
from zoneinfo import ZoneInfo

import akshare as ak
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.domain import (
    Alert,
    AlertStatus,
    InstitutionalHoldingSnapshot,
    MarketQuote,
    PortfolioPosition,
    PositionStatus,
    SectorLinkageEvent,
    SignalAction,
    Stock,
    StockStatus,
    StrategySignal,
    WatchlistItem,
)
from app.services.market_calendar import MarketCalendarService
from app.services.stock_status import StockStatusService
from app.services.strategy_engine import StrategyEngine

CN_TZ = ZoneInfo("Asia/Shanghai")


class SectorLinkageService:
    _industry_cache: dict[str, Any] = {"date": None, "boards": {}, "symbol_to_boards": {}}
    _theme_rules: list[dict[str, Any]] = [
        {"name": "光通信", "type": "local_concept_theme", "keywords": ["光迅", "长飞", "光纤", "光缆", "中际旭创", "新易盛", "天孚", "剑桥", "太辰光", "源杰", "仕佳", "博创", "联特", "光库", "铭普"]},
        {"name": "PCB", "type": "local_concept_theme", "keywords": ["电路", "沪电", "景旺", "鹏鼎", "胜宏", "生益", "世运", "东山精密", "方正科技", "崇达"]},
        {"name": "半导体", "type": "local_industry_theme", "keywords": ["半导体", "芯片", "微电", "澜起", "兆易", "韦尔", "中芯", "北方华创", "华天科技", "通富", "长电", "寒武纪", "海光", "沪硅"]},
        {"name": "机器人", "type": "local_concept_theme", "keywords": ["机器人", "埃斯顿", "汇川", "绿的谐波", "鸣志", "步科", "拓普", "三花"]},
        {"name": "新能源", "type": "local_industry_theme", "keywords": ["新能源", "锂", "电池", "宁德", "比亚迪", "天齐", "赣锋", "阳光电源", "隆基", "通威"]},
        {"name": "算力与服务器", "type": "local_concept_theme", "keywords": ["工业富联", "浪潮信息", "中科曙光", "紫光", "服务器", "算力", "数据中心"]},
        {"name": "消费电子", "type": "local_industry_theme", "keywords": ["立讯", "歌尔", "蓝思", "领益", "欣旺达", "欧菲", "传音", "安克"]},
        {"name": "低空经济", "type": "local_concept_theme", "keywords": ["低空", "无人机", "宗申", "万丰", "中信海直", "航天彩虹"]},
    ]

    def opening_scan(
        self,
        db: Session,
        force: bool = False,
        create_alerts: bool = False,
        persist_history: bool = True,
        trigger_threshold_pct: float = 3.0,
        sudden_window_minutes: int = 1,
        sudden_threshold_pct: float = 1.2,
        market_excess_threshold_pct: float = 0.8,
        amount_surge_ratio_threshold: float = 1.8,
        market_amount_ratio_threshold: float = 2.5,
        intraday_volume_intensity_threshold: float = 2.0,
        min_crowding_score: float = 80.0,
        min_candidate_score: float = 65.0,
        max_sectors: int = 10,
        limit_per_sector: int = 10,
    ) -> dict[str, Any]:
        now = datetime.now(CN_TZ)
        calendar = MarketCalendarService().today_status(db, now)
        window_active = self._opening_window_active(now, calendar)
        trade_time_active = bool(calendar.get("is_trade_time"))
        if not force and not (window_active or trade_time_active):
            return {
                "active": False,
                "forced": False,
                "window": "09:30-10:00",
                "sudden_window_minutes": sudden_window_minutes,
                "sudden_threshold_pct": sudden_threshold_pct,
                "market_excess_threshold_pct": market_excess_threshold_pct,
                "amount_surge_ratio_threshold": amount_surge_ratio_threshold,
                "market_amount_ratio_threshold": market_amount_ratio_threshold,
                "intraday_volume_intensity_threshold": intraday_volume_intensity_threshold,
                "generated_at": now.isoformat(),
                "trade_date": calendar["trade_date"],
                "message": "当前不在交易时段，板块联动扫描待开盘或盘中突发波动时自动启动。",
                "trigger_count": 0,
                "sector_count": 0,
                "groups": [],
                "history": self.history(db, limit=8),
            }

        quote_map = self._latest_quotes(db)
        if not quote_map:
            return self._empty(
                db,
                now,
                calendar,
                force,
                "暂无真实行情快照。",
                sudden_window_minutes,
                sudden_threshold_pct,
                market_excess_threshold_pct,
                amount_surge_ratio_threshold,
                market_amount_ratio_threshold,
                intraday_volume_intensity_threshold,
            )
        self._ensure_strategy_signals(db, quote_map)
        trigger_rows = self._institutional_triggers(
            db,
            quote_map,
            trigger_threshold_pct=trigger_threshold_pct,
            sudden_window_minutes=sudden_window_minutes,
            sudden_threshold_pct=sudden_threshold_pct,
            market_excess_threshold_pct=market_excess_threshold_pct,
            amount_surge_ratio_threshold=amount_surge_ratio_threshold,
            market_amount_ratio_threshold=market_amount_ratio_threshold,
            intraday_volume_intensity_threshold=intraday_volume_intensity_threshold,
            min_crowding_score=min_crowding_score,
            include_opening=force or window_active,
            include_sudden=force or trade_time_active,
        )
        if not trigger_rows:
            return self._empty(
                db,
                now,
                calendar,
                force,
                "暂无开盘或盘中单位时间明显放量波动的机构抱团触发股。",
                sudden_window_minutes,
                sudden_threshold_pct,
                market_excess_threshold_pct,
                amount_surge_ratio_threshold,
                market_amount_ratio_threshold,
                intraday_volume_intensity_threshold,
            )

        sector_groups = self._sector_groups_for_triggers(db, trigger_rows)
        excluded_symbols = self._acted_symbols(db) | {item["symbol"] for item in trigger_rows}
        candidates = self._candidate_signals(db, quote_map, min_candidate_score, excluded_symbols)
        groups: list[dict[str, Any]] = []
        for sector_key, sector in sector_groups.items():
            member_symbols = sector["members"]
            sector_candidates = [
                item
                for item in candidates
                if item["symbol"] in member_symbols
            ]
            sector_candidates.sort(key=lambda item: (item["score"], item["confidence"], item["amount"]), reverse=True)
            if not sector_candidates:
                continue
            triggers = sector["triggers"]
            direction_value = sum(item["change_pct"] for item in triggers) / len(triggers)
            groups.append(
                {
                    "sector": sector_key,
                    "sector_type": sector["sector_type"],
                    "direction": "up" if direction_value >= 0 else "down",
                    "sector_strength": round(
                        max(abs(item["change_pct"]) * item["crowding_score"] for item in triggers),
                        2,
                    ),
                    "trigger_count": len(triggers),
                    "trigger_symbols": triggers,
                    "candidate_count": len(sector_candidates),
                    "items": sector_candidates[:limit_per_sector],
                    "message": f"{sector_key} 受机构抱团股开盘波动触发，已筛出 {min(len(sector_candidates), limit_per_sector)} 只未动作优质候选。",
                    "trigger_types": sorted({item["trigger_type"] for item in triggers}),
                }
            )
        groups.sort(key=lambda item: (item["sector_strength"], item["candidate_count"]), reverse=True)
        groups = groups[:max_sectors]
        if create_alerts and groups:
            self._create_alerts(db, now, groups)
        if persist_history and groups:
            self._persist_events(db, now, calendar, groups)
        return {
            "active": window_active,
            "forced": force,
            "window": "09:30-10:00",
            "sudden_window_minutes": sudden_window_minutes,
            "generated_at": now.isoformat(),
            "trade_date": calendar["trade_date"],
            "trigger_threshold_pct": trigger_threshold_pct,
            "sudden_threshold_pct": sudden_threshold_pct,
            "market_excess_threshold_pct": market_excess_threshold_pct,
            "amount_surge_ratio_threshold": amount_surge_ratio_threshold,
            "market_amount_ratio_threshold": market_amount_ratio_threshold,
            "intraday_volume_intensity_threshold": intraday_volume_intensity_threshold,
            "min_crowding_score": min_crowding_score,
            "min_candidate_score": min_candidate_score,
            "trigger_count": len(trigger_rows),
            "sector_count": len(groups),
            "groups": groups,
            "history": self.history(db, limit=8),
            "message": "已完成机构抱团股开盘/盘中突发放量波动板块联动扫描。" if groups else "触发股所在板块暂无未动作优质候选。",
        }

    def dashboard_snapshot(self, db: Session) -> dict[str, Any]:
        now = datetime.now(CN_TZ)
        calendar = MarketCalendarService().today_status(db, now)
        window_active = self._opening_window_active(now, calendar)
        trade_time_active = bool(calendar.get("is_trade_time"))
        return {
            "active": window_active or trade_time_active,
            "forced": False,
            "window": "09:30-10:00",
            "sudden_window_minutes": 1,
            "sudden_threshold_pct": 1.2,
            "market_excess_threshold_pct": 0.8,
            "amount_surge_ratio_threshold": 1.8,
            "market_amount_ratio_threshold": 2.5,
            "intraday_volume_intensity_threshold": 2.0,
            "generated_at": now.isoformat(),
            "trade_date": calendar["trade_date"],
            "message": "盘中板块联动扫描由定时任务执行，当前展示最近触发与验证历史。",
            "trigger_count": 0,
            "sector_count": 0,
            "groups": [],
            "history": self.history(db, limit=8),
        }

    def _opening_window_active(self, now: datetime, calendar: dict[str, Any]) -> bool:
        return bool(calendar.get("is_trading_day")) and time(9, 30) <= now.time() <= time(10, 0)

    def _empty(
        self,
        db: Session,
        now: datetime,
        calendar: dict[str, Any],
        force: bool,
        message: str,
        sudden_window_minutes: int = 1,
        sudden_threshold_pct: float = 1.2,
        market_excess_threshold_pct: float = 0.8,
        amount_surge_ratio_threshold: float = 1.8,
        market_amount_ratio_threshold: float = 2.5,
        intraday_volume_intensity_threshold: float = 2.0,
    ) -> dict[str, Any]:
        return {
            "active": False,
            "forced": force,
            "window": "09:30-10:00",
            "sudden_window_minutes": sudden_window_minutes,
            "sudden_threshold_pct": sudden_threshold_pct,
            "market_excess_threshold_pct": market_excess_threshold_pct,
            "amount_surge_ratio_threshold": amount_surge_ratio_threshold,
            "market_amount_ratio_threshold": market_amount_ratio_threshold,
            "intraday_volume_intensity_threshold": intraday_volume_intensity_threshold,
            "generated_at": now.isoformat(),
            "trade_date": calendar["trade_date"],
            "message": message,
            "trigger_count": 0,
            "sector_count": 0,
            "groups": [],
            "history": self.history(db, limit=8),
        }

    def history(self, db: Session, limit: int = 20, trade_date: date | None = None) -> dict[str, Any]:
        self._refresh_event_followups(db)
        query = select(SectorLinkageEvent)
        if trade_date is not None:
            query = query.where(SectorLinkageEvent.trade_date == trade_date)
        rows = (
            db.execute(
                query.order_by(SectorLinkageEvent.triggered_at.desc(), SectorLinkageEvent.id.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
        today = datetime.now(CN_TZ).date()
        today_events = (
            db.execute(select(SectorLinkageEvent).where(SectorLinkageEvent.trade_date == today))
            .scalars()
            .all()
        )
        candidate_returns = [
            float((event.followup_metrics or {}).get("avg_candidate_return_pct"))
            for event in today_events
            if isinstance((event.followup_metrics or {}).get("avg_candidate_return_pct"), (int, float))
        ]
        positive_events = [
            event
            for event in today_events
            if float((event.followup_metrics or {}).get("avg_candidate_return_pct") or 0) > 0
        ]
        return {
            "total_today": len(today_events),
            "positive_today": len(positive_events),
            "avg_candidate_return_pct": round(sum(candidate_returns) / len(candidate_returns), 2) if candidate_returns else None,
            "items": [self._serialize_event(event) for event in rows],
        }

    def _latest_quotes(self, db: Session) -> dict[str, MarketQuote]:
        latest_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.quality == "ok", MarketQuote.last_price > 0, MarketQuote.amount > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote).join(
                    latest_times,
                    and_(MarketQuote.symbol == latest_times.c.symbol, MarketQuote.observed_at == latest_times.c.observed_at),
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: row for row in rows}

    def _ensure_strategy_signals(self, db: Session, quote_map: dict[str, MarketQuote]) -> None:
        latest_signal_at = db.execute(select(func.max(StrategySignal.generated_at))).scalar_one_or_none()
        latest_quote_at = max((quote.observed_at for quote in quote_map.values()), default=None)
        if latest_signal_at is None or (latest_quote_at is not None and latest_signal_at < latest_quote_at):
            engine = StrategyEngine()
            engine.seed_strategies(db)
            engine.generate_signals(db, limit=6000)

    def _institutional_triggers(
        self,
        db: Session,
        quote_map: dict[str, MarketQuote],
        trigger_threshold_pct: float,
        sudden_window_minutes: int,
        sudden_threshold_pct: float,
        market_excess_threshold_pct: float,
        amount_surge_ratio_threshold: float,
        market_amount_ratio_threshold: float,
        intraday_volume_intensity_threshold: float,
        min_crowding_score: float,
        include_opening: bool,
        include_sudden: bool,
    ) -> list[dict[str, Any]]:
        latest_dates = (
            select(
                InstitutionalHoldingSnapshot.symbol,
                func.max(InstitutionalHoldingSnapshot.report_date).label("report_date"),
            )
            .group_by(InstitutionalHoldingSnapshot.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(InstitutionalHoldingSnapshot)
                .join(
                    latest_dates,
                    and_(
                        InstitutionalHoldingSnapshot.symbol == latest_dates.c.symbol,
                        InstitutionalHoldingSnapshot.report_date == latest_dates.c.report_date,
                    ),
                )
                .where(InstitutionalHoldingSnapshot.crowding_score >= min_crowding_score)
                .order_by(InstitutionalHoldingSnapshot.crowding_score.desc())
                .limit(300)
            )
            .scalars()
            .all()
        )
        symbols = [row.symbol for row in rows if row.symbol in quote_map]
        market_move_map = self._recent_move_map(db, quote_map, list(quote_map), sudden_window_minutes) if include_sudden else {}
        move_map = {symbol: market_move_map[symbol] for symbol in symbols if symbol in market_move_map}
        adaptive_threshold = self._adaptive_sudden_threshold(market_move_map, sudden_threshold_pct)
        market_median_move = self._median_move(market_move_map)
        market_median_amount_delta = self._median_amount_delta(market_move_map)
        market_median_volume_delta = self._median_volume_delta(market_move_map)
        triggers: list[dict[str, Any]] = []
        for row in rows:
            quote = quote_map.get(row.symbol)
            if quote is None:
                continue
            move = move_map.get(row.symbol)
            excess_move_pct = float(move["move_pct"]) - market_median_move if move else 0.0
            volume_confirmed, volume_evidence = self._volume_confirmed(
                move,
                market_median_amount_delta,
                market_median_volume_delta,
                amount_surge_ratio_threshold,
                market_amount_ratio_threshold,
                intraday_volume_intensity_threshold,
            )
            opening_hit = include_opening and abs(quote.change_pct) >= trigger_threshold_pct and volume_confirmed
            sudden_hit = (
                bool(move)
                and abs(float(move["move_pct"])) >= adaptive_threshold
                and abs(excess_move_pct) >= market_excess_threshold_pct
                and volume_confirmed
            )
            if not (opening_hit or sudden_hit):
                continue
            trigger_type = "sudden_drop" if sudden_hit and float(move["move_pct"]) < 0 else "sudden_rise" if sudden_hit else "opening_drop" if quote.change_pct < 0 else "opening_rise"
            trigger_move_pct = float(move["move_pct"]) if sudden_hit and move else quote.change_pct
            triggers.append(
                {
                    "symbol": row.symbol,
                    "name": quote.name or row.name or row.symbol,
                    "last_price": quote.last_price,
                    "change_pct": quote.change_pct,
                    "trigger_type": trigger_type,
                    "trigger_move_pct": round(trigger_move_pct, 3),
                    "sudden_window_minutes": sudden_window_minutes if sudden_hit else None,
                    "sudden_threshold_pct": round(adaptive_threshold, 3) if sudden_hit else None,
                    "market_median_move_pct": round(market_median_move, 3) if sudden_hit else None,
                    "market_excess_move_pct": round(excess_move_pct, 3) if sudden_hit else None,
                    "market_excess_threshold_pct": market_excess_threshold_pct if sudden_hit else None,
                    "baseline_price": move.get("baseline_price") if move else None,
                    "baseline_at": move.get("baseline_at") if move else None,
                    "amount_delta": move.get("amount_delta") if move else None,
                    "previous_amount_delta": move.get("previous_amount_delta") if move else None,
                    "amount_surge_ratio": move.get("amount_surge_ratio") if move else None,
                    "volume_delta": move.get("volume_delta") if move else None,
                    "previous_volume_delta": move.get("previous_volume_delta") if move else None,
                    "volume_surge_ratio": move.get("volume_surge_ratio") if move else None,
                    "intraday_amount_intensity": move.get("intraday_amount_intensity") if move else None,
                    "intraday_volume_intensity": move.get("intraday_volume_intensity") if move else None,
                    "market_median_amount_delta": round(market_median_amount_delta, 2) if volume_confirmed else None,
                    "market_median_volume_delta": round(market_median_volume_delta, 2) if volume_confirmed else None,
                    "market_amount_ratio": volume_evidence.get("market_amount_ratio") if volume_confirmed else None,
                    "market_volume_ratio": volume_evidence.get("market_volume_ratio") if volume_confirmed else None,
                    "volume_confirmed": volume_confirmed,
                    "amount": quote.amount,
                    "crowding_score": row.crowding_score,
                    "institution_holding_pct": row.institution_holding_pct,
                    "institution_count": row.institution_count,
                    "fund_count": row.fund_count,
                    "report_date": row.report_date.isoformat(),
                    "quote_source": quote.source,
                    "observed_at": quote.observed_at.isoformat(),
                }
            )
        return sorted(triggers, key=lambda item: (abs(item["trigger_move_pct"]) * item["crowding_score"], item["amount"]), reverse=True)

    def _recent_move_map(
        self,
        db: Session,
        quote_map: dict[str, MarketQuote],
        symbols: list[str],
        window_minutes: int,
    ) -> dict[str, dict[str, Any]]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol in quote_map})
        if not unique_symbols:
            return {}
        cutoff_rows = [
            (symbol, quote_map[symbol].observed_at - timedelta(minutes=window_minutes))
            for symbol in unique_symbols
        ]
        global_cutoff = min(cutoff for _, cutoff in cutoff_rows)
        rows = (
            db.execute(
                select(MarketQuote)
                .where(
                    MarketQuote.symbol.in_(unique_symbols),
                    MarketQuote.quality == "ok",
                    MarketQuote.last_price > 0,
                    MarketQuote.observed_at >= global_cutoff - timedelta(minutes=max(2, window_minutes)),
                )
                .order_by(MarketQuote.symbol.asc(), MarketQuote.observed_at.asc())
            )
            .scalars()
            .all()
        )
        by_symbol: dict[str, list[MarketQuote]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)
        moves: dict[str, dict[str, Any]] = {}
        for symbol in unique_symbols:
            latest = quote_map[symbol]
            cutoff = latest.observed_at - timedelta(minutes=window_minutes)
            candidates = [row for row in by_symbol.get(symbol, []) if row.observed_at <= cutoff and row.last_price > 0]
            if not candidates:
                continue
            baseline = candidates[-1]
            previous_cutoff = baseline.observed_at - timedelta(minutes=window_minutes)
            previous_candidates = [
                row
                for row in by_symbol.get(symbol, [])
                if row.observed_at <= previous_cutoff and row.amount >= 0
            ]
            previous_baseline = previous_candidates[-1] if previous_candidates else None
            move_pct = (latest.last_price / baseline.last_price - 1) * 100
            amount_delta = max(0.0, latest.amount - baseline.amount)
            volume_delta = max(0.0, latest.volume - baseline.volume)
            previous_amount_delta = max(0.0, baseline.amount - previous_baseline.amount) if previous_baseline else None
            previous_volume_delta = max(0.0, baseline.volume - previous_baseline.volume) if previous_baseline else None
            amount_surge_ratio = amount_delta / previous_amount_delta if previous_amount_delta and previous_amount_delta > 0 else None
            volume_surge_ratio = volume_delta / previous_volume_delta if previous_volume_delta and previous_volume_delta > 0 else None
            elapsed_minutes = self._elapsed_trade_minutes(latest.observed_at)
            avg_amount_per_minute = latest.amount / elapsed_minutes if latest.amount > 0 else 0.0
            avg_volume_per_minute = latest.volume / elapsed_minutes if latest.volume > 0 else 0.0
            intraday_amount_intensity = amount_delta / avg_amount_per_minute if avg_amount_per_minute > 0 else None
            intraday_volume_intensity = volume_delta / avg_volume_per_minute if avg_volume_per_minute > 0 else None
            moves[symbol] = {
                "move_pct": round(move_pct, 3),
                "baseline_price": baseline.last_price,
                "baseline_at": baseline.observed_at.isoformat(),
                "amount_delta": round(amount_delta, 2),
                "previous_amount_delta": round(previous_amount_delta, 2) if previous_amount_delta is not None else None,
                "previous_baseline_at": previous_baseline.observed_at.isoformat() if previous_baseline else None,
                "amount_surge_ratio": round(amount_surge_ratio, 3) if amount_surge_ratio is not None else None,
                "volume_delta": round(volume_delta, 2),
                "previous_volume_delta": round(previous_volume_delta, 2) if previous_volume_delta is not None else None,
                "volume_surge_ratio": round(volume_surge_ratio, 3) if volume_surge_ratio is not None else None,
                "intraday_amount_intensity": round(intraday_amount_intensity, 3) if intraday_amount_intensity is not None else None,
                "intraday_volume_intensity": round(intraday_volume_intensity, 3) if intraday_volume_intensity is not None else None,
            }
        return moves

    def _adaptive_sudden_threshold(self, move_map: dict[str, dict[str, Any]], base_threshold: float) -> float:
        values = sorted(abs(float(item.get("move_pct") or 0)) for item in move_map.values())
        if len(values) < 12:
            return base_threshold
        median = values[len(values) // 2]
        deviations = sorted(abs(value - median) for value in values)
        mad = deviations[len(deviations) // 2]
        robust_threshold = median + 3.5 * (mad or 0)
        return max(base_threshold, min(base_threshold * 1.8, abs(robust_threshold)))

    def _median_move(self, move_map: dict[str, dict[str, Any]]) -> float:
        values = sorted(float(item.get("move_pct") or 0) for item in move_map.values())
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    def _median_amount_delta(self, move_map: dict[str, dict[str, Any]]) -> float:
        values = sorted(float(item.get("amount_delta") or 0) for item in move_map.values() if float(item.get("amount_delta") or 0) > 0)
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    def _median_volume_delta(self, move_map: dict[str, dict[str, Any]]) -> float:
        values = sorted(float(item.get("volume_delta") or 0) for item in move_map.values() if float(item.get("volume_delta") or 0) > 0)
        if not values:
            return 0.0
        middle = len(values) // 2
        if len(values) % 2:
            return values[middle]
        return (values[middle - 1] + values[middle]) / 2

    def _elapsed_trade_minutes(self, observed_at: datetime) -> int:
        observed_cn = observed_at
        if observed_cn.tzinfo is None:
            observed_cn = observed_cn.replace(tzinfo=timezone.utc)
        observed_cn = observed_cn.astimezone(CN_TZ)
        current = observed_cn.time()
        morning_start = time(9, 30)
        morning_end = time(11, 30)
        afternoon_start = time(13, 0)
        afternoon_end = time(15, 0)
        if current <= morning_start:
            return 1
        if current <= morning_end:
            return max(1, int((datetime.combine(observed_cn.date(), current) - datetime.combine(observed_cn.date(), morning_start)).total_seconds() // 60))
        if current <= afternoon_start:
            return 120
        if current <= afternoon_end:
            return max(121, 120 + int((datetime.combine(observed_cn.date(), current) - datetime.combine(observed_cn.date(), afternoon_start)).total_seconds() // 60))
        return 240

    def _volume_confirmed(
        self,
        move: dict[str, Any] | None,
        market_median_amount_delta: float,
        market_median_volume_delta: float,
        amount_surge_ratio_threshold: float,
        market_amount_ratio_threshold: float,
        intraday_volume_intensity_threshold: float,
    ) -> tuple[bool, dict[str, float | None]]:
        if not move:
            return False, {}
        amount_delta = float(move.get("amount_delta") or 0)
        volume_delta = float(move.get("volume_delta") or 0)
        if amount_delta <= 0 and volume_delta <= 0:
            return False, {}
        amount_surge_ratio = move.get("amount_surge_ratio")
        volume_surge_ratio = move.get("volume_surge_ratio")
        intraday_amount_intensity = move.get("intraday_amount_intensity")
        intraday_volume_intensity = move.get("intraday_volume_intensity")
        market_amount_ratio = amount_delta / market_median_amount_delta if market_median_amount_delta > 0 else None
        market_volume_ratio = volume_delta / market_median_volume_delta if market_median_volume_delta > 0 else None
        self_surge_ok = (
            (amount_surge_ratio is not None and float(amount_surge_ratio) >= amount_surge_ratio_threshold)
            or (volume_surge_ratio is not None and float(volume_surge_ratio) >= amount_surge_ratio_threshold)
        )
        market_surge_ok = (
            (market_amount_ratio is not None and market_amount_ratio >= market_amount_ratio_threshold)
            or (market_volume_ratio is not None and market_volume_ratio >= market_amount_ratio_threshold)
        )
        intraday_intensity_ok = (
            (intraday_amount_intensity is not None and float(intraday_amount_intensity) >= intraday_volume_intensity_threshold)
            or (intraday_volume_intensity is not None and float(intraday_volume_intensity) >= intraday_volume_intensity_threshold)
        )
        evidence = {
            "market_amount_ratio": round(market_amount_ratio, 3) if market_amount_ratio is not None else None,
            "market_volume_ratio": round(market_volume_ratio, 3) if market_volume_ratio is not None else None,
        }
        return self_surge_ok or market_surge_ok or intraday_intensity_ok, evidence

    def _sector_groups_for_triggers(self, db: Session, triggers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for trigger in triggers:
            sectors = self._sectors_for_symbol(db, trigger["symbol"], trigger["name"])
            for sector in sectors:
                key = sector["name"]
                bucket = groups.setdefault(
                    key,
                    {
                        "sector_type": sector["type"],
                        "members": set(sector["members"]),
                        "triggers": [],
                    },
                )
                bucket["members"].update(sector["members"])
                bucket["triggers"].append(trigger)
        return groups

    def _sectors_for_symbol(self, db: Session, symbol: str, name: str) -> list[dict[str, Any]]:
        industry_sectors = self._akshare_industry_memberships().get(symbol, [])
        if industry_sectors:
            return industry_sectors[:2]
        stock = db.get(Stock, symbol)
        if stock and stock.industry and not self._is_trading_board_name(stock.industry):
            members = {
                row[0]
                for row in db.execute(select(Stock.symbol).where(Stock.industry == stock.industry)).all()
            }
            if members:
                return [{"name": stock.industry, "type": "stock_industry", "members": members}]
        theme = self._local_theme_for_names([name])
        if theme:
            members = self._local_theme_members(db, theme)
            if members:
                return [{"name": theme["name"], "type": theme["type"], "members": members}]
        return []

    def _akshare_industry_memberships(self) -> dict[str, list[dict[str, Any]]]:
        today = datetime.now(CN_TZ).date().isoformat()
        if self._industry_cache.get("date") == today:
            return self._industry_cache["symbol_to_boards"]
        symbol_to_boards: dict[str, list[dict[str, Any]]] = {}
        try:
            names_df = ak.stock_board_industry_name_em()
            board_names = [str(item) for item in names_df["板块名称"].dropna().tolist()[:120]]
            for board_name in board_names:
                try:
                    cons = ak.stock_board_industry_cons_em(symbol=board_name)
                except Exception:
                    continue
                code_col = "代码" if "代码" in cons.columns else "股票代码"
                if code_col not in cons.columns:
                    continue
                members = {str(item).zfill(6) for item in cons[code_col].dropna().tolist()}
                sector = {"name": board_name, "type": "eastmoney_industry", "members": members}
                for member in members:
                    symbol_to_boards.setdefault(member, []).append(sector)
            try:
                concept_df = ak.stock_board_concept_name_em()
                concept_names = [str(item) for item in concept_df["板块名称"].dropna().tolist()[:180]]
            except Exception:
                concept_names = []
            for concept_name in concept_names:
                try:
                    cons = ak.stock_board_concept_cons_em(symbol=concept_name)
                except Exception:
                    continue
                code_col = "代码" if "代码" in cons.columns else "股票代码"
                if code_col not in cons.columns:
                    continue
                members = {str(item).zfill(6) for item in cons[code_col].dropna().tolist()}
                sector = {"name": concept_name, "type": "eastmoney_concept", "members": members}
                for member in members:
                    symbol_to_boards.setdefault(member, []).append(sector)
        except Exception:
            symbol_to_boards = {}
        self._industry_cache = {"date": today, "symbol_to_boards": symbol_to_boards}
        return symbol_to_boards

    def _local_theme_for_names(self, names: list[str]) -> dict[str, Any] | None:
        text = " ".join(name for name in names if name)
        if not text:
            return None
        matches: list[tuple[int, dict[str, Any]]] = []
        for rule in self._theme_rules:
            score = sum(1 for keyword in rule["keywords"] if keyword in text)
            if score > 0:
                matches.append((score, rule))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        return matches[0][1]

    def _local_theme_members(self, db: Session, theme: dict[str, Any]) -> set[str]:
        keywords = [str(keyword) for keyword in theme.get("keywords", []) if keyword]
        if not keywords:
            return set()
        rows = db.execute(select(Stock.symbol, Stock.name)).all()
        return {
            symbol
            for symbol, name in rows
            if any(keyword in (name or "") for keyword in keywords)
        }

    def _is_trading_board_name(self, value: str | None) -> bool:
        return value in {"深圳主板", "上海主板", "创业板", "科创板", "北交所", "A股"}

    def _acted_symbols(self, db: Session) -> set[str]:
        watch_symbols = {row[0] for row in db.execute(select(WatchlistItem.symbol)).all()}
        open_position_symbols = {
            row[0]
            for row in db.execute(
                select(PortfolioPosition.symbol).where(PortfolioPosition.status == PositionStatus.open)
            ).all()
        }
        active_alert_symbols = {
            row[0]
            for row in db.execute(
                select(Alert.symbol).where(Alert.status == AlertStatus.triggered)
            ).all()
        }
        return watch_symbols | open_position_symbols | active_alert_symbols

    def _candidate_signals(
        self,
        db: Session,
        quote_map: dict[str, MarketQuote],
        min_candidate_score: float,
        excluded_symbols: set[str],
    ) -> list[dict[str, Any]]:
        latest_generated_at = db.execute(select(func.max(StrategySignal.generated_at))).scalar_one_or_none()
        if latest_generated_at is None:
            return []
        rows = (
            db.execute(
                select(StrategySignal)
                .where(
                    StrategySignal.generated_at == latest_generated_at,
                    StrategySignal.score >= min_candidate_score,
                    StrategySignal.action.in_([SignalAction.buy, SignalAction.watch, SignalAction.hold]),
                )
                .order_by(StrategySignal.score.desc(), StrategySignal.confidence.desc())
                .limit(1200)
            )
            .scalars()
            .all()
        )
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if row.symbol in seen or row.symbol in excluded_symbols:
                continue
            quote = quote_map.get(row.symbol)
            if quote is None:
                continue
            seen.add(row.symbol)
            items.append(
                {
                    "symbol": row.symbol,
                    "name": quote.name or row.symbol,
                    "strategy_code": row.strategy_code,
                    "action": row.action.value if hasattr(row.action, "value") else str(row.action),
                    "score": round(row.score, 2),
                    "confidence": round(row.confidence, 3),
                    "reason": row.reason,
                    "last_price": quote.last_price,
                    "change_pct": quote.change_pct,
                    "amount": quote.amount,
                    "quote_source": quote.source,
                    "observed_at": quote.observed_at.isoformat(),
                    "data_status": str((row.evidence or {}).get("data_status") or "real_quote_signal"),
                }
            )
        return items

    def _persist_events(self, db: Session, now: datetime, calendar: dict[str, Any], groups: list[dict[str, Any]]) -> None:
        trade_date = calendar["trade_date"]
        if isinstance(trade_date, str):
            trade_date_value = date.fromisoformat(trade_date)
        else:
            trade_date_value = trade_date
        for group in groups:
            candidates = [
                {
                    "symbol": item["symbol"],
                    "name": item["name"],
                    "strategy_code": item["strategy_code"],
                    "action": item["action"],
                    "score": item["score"],
                    "confidence": item["confidence"],
                    "entry_price": item["last_price"],
                    "change_pct": item["change_pct"],
                    "quote_source": item["quote_source"],
                    "observed_at": item["observed_at"],
                    "reason": item["reason"],
                }
                for item in group.get("items", [])
            ]
            for trigger in group.get("trigger_symbols", []):
                observed_at = self._parse_dt(trigger.get("observed_at")) or now
                raw_key = f"{trade_date_value}:{trigger['symbol']}:{trigger.get('trigger_type')}:{observed_at.isoformat()}"
                event_key = sha1(raw_key.encode("utf-8")).hexdigest()
                existing = (
                    db.execute(
                        select(SectorLinkageEvent)
                        .where(
                            (SectorLinkageEvent.event_key == event_key)
                            | (
                                (SectorLinkageEvent.trade_date == trade_date_value)
                                & (SectorLinkageEvent.symbol == trigger["symbol"])
                                & (SectorLinkageEvent.trigger_type == (trigger.get("trigger_type") or "unknown"))
                                & (SectorLinkageEvent.triggered_at == observed_at.replace(tzinfo=None))
                            )
                        )
                        .order_by(SectorLinkageEvent.id.asc())
                        .limit(1)
                    )
                    .scalar_one_or_none()
                )
                payload = {
                    "group": {
                        "sector_strength": group.get("sector_strength"),
                        "trigger_count": group.get("trigger_count"),
                        "trigger_types": group.get("trigger_types"),
                        "message": group.get("message"),
                    },
                    "trigger": trigger,
                }
                if existing:
                    existing.event_key = event_key
                    existing.sector = group["sector"]
                    existing.sector_type = group.get("sector_type") or ""
                    existing.candidate_count = len(candidates)
                    existing.candidates = candidates
                    existing.trigger_payload = payload
                    existing.scan_at = now.replace(tzinfo=None)
                    existing.updated_at = datetime.utcnow()
                    continue
                db.add(
                    SectorLinkageEvent(
                        event_key=event_key,
                        trade_date=trade_date_value,
                        sector=group["sector"],
                        sector_type=group.get("sector_type") or "",
                        symbol=trigger["symbol"],
                        name=trigger.get("name") or trigger["symbol"],
                        trigger_type=trigger.get("trigger_type") or "unknown",
                        direction=group.get("direction") or "",
                        triggered_at=observed_at.replace(tzinfo=None),
                        scan_at=now.replace(tzinfo=None),
                        last_price=float(trigger.get("last_price") or 0),
                        trigger_move_pct=float(trigger.get("trigger_move_pct") or trigger.get("change_pct") or 0),
                        change_pct=float(trigger.get("change_pct") or 0),
                        crowding_score=float(trigger.get("crowding_score") or 0),
                        volume_confirmed=bool(trigger.get("volume_confirmed")),
                        candidate_count=len(candidates),
                        candidates=candidates,
                        trigger_payload=payload,
                    )
                )
        db.commit()
        self._refresh_event_followups(db)

    def _refresh_event_followups(self, db: Session, limit: int = 500) -> None:
        events = (
            db.execute(
                select(SectorLinkageEvent)
                .where(SectorLinkageEvent.status == "observing")
                .order_by(SectorLinkageEvent.triggered_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        if not events:
            return
        symbols = {
            event.symbol
            for event in events
        }
        for event in events:
            for candidate in event.candidates or []:
                symbol = candidate.get("symbol")
                if symbol:
                    symbols.add(str(symbol))
        quote_map = self._latest_quotes_for_symbols(db, symbols)
        now = datetime.utcnow()
        changed = False
        for event in events:
            repaired_sector = self._repair_event_sector(db, event)
            if repaired_sector:
                changed = True
            trigger_quote = quote_map.get(event.symbol)
            trigger_return = (
                round((trigger_quote.last_price / event.last_price - 1) * 100, 2)
                if trigger_quote and event.last_price > 0
                else None
            )
            candidate_metrics: list[dict[str, Any]] = []
            for candidate in event.candidates or []:
                symbol = str(candidate.get("symbol") or "")
                quote = quote_map.get(symbol)
                entry_price = float(candidate.get("entry_price") or 0)
                if not quote or entry_price <= 0:
                    continue
                candidate_metrics.append(
                    {
                        "symbol": symbol,
                        "name": candidate.get("name") or symbol,
                        "strategy_code": candidate.get("strategy_code"),
                        "entry_price": entry_price,
                        "current_price": quote.last_price,
                        "return_pct": round((quote.last_price / entry_price - 1) * 100, 2),
                    }
                )
            returns = [item["return_pct"] for item in candidate_metrics]
            elapsed_minutes = max(0, int((now - event.triggered_at).total_seconds() // 60))
            followup_metrics = {
                "trigger_current_price": trigger_quote.last_price if trigger_quote else None,
                "trigger_return_pct": trigger_return,
                "candidate_count": len(event.candidates or []),
                "measured_candidate_count": len(candidate_metrics),
                "avg_candidate_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
                "positive_candidate_count": sum(1 for value in returns if value > 0),
                "positive_candidate_rate_pct": round(sum(1 for value in returns if value > 0) / len(returns) * 100, 2) if returns else None,
                "best_candidate": max(candidate_metrics, key=lambda item: item["return_pct"]) if candidate_metrics else None,
                "worst_candidate": min(candidate_metrics, key=lambda item: item["return_pct"]) if candidate_metrics else None,
                "candidate_returns": sorted(candidate_metrics, key=lambda item: item["return_pct"], reverse=True)[:10],
                "elapsed_minutes": elapsed_minutes,
                "updated_at": now.isoformat(),
            }
            if event.followup_metrics != followup_metrics:
                event.followup_metrics = followup_metrics
                event.status = "matured" if elapsed_minutes >= 240 else "observing"
                event.updated_at = now
                changed = True
        if changed:
            db.commit()

    def _repair_event_sector(self, db: Session, event: SectorLinkageEvent) -> bool:
        theme = self._local_theme_for_names([event.name])
        if not theme:
            if not self._is_trading_board_name(event.sector) and event.sector_type != "trading_board_fallback":
                return False
            names = [str(candidate.get("name") or "") for candidate in event.candidates or []]
            theme = self._local_theme_for_names(names)
        if not theme:
            return False
        if event.sector == theme["name"] and event.sector_type == theme["type"]:
            return False
        event.sector = theme["name"]
        event.sector_type = theme["type"]
        event.updated_at = datetime.utcnow()
        return True

    def _latest_quotes_for_symbols(self, db: Session, symbols: set[str]) -> dict[str, MarketQuote]:
        if not symbols:
            return {}
        latest_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.symbol.in_(symbols), MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote).join(
                    latest_times,
                    and_(MarketQuote.symbol == latest_times.c.symbol, MarketQuote.observed_at == latest_times.c.observed_at),
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: row for row in rows}

    def _serialize_event(self, event: SectorLinkageEvent) -> dict[str, Any]:
        return {
            "id": event.id,
            "trade_date": event.trade_date.isoformat(),
            "sector": event.sector,
            "sector_type": event.sector_type,
            "symbol": event.symbol,
            "name": event.name,
            "trigger_type": event.trigger_type,
            "direction": event.direction,
            "triggered_at": event.triggered_at.isoformat(),
            "last_price": event.last_price,
            "trigger_move_pct": event.trigger_move_pct,
            "change_pct": event.change_pct,
            "crowding_score": event.crowding_score,
            "volume_confirmed": event.volume_confirmed,
            "candidate_count": event.candidate_count,
            "candidates": (event.candidates or [])[:10],
            "followup_metrics": event.followup_metrics or {},
            "status": event.status,
        }

    def _parse_dt(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _create_alerts(self, db: Session, now: datetime, groups: list[dict[str, Any]]) -> None:
        trade_date = now.date().isoformat().replace("-", "")
        for group in groups:
            trigger_key = "_".join(sorted(group.get("trigger_types") or ["sector"]))
            digest = sha1(f"{group['sector']}:{trigger_key}".encode("utf-8")).hexdigest()[:8]
            alert_type = f"sector_linkage_{trade_date}_{digest}"
            exists = db.execute(select(Alert).where(Alert.alert_type == alert_type).limit(1)).scalar_one_or_none()
            if exists:
                continue
            trigger = group["trigger_symbols"][0]
            top_items = group["items"][:3]
            primary = top_items[0]
            candidate_names = "、".join(
                f"{item['name']}({item['symbol']}) {float(item.get('score') or 0):.1f}分" for item in top_items
            )
            trigger_names = "、".join(
                f"{item['name']}({item['symbol']})" for item in group.get("trigger_symbols", [])[:3]
            )
            group["primary_candidate"] = primary
            db.add(
                Alert(
                    symbol=primary["symbol"],
                    alert_type=alert_type,
                    message=(
                        f"{group['sector']} 推荐关注/买入候选：{candidate_names}；"
                        f"依据触发股：{trigger_names} {self._alert_trigger_text(group)}。"
                    ),
                    status=AlertStatus.triggered,
                    triggered_at=datetime.utcnow(),
                    payload={
                        "sector_linkage": group,
                        "primary_candidate": primary,
                        "trigger_basis": group.get("trigger_symbols", [])[:5],
                        "source": "institutional_sector_linkage_scan",
                    },
                )
            )
        db.commit()

    def _alert_trigger_text(self, group: dict[str, Any]) -> str:
        trigger_types = set(group.get("trigger_types") or [])
        if "sudden_rise" in trigger_types or "sudden_drop" in trigger_types:
            return "盘中突然波动联动"
        return "开盘联动"
