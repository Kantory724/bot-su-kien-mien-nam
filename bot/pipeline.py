"""Luồng chính: thu thập -> lọc -> trích xuất -> khử trùng -> lưu -> cảnh báo; và bản tin hằng ngày."""
import difflib
import json
import logging
import re
import time
from datetime import date, timedelta

from . import config
from .collector import Item, fetch_article_text, fetch_source, load_sources
from .db import DB
from .extractor import extract_rules, is_candidate
from .formatter import format_alert, format_digest
from .geo import PROVINCES, detect_province, normalize
from .llm import LLM
from .models import Event
from .telegram import Telegram

log = logging.getLogger("pipeline")

# Từ chung chung trong tít báo: bỏ đi để so sánh dựa trên TÊN RIÊNG của sự kiện
STOP = {"le", "hoi", "tai", "cua", "va", "cac", "nam", "lan", "thu", "tinh", "thanh", "pho", "khai", "mac",
        "to", "chuc", "dien", "ra", "se", "la", "co", "cho", "voi", "trong", "tu", "den", "ngay", "sap", "dau",
        "hon", "nguoi", "khoang", "du", "kien", "hut", "khach", "luot", "hang", "nghin", "chuc", "don", "chao",
        "mung", "dip", "lon", "nhat", "quy", "mo", "tham", "gia", "khan", "dan", "ruc", "ro", "dep", "moi",
        "dai", "nhac", "concert", "festival", "countdown", "liveshow", "show", "phao", "hoa", "hoi", "cho",
        "trien", "lam", "giai", "chay", "marathon", "dem", "chuong", "trinh", "nghe", "thuat", "van", "tuan",
        "ky", "niem", "dien", "ra", "bat", "dau", "ben", "cung", "ve", "len", "xuong", "nhieu", "nua", "cua"}


def _strip_names(province: str) -> set[str]:
    top = {"tp hcm", "tphcm", "hcmc", "sai gon", "tp ho chi minh", "thanh pho ho chi minh"}
    return {normalize(PROVINCES[province][0])} | {a for a in PROVINCES[province][1] if a in top}


def _tokens(name: str, province: str) -> set[str]:
    n = f" {normalize(name)} "
    for a in _strip_names(province):
        n = n.replace(f" {a} ", " ")
    return {t for t in n.split() if not t.isdigit()}


def _overlap(a: Event, b: Event) -> bool:
    if not a.start_date or not b.start_date:
        return False
    return a.start_date <= (b.last_date + timedelta(days=1)) and b.start_date <= (a.last_date + timedelta(days=1))


def is_duplicate(a: Event, b: Event) -> bool:
    """Cùng tỉnh + ngày giao nhau (hoặc chưa rõ ngày) + tên riêng giống nhau/cùng địa điểm -> cùng một sự kiện."""
    if a.province != b.province:
        return False
    ta, tb = _tokens(a.name, a.province), _tokens(b.name, b.province)
    da, dbb = ta - STOP, tb - STOP
    ratio = difflib.SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    same_venue = bool(a.venue and b.venue and normalize(a.venue) == normalize(b.venue))
    if da and dbb:
        inter = len(da & dbb)
        jac, cont = inter / len(da | dbb), inter / min(len(da), len(dbb))
    else:  # tên toàn từ chung chung -> so cả bộ từ
        inter = len(ta & tb)
        jac = inter / len(ta | tb) if ta and tb else 0
        cont = inter / min(len(ta), len(tb)) if ta and tb else 0
    if a.start_date and b.start_date:
        if not _overlap(a, b):
            return False
        return (inter >= 2 or jac >= 0.5 or (cont >= 0.67 and min(len(da), len(dbb)) >= 2)
                or (same_venue and inter >= 1) or ratio >= 0.72)
    return jac >= 0.6 and inter >= 2   # thiếu ngày: yêu cầu chặt hơn


