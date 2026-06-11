from dataclasses import dataclass
from datetime import datetime, time
from math import floor
from typing import Any

from sqlalchemy.orm import Session

from app.services.market_calendar import MarketCalendarService
from app.services.stock_status import StockStatusService


@dataclass(frozen=True)
class StockRuleProfile:
    board: str
    lot_size: int
    price_tick: float
    limit_up_down_pct: float | None
    t_plus_one: bool
    notes: list[str]


class MarketRuleService:
    def profile_for_symbol(self, symbol: str, db: Session | None = None, name: str = "") -> StockRuleProfile:
        if db is not None:
            status = StockStatusService().get_status(db, symbol, name)
            notes = [f"{status.board} 状态来源：{status.source}。"]
            if status.is_st:
                notes.append("该标的被识别为 ST/风险警示，涨跌幅和风险约束需单独处理。")
            if status.is_suspended:
                notes.append("该标的当前状态为停牌，不应生成可交易提示。")
            if status.is_new_stock:
                notes.append("该标的可能处于新股特殊交易期，涨跌幅规则需用真实状态覆盖。")
            return StockRuleProfile(status.board, 100, 0.01, status.limit_up_down_pct, True, notes)
        if symbol.startswith(("300", "301")):
            return StockRuleProfile("创业板", 100, 0.01, 20.0, True, ["创业板通常适用 20% 涨跌幅限制。"])
        if symbol.startswith(("688", "689")):
            return StockRuleProfile("科创板", 100, 0.01, 20.0, True, ["科创板通常适用 20% 涨跌幅限制。"])
        if symbol.startswith(("8", "4")):
            return StockRuleProfile("北交所", 100, 0.01, 30.0, True, ["北交所股票通常适用 30% 涨跌幅限制。"])
        if symbol.startswith(("600", "601", "603", "605")):
            return StockRuleProfile("上海主板", 100, 0.01, 10.0, True, ["上海主板 A 股通常适用 10% 涨跌幅限制。"])
        if symbol.startswith(("000", "001", "002", "003")):
            return StockRuleProfile("深圳主板", 100, 0.01, 10.0, True, ["深圳主板 A 股通常适用 10% 涨跌幅限制。"])
        return StockRuleProfile("A股", 100, 0.01, 10.0, True, ["未识别板块，按普通 A 股规则估算。"])

    def evaluate_quote(self, quote: dict[str, Any], db: Session | None = None, calendar: dict[str, Any] | None = None) -> dict[str, Any]:
        symbol = str(quote.get("symbol", ""))
        name = str(quote.get("name", ""))
        profile = self.profile_for_symbol(symbol, db=db, name=name)
        change_pct = float(quote.get("change_pct") or 0)
        last_price = float(quote.get("last_price") or 0)
        limit = profile.limit_up_down_pct
        near_limit = limit is not None and abs(change_pct) >= max(0, limit - 0.5)
        at_limit_up = limit is not None and change_pct >= limit - 0.05
        at_limit_down = limit is not None and change_pct <= -limit + 0.05
        calendar_status = calendar or (MarketCalendarService().today_status(db) if db is not None else None)
        is_trade_time = bool(calendar_status["is_trade_time"]) if calendar_status else self.is_continuous_or_auction_time(datetime.now())
        is_trading_day = bool(calendar_status["is_trading_day"]) if calendar_status else True

        warnings: list[str] = []
        if not is_trading_day:
            warnings.append("当前不是交易日，提醒只作观察记录。")
        if at_limit_up:
            warnings.append("接近或达到涨停，买入成交可行性需要单独校验。")
        if at_limit_down:
            warnings.append("接近或达到跌停，卖出成交可行性需要单独校验。")
        if near_limit and not (at_limit_up or at_limit_down):
            warnings.append("接近涨跌幅限制，价格波动和流动性风险较高。")
        if not is_trade_time:
            warnings.append("当前非 A 股连续竞价时段，实时成交可行性仅供参考。")
        if last_price <= 0:
            warnings.append("价格缺失或异常，禁止生成可交易提示。")

        calendar_note = (
            "当前交易日历来自真实交易日源。"
            if calendar_status and calendar_status.get("source") != "estimated_runtime"
            else "当前交易日历为估算口径，真实数据接入后应接入交易所日历。"
        )
        return {
            "symbol": symbol,
            "board": profile.board,
            "lot_size": profile.lot_size,
            "price_tick": profile.price_tick,
            "limit_up_down_pct": profile.limit_up_down_pct,
            "t_plus_one": profile.t_plus_one,
            "is_trading_day": is_trading_day,
            "is_trade_time": is_trade_time,
            "calendar": calendar_status,
            "near_limit": near_limit,
            "at_limit_up": at_limit_up,
            "at_limit_down": at_limit_down,
            "can_buy_hint": last_price > 0 and not at_limit_up and is_trading_day,
            "can_sell_hint": last_price > 0 and not at_limit_down and is_trading_day,
            "rounded_price": self.round_price(last_price, profile.price_tick),
            "rounded_lot_quantity": self.round_quantity(1000, profile.lot_size),
            "notes": profile.notes,
            "warnings": warnings,
        }

    def dashboard_summary(self, quotes: list[dict[str, Any]], db: Session | None = None) -> dict[str, Any]:
        calendar_status = MarketCalendarService().today_status(db) if db is not None else None
        evaluations = [self.evaluate_quote(quote, db=db, calendar=calendar_status) for quote in quotes]
        calendar_note = (
            "当前交易日历来自真实交易日源。"
            if calendar_status and calendar_status.get("source") != "estimated_runtime"
            else "当前交易日历为估算口径，真实数据接入后应接入交易所日历。"
        )
        return {
            "trade_calendar_status": calendar_status["source"] if calendar_status else "estimated",
            "trade_date": calendar_status["trade_date"] if calendar_status else datetime.now().date().isoformat(),
            "is_trading_day": calendar_status["is_trading_day"] if calendar_status else True,
            "session_state": calendar_status["session_state"] if calendar_status else "estimated",
            "trade_time": calendar_status["is_trade_time"] if calendar_status else self.is_continuous_or_auction_time(datetime.now()),
            "near_limit_count": sum(1 for item in evaluations if item["near_limit"]),
            "limit_up_count": sum(1 for item in evaluations if item["at_limit_up"]),
            "limit_down_count": sum(1 for item in evaluations if item["at_limit_down"]),
            "blocked_buy_count": sum(1 for item in evaluations if not item["can_buy_hint"]),
            "blocked_sell_count": sum(1 for item in evaluations if not item["can_sell_hint"]),
            "rule_notes": [
                "买入数量按 100 股或其整数倍校验。",
                "涨跌停、停牌、T+1 会影响回测和提醒的可成交性。",
                calendar_note,
                "ST、停牌、新股特殊期已预留股票状态覆盖入口。",
            ],
        }

    def is_continuous_or_auction_time(self, value: datetime) -> bool:
        current = value.time()
        return (
            time(9, 15) <= current <= time(11, 30)
            or time(13, 0) <= current <= time(15, 0)
        )

    def round_price(self, price: float, tick: float = 0.01) -> float:
        if price <= 0:
            return 0
        return round(round(price / tick) * tick, 2)

    def round_quantity(self, quantity: int, lot_size: int = 100) -> int:
        if quantity <= 0:
            return 0
        return floor(quantity / lot_size) * lot_size
