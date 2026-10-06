"""Luồng chính: thu thập -> lọc -> trích xuất -> khử trùng -> lưu -> cảnh báo; và bản tin hằng ngày."""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from . import config
from .collector import Item, fetch_article_text, fetch_source, load_sources
from .db import DB
from .extractor import (canonical_name, choose_date, event_name, event_keyword_hits, extract_rules, find_dates,
                        has_negative, is_bulletin, is_cultural_event, is_prep_title, not_vietnamese, valid_venue)
from .formatter import format_alert, format_digest
from .geo import (PROVINCES, detect_province, held_elsewhere, normalize, other_area_score, score_provinces,
                  title_elsewhere)
from .llm import LLM
from .models import Event
from .telegram import Telegram

log = logging.getLogger("pipeline")

STOP = {"le", "hoi", "tai", "cua", "va", "cac", "nam", "lan", "thu", "tinh", "thanh", "pho", "khai", "mac",
        "to", "chuc", "dien", "ra", "se", "la", "co", "cho", "voi", "trong", "tu", "den", "ngay", "sap", "dau",
        "hon", "nguoi", "khoang", "du", "kien"}


# ---------- Khử trùng ----------
def _tokens(name: str, province: str) -> set[str]:
    n = f" {normalize(name)} "
    for a in PROVINCES[province][1] + [normalize(PROVINCES[province][0])]:
        n = n.replace(f" {a} ", " ")
    return {t for t in n.split() if t not in STOP and not t.isdigit()}


def is_duplicate(a: Event, b: Event) -> bool:
    if a.province != b.province:
        return False
    ta, tb = _tokens(a.name, a.province), _tokens(b.name, b.province)
    if not ta or not tb:
        return False
    inter = len(ta & tb)
    jac, cont = inter / len(ta | tb), inter / min(len(ta), len(tb))
    if a.start_date is None or b.start_date is None:
        return jac >= 0.4 or (cont >= 0.6 and inter >= 3)
    return jac >= 0.34 or (cont >= 0.6 and inter >= 3)


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
    elif new.start_date and old.start_date and (new.start_date != old.start_date or new.last_date != old.last_date):
        gap = (max(old.start_date, new.start_date) - min(old.last_date, new.last_date)).days
        if gap <= 1:  # hai khoảng ngày giao/sát nhau = các hoạt động của CÙNG một lễ hội -> lấy cả đợt
            s0, e0 = min(old.start_date, new.start_date), max(old.last_date, new.last_date)
            if (s0, e0) != (old.start_date, old.last_date):
                old.start_date, old.end_date, changed = s0, e0, True
        else:
            span = lambda e: ((e.end_date or e.start_date) - e.start_date).days
            if span(new) > span(old):  # lấy mốc nhiều ngày (đợt lễ) thay cho mốc 1 ngày (hạn chót chuẩn bị)
                old.start_date, old.end_date, changed = new.start_date, new.end_date, True
    # tên dài/đầy đủ hơn thường chính xác hơn khi nguồn trước chỉ là tít báo cụt
    if len(new.name) > len(old.name) + 15 and len(new.name) <= 120:
        old.name, changed = new.name, True
    return changed


def _maybe_same(a: Event, b: Event) -> bool:
    """Lọc thô trước khi tốn 1 lệnh gọi AI: cùng tỉnh và chung >= 2 từ đặc trưng."""
    return a.province == b.province and len(_tokens(a.name, a.province) & _tokens(b.name, b.province)) >= 2


def _desc(e: Event) -> str:
    return f"{e.name} | {e.start_date}→{e.end_date} | {e.venue} | {e.summary}"[:400]