def merge_into(old: Event, new: Event) -> bool:
    """Gộp thông tin mới vào sự kiện cũ. Trả True nếu có thay đổi."""
    changed = False
    for link in new.sources:
        if link not in old.sources and len(old.sources) < 6:
            old.sources.append(link)
            changed = True
    if new.crowd and (old.crowd or 0) < new.crowd:
        old.crowd, changed = new.crowd, True
    for f in ("fireworks", "big_concert"):
        if getattr(new, f) and not getattr(old, f):
            setattr(old, f, True)
            changed = True
    for f in ("venue", "start_time", "summary"):
        if not getattr(old, f) and getattr(new, f):
            setattr(old, f, getattr(new, f))
            changed = True
    if old.start_date is None and new.start_date:
        old.start_date, old.end_date, changed = new.start_date, new.end_date, True
    elif new.end_date and old.end_date and new.end_date > old.end_date and new.start_date == old.start_date:
        old.end_date, changed = new.end_date, True
    # tên dài/đầy đủ hơn thường chính xác hơn khi nguồn trước chỉ là tít báo cụt
    if len(new.name) > len(old.name) + 15 and len(new.name) <= 120:
        old.name, changed = new.name, True
    return changed


# ---------- Xử lý 1 bài ----------
def ingest(db: DB, item: Item, body: str, llm: LLM | None, today: date) -> str:
    """Trả về trạng thái: skip | out_of_scope | not_event | past | new | merged | dup."""
    if not is_candidate(item.title, item.summary):
        return "skip"
    province = detect_province(item.title, f"{item.summary} {body}", item.province_hint)
    if not province:
        return "out_of_scope"
    info = extract_rules(item.title, f"{item.summary}\n{body}".strip(), item.published, today)
    if llm and llm.enabled:
        ai = llm.extract(item.title, f"{item.summary}\n{body}".strip(), item.published, today)
        if ai:
            if not ai["is_event"]:
                return "not_event"
            province = ai["province"] or province
            for k in ("name", "venue", "start_date", "end_date", "start_time", "crowd", "summary"):
                if ai.get(k):
                    info[k] = ai[k]
            info["fireworks"] = info["fireworks"] or ai["fireworks"]
            info["big_concert"] = info["big_concert"] or ai["big_concert"]
    ev = Event(name=info["name"], province=province, venue=info["venue"], start_date=info["start_date"],
               end_date=info["end_date"], start_time=info["start_time"], crowd=info["crowd"],
               fireworks=info["fireworks"], big_concert=info["big_concert"], summary=info["summary"],
               sources=[item.link])
    if not body and not ev.start_date:
        return "no_date"   # không đọc được bài (vd link Google News chưa giải mã) và tiêu đề không có ngày -> bỏ, tránh tin "chưa rõ ngày"
    if ev.last_date and ev.last_date < today - timedelta(days=3):
        return "past"
    if ev.start_date and ev.start_date > today + timedelta(days=240):
        return "far"
    for cand in db.candidates_for_dedupe(province, ev.start_date):
        if is_duplicate(ev, cand):
            if merge_into(cand, ev):
                db.update_event(cand)
                return "merged"
            return "dup"
    db.insert_event(ev)
    return "new"


