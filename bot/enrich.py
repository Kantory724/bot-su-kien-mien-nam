"""Làm giàu sự kiện: từ TÊN sự kiện đã lưu, tìm lại các bài báo liên quan (ưu tiên báo địa phương) để bổ sung
ngày, địa điểm, quy mô, nguồn chính thống. Chạy sau mỗi lượt thu thập."""
import logging
import re
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from urllib.parse import quote

import feedparser

from . import config
from .collector import Item, _get, _resolve_gnews, _text, fetch_article_text
from .extractor import (canonical_name, choose_date, extract_rules, find_dates, is_bulletin, is_prep_title,
                        valid_venue)
from .geo import LOCAL_SITES, PROVINCES, detect_province, normalize, province_name, title_elsewhere
from .models import Event
from .pipeline import STOP, _desc, _tokens, merge_into

log = logging.getLogger("enrich")
MAX_TRIES = 8
RETRY_AFTER = timedelta(hours=6)
GNEWS = "https://news.google.com/rss/search?q={q}&hl=vi&gl=VN&ceid=VN:vi"


def _terms(ev: Event) -> str:
    words = [w for w in re.split(r"\s+", canonical_name(ev.name))
             if normalize(w) and normalize(w) not in STOP and not normalize(w).isdigit()]
    return " ".join(words[:8] + [province_name(ev.province)])


def _search(q: str) -> list[tuple[Item, str]]:
    """Tìm trên Google News. Trả về [(bài, tên miền báo gốc)]."""
    feed = feedparser.parse(_get(GNEWS.format(q=quote(q))).content)
    out = []
    for e in feed.entries[:15]:
        if not e.get("link") or not e.get("title"):
            continue
        src = e.get("source") or {}
        href = src.get("href", "") if hasattr(src, "get") else ""
        pub = datetime(*e.published_parsed[:6]).date() if e.get("published_parsed") else None
        out.append((Item("enrich", "Google News", _text(e.title), e.link, "", pub, None, True), href))
    return out


def _info(item: Item, body: str, llm, today: date) -> dict | None:
    """Trích xuất thông tin từ bài (luật + AI nếu bật). None nếu AI bảo không phải sự kiện."""
    text = f"{item.summary}\n{body}".strip()
    info = extract_rules(item.title, text, item.published, today)
    if llm and llm.enabled:
        ai = llm.extract(item.title, text, item.published, today)
        if ai:
            if not ai["is_event"]:
                return None
            for k in ("name", "venue", "start_date", "end_date", "start_time", "crowd", "summary"):
                if ai.get(k) and (k != "venue" or valid_venue(ai[k])):
                    info[k] = ai[k]
            info["fireworks"] = info["fireworks"] or ai["fireworks"]
            info["big_concert"] = info["big_concert"] or ai["big_concert"]
    if is_prep_title(item.title):  # ngày trong tít là hạn chót chuẩn bị -> chỉ lấy ngày từ nội dung
        bd = choose_date(find_dates(text, item.published or today), item.published or today)
        info["start_date"], info["end_date"] = bd if bd else (None, None)
    info["name"] = canonical_name(info["name"])
    return info


