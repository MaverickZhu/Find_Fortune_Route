from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import MarketCalendar


DEFAULT_SESSION = {
    "auction_open": ["09:15", "09:25"],
    "morning": ["09:30", "11:30"],
    "afternoon": ["13:00", "15:00"],
}

CN_TZ = ZoneInfo("Asia/Shanghai")


class MarketCalendarService:
    def seed_real_calendar(self, db: Session, days_back: int = 370, days_forward: int = 370) -> bool:
        today = datetime.now(CN_TZ).date()
        start = today - timedelta(days=days_back)
        end = today + timedelta(days=days_forward)
        try:
            import akshare as ak

            df = ak.tool_trade_date_hist_sina()
            trade_dates = {
                value.date() if isinstance(value, datetime) else datetime.fromisoformat(str(value)).date()
                for value in df["trade_date"].tolist()
            }
        except Exception:
            return False

        existing = {
            row.trade_date: row
            for row in db.execute(
                select(MarketCalendar).where(
                    MarketCalendar.market == "CN_A",
                    MarketCalendar.trade_date >= start,
                    MarketCalendar.trade_date <= end,
                )
            ).scalars().all()
        }
        current = start
        while current <= end:
            is_trading_day = current in trade_dates
            note = None if is_trading_day else "真实交易日历识别为休市"
            row = existing.get(current)
            if row is None:
                db.add(
                    MarketCalendar(
                        market="CN_A",
                        trade_date=current,
                        is_trading_day=is_trading_day,
                        session=DEFAULT_SESSION,
                        source="akshare_sina_trade_calendar",
                        note=note,
                    )
                )
            else:
                row.is_trading_day = is_trading_day
                row.session = DEFAULT_SESSION
                row.source = "akshare_sina_trade_calendar"
                row.note = note
            current += timedelta(days=1)
        db.commit()
        return True

    def seed_estimated_calendar(self, db: Session, days_back: int = 20, days_forward: int = 60) -> None:
        today = date.today()
        start = today - timedelta(days=days_back)
        end = today + timedelta(days=days_forward)
        existing = {
            row[0]
            for row in db.execute(
                select(MarketCalendar.trade_date).where(
                    MarketCalendar.market == "CN_A",
                    MarketCalendar.trade_date >= start,
                    MarketCalendar.trade_date <= end,
                )
            ).all()
        }
        current = start
        while current <= end:
            if current not in existing:
                is_trading_day = current.weekday() < 5
                db.add(
                    MarketCalendar(
                        market="CN_A",
                        trade_date=current,
                        is_trading_day=is_trading_day,
                        session=DEFAULT_SESSION,
                        source="estimated_weekday",
                        note=None if is_trading_day else "周末估算休市",
                    )
                )
            current += timedelta(days=1)
        db.commit()

    def today_status(self, db: Session, value: datetime | None = None) -> dict[str, Any]:
        now = value.astimezone(CN_TZ) if value and value.tzinfo else (value or datetime.now(CN_TZ))
        if not self.seed_real_calendar(db, days_back=30, days_forward=90):
            self.seed_estimated_calendar(db)
        row = db.execute(
            select(MarketCalendar).where(MarketCalendar.market == "CN_A", MarketCalendar.trade_date == now.date())
        ).scalar_one_or_none()
        is_trading_day = bool(row.is_trading_day) if row else now.weekday() < 5
        session_state = self.session_state(now, is_trading_day)
        return {
            "market": "CN_A",
            "trade_date": now.date().isoformat(),
            "is_trading_day": is_trading_day,
            "session_state": session_state,
            "is_trade_time": session_state in {"opening_auction", "morning", "afternoon"},
            "source": row.source if row else "estimated_runtime",
            "note": row.note if row else None,
            "sessions": row.session if row else DEFAULT_SESSION,
        }

    def session_state(self, value: datetime, is_trading_day: bool) -> str:
        if not is_trading_day:
            return "closed"
        current = value.time()
        if time(9, 15) <= current <= time(9, 25):
            return "opening_auction"
        if time(9, 30) <= current <= time(11, 30):
            return "morning"
        if time(13, 0) <= current <= time(15, 0):
            return "afternoon"
        if current < time(9, 15):
            return "pre_market"
        if time(11, 30) < current < time(13, 0):
            return "midday_break"
        return "closed"