# ---------- Thu thập ----------
def run_collect(db: DB, tg: Telegram | None, llm: LLM | None = None, budget: int = 0) -> dict:
    """budget > 0: dừng sớm khi quá số giây này (lượt sau làm tiếp phần còn lại), tránh bị cắt ngang khi chạy nền."""
    started = time.monotonic()
    today = config.today()
    sources = load_sources()
    stats = {"sources": len(sources), "failed": 0, "items": 0, "new": 0, "merged": 0, "fetched": 0, "body_empty": 0, "llm": 0}
    items: list[Item] = []

    for src in sources:
        try:
            got = fetch_source(src)
            recovered = db.health_ok(src["id"], src["name"], len(got))
            items += got
            log.info("OK   %-28s %3d mục", src["id"], len(got))
            if recovered and tg:
                tg.broadcast(f"✅ Nguồn đã hoạt động trở lại: {src['name']}", config.admin_ids())
        except Exception as e:  # noqa - 1 nguồn hỏng không làm dừng cả lượt
            stats["failed"] += 1
            n = db.health_fail(src["id"], src["name"], _short(e))
            log.error("LỖI %-28s (lần %d liên tiếp): %s", src["id"], n, _short(e))
        time.sleep(0.3)

    items.sort(key=lambda i: i.published or today, reverse=True)
    cap = config.max_article_fetch()
    for it in items:
        if db.seen(it.link):
            continue
        if budget and time.monotonic() - started > budget:
            stats["stopped_early"] = 1
            break
        stats["items"] += 1
        if not is_candidate(it.title, it.summary):
            db.add_article(it.link, it.source_id, it.title, "skip")
            continue
        body = ""
        if stats["fetched"] < cap:
            body = fetch_article_text(it)
            stats["fetched"] += 1
            if not body:
                stats["body_empty"] += 1
        status = ingest(db, it, body, llm, today)
        db.add_article(it.link, it.source_id, it.title, status)
        stats[status] = stats.get(status, 0) + 1
    db.conn.commit()
    if llm:
        stats["llm"] = llm.calls

    stats["deduped"] = dedupe_all(db)
    notify_source_failures(db, tg)
    if tg:
        send_alerts(db, tg)
    db.purge_old()
    if stats.get("stopped_early"):  # chưa xong: hẹn làm tiếp sau ~5 phút thay vì chờ cả chu kỳ
        resume = config.now() - timedelta(minutes=max(config.collect_interval_min() - 5, 0))
        db.set("last_collect", resume.isoformat(timespec="seconds"))
    else:
        db.set("last_collect", config.now().isoformat(timespec="seconds"))
    db.set("last_stats", json.dumps(stats))
    log.info("Xong thu thập: %s", stats)
    return stats


def _short(e: Exception) -> str:
    """Rút gọn lỗi (bỏ URL dài) để log và tin nhắn gọn."""
    msg = re.sub(r"https?://\S+", "<url>", str(e))
    return f"{type(e).__name__}: {msg[:140]}"


def dedupe_all(db: DB) -> int:
    """Quét toàn bộ CSDL, gộp các sự kiện trùng (kể cả dữ liệu cũ). Trả về số bản ghi trùng đã gộp."""
    kept: list[Event] = []
    removed = 0
    for e in db.all_events():
        target = next((k for k in kept if is_duplicate(k, e)), None)
        if target is None:
            kept.append(e)
            continue
        merge_into(target, e)
        target.alerted = target.alerted or e.alerted
        db.update_event(target)
        if target.alerted:
            db.mark_alerted(target.id)
        db.delete_event(e.id)
        removed += 1
    return removed


def notify_source_failures(db: DB, tg: Telegram | None) -> None:
    """Gộp mọi nguồn vừa vượt ngưỡng lỗi thành MỘT tin nhắn cho admin (tránh dồn tin khi mất mạng hàng loạt)."""
    th = config.fail_threshold()
    bad = [r for r in db.health_all() if db.health_should_notify(r["source_id"], th)]
    if not bad:
        return
    lines = [f"⚠️ {len(bad)} nguồn tin lỗi liên tiếp từ {th} lần trở lên:"]
    lines += [f" - {r['name']} ({r['source_id']}): {r['last_error'][:100]}" for r in bad[:15]]
    if len(bad) > 15:
        lines.append(f" … và {len(bad) - 15} nguồn khác")
    lines.append("Kiểm tra bằng: python main.py check-sources (hoặc /trangthai)")
    if tg:
        tg.broadcast("\n".join(lines), config.admin_ids())
    for r in bad:
        db.health_mark_notified(r["source_id"])


def send_alerts(db: DB, tg: Telegram) -> int:
    n = 0
    for e in db.unalerted_large(config.today()):
        if tg.broadcast(format_alert(e)):
            db.mark_alerted(e.id)
            n += 1
    return n


# ---------- Bản tin hằng ngày ----------
def run_digest(db: DB, tg: Telegram, force: bool = False) -> bool:
    today = config.today()
    if not force and db.get("last_digest_date") == today.isoformat():
        log.info("Bản tin hôm nay đã gửi, bỏ qua.")
        return False
    days = config.lookahead_days()
    events = db.events_between(today, today + timedelta(days=days))
    ok = tg.broadcast(format_digest(events, today, days))
    if ok:
        db.set("last_digest_date", today.isoformat())
    return bool(ok)
