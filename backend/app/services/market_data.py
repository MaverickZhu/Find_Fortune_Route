import json
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
import pandas as pd

from app.core.config import get_settings


class MarketSourceError(RuntimeError):
    pass


class MarketDataProvider:
    PRIMARY_SOURCE_ORDER = ["sina_finance", "tencent_quote", "akshare_eastmoney", "eastmoney_web"]

    def __init__(self) -> None:
        self.settings = get_settings()

    def fetch_realtime_quotes(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        target_symbols = symbols or self.settings.stock_pool
        source_errors: list[str] = []
        for source_name, fetcher in self._source_fetchers(self.PRIMARY_SOURCE_ORDER):
            try:
                return fetcher(target_symbols)
            except Exception as exc:
                source_errors.append(f"{source_name}:{type(exc).__name__}:{exc}")
        return []

    def fetch_realtime_quotes_by_source(
        self,
        symbols: list[str] | None = None,
        source_codes: list[str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        target_symbols = symbols or self.settings.stock_pool
        sources = self._source_fetchers(source_codes or self.PRIMARY_SOURCE_ORDER)
        result: dict[str, dict[str, Any]] = {}
        for source_name, fetcher in sources:
            started = datetime.utcnow()
            try:
                quotes = fetcher(target_symbols)
                result[source_name] = {
                    "status": "ok",
                    "quotes": quotes,
                    "error": None,
                    "latency_ms": int((datetime.utcnow() - started).total_seconds() * 1000),
                }
            except Exception as exc:
                result[source_name] = {
                    "status": "fail",
                    "quotes": [],
                    "error": f"{type(exc).__name__}: {exc}",
                    "latency_ms": int((datetime.utcnow() - started).total_seconds() * 1000),
                }
        return result

    def fetch_a_share_universe(self, limit: int | None = None) -> list[dict[str, str]]:
        errors: list[str] = []
        for fetcher in [self._fetch_eastmoney_universe, self._fetch_akshare_universe]:
            try:
                universe = fetcher()
                if limit:
                    universe = universe[:limit]
                if universe:
                    return universe
            except Exception as exc:
                errors.append(f"{fetcher.__name__}:{type(exc).__name__}:{exc}")
        fallback = [{"symbol": symbol, "name": symbol, "exchange": self._exchange_for_symbol(symbol)} for symbol in self.settings.stock_pool]
        for item in fallback:
            item["universe_errors"] = "; ".join(errors)
        return fallback[:limit] if limit else fallback

    def fetch_daily_bars(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            return self._fetch_sina_daily_bars(symbol, start_date, end_date)
        except Exception:
            pass
        if self.settings.akshare_enabled:
            try:
                import akshare as ak

                df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date)
                return df.rename(
                    columns={
                        "日期": "trade_date",
                        "开盘": "open",
                        "最高": "high",
                        "最低": "low",
                        "收盘": "close",
                        "成交量": "volume",
                        "成交额": "amount",
                        "换手率": "turnover_rate",
                    }
                )
            except Exception:
                return pd.DataFrame()
        return pd.DataFrame()

    def _fetch_sina_daily_bars(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        prefixed = self._market_prefixed_symbol(symbol)
        datalen = max(80, min(1200, (datetime.strptime(end_date, "%Y%m%d") - datetime.strptime(start_date, "%Y%m%d")).days + 80))
        callback = f"var _{prefixed}_{datetime.utcnow().strftime('%Y_%m_%d')}"
        url = (
            "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
            f"{callback}=/CN_MarketDataService.getKLineData"
        )
        params = {"symbol": prefixed, "scale": 240, "ma": "no", "datalen": datalen}
        headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
        text = httpx.get(url, params=params, headers=headers, timeout=8).text
        match = re.search(r"=\((\[.*\])\)", text, flags=re.S)
        if not match:
            raise MarketSourceError("sina daily returned no JSON payload")
        rows = json.loads(match.group(1))
        start_dt = datetime.strptime(start_date, "%Y%m%d").date()
        end_dt = datetime.strptime(end_date, "%Y%m%d").date()
        normalized = []
        for row in rows:
            trade_date = pd.to_datetime(row.get("day")).date()
            if trade_date < start_dt or trade_date > end_dt:
                continue
            close = self._safe_float(row.get("close"))
            normalized.append(
                {
                    "trade_date": trade_date.isoformat(),
                    "open": self._safe_float(row.get("open")),
                    "high": self._safe_float(row.get("high")),
                    "low": self._safe_float(row.get("low")),
                    "close": close,
                    "volume": self._safe_float(row.get("volume")),
                    "amount": self._safe_float(row.get("volume")) * close,
                    "turnover_rate": 0,
                    "source": "sina_daily",
                }
            )
        if not normalized:
            raise MarketSourceError("sina daily returned no rows in date range")
        return pd.DataFrame(normalized)

    def fetch_stock_detail(
        self,
        symbol: str,
        quote: dict[str, Any] | None = None,
        signals: list[dict[str, Any]] | None = None,
        daily_bars: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        latest_quote = self._fresh_quote_for_detail(symbol, quote)
        daily_bars = self._fresh_daily_bars_for_detail(symbol, latest_quote, daily_bars)
        intraday_points = self._intraday_points_for_detail(symbol, latest_quote)
        fundamentals = self._fundamentals_for_detail(symbol, latest_quote, daily_bars)

        return {
            "symbol": symbol,
            "name": latest_quote.get("name") or symbol,
            "quote": latest_quote,
            "fundamentals": fundamentals,
            "intraday": intraday_points,
            "daily_bars": daily_bars,
            "signals": signals or [],
            "risk_notes": self._risk_notes(latest_quote, fundamentals),
        }

    def _quote_from_akshare(self, row: pd.Series) -> dict[str, Any]:
        return {
            "symbol": str(row.get("代码", "")),
            "name": str(row.get("名称", "")),
            "observed_at": datetime.utcnow(),
            "last_price": float(row.get("最新价") or 0),
            "change_pct": float(row.get("涨跌幅") or 0),
            "volume": float(row.get("成交量") or 0),
            "amount": float(row.get("成交额") or 0),
            "source": "akshare_eastmoney",
            "quality": "ok",
        }

    def _fetch_akshare_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        if symbols:
            df = df[df["代码"].astype(str).isin(symbols)]
        quotes = [self._quote_from_akshare(row) for _, row in df.head(200).iterrows()]
        return self._require_quotes("akshare_eastmoney", quotes)

    def _fetch_eastmoney_push2_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        secids = ",".join(self._eastmoney_secid(symbol) for symbol in symbols)
        url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "fltt": "2",
            "secids": secids,
            "fields": "f12,f14,f2,f3,f5,f6",
        }
        headers = {"Referer": "https://quote.eastmoney.com/"}
        payload = httpx.get(url, params=params, headers=headers, timeout=6).json()
        rows = ((payload.get("data") or {}).get("diff") or [])
        now = datetime.utcnow()
        quotes = []
        for row in rows:
            last_price = self._safe_float(row.get("f2"))
            change_pct = self._safe_float(row.get("f3"))
            amount = self._safe_float(row.get("f6"))
            volume = self._safe_float(row.get("f5"))
            if last_price <= 0:
                continue
            quotes.append(
                {
                    "symbol": str(row.get("f12", "")),
                    "name": str(row.get("f14", "")),
                    "observed_at": now,
                    "last_price": last_price,
                    "change_pct": change_pct,
                    "volume": volume,
                    "amount": amount,
                    "source": "eastmoney_web",
                    "quality": "ok",
                }
            )
        return self._require_quotes("eastmoney_web", quotes)

    def _fetch_sina_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        codes = ",".join(self._market_prefixed_symbol(symbol) for symbol in symbols)
        url = "https://hq.sinajs.cn/list=" + codes
        headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
        text = httpx.get(url, headers=headers, timeout=6).text
        now = datetime.utcnow()
        quotes = []
        for raw in text.splitlines():
            if '="' not in raw:
                continue
            left, right = raw.split('="', 1)
            symbol = left.rsplit("_", 1)[-1][-6:]
            fields = right.rstrip('";').split(",")
            if len(fields) < 10 or not fields[0]:
                continue
            last_price = self._safe_float(fields[3])
            open_price = self._safe_float(fields[1])
            previous_close = self._safe_float(fields[2])
            high_price = self._safe_float(fields[4])
            low_price = self._safe_float(fields[5])
            amount = self._safe_float(fields[9])
            volume = self._safe_float(fields[8])
            if last_price <= 0 or amount <= 0 or volume <= 0:
                continue
            change_pct = ((last_price / previous_close) - 1) * 100 if previous_close > 0 else 0
            quotes.append(
                {
                    "symbol": symbol,
                    "name": fields[0],
                    "observed_at": now,
                    "last_price": round(last_price, 2),
                    "open": round(open_price, 2),
                    "high": round(high_price, 2),
                    "low": round(low_price, 2),
                    "previous_close": round(previous_close, 2),
                    "change_pct": round(change_pct, 2),
                    "volume": volume,
                    "amount": amount,
                    "source": "sina_finance",
                    "quality": "ok",
                    "trade_date": fields[30] if len(fields) > 30 else None,
                    "trade_time": fields[31] if len(fields) > 31 else None,
                }
            )
        return self._require_quotes("sina_finance", quotes)

    def _fetch_tencent_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        codes = ",".join(self._market_prefixed_symbol(symbol) for symbol in symbols)
        url = "https://qt.gtimg.cn/q=" + codes
        headers = {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"}
        text = httpx.get(url, headers=headers, timeout=6).text
        now = datetime.utcnow()
        quotes = []
        for raw in text.splitlines():
            if '="' not in raw:
                continue
            left, right = raw.split('="', 1)
            symbol = left.rsplit("_", 1)[-1][-6:]
            fields = right.rstrip('";').split("~")
            if len(fields) < 40:
                continue
            last_price = self._safe_float(fields[3])
            previous_close = self._safe_float(fields[4])
            change_pct = self._safe_float(fields[32]) if len(fields) > 32 else 0
            volume = self._safe_float(fields[6]) * 100
            amount = self._safe_float(fields[37]) * 10_000 if len(fields) > 37 else 0
            if amount <= 0 and len(fields) > 35:
                parts = str(fields[35]).split("/")
                if len(parts) >= 3:
                    amount = self._safe_float(parts[2])
            if volume <= 0 and len(fields) > 36:
                volume = self._safe_float(fields[36]) * 100
            if last_price <= 0 or amount <= 0 or volume <= 0:
                continue
            if change_pct == 0 and previous_close > 0:
                change_pct = ((last_price / previous_close) - 1) * 100
            quotes.append(
                {
                    "symbol": symbol,
                    "name": fields[1],
                    "observed_at": now,
                    "last_price": round(last_price, 2),
                    "change_pct": round(change_pct, 2),
                    "volume": volume,
                    "amount": amount,
                    "source": "tencent_quote",
                    "quality": "ok",
                }
            )
        return self._require_quotes("tencent_quote", quotes)

    def _require_quotes(self, source: str, quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not quotes:
            raise MarketSourceError(f"{source} returned no usable quotes")
        return quotes

    def _source_fetchers(self, source_codes: list[str]) -> list[tuple[str, Any]]:
        fetchers = {
            "sina_finance": self._fetch_sina_quotes,
            "tencent_quote": self._fetch_tencent_quotes,
            "akshare_eastmoney": self._fetch_akshare_quotes,
            "eastmoney_web": self._fetch_eastmoney_push2_quotes,
        }
        result: list[tuple[str, Any]] = []
        for code in source_codes:
            if code == "akshare_eastmoney" and not self.settings.akshare_enabled:
                continue
            fetcher = fetchers.get(code)
            if fetcher:
                result.append((code, fetcher))
        return result

    def _safe_float(self, value: Any) -> float:
        try:
            if value in {"-", None, ""}:
                return 0.0
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _market_prefixed_symbol(self, symbol: str) -> str:
        return ("sh" if symbol.startswith("6") else "sz") + symbol

    def _eastmoney_secid(self, symbol: str) -> str:
        return ("1." if symbol.startswith("6") else "0.") + symbol

    def _exchange_for_symbol(self, symbol: str) -> str:
        if symbol.startswith("6"):
            return "SH"
        if symbol.startswith(("0", "3")):
            return "SZ"
        return "CN"

    def _fetch_eastmoney_universe(self) -> list[dict[str, str]]:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": 1,
            "pz": 6000,
            "po": 1,
            "np": 1,
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
            "fields": "f12,f14",
        }
        headers = {"Referer": "https://quote.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
        payload = httpx.get(url, params=params, headers=headers, timeout=10).json()
        rows = ((payload.get("data") or {}).get("diff") or [])
        return self._normalize_universe_rows(
            [{"symbol": str(row.get("f12", "")), "name": str(row.get("f14", ""))} for row in rows]
        )

    def _fetch_akshare_universe(self) -> list[dict[str, str]]:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        code_col = "code" if "code" in df.columns else "代码"
        name_col = "name" if "name" in df.columns else "名称"
        rows = [{"symbol": str(row.get(code_col, "")), "name": str(row.get(name_col, ""))} for _, row in df.iterrows()]
        return self._normalize_universe_rows(rows)

    def _normalize_universe_rows(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        seen: set[str] = set()
        universe: list[dict[str, str]] = []
        for row in rows:
            symbol = str(row.get("symbol", "")).strip().zfill(6)
            if len(symbol) != 6 or symbol in seen:
                continue
            if not symbol.startswith(("0", "3", "6")):
                continue
            name = str(row.get("name") or symbol).strip()
            seen.add(symbol)
            universe.append({"symbol": symbol, "name": name, "exchange": self._exchange_for_symbol(symbol)})
        return sorted(universe, key=lambda item: item["symbol"])

    def _latest_real_quote(self, symbol: str) -> dict[str, Any]:
        try:
            quotes = self.fetch_realtime_quotes([symbol])
            real_quotes = [item for item in quotes if item.get("source") != "demo"]
            if real_quotes:
                return real_quotes[0]
        except Exception:
            pass
        return {
            "symbol": symbol,
            "name": symbol,
            "observed_at": datetime.utcnow(),
            "last_price": 0,
            "change_pct": 0,
            "volume": 0,
            "amount": 0,
            "source": "missing",
            "quality": "missing",
        }

    def _fresh_quote_for_detail(self, symbol: str, fallback: dict[str, Any] | None) -> dict[str, Any]:
        fresh_quote = self._latest_real_quote(symbol)
        if fresh_quote.get("quality") == "ok" and float(fresh_quote.get("last_price") or 0) > 0:
            return fresh_quote
        return fallback or fresh_quote

    def _daily_bars_for_detail(self, symbol: str, quote: dict[str, Any]) -> list[dict[str, Any]]:
        end = datetime.utcnow()
        start = end - timedelta(days=140)
        df = self.fetch_daily_bars(symbol, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
        if not df.empty:
            rows = []
            for _, row in df.tail(80).iterrows():
                rows.append(
                    {
                        "trade_date": str(row.get("trade_date", ""))[:10],
                        "open": float(row.get("open") or 0),
                        "high": float(row.get("high") or 0),
                        "low": float(row.get("low") or 0),
                        "close": float(row.get("close") or 0),
                        "volume": float(row.get("volume") or 0),
                        "amount": float(row.get("amount") or 0),
                        "turnover_rate": float(row.get("turnover_rate") or 0),
                    }
                )
            return rows
        return []

    def _fresh_daily_bars_for_detail(
        self,
        symbol: str,
        quote: dict[str, Any],
        daily_bars: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        quote_trade_date = str(quote.get("trade_date") or "")[:10]
        bars = daily_bars or []
        if not quote_trade_date:
            return bars or self._daily_bars_for_detail(symbol, quote)

        latest_bar_date = str((bars[-1] if bars else {}).get("trade_date") or "")[:10]
        if latest_bar_date and latest_bar_date >= quote_trade_date:
            return bars

        fetched = self._daily_bars_for_detail(symbol, quote)
        fetched_latest_date = str((fetched[-1] if fetched else {}).get("trade_date") or "")[:10]
        if fetched_latest_date > latest_bar_date:
            bars = fetched
            latest_bar_date = fetched_latest_date
        if latest_bar_date < quote_trade_date:
            bars = [*bars, self._quote_as_daily_bar(quote)]
        return bars[-90:]

    def _quote_as_daily_bar(self, quote: dict[str, Any]) -> dict[str, Any]:
        last_price = self._safe_float(quote.get("last_price"))
        open_price = self._safe_float(quote.get("open")) or last_price
        high_price = max(self._safe_float(quote.get("high")), last_price, open_price)
        low_candidates = [value for value in [self._safe_float(quote.get("low")), last_price, open_price] if value > 0]
        low_price = min(low_candidates) if low_candidates else last_price
        return {
            "trade_date": str(quote.get("trade_date") or "")[:10],
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "close": round(last_price, 2),
            "volume": float(quote.get("volume") or 0),
            "amount": float(quote.get("amount") or 0),
            "turnover_rate": 0,
            "source": "sina_realtime_quote",
        }

    def _intraday_points_for_detail(self, symbol: str, quote: dict[str, Any]) -> list[dict[str, Any]]:
        quote_trade_date = str(quote.get("trade_date") or "")[:10]
        for fetcher in [self._fetch_sina_intraday_points, self._fetch_tencent_intraday_points]:
            try:
                points = fetcher(symbol)
                if not points:
                    continue
                point_dates = {str(point.get("date") or "")[:10] for point in points}
                if quote_trade_date and quote_trade_date not in point_dates:
                    continue
                if self._intraday_deviates_from_quote(points, quote):
                    continue
                return points
            except Exception:
                continue
        return []

    def _fetch_sina_intraday_points(self, symbol: str) -> list[dict[str, Any]]:
        prefixed = self._market_prefixed_symbol(symbol)
        callback = f"var _{prefixed}_5"
        url = (
            "https://quotes.sina.cn/cn/api/jsonp_v2.php/"
            f"{callback}=/CN_MarketDataService.getKLineData"
        )
        params = {"symbol": prefixed, "scale": 5, "ma": "no", "datalen": 96}
        headers = {"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"}
        text = httpx.get(url, params=params, headers=headers, timeout=8).text
        match = re.search(r"=\((\[.*\])\)", text, flags=re.S)
        if not match:
            raise MarketSourceError("sina intraday returned no JSON payload")
        rows = json.loads(match.group(1))
        normalized = []
        for row in rows:
            value_time = pd.to_datetime(row.get("day"))
            close = self._safe_float(row.get("close"))
            volume = self._safe_float(row.get("volume"))
            amount = self._safe_float(row.get("amount"))
            if close <= 0:
                continue
            normalized.append(
                {
                    "date": value_time.date().isoformat(),
                    "time": value_time.strftime("%H:%M"),
                    "price": round(close, 2),
                    "volume": volume,
                    "amount": amount,
                }
            )
        if not normalized:
            return []
        dates = sorted({point["date"] for point in normalized})
        latest_date = dates[-1]
        today_points = [point for point in normalized if point["date"] == latest_date]
        if len(today_points) < 8 and len(dates) > 1:
            previous_date = dates[-2]
            previous_points = [point for point in normalized if point["date"] == previous_date]
            if len(previous_points) > len(today_points):
                today_points = previous_points
        total_amount = 0.0
        total_volume = 0.0
        result = []
        for point in today_points:
            total_amount += float(point["amount"] or 0)
            total_volume += float(point["volume"] or 0)
            avg_price = total_amount / total_volume if total_volume > 0 and total_amount > 0 else point["price"]
            result.append(
                {
                    "date": point["date"],
                    "time": point["time"],
                    "price": point["price"],
                    "avg_price": round(avg_price, 2),
                    "volume": point["volume"],
                }
            )
        return result

    def _fetch_tencent_intraday_points(self, symbol: str) -> list[dict[str, Any]]:
        prefixed = self._market_prefixed_symbol(symbol)
        url = "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
        headers = {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"}
        payload = httpx.get(url, params={"code": prefixed}, headers=headers, timeout=8).json()
        data = ((payload.get("data") or {}).get(prefixed) or {}).get("data") or {}
        rows = data.get("data") or []
        raw_date = str(data.get("date") or "")
        if len(raw_date) == 8:
            point_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        else:
            point_date = datetime.utcnow().date().isoformat()
        result = []
        previous_volume = 0.0
        previous_amount = 0.0
        for row in rows:
            parts = str(row).split()
            if len(parts) < 4:
                continue
            raw_time, raw_price, raw_volume, raw_amount = parts[:4]
            price = self._safe_float(raw_price)
            cumulative_volume = self._safe_float(raw_volume) * 100
            cumulative_amount = self._safe_float(raw_amount)
            if price <= 0 or len(raw_time) != 4:
                continue
            minute_volume = max(0.0, cumulative_volume - previous_volume)
            minute_amount = max(0.0, cumulative_amount - previous_amount)
            previous_volume = max(previous_volume, cumulative_volume)
            previous_amount = max(previous_amount, cumulative_amount)
            avg_price = cumulative_amount / cumulative_volume if cumulative_volume > 0 and cumulative_amount > 0 else price
            result.append(
                {
                    "date": point_date,
                    "time": f"{raw_time[:2]}:{raw_time[2:]}",
                    "price": round(price, 2),
                    "avg_price": round(avg_price, 2),
                    "volume": minute_volume,
                    "amount": minute_amount,
                }
            )
        return result

    def _intraday_deviates_from_quote(self, points: list[dict[str, Any]], quote: dict[str, Any]) -> bool:
        quote_price = self._safe_float(quote.get("last_price"))
        last_point_price = self._safe_float(points[-1].get("price") if points else 0)
        if quote_price <= 0 or last_point_price <= 0:
            return False
        deviation_pct = abs(last_point_price / quote_price - 1) * 100
        return deviation_pct > 3

    def _fundamentals_for_detail(
        self,
        symbol: str,
        quote: dict[str, Any],
        daily_bars: list[dict[str, Any]],
    ) -> dict[str, Any]:
        closes = [float(row["close"]) for row in daily_bars if row.get("close")]
        high_60 = max(closes[-60:]) if closes else float(quote.get("last_price") or 0)
        low_60 = min(closes[-60:]) if closes else float(quote.get("last_price") or 0)
        fundamentals = self._fetch_real_fundamentals(symbol)
        turnover = fundamentals.get("turnover_rate")
        if turnover in {None, 0}:
            recent_turnover = [float(row.get("turnover_rate") or 0) for row in daily_bars if float(row.get("turnover_rate") or 0) > 0]
            turnover = recent_turnover[-1] if recent_turnover else None
        derived_turnover = self._derive_turnover_rate(quote, fundamentals)
        if turnover in {None, 0} or (derived_turnover is not None and float(turnover or 0) <= 0.05 and derived_turnover > float(turnover or 0)):
            turnover = derived_turnover
        fundamentals.update(
            {
                "turnover_rate": turnover,
                "high_60d": round(high_60, 2),
                "low_60d": round(low_60, 2),
                "data_quality": quote.get("quality", "unknown"),
            }
        )
        if not fundamentals.get("data_source"):
            fundamentals["data_source"] = "missing"
        return fundamentals

    def _derive_turnover_rate(self, quote: dict[str, Any], fundamentals: dict[str, Any]) -> float | None:
        circulating_cap_yi = fundamentals.get("circulating_market_cap")
        last_price = self._safe_nullable_float(quote.get("last_price"))
        volume = self._safe_nullable_float(quote.get("volume"))
        if not circulating_cap_yi or not last_price or not volume:
            return None
        circulating_shares = float(circulating_cap_yi) * 100_000_000 / last_price
        if circulating_shares <= 0:
            return None
        return round(volume / circulating_shares * 100, 2)

    def _fetch_real_fundamentals(self, symbol: str) -> dict[str, Any]:
        errors: list[str] = []
        for fetcher in [self._fetch_eastmoney_fundamentals, self._fetch_tencent_fundamentals]:
            try:
                data = fetcher(symbol)
                if data:
                    data["errors"] = errors
                    return data
            except Exception as exc:
                errors.append(f"{fetcher.__name__}:{type(exc).__name__}:{exc}")
        return {
            "industry": "",
            "region": "",
            "concepts": [],
            "market_cap": None,
            "circulating_market_cap": None,
            "pe_ttm": None,
            "pb": None,
            "roe": None,
            "turnover_rate": None,
            "data_source": "missing",
            "errors": errors,
        }

    def _fetch_eastmoney_fundamentals(self, symbol: str) -> dict[str, Any]:
        fields = ",".join(
            [
                "f57",
                "f58",
                "f116",
                "f117",
                "f127",
                "f128",
                "f129",
                "f162",
                "f167",
                "f168",
                "f173",
            ]
        )
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {"secid": self._eastmoney_secid(symbol), "fields": fields}
        headers = {"Referer": "https://quote.eastmoney.com/", "User-Agent": "Mozilla/5.0"}
        payload = self._get_eastmoney_json(url, params, headers)
        data = payload.get("data") or {}
        if str(data.get("f57") or "") != symbol:
            raise MarketSourceError("eastmoney fundamentals returned no matching symbol")
        market_cap = self._yuan_to_yi(data.get("f116"))
        pe_ttm = self._scaled_number(data.get("f162"), 100)
        pb = self._scaled_number(data.get("f167"), 100)
        turnover = self._scaled_number(data.get("f168"), 100)
        if not any(value is not None for value in [market_cap, pe_ttm, pb, turnover]):
            raise MarketSourceError("eastmoney fundamentals returned no usable fields")
        return {
            "industry": str(data.get("f127") or ""),
            "region": str(data.get("f128") or ""),
            "concepts": [item for item in str(data.get("f129") or "").split(",") if item],
            "market_cap": market_cap,
            "circulating_market_cap": self._yuan_to_yi(data.get("f117")),
            "pe_ttm": pe_ttm,
            "pb": pb,
            "roe": self._safe_nullable_float(data.get("f173")),
            "turnover_rate": turnover,
            "data_source": "eastmoney_stock",
        }

    def _get_eastmoney_json(self, url: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        errors: list[str] = []
        try:
            import requests

            for _ in range(2):
                try:
                    return requests.get(url, params=params, headers=headers, timeout=8).json()
                except Exception as exc:
                    errors.append(f"requests:{type(exc).__name__}:{exc}")
        except Exception as exc:
            errors.append(f"requests_import:{type(exc).__name__}:{exc}")
        try:
            return httpx.get(url, params=params, headers=headers, timeout=8).json()
        except Exception as exc:
            errors.append(f"httpx:{type(exc).__name__}:{exc}")
        raise MarketSourceError("; ".join(errors))

    def _fetch_tencent_fundamentals(self, symbol: str) -> dict[str, Any]:
        code = self._market_prefixed_symbol(symbol)
        url = "https://qt.gtimg.cn/q=" + code
        headers = {"Referer": "https://gu.qq.com/", "User-Agent": "Mozilla/5.0"}
        text = httpx.get(url, headers=headers, timeout=8).text
        if '="' not in text:
            raise MarketSourceError("tencent fundamentals returned no payload")
        fields = text.split('="', 1)[1].rstrip('";').split("~")
        if len(fields) < 57:
            raise MarketSourceError("tencent fundamentals returned incomplete payload")
        market_cap = self._safe_nullable_float(fields[45])
        if market_cap is None:
            raise MarketSourceError("tencent fundamentals returned no market cap")
        return {
            "industry": "",
            "region": "",
            "concepts": [],
            "market_cap": market_cap,
            "circulating_market_cap": self._safe_nullable_float(fields[44]),
            "pe_ttm": self._safe_nullable_float(fields[52]),
            "pb": self._safe_nullable_float(fields[56]),
            "roe": None,
            "turnover_rate": self._safe_nullable_float(fields[38]),
            "data_source": "tencent_quote",
        }

    def _safe_nullable_float(self, value: Any) -> float | None:
        try:
            if value in {"-", None, ""}:
                return None
            parsed = float(value)
            return parsed if parsed != 0 else None
        except (TypeError, ValueError):
            return None

    def _scaled_number(self, value: Any, scale: float) -> float | None:
        parsed = self._safe_nullable_float(value)
        if parsed is None:
            return None
        return round(parsed / scale, 2)

    def _yuan_to_yi(self, value: Any) -> float | None:
        parsed = self._safe_nullable_float(value)
        if parsed is None:
            return None
        return round(parsed / 100_000_000, 2)

    def _risk_notes(self, quote: dict[str, Any], fundamentals: dict[str, Any]) -> list[str]:
        notes = ["本系统只提供研究与提醒，不自动下单。"]
        if str(quote.get("quality", "")).startswith("demo") or quote.get("quality") == "missing":
            notes.append("当前详情包含演示或降级数据，真实决策前需刷新并校验行情源。")
        if fundamentals.get("data_source") == "missing":
            notes.append("基本面估值数据暂未从真实数据源取得，相关字段不参与当前策略判断。")
        if abs(float(quote.get("change_pct") or 0)) >= 5:
            notes.append("当日涨跌幅较大，需关注涨跌停、流动性和追高/杀跌风险。")
        if float(fundamentals.get("turnover_rate") or 0) >= 5:
            notes.append("换手率偏高，短线情绪可能放大价格波动。")
        return notes
