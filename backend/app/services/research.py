from __future__ import annotations

import re
from datetime import datetime
from html import unescape

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import ResearchItem


class ResearchService:
    article_enrich_limit = 30

    def ingest_seed_research(self, db: Session) -> int:
        return self.collect_real_research(db)

    def collect_real_research(self, db: Session) -> int:
        items = self._fetch_all_real_items()
        existing_by_url = {
            row.url: row
            for row in db.execute(select(ResearchItem).where(ResearchItem.url.is_not(None))).scalars().all()
            if row.url
        }
        existing_title_keys = {
            self._title_key(row[0])
            for row in db.execute(select(ResearchItem.title)).all()
            if row[0]
        }
        created = 0
        for item in items:
            title_key = self._title_key(item["title"])
            existing = existing_by_url.get(item["url"])
            if existing:
                if self._is_incomplete_summary(existing.title, existing.summary) and not self._is_incomplete_summary(item["title"], item["summary"]):
                    existing.summary = item["summary"]
                    existing.tags = item["tags"]
                    existing.published_at = item["published_at"]
                continue
            if title_key in existing_title_keys:
                continue
            db.add(ResearchItem(**item))
            existing_title_keys.add(title_key)
            created += 1
        db.commit()
        return created

    def _fetch_all_real_items(self) -> list[dict]:
        items: list[dict] = []
        for fetcher in [self._fetch_eastmoney_news, self._fetch_sina_stock_news, self._fetch_tencent_finance_news]:
            try:
                items.extend(fetcher())
            except Exception:
                continue
        enriched = self._enrich_article_summaries(items)
        seen: set[str] = set()
        deduped: list[dict] = []
        for item in enriched:
            key = self._title_key(item["title"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:80]

    def _fetch_eastmoney_news(self) -> list[dict]:
        url = "https://finance.eastmoney.com/a/czqyw.html"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.eastmoney.com/"}
        text = httpx.get(url, headers=headers, timeout=10).text
        pattern = re.compile(r'<a[^>]+href="(https://finance\.eastmoney\.com/a/[^"]+)"[^>]*>([^<]{8,120})</a>')
        seen: set[str] = set()
        items = []
        for link, raw_title in pattern.findall(text):
            title = unescape(re.sub(r"\s+", " ", raw_title)).strip()
            if not title or link in seen:
                continue
            seen.add(link)
            tags = self._tags_for_title(title)
            items.append(
                {
                    "title": title,
                    "source": "eastmoney_finance",
                    "url": link,
                    "summary": title,
                    "credibility": 0.68,
                    "tags": tags,
                    "published_at": self._published_at_from_url(link),
                    "collected_at": datetime.utcnow(),
                }
            )
            if len(items) >= 30:
                break
        return items

    def _fetch_sina_stock_news(self) -> list[dict]:
        url = "https://finance.sina.com.cn/stock/"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"}
        text = httpx.get(url, headers=headers, timeout=10).text
        pattern = re.compile(r'<a[^>]+href="(https?://finance\.sina\.com\.cn/[^"]+)"[^>]*>([^<]{8,120})</a>')
        return self._items_from_links(pattern.findall(text), source="sina_stock", credibility=0.7, limit=25)

    def _fetch_tencent_finance_news(self) -> list[dict]:
        url = "https://finance.qq.com/"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com/"}
        text = httpx.get(url, headers=headers, timeout=10).text
        pattern = re.compile(r'<a[^>]+href="(https?://new\.qq\.com/rain/a/[^"]+|https?://finance\.qq\.com/[^"]+)"[^>]*>([^<]{8,120})</a>')
        return self._items_from_links(pattern.findall(text), source="tencent_finance", credibility=0.66, limit=25)

    def _items_from_links(
        self,
        links: list[tuple[str, str]],
        *,
        source: str,
        credibility: float,
        limit: int,
    ) -> list[dict]:
        seen: set[str] = set()
        items: list[dict] = []
        for link, raw_title in links:
            title = unescape(re.sub(r"\s+", " ", raw_title)).strip()
            if not title or link in seen:
                continue
            if any(token in title for token in ["视频", "直播", "登录", "注册"]):
                continue
            seen.add(link)
            items.append(
                {
                    "title": title,
                    "source": source,
                    "url": link,
                    "summary": title,
                    "credibility": credibility,
                    "tags": self._tags_for_title(title),
                    "published_at": self._published_at_from_url(link),
                    "collected_at": datetime.utcnow(),
                }
            )
            if len(items) >= limit:
                break
        return items

    def _enrich_article_summaries(self, items: list[dict]) -> list[dict]:
        enriched: list[dict] = []
        for index, item in enumerate(items):
            if index < self.article_enrich_limit and self._is_incomplete_summary(item["title"], item["summary"]):
                summary = self._fetch_article_summary(item.get("url") or "")
                if summary:
                    item = {**item, "summary": summary}
            enriched.append(item)
        return enriched

    def _fetch_article_summary(self, url: str) -> str | None:
        if not url:
            return None
        headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
        try:
            text = httpx.get(url, headers=headers, timeout=6, follow_redirects=True).text
        except Exception:
            return None
        content = self._extract_main_text(text)
        if len(content) < 80:
            return None
        return content[:520]

    def _extract_main_text(self, html: str) -> str:
        html = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<!--.*?-->", " ", html)
        candidates = re.findall(r"(?is)<p[^>]*>(.*?)</p>", html)
        if not candidates:
            candidates = re.findall(r"(?is)<div[^>]+class=\"[^\"]*(?:content|article|text|main)[^\"]*\"[^>]*>(.*?)</div>", html)
        text = " ".join(self._clean_text(item) for item in candidates)
        return re.sub(r"\s+", " ", text).strip()

    def _clean_text(self, value: str) -> str:
        value = re.sub(r"(?is)<[^>]+>", " ", value)
        value = unescape(value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    def _is_incomplete_summary(self, title: str, summary: str) -> bool:
        return not summary or self._title_key(title) == self._title_key(summary) or len(summary.strip()) < 36

    def _title_key(self, value: str) -> str:
        value = unescape(value or "")
        value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value.lower())
        return value[:80]

    def _published_at_from_url(self, url: str) -> datetime | None:
        match = re.search(r"/a/(\d{8})", url)
        if not match:
            return None
        try:
            return datetime.strptime(match.group(1), "%Y%m%d")
        except ValueError:
            return None

    def _tags_for_title(self, title: str) -> list[str]:
        tags = ["财经新闻"]
        keywords = {
            "A股": "A股",
            "股市": "市场情绪",
            "政策": "政策",
            "产业链": "产业链",
            "业绩": "财报",
            "涨停": "短线情绪",
            "资金": "资金流",
            "美联储": "宏观",
            "新能源": "新能源",
            "AI": "科技",
            "华为": "科技",
        }
        for keyword, tag in keywords.items():
            if keyword in title and tag not in tags:
                tags.append(tag)
        return tags[:5]
