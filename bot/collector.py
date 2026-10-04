"""Thu thập tin từ RSS / Google News / trang HTML và tải nội dung bài viết.

Bản sửa: tự giải mã link Google News (không phụ thuộc hoàn toàn vào googlenewsdecoder):
  1) giải base64 (link kiểu cũ),
  2) gọi batchexecute của Google (link kiểu mới "AU_yqL..."), thử cả 2 dạng URL trang bài,
  3) dự phòng: thư viện googlenewsdecoder nếu có.
Có cache, chống spam khi bị 429 (nghỉ tạm rồi thử lại), không còn "tắt vĩnh viễn" cả phiên.
"""
import base64
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote, unquote, urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from . import config

log = logging.getLogger("collector")
UA = "Mozilla/5.0 (compatible; MienNamEventsBot/1.0; +https://github.com)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
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
    trust_hint: bool = False  # nguồn báo địa phương: tin gợi ý tỉnh của nguồn khi tít/đoạn đầu không nhắc tên tỉnh


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


def _get(url: str, headers: dict | None = None) -> requests.Response:
    r = requests.get(url, headers=headers or HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r


_TAIL_DATE = re.compile(r"\s+(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+\d+)?\s*$")


def _items_from_feed(src: dict, content: bytes) -> list[Item]:
    feed = feedparser.parse(content)
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
        is_g = bool(src.get("is_gnews")) or "news.google.com" in link
        items.append(Item(src["id"], src["name"], _text(e.title), link, "" if is_g else _text(e.get("summary", "")),
                          pub, src.get("province_hint"), is_g, bool(src.get("local"))))
    return items


def fetch_source(src: dict) -> list[Item]:
    if src["type"] == "html":
        return _fetch_html(src)
    if src["type"] == "auto":
        return _fetch_auto(src)
    r = _get(src["url"])
    return _items_from_feed(src, r.content)


# ---------- Nguồn "auto": tự tìm RSS, không có thì lấy các link bài trên trang ----------
_FEED_HREF = re.compile(r"(\.rss(\?.*)?$|rss\.xml$|/feed/?$|/rss/[^/]+\.(rss|xml)$|/rss/[^/]+/?$)", re.I)
_SKIP_HREF = re.compile(r"^(javascript|mailto|tel):|#|/(tag|tags|search|tim-kiem|rss|login|dang-nhap|lien-he|"
                        r"gioi-thieu|video|photo)(/|$|\?)", re.I)


def _host(u: str) -> str:
    return (urlparse(u).hostname or "").lower().removeprefix("www.")


def _find_feeds(soup: BeautifulSoup, base: str) -> list[str]:
    out = []
    for l in soup.find_all("link", href=True):
        rel = " ".join(l.get("rel") or []) if isinstance(l.get("rel"), list) else str(l.get("rel") or "")
        t = (l.get("type") or "").lower()
        if "alternate" in rel.lower() and ("rss" in t or "atom" in t):
            out.append(urljoin(base, l["href"]))
    for a in soup.find_all("a", href=True):
        if _FEED_HREF.search(a["href"]) and "rss.html" not in a["href"].lower():
            out.append(urljoin(base, a["href"]))
    return list(dict.fromkeys(out))


def _heuristic_items(soup: BeautifulSoup, base: str, src: dict) -> list[Item]:
    """Trang không có RSS: lấy các link trông giống bài báo (cùng tên miền, tít dài, đường dẫn có số/.html)."""
    items, seen, bh = [], set(), _host(base)
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if _SKIP_HREF.search(href):
            continue
        link = urljoin(base, href).split("#")[0]
        lh = _host(link)
        if not (lh == bh or lh.endswith("." + bh)) or link in seen:
            continue
        path = urlparse(link).path
        if len(path.strip("/")) < 10 or not (re.search(r"\d", path) or path.lower().endswith((".html", ".htm", ".aspx", ".chn"))):
            continue
        title = _text(a.get_text(" ")) or (a.get("title") or "").strip()
        pub = None
        m = _TAIL_DATE.search(title)
        if m:
            try:
                pub = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pub = None
            title = title[:m.start()].strip()
        if len(title) < 20 or len(title.split()) < 4:
            continue
        seen.add(link)
        items.append(Item(src["id"], src["name"], title, link, "", pub, src.get("province_hint"), False,
                          bool(src.get("local"))))
        if len(items) >= 80:
            break
    return items


def _fetch_auto(src: dict) -> list[Item]:
    urls = src.get("urls") or [src["url"]]
    items, seen, ok, last = [], set(), 0, None
    for u in urls:
        try:
            r = _get(u)
            got: list[Item] = []
            if "xml" in r.headers.get("content-type", "").lower() or r.content[:120].lstrip().startswith(b"<?xml"):
                got = _items_from_feed(src, r.content)  # chính URL này là một RSS
            else:
                soup = BeautifulSoup(_decode_response(r), "html.parser")
                for f in _find_feeds(soup, r.url)[:3]:
                    try:
                        got += _items_from_feed(src, _get(f).content)
                    except Exception:  # noqa - RSS hỏng thì bỏ qua, còn lấy link trên trang
                        continue
                got += _heuristic_items(soup, r.url, src)
            ok += 1
            for it in got:
                if it.link not in seen:
                    seen.add(it.link)
                    items.append(it)
        except Exception as e:  # noqa - 1 địa chỉ hỏng không làm hỏng cả nguồn
            last = e
    if not ok:
        raise last or RuntimeError("không truy cập được địa chỉ nào")
    if not items:
        raise RuntimeError("Không tìm thấy bài nào (trang đổi giao diện hoặc chặn bot?)")
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
        pub = None
        m = _TAIL_DATE.search(title)  # vd: "Hội Đua bò ... 2026 01/10/2026 85" -> tách ngày đăng + lượt xem khỏi tít
        if m:
            try:
                pub = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            except ValueError:
                pub = None
            title = title[:m.start()].strip()
        if link in seen or len(title) < 8:
            continue
        seen.add(link)
        items.append(Item(src["id"], src["name"], title, link, "", pub, src.get("province_hint"), False, bool(src.get("local"))))
    return items


# ======================= Giải mã link Google News =======================
_GN_CACHE: dict[str, str | None] = {}      # link gốc -> link bài thật (None = thất bại chắc chắn)
_GN_COOLDOWN_UNTIL = 0.0                   # monotonic; > now nghĩa là đang nghỉ do bị giới hạn tốc độ
_GN_LAST_CALL = 0.0
_GN_MIN_GAP = 0.8                          # giây giữa 2 lần gọi mạng tới Google


def _gn_id(url: str) -> str | None:
    p = urlparse(url)
    if p.hostname != "news.google.com":
        return None
    parts = [x for x in p.path.split("/") if x]
    if len(parts) >= 2 and parts[-2] in ("articles", "read"):
        return parts[-1]
    return None


def _gn_from_base64(b64: str) -> tuple[str | None, bool]:
    """Trả (url, cần_gọi_mạng). Link kiểu cũ chứa sẵn URL trong base64."""
    try:
        raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception:  # noqa
        return None, True
    s = raw.decode("latin1")
    if s.startswith("\x08\x13\x22"):
        s = s[3:]
    if s.endswith("\xd2\x01\x00"):
        s = s[:-3]
    if not s:
        return None, True
    ba = bytearray(s, "latin1")
    n = ba[0]
    s = s[2:n + 1] if n >= 0x80 else s[1:n + 1]
    if s.startswith("AU_yqL"):
        return None, True
    if s.startswith("http"):
        return s, False
    m = re.search(r"https?://[^\s\x00-\x1f\x7f-\xff\"<>]+", s)
    return (m.group(0), False) if m else (None, True)


def _gn_throttle() -> None:
    global _GN_LAST_CALL
    wait = _GN_MIN_GAP - (time.monotonic() - _GN_LAST_CALL)
    if wait > 0:
        time.sleep(wait)
    _GN_LAST_CALL = time.monotonic()


def _gn_via_batchexecute(b64: str) -> str | None:
    """Link kiểu mới: lấy signature/timestamp từ trang bài rồi hỏi batchexecute."""
    global _GN_COOLDOWN_UNTIL
    sess = requests.Session()
    sess.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "vi,en;q=0.8"})
    sess.cookies.set("CONSENT", "YES+cb", domain=".google.com")
    sig = ts = None
    for page in (f"https://news.google.com/rss/articles/{b64}?hl=vi&gl=VN&ceid=VN:vi",
                 f"https://news.google.com/articles/{b64}?hl=vi&gl=VN&ceid=VN:vi"):
        _gn_throttle()
        r = sess.get(page, timeout=TIMEOUT)
        if r.status_code == 429:
            _GN_COOLDOWN_UNTIL = time.monotonic() + 120
            raise RuntimeError("Google News giới hạn tốc độ (429)")
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        el = soup.select_one("c-wiz > div[jscontroller]") or soup.select_one("[data-n-a-sg]")
        if el and el.get("data-n-a-sg") and el.get("data-n-a-ts"):
            sig, ts = el["data-n-a-sg"], el["data-n-a-ts"]
            break
        m1, m2 = re.search(r'data-n-a-sg="([^"]+)"', r.text), re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if m1 and m2:
            sig, ts = m1.group(1), m2.group(1)
            break
    if not sig:
        raise RuntimeError("không lấy được signature từ trang Google News")
    inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
             f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{b64}",{ts},"{sig}"]')
    payload = "f.req=" + quote(json.dumps([[["Fbv4je", inner]]]))
    _gn_throttle()
    r = sess.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                  headers={"content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
                  data=payload, timeout=TIMEOUT)
    if r.status_code == 429:
        _GN_COOLDOWN_UNTIL = time.monotonic() + 120
        raise RuntimeError("Google News giới hạn tốc độ (429)")
    r.raise_for_status()
    chunks = r.text.split("\n\n")
    parsed = json.loads(chunks[1][:-2] if len(chunks) > 1 else chunks[0].lstrip(")]}'\n"))
    url = json.loads(parsed[0][2])[1]
    if not (isinstance(url, str) and url.startswith("http")):
        raise RuntimeError("batchexecute không trả về URL")
    return url


