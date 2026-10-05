"""Điều phối: poll lệnh, lịch thu thập/bản tin ("tick") và chế độ chạy liên tục ("serve")."""
import logging
import time
from datetime import datetime, timedelta

from . import config
from .commands import Reply, handle, parse
from .db import DB
from .exporter import export_excel
from .llm import LLM
from .pipeline import run_collect, run_digest
from .telegram import Telegram

log = logging.getLogger("runner")
OPEN_CMDS = {"start", "stop", "help", "id"}  # ai cũng dùng được: đăng ký / huỷ / trợ giúp


def poll_once(db: DB, tg: Telegram, timeout: int = 0) -> int:
    """Đọc tin nhắn mới và trả lời. Chỉ chat nằm trong TELEGRAM_CHAT_IDS mới dùng được các lệnh tra cứu."""
    offset = int(db.get("tg_offset", "0") or 0) or None
    updates = tg.get_updates(offset, timeout)
    allowed = set(db.recipients())
    for u in updates:
        db.set("tg_offset", u["update_id"] + 1)
        msg = u.get("message") or {}
        text, chat_id = msg.get("text"), str((msg.get("chat") or {}).get("id", ""))
        if not text or not chat_id:
            continue
        cmd, _ = parse(text)
        if not cmd:
            continue
        try:
            if chat_id not in allowed and cmd not in OPEN_CMDS:
                tg.send(chat_id, "Bạn chưa đăng ký nhận tin. Gõ /start" + (" <mã tham gia>" if config.join_code() else "") + " để đăng ký.")
                continue
            rep: Reply = handle(text, chat_id, db, (msg.get("chat") or {}).get("first_name") or (msg.get("chat") or {}).get("title") or "")
            for t in rep.texts:
                tg.send(chat_id, t)
            if rep.excel:
                path = export_excel(db, rep.excel)
                tg.send_document(chat_id, path, f"Danh sách sự kiện ({'tháng' if rep.excel == 'month' else '7 ngày'})")
        except Exception as e:  # noqa
            log.error("Lỗi xử lý lệnh %s: %s", cmd, e)
    return len(updates)


def _last_collect_age(db: DB) -> timedelta | None:
    v = db.get("last_collect")
    return config.now() - datetime.fromisoformat(v) if v else None


def tick(db: DB, tg: Telegram, llm: LLM | None = None, poll_timeout: int = 0) -> None:
    """Một vòng: trả lời lệnh → thu thập nếu đến hạn → gửi bản tin nếu đến 07:00 mà chưa gửi."""
    try:
        poll_once(db, tg, poll_timeout)
    except Exception as e:  # noqa
        log.error("Poll lệnh lỗi: %s", e)

    now = config.now()
    age = _last_collect_age(db)
    if age is None or age >= timedelta(minutes=config.collect_interval_min()):
        run_collect(db, tg, llm)
        age = timedelta(0)

    due = (now.hour >= config.digest_hour() and now.hour < 22 and db.get("last_digest_date") != now.date().isoformat())
    if due:
        if age >= timedelta(minutes=30):
            run_collect(db, tg, llm)
        if run_digest(db, tg):
            if now.weekday() == 0 and db.get("last_week_excel") != now.date().isoformat():
                _send_excel(db, tg, "week")
                db.set("last_week_excel", now.date().isoformat())
            if now.day == 1 and db.get("last_month_excel") != now.date().isoformat():
                _send_excel(db, tg, "month")
                db.set("last_month_excel", now.date().isoformat())


def _send_excel(db: DB, tg: Telegram, period: str) -> None:
    path = export_excel(db, period)
    cap = "Danh sách sự kiện tuần này" if period == "week" else "Danh sách sự kiện tháng này"
    for cid in db.recipients():
        try:
            tg.send_document(cid, path, cap)
        except Exception as e:  # noqa
            log.error("Gửi Excel tới %s lỗi: %s", cid, e)


def serve(db: DB, tg: Telegram, llm: LLM | None = None) -> None:
    """Chạy liên tục (VPS/Docker/Render/máy riêng): trả lời lệnh gần như tức thì."""
    log.info("Bot chạy chế độ liên tục. Nhấn Ctrl+C để dừng.")
    while True:
        try:
            tick(db, tg, llm, poll_timeout=25)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # noqa
            log.error("Lỗi vòng lặp: %s", e)
            time.sleep(15)