def dedupe_existing(db: DB, llm: LLM | None) -> int:
    """Dọn các sự kiện trùng đã lưu trong DB. Trả về số bản ghi đã gộp."""
    evs, gone, n = db.all_events(), set(), 0
    for e in evs:  # chuẩn hoá tên cũ ("Hoàn tất công tác chuẩn bị ...") trước khi so sánh
        changed = False
        # tít chuẩn bị + mốc 1 ngày = hạn chót chuẩn bị, không phải ngày diễn ra -> bỏ ngày
        if is_prep_title(e.name) and e.start_date and (e.end_date or e.start_date) == e.start_date:
            e.start_date = e.end_date = None
            changed = True
        cn = canonical_name(event_name(e.name, e.summary) or e.name)  # tên cũ là tít bài -> rút ra tên lễ hội/sự kiện
        if cn != e.name:
            e.name, changed = cn, True
        if changed:
            db.update_event(e)
    for i, a in enumerate(evs):
        if a.id in gone:
            continue
        for b in evs[i + 1:]:
            if b.id in gone or a.province != b.province:
                continue
            if a.start_date and b.start_date and \
                    (max(a.start_date, b.start_date) - min(a.last_date, b.last_date)).days > 3:
                continue
            if is_duplicate(a, b) or (llm and llm.enabled and _maybe_same(a, b) and llm.same_event(_desc(a), _desc(b))):
                merge_into(a, b)
                db.update_event(a)
                if b.alerted:
                    db.mark_alerted(a.id)
                db.delete_event(b.id)
                gone.add(b.id)
                n += 1
    return n


def prune_elsewhere(db: DB) -> list[str]:
    """Xoá các sự kiện đã lưu nhưng thực tế diễn ra ngoài 8 tỉnh (chạy một lần tự động)."""
    gone = []
    for e in db.all_events():
        if held_elsewhere(e.name, e.summary):
            db.delete_event(e.id)
            gone.append(f"{e.start_date} | {e.name[:70]}")
    return gone


def ingest_ai(db: DB, d: dict, province: str, today: date) -> str:
    """Nhận một sự kiện do Gemini (Google Search) tìm được: lọc nhẹ, khử trùng, lưu."""
    name = canonical_name(d["name"])
    if (len(name) < 6 or has_negative(name) or title_elsewhere(name) or d["start_date"] is None
            or not_vietnamese(f"{name} {d['summary']}")):
        return "not_event"
    head, tail = f"{name} {d['venue']}", d["summary"]
    sc = score_provinces(head, tail)
    best = max(sc, key=sc.get) if sc else None
    if best and best != province and sc[best] > sc.get(province, 0):
        province = best  # Gemini trả nhầm tỉnh: theo tỉnh được nhắc nhiều nhất trong tên/địa điểm/tóm tắt
    if other_area_score(head, tail) > sc.get(province, 0):
        return "out_of_scope"
    ev = Event(name=name, province=province, venue=d["venue"] if valid_venue(d["venue"]) else "",
               start_date=d["start_date"], end_date=d["end_date"] or d["start_date"], start_time=d["start_time"],
               crowd=d["crowd"], fireworks=d["fireworks"], big_concert=d["big_concert"], summary=d["summary"], sources=[])
    if ev.last_date < today - timedelta(days=3):
        return "past"
    if ev.start_date > today + timedelta(days=75):
        return "far"
    match = next((c for c in db.candidates_for_dedupe(province, ev.start_date, ev.end_date) if is_duplicate(ev, c)), None)
    if match:
        if merge_into(match, ev):
            db.update_event(match)
            return "merged"
        return "dup"
    db.insert_event(ev)
    return "new"