def _gn_via_library(url: str) -> str | None:
    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        return None
    try:
        res = gnewsdecoder(url, interval=1)
        if res.get("status"):
            return res["decoded_url"]
    except Exception:  # noqa
        pass
    return None


def _clean_real_url(u: str) -> str | None:
    if not u or not u.startswith("http") or "news.google.com" in u:
        return None
    return u.strip()


def resolve_gnews(url: str) -> str | None:
    """Link Google News -> link bài báo gốc. None nếu không giải mã được (có cache)."""
    if "news.google.com" not in url:
        return url  # đã là link thật
    if url in _GN_CACHE:
        return _GN_CACHE[url]
    b64 = _gn_id(url)
    if not b64:
        # có thể là link dạng ?url=... hoặc redirect
        m = re.search(r"[?&]url=([^&]+)", url)
        real = _clean_real_url(unquote(m.group(1))) if m else None
        _GN_CACHE[url] = real
        return real
    real, need_net = _gn_from_base64(b64)
    real = _clean_real_url(real) if real else None
    if real:
        _GN_CACHE[url] = real
        return real
    if time.monotonic() < _GN_COOLDOWN_UNTIL:
        return None  # đang nghỉ do 429: không cache để lượt sau thử lại
    err = None
    for attempt in range(2):
        try:
            real = _clean_real_url(_gn_via_batchexecute(b64))
            if real:
                _GN_CACHE[url] = real
                return real
        except Exception as e:  # noqa
            err = e
            if time.monotonic() < _GN_COOLDOWN_UNTIL:
                break
            time.sleep(1.5 * (attempt + 1))
    real = _clean_real_url(_gn_via_library(url) or "")
    if real:
        _GN_CACHE[url] = real
        return real
    log.warning("Không giải mã được link Google News: %s", type(err).__name__ if err else "unknown")
    if time.monotonic() >= _GN_COOLDOWN_UNTIL:
        _GN_CACHE[url] = None  # thất bại thật sự (không phải do bị giới hạn) -> khỏi thử lại trong phiên
    return None


