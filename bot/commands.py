"""Xử lý lệnh người dùng gõ vào bot: /homnay /tuannay /tinh /sukien ..."""
from dataclasses import dataclass, field
from datetime import timedelta

from . import config
from .db import DB
from .formatter import fmt_range, format_list
from .geo import PROVINCES, province_name, resolve_province

HELP = """Bot tin lễ hội, sự kiện miền Nam
/homnay – sự kiện đang diễn ra hôm nay
/tuannay – sự kiện trong 7 ngày tới
/tinh <tên tỉnh> – sự kiện 60 ngày tới của một tỉnh (vd: /tinh Cần Thơ)
/sukien <từ khóa> – tìm sự kiện (vd: /sukien pháo hoa)
/excel [tuan|thang] – nhận file Excel danh sách sự kiện
/trangthai – tình trạng hệ thống và các nguồn tin
/tatca – liệt kê mọi sự kiện đang lưu (kiểm tra dữ liệu)
/id – xem Chat ID của cuộc trò chuyện này
Tỉnh hỗ trợ: """ + ", ".join(v[0] for v in PROVINCES.values())


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
        ev = db.events_between(today, today + timedelta(days=d - 1))
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
        return Reply([format_list(f"KẾT QUẢ CHO “{arg}” ({len(ev)})", ev)])
    if cmd == "excel":
        period = "month" if arg.lower() in ("thang", "tháng", "month") else "week"
        return Reply(["Đang tạo file Excel…"], excel=period)
    if cmd == "tatca":
        evs = sorted((e for e in db.all_events() if not e.last_date or e.last_date >= today),
                     key=lambda e: (e.start_date is None, e.start_date or today))
        if not evs:
            return Reply(["DB chưa có sự kiện nào."])
        lines = [f"Đang lưu {len(evs)} sự kiện:"]
        lines += [f"{i}. {fmt_range(e.start_date, e.end_date)} · {province_name(e.province)} · {e.name[:70]}"
                  for i, e in enumerate(evs[:40], 1)]
        return Reply(["\n".join(lines)])
    if cmd == "trangthai":
        rows = db.health_all()
        bad = [r for r in rows if r["fail_count"]]
        lines = [f"Số sự kiện đang lưu: {db.count_events()}",
                 f"Lần thu thập gần nhất: {db.get('last_collect', 'chưa có')}",
                 f"Bản tin gần nhất: {db.get('last_digest_date', 'chưa có')}",
                 f"Nguồn: {len(rows)} (lỗi: {len(bad)})"]
        st = db.conn.execute("SELECT status, COUNT(*) FROM articles GROUP BY 1 ORDER BY 2 DESC").fetchall()
        lines.append("Bài đã xử lý: " + (", ".join(f"{r[0]}={r[1]}" for r in st) or "chưa có"))
        lines += [f" - {r['name']}: lỗi {r['fail_count']} lần – {r['last_error'][:80]}" for r in bad]
        return Reply(["\n".join(lines)])
    if cmd:
        return Reply(["Lệnh không hợp lệ. Gõ /help để xem danh sách lệnh."])
    return Reply(["Gõ /help để xem các lệnh."])
