"""Xử lý lệnh người dùng gõ vào bot: /homnay /tuannay /tinh /sukien ..."""
import json
from dataclasses import dataclass, field
from datetime import timedelta

from . import config
from .db import DB
from .formatter import format_list
from .geo import PROVINCES, province_name, resolve_province

HELP = """Bot tin lễ hội, sự kiện miền Nam
/homnay – sự kiện đang diễn ra hôm nay
/tuannay – sự kiện trong 7 ngày tới
/tinh <tên tỉnh> – sự kiện 60 ngày tới của một tỉnh (vd: /tinh Cần Thơ)
/sukien <từ khóa> – tìm sự kiện (vd: /sukien pháo hoa)
/excel [tuan|thang] – nhận file Excel danh sách sự kiện
/trangthai – tình trạng hệ thống và các nguồn tin
/id – xem Chat ID của cuộc trò chuyện này
Tỉnh hỗ trợ: """ + ", ".join(v[0] for v in PROVINCES.values())


STAT_LABELS = [("items", "bài mới xem"), ("fetched", "bài đã tải nội dung"), ("body_empty", "không đọc được nội dung bài"),
               ("new", "sự kiện mới"), ("merged", "gộp vào sự kiện cũ"), ("dup", "trùng"),
               ("skip", "không phải sự kiện"), ("out_of_scope", "ngoài địa bàn"), ("no_date", "thiếu ngày"),
               ("past", "đã qua"), ("far", "quá xa"), ("not_event", "AI loại"), ("failed", "nguồn lỗi")]


def _stats_text(raw: str | None) -> str:
    try:
        st = json.loads(raw or "")
    except ValueError:
        return "Kết quả thu thập: chưa có"
    parts = [f"{lab} {st[k]}" for k, lab in STAT_LABELS if st.get(k)]
    note = " (dừng sớm, sẽ làm tiếp)" if st.get("stopped_early") else ""
    return "Kết quả thu thập gần nhất: " + (", ".join(parts) or "không có bài mới") + note


@dataclass
class Reply:
    texts: list[str] = field(default_factory=list)
    excel: str | None = None  # "week" | "month"


def parse(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    if not text.startswith("/"):
        return "", text
    first, _, arg = text.partition(" ")
    cmd = first[1:].split("@")[0].lower()
    return cmd, arg.strip()


def handle(text: str, chat_id: str, db: DB) -> Reply:
    cmd, arg = parse(text)
    today = config.today()
    if cmd in ("start", "help"):
        return Reply([HELP])
    if cmd == "id":
        return Reply([f"Chat ID của bạn: {chat_id}"])
    if cmd == "homnay":
        ev = db.events_between(today, today)
        return Reply([format_list(f"SỰ KIỆN HÔM NAY {today:%d/%m/%Y}: {len(ev)}", ev)])
    if cmd in ("tuannay", "tuan"):
        d = config.lookahead_days()
        ev = db.events_between(today, today + timedelta(days=d))
        return Reply([format_list(f"SỰ KIỆN {d} NGÀY TỚI ({len(ev)})", ev)])
    if cmd == "tinh":
        if not arg:
            return Reply(["Cú pháp: /tinh <tên tỉnh>\nVí dụ: /tinh Cần Thơ\nTỉnh hỗ trợ: "
                          + ", ".join(v[0] for v in PROVINCES.values())])
        key = resolve_province(arg)
        if not key:
            return Reply([f"Không nhận ra tỉnh “{arg}”. Tỉnh hỗ trợ: " + ", ".join(v[0] for v in PROVINCES.values())])
        ev = db.events_between(today, today + timedelta(days=60), key)
        return Reply([format_list(f"SỰ KIỆN 60 NGÀY TỚI – {province_name(key).upper()} ({len(ev)})", ev)])
    if cmd == "sukien":
        if not arg:
            return Reply(["Cú pháp: /sukien <từ khóa>\nVí dụ: /sukien pháo hoa, /sukien Ok Om Bok"])
        ev = db.search(arg, today)
        return Reply([format_list(f"KẾT QUẢ CHO “{arg}” ({len(ev)})", ev, show_summary=True)])
    if cmd == "excel":
        period = "month" if arg.lower() in ("thang", "tháng", "month") else "week"
        return Reply(["Đang tạo file Excel…"], excel=period)
    if cmd == "trangthai":
        rows = db.health_all()
        bad = [r for r in rows if r["fail_count"]]
        lines = [f"Số sự kiện đang lưu: {db.count_events()}",
                 f"Lần thu thập gần nhất: {db.get('last_collect', 'chưa có')}",
                 f"Bản tin gần nhất: {db.get('last_digest_date', 'chưa có')}",
                 _stats_text(db.get("last_stats")),
                 f"Nguồn: {len(rows)} (lỗi: {len(bad)})"]
        lines += [f" - {r['name']}: lỗi {r['fail_count']} lần – {r['last_error'][:80]}" for r in bad]
        return Reply(["\n".join(lines)])
    if cmd:
        return Reply(["Lệnh không hợp lệ. Gõ /help để xem danh sách lệnh."])
    return Reply(["Gõ /help để xem các lệnh."])