_resolve_gnews = resolve_gnews  # tên cũ, enrich.py vẫn import tên này


# ======================= Tải nội dung bài =======================
_END_MARKERS = ("related post", "bài viết liên quan", "tin liên quan", "tin cùng chuyên mục", "bình luận",
                "comments", "có thể bạn quan tâm", "tin mới nhất", "tin khác")


def _decode_response(r: requests.Response) -> str:
    enc = r.encoding
    if not enc or enc.lower() in ("iso-8859-1", "latin-1", "ascii"):
        enc = r.apparent_encoding or "utf-8"
    try:
        return r.content.decode(enc, errors="replace")
    except LookupError:
        return r.content.decode("utf-8", errors="replace")


def _meta(soup: BeautifulSoup, *names: str) -> list[str]:
    out = []
    for n in names:
        d = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if d and d.get("content") and d["content"].strip():
            out.append(re.sub(r"\s+", " ", d["content"]).strip())
    return out


def _main_text(soup: BeautifulSoup) -> str:
    """Lấy nội dung chính. Ưu tiên các thẻ <p>; nếu CMS không dùng <p> (nội dung nằm trong <div>/<br>)
    thì lấy theo dòng và cắt tại mục 'Related Post/Bình luận' để không lẫn tin khác."""
    for t in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        t.decompose()
    root = soup.find("article") or soup
    paras = [p.get_text(" ", strip=True) for p in root.find_all("p")]
    text = "\n".join(p for p in paras if len(p) > 25)
    if len(text) < 600:
        lines = []
        for ln in root.get_text("\n").split("\n"):
            ln = re.sub(r"\s+", " ", ln).strip()
            if ln.lower().startswith(_END_MARKERS) and len(ln) < 40:
                break
            if len(ln) > 25:
                lines.append(ln)
        alt = "\n".join(dict.fromkeys(lines))
        if len(alt) > len(text):
            text = alt
    return text