def run_discover(db: DB, llm: LLM | None, today: date, deadline: float, limit: int | None = None) -> int:
    """Mỗi lượt hỏi Gemini+Google Search về vài tỉnh (tỉnh nào lâu chưa hỏi nhất trước, mỗi tỉnh >= 12 giờ/lần).
    Kết quả từng tỉnh (Gemini trả bao nhiêu, bao nhiêu mới/trùng/bị loại) lưu ở meta disc_res_<tỉnh> để xem bằng /trangthai."""
    if not llm or not llm.can_search():
        return 0
    limit = limit or config.discover_provinces_per_run()
    now, new, done = config.now(), 0, 0
    for k in sorted(PROVINCES, key=lambda k: db.get(f"disc_{k}", "")):
        if done >= limit or deadline - time.monotonic() < 45 or not llm.can_search():
            break
        last = db.get(f"disc_{k}")
        if last and now - datetime.fromisoformat(last) < timedelta(hours=12):
            break  # đã xếp theo thời điểm cũ nhất; các tỉnh còn lại đều mới hỏi
        res = llm.discover(k, today)
        if res is None:
            break  # lỗi gọi (hết hạn mức...) - thử lại lượt sau
        done += 1
        stamp = now.strftime("%d/%m %H:%M")
        if not llm.last_parse_ok:  # không đọc được JSON: KHÔNG đánh dấu đã hỏi để lượt sau thử lại
            db.set(f"disc_res_{k}", f"{stamp}: không đọc được JSON của Gemini (sẽ thử lại)")
            continue
        stats: dict[str, int] = {}
        for d in res:
            st = ingest_ai(db, d, k, today)
            stats[st] = stats.get(st, 0) + 1
        new += stats.get("new", 0)
        detail = ", ".join(f"{a}={b}" for a, b in stats.items()) or "không có sự kiện hợp lệ"
        db.set(f"disc_{k}", now.isoformat(timespec="seconds"))
        db.set(f"disc_res_{k}", f"{stamp}: Gemini trả {llm.last_raw}, hợp lệ {len(res)} -> {detail}")
        log.info("AI tìm kiếm %s: trả %d, hợp lệ %d -> %s", k, llm.last_raw, len(res), detail)
        time.sleep(2)
    return new


def prune_foreign(db: DB) -> list[str]:
    """Xoá sự kiện đã lưu từ bài ngoại ngữ (chạy một lần tự động)."""
    gone = []
    for e in db.all_events():
        if not_vietnamese(f"{e.name} {e.summary}"):
            db.delete_event(e.id)
            gone.append(e.name[:70])
    return gone


def prune_noise(db: DB) -> list[str]:
    """Xoá khỏi DB các 'sự kiện' không phải lễ hội/văn hoá cụ thể: tin hành chính, tin chung chung không có tên lễ hội,
    hoặc sự kiện ở ngoài 8 tỉnh (vd Hà Nội). Trả về danh sách đã xoá."""
    gone = []
    for e in db.all_events():
        head = f"{e.name} {e.venue}"
        own = score_provinces(head, e.summary).get(e.province, 0)
        why = None
        if e.sources and all(is_bulletin("", u) for u in e.sources):
            why = "chỉ có nguồn là bản tin thời sự (ngày là ngày phát sóng)"
        elif not is_cultural_event(e.name, e.summary, e.crowd, e.fireworks, e.big_concert):
            why = "không phải sự kiện văn hoá"
        elif not event_name(e.name, e.summary):
            why = "không có tên lễ hội cụ thể"
        elif held_elsewhere(e.name, e.summary):
            why = "diễn ra ngoài 8 tỉnh"
        elif other_area_score(head, e.summary) > own:
            why = "ngoài 8 tỉnh"
        if why:
            db.delete_event(e.id)
            gone.append(f"{e.start_date} | {e.name[:70]} ({why})")
    return gone


