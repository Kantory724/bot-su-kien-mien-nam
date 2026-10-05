#!/usr/bin/env python3
"""Bot tin lễ hội, sự kiện miền Nam – điểm vào dòng lệnh. Xem README.md."""
import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from bot import config


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        (config.ROOT / "logs").mkdir(exist_ok=True)
        handlers.append(RotatingFileHandler(config.ROOT / "logs" / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bot tin lễ hội, sự kiện miền Nam")
    ap.add_argument("--dry-run", action="store_true", help="in tin ra màn hình thay vì gửi Telegram")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="chạy thử bằng dữ liệu giả, không cần mạng/token")
    sub.add_parser("test-send", help="gửi tin thử để kiểm tra token và chat id")
    sub.add_parser("get-chat-id", help="liệt kê chat id của những người đã nhắn cho bot")
    sub.add_parser("check-sources", help="kiểm tra từng nguồn tin còn hoạt động không")
    sub.add_parser("collect", help="thu thập tin + cảnh báo sự kiện lớn")
    u = sub.add_parser("add-url", help="thêm tay một bài báo (link thường hoặc Google News) vào danh sách sự kiện")
    u.add_argument("url")
    sub.add_parser("enrich", help="tìm lại bài báo để bổ sung ngày/địa điểm cho các sự kiện còn thiếu")
    sub.add_parser("discover", help="hỏi Gemini+Google Search tìm sự kiện cả 8 tỉnh")
    sub.add_parser("set-commands", help="đặt menu gợi ý lệnh (gõ / trong Telegram)")
    sub.add_parser("prune-noise", help="xoá khỏi DB các tin không phải lễ hội/văn hoá/sự kiện")
    sub.add_parser("dedupe", help="gộp các sự kiện trùng đã lưu trong DB")
    d = sub.add_parser("digest", help="gửi bản tin 7 ngày tới")
    d.add_argument("--force", action="store_true", help="gửi lại dù hôm nay đã gửi")
    sub.add_parser("poll", help="trả lời lệnh người dùng (một lượt)")
    sub.add_parser("tick", help="poll + thu thập/bản tin nếu đến hạn (dùng cho GitHub Actions/cron)")
    sub.add_parser("serve", help="chạy liên tục 24/7 (VPS/Docker)")
    e = sub.add_parser("export", help="xuất Excel")
    e.add_argument("--period", choices=["week", "month"], default="week")
    e.add_argument("--out")
    e.add_argument("--send", action="store_true", help="gửi file qua Telegram")
    a = ap.parse_args(argv)

    setup_logging()
    if a.cmd == "demo":
        from bot.demo import run_demo
        run_demo()
        return 0

    from bot.collector import fetch_source, load_sources
    from bot.db import DB
    from bot.exporter import export_excel
    from bot.geo import PROVINCES
    from bot.llm import LLM
    from bot.pipeline import _short, dedupe_existing, prune_noise, run_collect, run_digest, run_discover
    from bot.runner import serve, tick
    from bot.telegram import Telegram

    if a.cmd == "check-sources":
        bad = 0
        for s in load_sources():
            try:
                items = fetch_source(s)
                print(f"OK   {s['id']:28s} {len(items):3d} mục | mới nhất: {items[0].title[:60] if items else '-'}")
            except Exception as ex:  # noqa
                bad += 1
                print(f"LỖI  {s['id']:28s} {_short(ex)}")
        print(f"\nTổng: {bad} nguồn lỗi")
        return 1 if bad else 0

    if a.cmd == "add-url":
        from datetime import timedelta
        from bot.collector import fetch_article_text, item_from_url
        from bot.pipeline import ingest
        db, llm = DB(), LLM()
        try:
            item = item_from_url(a.url)
            body = fetch_article_text(item)
            print(f"Tít: {item.title}\nĐọc được {len(body)} ký tự nội dung")
            db.conn.execute("DELETE FROM articles WHERE url=?", (item.link,))  # cho phép xử lý lại bài từng bị loại
            today = config.today()
            status = ingest(db, item, body, llm, today)
            db.add_article(item.link, item.source_id, item.title, status)
            db.conn.commit()
            print(f"Kết quả: {status}  (new/merged/dup = đã có trong DB; out_of_scope = không thuộc 8 tỉnh; "
                  f"not_event/past = bị loại)")
            for e in db.search(item.title.split(" lần")[0][:30], today - timedelta(days=3))[:3]:
                print(f" -> {e.start_date}~{e.end_date} | {e.province} | {e.venue or '(chưa rõ địa điểm)'} | {e.name}")
        finally:
            db.close()
        return 0

    tg = Telegram(dry_run=a.dry_run or (a.cmd == "export" and not a.send))
    if a.cmd == "get-chat-id":
        seen = {}
        for u in tg.get_updates(None):
            m = u.get("message") or {}
            c = m.get("chat") or {}
            if c.get("id"):
                seen[c["id"]] = c.get("title") or c.get("username") or c.get("first_name", "")
        print("\n".join(f"{k}\t{v}" for k, v in seen.items()) or "Chưa có ai nhắn cho bot. Hãy mở bot trên Telegram, bấm Start rồi chạy lại.")
        return 0
    if a.cmd == "test-send":
        n = tg.broadcast("✅ Bot tin sự kiện miền Nam đã kết nối thành công.\nGõ /help để xem lệnh.")
        print(f"Đã gửi tới {n}/{len(config.chat_ids())} chat")
        return 0 if n else 1
    if a.cmd == "set-commands":
        tg.set_commands([
            ("homnay", "Sự kiện hôm nay"), ("tuannay", "Sự kiện 7 ngày tới"),
            ("tinh", "Sự kiện theo tỉnh (vd: /tinh Cần Thơ)"), ("sukien", "Tìm sự kiện theo từ khóa"),
            ("excel", "Nhận file Excel (tuan hoặc thang)"), ("trangthai", "Tình trạng hệ thống"),
            ("tatca", "Liệt kê mọi sự kiện đang lưu"), ("help", "Xem danh sách lệnh"),
            ("stop", "Huỷ nhận tin"), ("id", "Xem Chat ID")])
        print("Đã đặt menu lệnh")
        return 0
    db = DB()
    llm = LLM()
    try:
        if a.cmd == "collect":
            run_collect(db, tg, llm)
        elif a.cmd == "discover":
            for k in PROVINCES:  # ép hỏi lại cả 8 tỉnh, bỏ qua mốc 'đã hỏi gần đây'
                db.set(f"disc_{k}", "")
            n = run_discover(db, llm, config.today(), time.monotonic() + 600)
            db.set("last_discover", f"{config.now().isoformat(timespec='seconds')}: thêm {n} sự kiện")
            db.set("llm_state", llm.status())
            print(f"Đã thêm {n} sự kiện mới")
            print("AI:", llm.status())
        elif a.cmd == "enrich":
            from bot.enrich import enrich_events
            print(f"Đã bổ sung {enrich_events(db, llm, config.today(), force=True, limit=10)} sự kiện")
        elif a.cmd == "prune-noise":
            gone = prune_noise(db)
            print("\n".join(gone) or "Không có tin nào cần xoá")
            print(f"Đã xoá {len(gone)} tin không phải sự kiện")
        elif a.cmd == "dedupe":
            print(f"Đã gộp {dedupe_existing(db, llm)} sự kiện trùng")
        elif a.cmd == "digest":
            run_digest(db, tg, force=a.force)
        elif a.cmd == "poll":
            from bot.runner import poll_once
            poll_once(db, tg)
        elif a.cmd == "tick":
            tick(db, tg, llm)
        elif a.cmd == "serve":
            serve(db, tg, llm)
        elif a.cmd == "export":
            p = export_excel(db, a.period, a.out)
            print(f"Đã xuất: {p}")
            if a.send:
                for cid in db.recipients():
                    tg.send_document(cid, p, "Danh sách sự kiện")
    except KeyboardInterrupt:
        pass
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