def _download(url: str) -> str:
    try:
        r = _get(url)
    except requests.HTTPError as e:  # nhiều báo chặn UA bot -> thử lại bằng UA trình duyệt
        if e.response is not None and e.response.status_code in (401, 403, 406, 429, 503):
            r = _get(url, {"User-Agent": BROWSER_UA, "Accept-Language": "vi,en;q=0.8"})
        else:
            raise
    return _decode_response(r)


def fetch_article_text(item: Item) -> str:
    """Tải nội dung bài. Lỗi -> trả chuỗi rỗng (vẫn xử lý được bằng tiêu đề/tóm tắt)."""
    url = item.link
    if item.is_gnews or "news.google.com" in url:
        url = resolve_gnews(url) or ""
        if not url:
            return ""
    try:
        soup = BeautifulSoup(_download(url), "html.parser")
        desc_parts = _meta(soup, "og:description", "description")
        text = _main_text(soup)
        if desc_parts:
            text = "\n".join(dict.fromkeys(desc_parts)) + "\n" + text
        time.sleep(0.4)
        return text[:8000]
    except Exception as e:  # noqa
        log.info("Không tải được bài %s: %s", url[:80], type(e).__name__)
        return ""


def item_from_url(url: str) -> Item:
    """Tạo Item từ một đường link bất kỳ (báo thường hoặc Google News) - dùng cho lệnh add-url."""
    is_g = "news.google.com" in url
    real = resolve_gnews(url) if is_g else url
    if not real:
        raise RuntimeError("Không giải mã được link Google News này")
    soup = BeautifulSoup(_download(real), "html.parser")
    title = (_meta(soup, "og:title") or [""])[0]
    if not title and soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) < 8:
        raise RuntimeError("Không đọc được tiêu đề bài")
    return Item("manual", "Thêm tay", title, real, "", config.today(), None, False)