# ---------- Xử lý 1 bài ----------
def ingest(db: DB, item: Item, body: str, llm: LLM | None, today: date, require_date: bool = False) -> str:
    """Trả về trạng thái: skip | out_of_scope | not_event | past | new | merged | dup."""
    if is_bulletin(item.title, item.link):
        return "not_event"  # bản tin thời sự tổng hợp: ngày trong tít là ngày phát sóng, không phải ngày sự kiện
    if title_elsewhere(item.title):
        return "out_of_scope"  # tít nói về nơi ngoài 8 tỉnh
    if not_vietnamese(item.title) or not_vietnamese(f"{item.summary} {body}"[:300]):
        return "not_event"  # bài ngoại ngữ
    text_all = f"{item.summary} {body}"
    # chỉ xét tít + phần đầu bài: nơi diễn ra sự kiện luôn nằm ở đó, tránh bài Hà Nội có nhắc TP.HCM ở cuối bài
    lead = text_all.strip()[:1500]
    if held_elsewhere(item.title, lead):
        return "out_of_scope"  # đơn vị trong vùng đi biểu diễn/giao lưu nơi khác (vd Hà Nội)
    # bài nhắc tới nơi NGOÀI 8 tỉnh nhiều hơn 8 tỉnh (vd tin Hà Nội đăng trên báo Vĩnh Long) -> không phải sự kiện của vùng này
    full = text_all.strip()[:4000]
    for seg in (lead, full):  # kiểm tra cả đoạn đầu lẫn toàn bài: nhắc Hà Nội/nơi khác nhiều hơn 8 tỉnh -> loại
        own = score_provinces(item.title, seg)
        oa = other_area_score(item.title, seg)
        if oa and oa > max(own.values(), default=0):
            return "out_of_scope"
    strong = detect_province(item.title, lead)
    hint = item.province_hint if item.province_hint in PROVINCES else None
    # gợi ý của nguồn chỉ được tin khi tít/đoạn mở đầu có nhắc tới tỉnh đó
    province = strong or (hint if hint and score_provinces(item.title, lead).get(hint) else None)
    if not province and item.trust_hint and hint:
        province = hint  # báo địa phương (vd Báo Cần Thơ): bài hiếm khi nhắc tên tỉnh trong tít -> tin theo nguồn
    if not province and item.is_gnews and not body and hint:
        province = hint  # chưa đọc được bài: tin theo vùng đã tìm trên Google News (đã chặn tít nói về nơi khác)
    if not province:
        return "out_of_scope"
    info = extract_rules(item.title, f"{item.summary}\n{body}".strip(), item.published, today)
    ai_ok = False
    if llm and llm.enabled:
        ai = llm.extract(item.title, f"{item.summary}\n{body}".strip(), item.published, today)
        if ai:
            if not ai["is_event"]:
                return "not_event"
            ai_ok = True
            if not ai["province"]:
                return "out_of_scope"  # AI xác định sự kiện diễn ra ngoài 8 tỉnh -> tin AI hơn regex
            province = ai["province"] or province
            for k in ("name", "venue", "start_date", "end_date", "start_time", "crowd", "summary"):
                if ai.get(k) and (k != "venue" or valid_venue(ai[k])):
                    info[k] = ai[k]
            info["fireworks"] = info["fireworks"] or ai["fireworks"]
            info["big_concert"] = info["big_concert"] or ai["big_concert"]
    if not ai_ok and not info.get("name_ok"):
        return "not_event"  # tin chung chung về lễ hội, không có TÊN một lễ hội/sự kiện cụ thể
    if not ai_ok and not is_cultural_event(item.title, f"{item.summary} {body}".strip(), info["crowd"],
                                           info["fireworks"], info["big_concert"]):
        return "not_event"  # tin hành chính / hội nghị / tập huấn... không phải lễ hội, văn hoá, sự kiện đông người
    if is_prep_title(item.title):  # ngày diễn ra chỉ lấy từ nội dung bài, không lấy từ tít (hạn chót chuẩn bị)
        bd = choose_date(find_dates(f"{item.summary}\n{body}", item.published or today), item.published or today)
        info["start_date"], info["end_date"] = bd if bd else (None, None)
    info["name"] = canonical_name(info["name"])
    # địa điểm/tên nằm ngoài 8 tỉnh (vd "Trung tâm Hội nghị Quốc gia", "Thủ đô") -> loại
    if (other_area_score(f"{info['name']} {info['venue']}") and
            not score_provinces(f"{info['name']} {info['venue']}", "").get(province)):
        return "out_of_scope"
    ev = Event(name=info["name"], province=province, venue=info["venue"], start_date=info["start_date"],
               end_date=info["end_date"], start_time=info["start_time"], crowd=info["crowd"],
               fireworks=info["fireworks"], big_concert=info["big_concert"], summary=info["summary"],
               sources=[item.link])
    if ev.last_date and ev.last_date < today - timedelta(days=3):
        return "past"
    if require_date and ev.start_date is None:
        return "not_event"  # tin không có từ khoá sự kiện thì phải có ngày mới giữ
    cands = db.candidates_for_dedupe(province, ev.start_date, ev.end_date)
    match = next((c for c in cands if is_duplicate(ev, c)), None)
    if not match and llm and llm.enabled:
        match = next((c for c in cands[:5] if _maybe_same(ev, c) and llm.same_event(_desc(ev), _desc(c))), None)
    if match:
        if merge_into(match, ev):
            db.update_event(match)
            return "merged"
        return "dup"
    db.insert_event(ev)
    return "new"


