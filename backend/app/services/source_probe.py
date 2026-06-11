from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.market_data import MarketDataProvider


class SourceProbeService:
    def probe(self) -> dict[str, Any]:
        symbols = get_settings().stock_pool[:3]
        provider = MarketDataProvider()
        probes = [
            self._probe_adapter("sina_finance", "新浪财经行情", provider._fetch_sina_quotes, symbols),
            self._probe_adapter("tencent_quote", "腾讯财经行情", provider._fetch_tencent_quotes, symbols),
            self._probe_akshare_eastmoney(symbols),
            self._probe_adapter("eastmoney_web", "东方财富 Push2", provider._fetch_eastmoney_push2_quotes, symbols),
        ]
        ok_count = sum(1 for item in probes if item["status"] == "ok")
        return {
            "status": "ok" if ok_count > 0 else "unavailable",
            "ok_count": ok_count,
            "probes": probes,
            "generated_at": datetime.utcnow().isoformat(),
        }

    def _probe_akshare_eastmoney(self, symbols: list[str]) -> dict[str, Any]:
        started = datetime.utcnow()
        try:
            import akshare as ak

            df = ak.stock_zh_a_spot_em()
            if symbols:
                df = df[df["代码"].astype(str).isin(symbols)]
            required = {"代码", "名称", "最新价", "涨跌幅", "成交量", "成交额"}
            columns = set(map(str, df.columns))
            missing = sorted(required - columns)
            if df.empty:
                status = "fail"
                message = "AkShare 东方财富行情返回空表。"
            elif missing:
                status = "fail"
                message = f"AkShare 字段缺失：{', '.join(missing)}。"
            else:
                status = "ok"
                message = f"AkShare 东方财富行情可用，返回 {len(df)} 行。"
            sample = {}
            if not df.empty:
                row = df.head(1).iloc[0]
                sample = {key: self._safe_value(row.get(key)) for key in ["代码", "名称", "最新价", "涨跌幅", "成交额"]}
            return {
                "code": "akshare_eastmoney",
                "name": "AkShare 东方财富行情",
                "status": status,
                "message": message,
                "latency_ms": self._latency_ms(started),
                "row_count": int(len(df)),
                "columns": list(map(str, df.columns[:16])),
                "sample": sample,
            }
        except Exception as exc:
            return {
                "code": "akshare_eastmoney",
                "name": "AkShare 东方财富行情",
                "status": "fail",
                "message": f"{type(exc).__name__}: {exc}",
                "latency_ms": self._latency_ms(started),
                "row_count": 0,
                "columns": [],
                "sample": {},
            }

    def _probe_adapter(self, code: str, name: str, fetcher: Any, symbols: list[str]) -> dict[str, Any]:
        started = datetime.utcnow()
        try:
            quotes = fetcher(symbols)
            sample = quotes[0] if quotes else {}
            return {
                "code": code,
                "name": name,
                "status": "ok" if quotes else "fail",
                "message": f"{name} 可用，返回 {len(quotes)} 条标准化行情。" if quotes else f"{name} 返回空结果。",
                "latency_ms": self._latency_ms(started),
                "row_count": len(quotes),
                "columns": ["symbol", "name", "last_price", "change_pct", "volume", "amount"],
                "sample": {key: self._safe_value(sample.get(key)) for key in ["symbol", "name", "last_price", "change_pct", "amount"]},
            }
        except httpx.HTTPError as exc:
            return self._failed_probe(code, name, started, f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            return self._failed_probe(code, name, started, f"{type(exc).__name__}: {exc}")

    def _failed_probe(self, code: str, name: str, started: datetime, message: str) -> dict[str, Any]:
        return {
            "code": code,
            "name": name,
            "status": "fail",
            "message": message,
            "latency_ms": self._latency_ms(started),
            "row_count": 0,
            "columns": [],
            "sample": {},
        }

    def _latency_ms(self, started: datetime) -> int:
        return int((datetime.utcnow() - started).total_seconds() * 1000)

    def _safe_value(self, value: Any) -> str | float | int | None:
        if value is None:
            return None
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, (str, int, float)):
            return value
        return str(value)
