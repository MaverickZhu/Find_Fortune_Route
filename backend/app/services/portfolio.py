from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.domain import MarketQuote, PortfolioPosition, PositionStatus, TradeAction, UserTradeSample
from app.services.market_data import MarketDataProvider


class PortfolioService:
    _corporate_action_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
    _corporate_action_cache_ttl = timedelta(hours=6)

    def apply_trade_sample(self, db: Session, sample: UserTradeSample) -> dict[str, Any]:
        if sample.action == TradeAction.buy:
            position = self._open_or_add_position(db, sample)
            return {"event": "position_opened", "position": self.serialize(position)}
        if sample.action == TradeAction.sell:
            position = self._close_position(db, sample)
            if position is None:
                return {"event": "no_open_position", "position": None}
            if position.status == PositionStatus.open:
                return {"event": "position_reduced", "position": self.serialize(position)}
            return {"event": "position_closed", "position": self.serialize(position)}
        return {"event": "no_position_change", "position": None}

    def summary(self, db: Session) -> dict[str, Any]:
        positions = (
            db.execute(select(PortfolioPosition).order_by(PortfolioPosition.entry_at.desc()).limit(300))
            .scalars()
            .all()
        )
        open_positions = sorted(
            [item for item in positions if item.status == PositionStatus.open],
            key=lambda item: item.entry_at or datetime.min,
            reverse=True,
        )
        closed_positions = sorted(
            [item for item in positions if item.status == PositionStatus.closed],
            key=lambda item: item.exit_at or item.entry_at or datetime.min,
            reverse=True,
        )
        self._apply_corporate_actions(db, open_positions)
        quote_map = self._latest_quotes(db, [item.symbol for item in open_positions + closed_positions])
        quote_map.update(self._fresh_open_quotes(open_positions))
        open_payloads = [self.serialize(item, quote_map.get(item.symbol)) for item in open_positions]
        closed_history_payloads = [self.serialize(item, quote_map.get(item.symbol)) for item in closed_positions[:40]]
        closed_payloads = closed_history_payloads[:8]
        realized = [item.realized_return_pct for item in closed_positions if item.realized_return_pct is not None]
        floating_values = [item.get("floating_pnl") for item in open_payloads if item.get("floating_pnl") is not None]
        market_values = [item.get("current_value") for item in open_payloads if item.get("current_value") is not None]
        return {
            "open_count": len(open_positions),
            "closed_count": len(closed_positions),
            "avg_realized_return_pct": round(sum(realized) / len(realized), 2) if realized else None,
            "total_floating_pnl": round(sum(floating_values), 2) if floating_values else None,
            "total_market_value": round(sum(market_values), 2) if market_values else None,
            "open_positions": open_payloads,
            "recent_closed": closed_payloads,
            "trade_history": closed_history_payloads,
            "strategy_trade_summary": self._strategy_trade_summary(closed_positions),
        }

    def _strategy_trade_summary(self, positions: list[PortfolioPosition]) -> list[dict[str, Any]]:
        grouped: dict[str, list[PortfolioPosition]] = {}
        for position in positions:
            strategy_code = position.strategy_code or "未绑定策略"
            grouped.setdefault(strategy_code, []).append(position)

        payloads: list[dict[str, Any]] = []
        for strategy_code, rows in grouped.items():
            returns = [item.realized_return_pct for item in rows if item.realized_return_pct is not None]
            wins = [value for value in returns if value > 0]
            holding_days = []
            for item in rows:
                if item.entry_at and item.exit_at:
                    holding_days.append(max(0, (item.exit_at - item.entry_at).days))
            total_pnl = [
                (item.exit_price - item.entry_price) * item.quantity
                for item in rows
                if item.exit_price is not None and item.entry_price is not None and item.quantity is not None
            ]
            payloads.append(
                {
                    "strategy_code": strategy_code,
                    "trade_count": len(rows),
                    "win_count": len(wins),
                    "win_rate_pct": round(len(wins) / len(returns) * 100, 2) if returns else None,
                    "avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
                    "best_return_pct": round(max(returns), 2) if returns else None,
                    "worst_return_pct": round(min(returns), 2) if returns else None,
                    "total_realized_pnl": round(sum(total_pnl), 2) if total_pnl else None,
                    "avg_holding_days": round(sum(holding_days) / len(holding_days), 1) if holding_days else None,
                    "latest_exit_at": max((item.exit_at for item in rows if item.exit_at), default=None).isoformat()
                    if any(item.exit_at for item in rows)
                    else None,
                }
            )
        return sorted(
            payloads,
            key=lambda item: (item["trade_count"], item["avg_return_pct"] or -999),
            reverse=True,
        )

    def serialize(self, position: PortfolioPosition, quote: MarketQuote | dict[str, Any] | None = None) -> dict[str, Any]:
        current_price = self._quote_float(quote, "last_price")
        floating_return_pct = None
        floating_pnl = None
        current_value = None
        if position.status == PositionStatus.open and current_price and position.entry_price:
            floating_return_pct = round((current_price / position.entry_price - 1) * 100, 2)
            current_value = round(current_price * position.quantity, 2)
            floating_pnl = round((current_price - position.entry_price) * position.quantity, 2)

        end_at = position.exit_at if position.status == PositionStatus.closed else datetime.utcnow()
        holding_days = None
        if position.entry_at:
            holding_days = max(0, (end_at - position.entry_at).days)

        return {
            "id": position.id,
            "symbol": position.symbol,
            "name": position.name,
            "strategy_code": position.strategy_code,
            "quantity": position.quantity,
            "entry_price": position.entry_price,
            "entry_at": position.entry_at.isoformat() if position.entry_at else None,
            "exit_price": position.exit_price,
            "exit_at": position.exit_at.isoformat() if position.exit_at else None,
            "realized_return_pct": position.realized_return_pct,
            "status": position.status.value,
            "entry_sample_id": position.entry_sample_id,
            "exit_sample_id": position.exit_sample_id,
            "current_price": current_price,
            "current_change_pct": self._quote_float(quote, "change_pct"),
            "current_value": current_value,
            "floating_pnl": floating_pnl,
            "floating_return_pct": floating_return_pct,
            "holding_days": holding_days,
            "latest_quote_at": self._quote_datetime_iso(quote, "observed_at"),
            "quote_source": self._quote_text(quote, "source"),
            "meta": position.meta,
        }

    def _fresh_open_quotes(self, positions: list[PortfolioPosition]) -> dict[str, dict[str, Any]]:
        symbols = sorted({item.symbol for item in positions if item.symbol})
        if not symbols:
            return {}
        try:
            quotes = MarketDataProvider().fetch_realtime_quotes(symbols)
        except Exception:
            return {}
        return {
            str(item["symbol"]): item
            for item in quotes
            if item.get("quality") == "ok" and float(item.get("last_price") or 0) > 0
        }

    def _quote_value(self, quote: MarketQuote | dict[str, Any] | None, key: str) -> Any:
        if quote is None:
            return None
        if isinstance(quote, dict):
            return quote.get(key)
        return getattr(quote, key, None)

    def _quote_float(self, quote: MarketQuote | dict[str, Any] | None, key: str) -> float | None:
        value = self._quote_value(quote, key)
        if value is None:
            return None
        return float(value)

    def _quote_text(self, quote: MarketQuote | dict[str, Any] | None, key: str) -> str | None:
        value = self._quote_value(quote, key)
        return str(value) if value is not None else None

    def _quote_datetime_iso(self, quote: MarketQuote | dict[str, Any] | None, key: str) -> str | None:
        value = self._quote_value(quote, key)
        if value is None:
            return None
        return value.isoformat() if hasattr(value, "isoformat") else str(value)

    def _latest_quotes(self, db: Session, symbols: list[str]) -> dict[str, MarketQuote]:
        unique_symbols = sorted({symbol for symbol in symbols if symbol})
        if not unique_symbols:
            return {}

        latest_times = (
            select(MarketQuote.symbol, func.max(MarketQuote.observed_at).label("observed_at"))
            .where(MarketQuote.symbol.in_(unique_symbols), MarketQuote.quality == "ok", MarketQuote.last_price > 0)
            .group_by(MarketQuote.symbol)
            .subquery()
        )
        rows = (
            db.execute(
                select(MarketQuote).join(
                    latest_times,
                    and_(
                        MarketQuote.symbol == latest_times.c.symbol,
                        MarketQuote.observed_at == latest_times.c.observed_at,
                    ),
                )
            )
            .scalars()
            .all()
        )
        return {row.symbol: row for row in rows}

    def _open_or_add_position(self, db: Session, sample: UserTradeSample) -> PortfolioPosition:
        quantity = sample.quantity or 100
        existing = (
            db.execute(
                select(PortfolioPosition)
                .where(PortfolioPosition.symbol == sample.symbol, PortfolioPosition.status == PositionStatus.open)
                .order_by(PortfolioPosition.entry_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        quote = sample.features.get("quote", {}) if isinstance(sample.features, dict) else {}
        if existing is None:
            position = PortfolioPosition(
                symbol=sample.symbol,
                name=str(quote.get("name") or ""),
                strategy_code=sample.strategy_code,
                quantity=quantity,
                entry_price=sample.decision_price,
                entry_at=sample.decision_at,
                status=PositionStatus.open,
                entry_sample_id=sample.id,
                meta={"entry_features": sample.features},
            )
            db.add(position)
            db.flush()
            sample.status = "position_opened"
            return position

        if not sample.strategy_code and existing.strategy_code:
            sample.strategy_code = existing.strategy_code

        total_quantity = existing.quantity + quantity
        existing.entry_price = round(
            ((existing.entry_price * existing.quantity) + (sample.decision_price * quantity)) / total_quantity,
            4,
        )
        existing.quantity = total_quantity
        existing.meta = {
            **(existing.meta or {}),
            "last_add_sample_id": sample.id,
            "last_add_price": sample.decision_price,
        }
        sample.status = "position_added"
        return existing

    def _close_position(self, db: Session, sample: UserTradeSample) -> PortfolioPosition | None:
        position = (
            db.execute(
                select(PortfolioPosition)
                .where(PortfolioPosition.symbol == sample.symbol, PortfolioPosition.status == PositionStatus.open)
                .order_by(PortfolioPosition.entry_at.asc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if position is None:
            sample.status = "no_open_position"
            return None

        if not sample.strategy_code and position.strategy_code:
            sample.strategy_code = position.strategy_code

        sell_quantity = sample.quantity or position.quantity
        if sell_quantity <= 0:
            sell_quantity = position.quantity
        sell_quantity = min(sell_quantity, position.quantity)
        realized_return_pct = round((sample.decision_price / position.entry_price - 1) * 100, 2)
        if sell_quantity < position.quantity:
            position.quantity -= sell_quantity
            partial_exits = list((position.meta or {}).get("partial_exits", []))
            partial_exits.append(
                {
                    "sample_id": sample.id,
                    "quantity": sell_quantity,
                    "price": sample.decision_price,
                    "return_pct": realized_return_pct,
                    "sold_at": (sample.decision_at or datetime.utcnow()).isoformat(),
                }
            )
            position.meta = {**(position.meta or {}), "partial_exits": partial_exits}
            sample.realized_return_pct = realized_return_pct
            sample.status = "position_reduced"
            return position

        position.exit_price = sample.decision_price
        position.exit_at = sample.decision_at or datetime.utcnow()
        position.realized_return_pct = realized_return_pct
        position.status = PositionStatus.closed
        position.exit_sample_id = sample.id
        position.meta = {**(position.meta or {}), "exit_features": sample.features}
        sample.realized_return_pct = position.realized_return_pct
        sample.status = "position_closed"
        return position

    def _apply_corporate_actions(self, db: Session, positions: list[PortfolioPosition]) -> None:
        changed = False
        today = datetime.utcnow().date()
        for position in positions:
            if not position.entry_at or position.quantity <= 0 or position.entry_price <= 0:
                continue
            meta = dict(position.meta or {})
            applied_keys = {
                str(item.get("key"))
                for item in meta.get("corporate_action_adjustments", [])
                if isinstance(item, dict) and item.get("key")
            }
            for action in self._corporate_actions(position.symbol):
                key = str(action["key"])
                if key in applied_keys:
                    continue
                record_date = action.get("record_date")
                ex_date = action.get("ex_date")
                if not record_date or not ex_date:
                    continue
                if position.entry_at.date() > record_date or ex_date > today:
                    continue
                previous_quantity = int(position.quantity)
                previous_entry_price = float(position.entry_price)
                transfer_ratio = float(action.get("transfer_ratio") or 0)
                cash_per_share = float(action.get("cash_per_share") or 0)
                bonus_quantity = int(round(previous_quantity * transfer_ratio))
                new_quantity = previous_quantity + bonus_quantity
                if new_quantity <= 0:
                    continue
                cash_dividend = round(previous_quantity * cash_per_share, 4)
                previous_cost = previous_entry_price * previous_quantity
                adjusted_cost = max(0.0, previous_cost - cash_dividend)
                adjusted_entry_price = round(adjusted_cost / new_quantity, 4)
                position.quantity = new_quantity
                position.entry_price = adjusted_entry_price
                adjustment = {
                    "key": key,
                    "symbol": position.symbol,
                    "description": action.get("description") or "",
                    "record_date": record_date.isoformat(),
                    "ex_date": ex_date.isoformat(),
                    "previous_quantity": previous_quantity,
                    "bonus_quantity": bonus_quantity,
                    "new_quantity": new_quantity,
                    "cash_dividend": cash_dividend,
                    "previous_entry_price": previous_entry_price,
                    "adjusted_entry_price": adjusted_entry_price,
                    "applied_at": datetime.utcnow().isoformat(),
                    "source": action.get("source") or "akshare_stock_history_dividend_detail",
                }
                meta["corporate_action_adjustments"] = [
                    *list(meta.get("corporate_action_adjustments", [])),
                    adjustment,
                ]
                meta["cash_dividend_total"] = round(float(meta.get("cash_dividend_total") or 0) + cash_dividend, 4)
                position.meta = meta
                applied_keys.add(key)
                changed = True
        if changed:
            db.commit()

    def _corporate_actions(self, symbol: str) -> list[dict[str, Any]]:
        cached = self._corporate_action_cache.get(symbol)
        now = datetime.utcnow()
        if cached and now - cached[0] < self._corporate_action_cache_ttl:
            return cached[1]
        actions: list[dict[str, Any]] = []
        try:
            import akshare as ak

            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        except Exception:
            self._corporate_action_cache[symbol] = (now, actions)
            return actions
        if df is None or df.empty:
            self._corporate_action_cache[symbol] = (now, actions)
            return actions
        for row in df.to_dict("records"):
            ex_date = self._parse_date(row.get("除权除息日"))
            record_date = self._parse_date(row.get("股权登记日"))
            if not ex_date or not record_date:
                continue
            send = self._safe_float(row.get("送股"))
            transfer = self._safe_float(row.get("转增"))
            dividend = self._safe_float(row.get("派息"))
            if send <= 0 and transfer <= 0 and dividend <= 0:
                continue
            actions.append(
                {
                    "key": f"{symbol}:{ex_date.isoformat()}:{send}:{transfer}:{dividend}",
                    "record_date": record_date,
                    "ex_date": ex_date,
                    "transfer_ratio": (send + transfer) / 10,
                    "cash_per_share": dividend / 10,
                    "description": f"10送{send:g}转{transfer:g}派{dividend:g}元",
                    "source": "akshare_stock_history_dividend_detail",
                }
            )
        actions.sort(key=lambda item: item["ex_date"])
        self._corporate_action_cache[symbol] = (now, actions)
        return actions

    def _parse_date(self, value: Any) -> date | None:
        if value is None:
            return None
        text = str(value)
        if not text or text in {"NaT", "nan", "None"}:
            return None
        try:
            import pandas as pd

            if pd.isna(value):
                return None
        except Exception:
            pass
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if hasattr(value, "date"):
            try:
                parsed = value.date()
                return parsed if isinstance(parsed, date) else None
            except Exception:
                pass
        try:
            return datetime.fromisoformat(text[:10]).date()
        except ValueError:
            return None

    def _safe_float(self, value: Any) -> float:
        if value is None:
            return 0.0
        try:
            if value != value:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0
