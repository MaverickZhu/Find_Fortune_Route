from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import StockStatus


class StockStatusService:
    def ensure_status(self, db: Session, symbol: str, name: str = "") -> StockStatus:
        status = db.execute(select(StockStatus).where(StockStatus.symbol == symbol)).scalar_one_or_none()
        inferred = self.infer(symbol, name)
        if status is None:
            status = StockStatus(symbol=symbol, name=name, **inferred)
            db.add(status)
        else:
            status.name = name or status.name
            status.board = inferred["board"]
            status.limit_up_down_pct = inferred["limit_up_down_pct"]
            status.is_st = inferred["is_st"]
            status.source = inferred["source"]
            for key, value in inferred.items():
                if getattr(status, key) in (None, "", False):
                    setattr(status, key, value)
        db.commit()
        db.refresh(status)
        return status

    def upsert_from_quote_batch(self, db: Session, quotes: list[dict[str, Any]]) -> None:
        for quote in quotes:
            status = self.ensure_status(db, str(quote.get("symbol", "")), str(quote.get("name", "")))
            amount = float(quote.get("amount") or 0)
            volume = float(quote.get("volume") or 0)
            status.is_suspended = amount <= 0 or volume <= 0
            if status.is_suspended:
                status.source = "sina_zero_volume"
        db.commit()

    def get_status(self, db: Session, symbol: str, name: str = "") -> StockStatus:
        return self.ensure_status(db, symbol, name)

    def infer(self, symbol: str, name: str = "") -> dict[str, Any]:
        board, limit = self.board_and_limit(symbol)
        is_st = "ST" in name.upper() or "退" in name
        if is_st:
            limit = 5.0
        return {
            "board": board,
            "is_st": is_st,
            "is_suspended": False,
            "is_new_stock": False,
            "listing_date": None,
            "limit_up_down_pct": limit,
            "source": "symbol_rule_name",
        }

    def board_and_limit(self, symbol: str) -> tuple[str, float]:
        if symbol.startswith(("300", "301")):
            return "创业板", 20.0
        if symbol.startswith(("688", "689")):
            return "科创板", 20.0
        if symbol.startswith(("8", "4")):
            return "北交所", 30.0
        if symbol.startswith(("600", "601", "603", "605")):
            return "上海主板", 10.0
        if symbol.startswith(("000", "001", "002", "003")):
            return "深圳主板", 10.0
        return "A股", 10.0

    def serialize(self, status: StockStatus) -> dict[str, Any]:
        return {
            "symbol": status.symbol,
            "name": status.name,
            "board": status.board,
            "is_st": status.is_st,
            "is_suspended": status.is_suspended,
            "is_new_stock": status.is_new_stock,
            "listing_date": status.listing_date.isoformat() if isinstance(status.listing_date, date) else None,
            "limit_up_down_pct": status.limit_up_down_pct,
            "source": status.source,
            "updated_at": status.updated_at,
        }