def enrich_event(ev: Event, llm, today: date, deadline: float) -> bool:
    """Tìm lại bài về `ev`, gộp thông tin mới vào `ev` (chưa ghi DB). Trả True nếu có thay đổi."""
    sites = LOCAL_SITES.get(ev.province, [])
    terms = _terms(ev)
    queries = []
    if sites:
        queries.append(f"{terms} ({' OR '.join('site:' + d for d in sites[:5])}) when:90d")
    queries.append(f"{terms} when:90d")
    evt = _tokens(ev.name, ev.province)
    cands, seen = [], set()
    for q in queries:
        if time.monotonic() > deadline:
            break
        try:
            res = _search(q)
        except Exception as e:  # noqa
            log.info("Tìm '%s' lỗi: %s", q[:60], type(e).__name__)
            continue
        for it, href in res:
            if it.link in seen or title_elsewhere(it.title):
                continue
            seen.add(it.link)
            ov = len(_tokens(it.title, ev.province) & evt)
            if ov >= 2:
                cands.append((not any(d in href for d in sites), -ov, it))
        if sum(1 for c in cands if not c[0]) >= 2:
            break  # đã có đủ bài của báo địa phương
        time.sleep(0.5)
    cands.sort(key=lambda c: (c[0], c[1]))
    changed = False
    for is_other, _, it in cands[:4]:
        if time.monotonic() > deadline:
            break
        real = _resolve_gnews(it.link)
        if not real:
            continue
        if is_bulletin(it.title, real):
            continue  # bản tin thời sự: không đáng tin để lấy ngày
        art = replace(it, link=real, is_gnews=False)
        body = fetch_article_text(art)
        if not body:
            continue
        strong = detect_province(art.title, body[:1500])
        if strong and strong != ev.province:
            continue  # bài nói về nơi khác
        info = _info(art, body, llm, today)
        if not info:
            continue
        cand = Event(name=info["name"], province=ev.province, venue=info["venue"], start_date=info["start_date"],
                     end_date=info["end_date"], start_time=info["start_time"], crowd=info["crowd"],
                     fireworks=info["fireworks"], big_concert=info["big_concert"], summary=info["summary"],
                     sources=[real])
        if cand.last_date and cand.last_date < today - timedelta(days=3):
            continue
        if ev.start_date and cand.start_date and abs((ev.start_date - cand.start_date).days) > 3:
            continue  # lệch ngày nhiều -> có thể là đợt/sự kiện khác
        if llm and llm.enabled and llm.same_event(_desc(ev), _desc(cand)) is False:
            continue
        had = list(ev.sources)
        ch = merge_into(ev, cand)
        if real in ev.sources and not is_other:  # nguồn báo địa phương lên đầu
            ev.sources.remove(real)
            ev.sources.insert(0, real)
            ch = True
        changed = changed or ch or ev.sources != had
    # thay link Google News dài bằng link bài gốc (nếu giải mã được)
    if changed:
        new_src = []
        for s in ev.sources:
            if s.startswith("https://news.google.com/"):
                s = _resolve_gnews(s) or s
            if s not in new_src:
                new_src.append(s)
        ev.sources = new_src
    return changed


def _lookup_ai(ev: Event, llm, today: date) -> bool:
    """Tra ngày/địa điểm bằng Gemini + Google Search (không cần giải mã link Google News)."""
    d = llm.lookup(ev.name, ev.province, today)
    if not d:
        return False
    if d["end_date"] and d["end_date"] < today - timedelta(days=3):
        return False
    if ev.start_date and abs((ev.start_date - d["start_date"]).days) > 3:
        return False
    cand = Event(name=ev.name, province=ev.province, venue=d["venue"] if valid_venue(d["venue"]) else "",
                 start_date=d["start_date"], end_date=d["end_date"] or d["start_date"], start_time=d["start_time"],
                 crowd=d["crowd"], fireworks=d["fireworks"], big_concert=d["big_concert"], summary=d["summary"], sources=[])
    return merge_into(ev, cand)


def _pending(db, today: date) -> list[Event]:
    out = []
    for e in db.all_events():
        if e.last_date and e.last_date < today:
            continue
        if e.start_date and e.venue:
            continue  # đã đủ ngày + địa điểm
        if e.start_date and e.start_date > today + timedelta(days=60):
            continue
        out.append(e)
    out.sort(key=lambda e: (e.start_date is None, e.start_date or today))
    return out


def enrich_events(db, llm, today: date, force: bool = False, limit: int | None = None) -> int:
    """Làm giàu các sự kiện còn thiếu ngày/địa điểm. Trả về số sự kiện được bổ sung."""
    limit = limit or config.enrich_max()
    deadline = time.monotonic() + config.enrich_budget_sec()
    done, now, lookups = 0, config.now(), 0
    for ev in _pending(db, today):
        if done >= limit or time.monotonic() > deadline:
            break
        key = f"enr_{ev.id}"
        n, _, ts = (db.get(key, "0|") or "0|").partition("|")
        tries = int(n or 0)
        last = datetime.fromisoformat(ts) if ts else None
        if not force and (tries >= MAX_TRIES or (last and now - last < RETRY_AFTER)):
            continue
        done += 1
        try:
            changed = enrich_event(ev, llm, today, deadline)
        except Exception as e:  # noqa - 1 sự kiện lỗi không làm hỏng cả lượt
            log.warning("Làm giàu '%s' lỗi: %s", ev.name[:50], type(e).__name__)
            changed = False
        if (not (ev.start_date and ev.venue) and llm and llm.can_search() and lookups < config.lookup_max()):
            lookups += 1
            try:
                changed = _lookup_ai(ev, llm, today) or changed
            except Exception as ex:  # noqa
                log.warning("Tra AI '%s' lỗi: %s", ev.name[:50], type(ex).__name__)
        if changed:
            db.update_event(ev)
            log.info("Bổ sung: %s | %s | %s", ev.name[:50], ev.start_date, ev.venue)
        complete = bool(ev.start_date and ev.venue)
        db.set(key, f"{99 if complete else tries + 1}|{now.isoformat(timespec='seconds')}")
    return done