# ---------- Thu thập ----------
def run_collect(db: DB, tg: Telegram | None, llm: LLM | None = None) -> dict:
    today = config.today()
    if db.get("fix_scope_v3") != "1":  # dọn một lần: xử lý lại các bài từng bị loại (giữ nguyên sự kiện đã lưu)
        db.conn.execute("DELETE FROM articles")
        db.conn.commit()
        db.set("fix_scope_v3", "1")
    db.conn.execute("DELETE FROM articles WHERE status IN ('no_body','title_only') AND seen_at < ?",
                    ((config.now() - timedelta(hours=6)).isoformat(timespec="seconds"),))
    db.conn.commit()
    if db.get("fix_prep_v1") != "1":  # dọn một lần dữ liệu cũ bị nhận nhầm ngày chuẩn bị
        log.info("Dọn dữ liệu cũ: %d sự kiện đã gộp", dedupe_existing(db, None))
        db.set("fix_prep_v1", "1")
    if db.get("fix_elsewhere_v1") != "1":  # dọn một lần: sự kiện diễn ra ngoài 8 tỉnh (vd Hà Nội)
        log.info("Dọn sự kiện ngoài 8 tỉnh: %d", len(prune_elsewhere(db)))
        db.set("fix_elsewhere_v1", "1")
    if db.get("fix_foreign_v1") != "1":
        log.info("Dọn sự kiện ngoại ngữ: %d", len(prune_foreign(db)))
        db.set("fix_foreign_v1", "1")
    if llm:
        log.info("AI: %s", llm.status())
        if llm.enabled and db.get("fix_llm_v1") != "1":  # bật AI lần đầu: cho AI xét lại các bài regex từng loại/chỉ có tít
            db.conn.execute("DELETE FROM articles WHERE status IN ('not_event','title_only','no_body')")
            db.conn.commit()
            db.set("fix_llm_v1", "1")
    sources = load_sources()
    stats = {"sources": len(sources), "failed": 0, "items": 0, "new": 0, "merged": 0, "fetched": 0, "llm": 0}
    items: list[Item] = []
    t0 = time.monotonic()
    deadline = t0 + config.collect_budget_sec()
    art_deadline = deadline - min(90, config.collect_budget_sec() // 3)  # chừa thời gian cho tìm kiếm AI + làm giàu

    def _one(src):
        try:
            return src, fetch_source(src), None
        except Exception as e:  # noqa - 1 nguồn hỏng không làm dừng cả lượt
            return src, None, e

    with ThreadPoolExecutor(max_workers=8) as ex:  # tải song song các nguồn: nhanh hơn nhiều so với lần lượt
        results = list(ex.map(_one, sources))
    for src, got, err in results:
        if err is None:
            recovered = db.health_ok(src["id"], src["name"], len(got))
            items += got
            log.info("OK   %-28s %3d mục", src["id"], len(got))
            if recovered and tg:
                tg.broadcast(f"✅ Nguồn đã hoạt động trở lại: {src['name']}", config.admin_ids())
        else:
            stats["failed"] += 1
            n = db.health_fail(src["id"], src["name"], _short(err))
            log.error("LỖI %-28s (lần %d liên tiếp): %s", src["id"], n, _short(err))
    log.info("Đã tải %d nguồn sau %.0fs", len(sources), time.monotonic() - t0)

    items.sort(key=lambda i: i.published or today, reverse=True)
    cap = config.max_article_fetch()
    for it in items:
        if db.seen(it.link):
            continue
        stats["items"] += 1
        hits = event_keyword_hits(f"{it.title} {it.summary}")
        # Google News đã được lọc theo chủ đề ngay từ truy vấn -> không bắt buộc có từ khoá trong tít
        if (not hits and not it.is_gnews) or has_negative(it.title):
            db.add_article(it.link, it.source_id, it.title, "skip")
            continue
        if stats["fetched"] >= cap or time.monotonic() > art_deadline:
            continue  # lượt sau xử lý tiếp, chưa đánh dấu đã xem
        body = fetch_article_text(it)
        stats["fetched"] += 1
        if not body and it.is_gnews and not (detect_province(it.title) or it.province_hint):
            db.add_article(it.link, it.source_id, it.title, "no_body")  # chưa đọc được bài và tít không nêu tỉnh
            continue
        status = ingest(db, it, body, llm, today, require_date=not hits)
        if status == "new":
            stats["new"] += 1
        elif status == "merged":
            stats["merged"] += 1
        if not body and it.is_gnews and status in ("new", "merged", "dup"):
            status = "title_only"  # mới có tít: 6 giờ sau thử tải lại nội dung để bổ sung ngày/địa điểm
        db.add_article(it.link, it.source_id, it.title, status)
        db.conn.commit()  # lưu ngay từng bài: lỡ job bị huỷ giữa chừng vẫn không mất tiến độ
    db.conn.commit()
    from .collector import GN_STATS
    db.set("last_gn", f"giải mã được {GN_STATS['ok']}, thất bại {GN_STATS['fail']}")
    stats["gn"] = dict(GN_STATS)

    try:
        stats["discovered"] = run_discover(db, llm, today, deadline)
        db.set("last_discover", f"{config.now().isoformat(timespec='seconds')}: thêm {stats['discovered']} sự kiện")
    except Exception as e:  # noqa
        log.warning("Bước tìm kiếm AI lỗi: %s", _short(e))

    try:  # từ tên sự kiện đã lưu, tìm lại bài báo (ưu tiên báo địa phương) để bổ sung ngày/địa điểm
        from .enrich import enrich_events
        if deadline - time.monotonic() > 30:
            stats["enriched"] = enrich_events(db, llm, today)
    except Exception as e:  # noqa
        log.warning("Bước làm giàu lỗi: %s", _short(e))

    if llm:
        stats["llm"] = llm.calls
        db.set("llm_state", llm.status())
    notify_source_failures(db, tg)
    if tg:
        send_alerts(db, tg)
    db.purge_old()
    db.set("last_collect", config.now().isoformat(timespec="seconds"))
    log.info("Xong thu thập: %s", stats)
    return stats


def _short(e: Exception) -> str:
    """Rút gọn lỗi (bỏ URL dài) để log và tin nhắn gọn."""
    msg = re.sub(r"https?://\S+", "<url>", str(e))
    return f"{type(e).__name__}: {msg[:140]}"


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


def prune_dead(db: DB, tg: Telegram) -> None:
    """Xoá người đăng ký đã chặn bot để không gửi lại."""
    for cid in list(tg.dead):
        db.remove_subscriber(cid)
    tg.dead.clear()


def send_alerts(db: DB, tg: Telegram) -> int:
    tg.set_footer(db)
    n = 0
    for e in db.unalerted_large(config.today()):
        if tg.broadcast(format_alert(e), db.recipients()):
            db.mark_alerted(e.id)
            n += 1
    prune_dead(db, tg)
    return n


# ---------- Bản tin hằng ngày ----------
def run_digest(db: DB, tg: Telegram, force: bool = False) -> bool:
    today = config.today()
    if not force and db.get("last_digest_date") == today.isoformat():
        log.info("Bản tin hôm nay đã gửi, bỏ qua.")
        return False
    tg.set_footer(db)
    days = config.lookahead_days()
    events = db.events_between(today, today + timedelta(days=days - 1))
    ok = tg.broadcast(format_digest(events, today, days), db.recipients())
    prune_dead(db, tg)
    if ok:
        db.set("last_digest_date", today.isoformat())
    return bool(ok)
