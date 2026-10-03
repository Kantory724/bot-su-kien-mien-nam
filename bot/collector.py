"""Thu thập tin từ RSS / Google News / trang HTML và tải nội dung bài viết."""
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote, urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger("collector")
UA = "Mozilla/5.0 (compatible; MienNamEventsBot/1.0; +https://github.com)"
HEADERS = {"User-Agent": UA, "Accept-Language": "vi,en;q=0.8"}
TIMEOUT = 20


@dataclass
class Item:
    source_id: str
    source_name: str
    title: str
    link: str
    summary: str = ""
    published: date | None = None
    province_hint: str | None = None
    is_gnews: bool = False


def load_sources(path=None) -> list[dict]:
    path = path or (config.ROOT / "config" / "sources.yaml")
    data = yaml.safe_load(open(path, encoding="utf-8"))
    out = []
    for s in data.get("sources", []):
        if s.get("enabled", True) is False:
            continue
        if s["type"] == "google_news":
            for key, region in (s.get("provinces") or {}).items():
                q = f'{s["topics"]} ({region}) when:{s.get("window", "14d")}'
                url = f"https://news.google.com/rss/search?q={quote(q)}&hl=vi&gl=VN&ceid=VN:vi"
                out.append({"id": f'{s["id"]}-{key}', "name": f'{s["name"]} - {key}', "type": "rss",
                            "url": url, "province_hint": key, "is_gnews": True})
        else:
            out.append(s)
    return out


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ")).strip()


def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def fetch_source(src: dict) -> list[Item]:
    if src["type"] == "html":
        return _fetch_html(src)
    r = _get(src["url"])
    feed = feedparser.parse(r.content)
    if not feed.entries:
        raise RuntimeError(f"RSS không có mục nào (bozo={getattr(feed, 'bozo', '?')})")
    items = []
    cutoff = config.today() - timedelta(days=21)
    for e in feed.entries:
        pub = None
        if e.get("published_parsed"):
            pub = datetime(*e.published_parsed[:6]).date()
        if pub and pub < cutoff:
            continue
        link = e.get("link", "")
        if not link or not e.get("title"):
            continue
        is_g = bool(src.get("is_gnews"))
        items.append(Item(src["id"], src["name"], _text(e.title), link, "" if is_g else _text(e.get("summary", "")),
                          pub, src.get("province_hint"), is_g))
    return items


def _fetch_html(src: dict) -> list[Item]:
    r = _get(src["url"])
    soup = BeautifulSoup(r.content, "html.parser")
    els = soup.select(src["item_selector"])
    if not els:
        raise RuntimeError(f"Selector '{src['item_selector']}' không khớp phần tử nào (trang đổi giao diện?)")
    items, seen = [], set()
    for el in els[:60]:
        a = el if el.name == "a" else el.find("a")
        if not a or not a.get("href"):
            continue
        link = urljoin(src["url"], a["href"])
        title = _text(a.get_text(" ")) or _text(el.get_text(" "))
        if link in seen or len(title) < 8:
            continue
        seen.add(link)
        items.append(Item(src["id"], src["name"], title, link, "", None, src.get("province_hint")))
    return items


def _resolve_gnews(url: str) -> str | None:
    try:
        from googlenewsdecoder import gnewsdecoder
        res = gnewsdecoder(url, interval=1)
        if res.get("status"):
            return res["decoded_url"]
        log.warning("Giải mã Google News thất bại: %s", str(res.get("message"))[:120])
    except Exception as e:  # noqa
        log.warning("Không giải mã được link Google News: %s", type(e).__name__)
    return None


def fetch_article_text(item: Item) -> str:
    """Tải nội dung bài. Lỗi -> trả chuỗi rỗng (vẫn xử lý được bằng tiêu đề/tóm tắt)."""
    url = item.link
    if item.is_gnews:
        url = _resolve_gnews(url) or ""
        if not url:
            return ""
    try:
        r = _get(url)
        soup = BeautifulSoup(r.content, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            t.decompose()
        root = soup.find("article") or soup
        paras = [p.get_text(" ", strip=True) for p in root.find_all("p")]
        text = "\n".join(p for p in paras if len(p) > 25)
        desc = soup.find("meta", attrs={"property": "og:description"})
        if desc and desc.get("content"):
            text = desc["content"] + "\n" + text
        time.sleep(0.4)
        return text[:8000]
    except Exception as e:  # noqa
        log.info("Không tải được bài %s: %s", url[:80], type(e).__name__)
        return ""
