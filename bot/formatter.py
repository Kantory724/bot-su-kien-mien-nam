"""Định dạng tin nhắn (văn bản thuần, an toàn cho Telegram, không cần escape)."""
from datetime import date

from . import config
from .geo import province_name
from .models import PRIORITY_LABEL, Event

WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def fmt_num(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def fmt_range(a: date | None, b: date | None) -> str:
    if not a:
        return "chưa rõ ngày"
    b = b or a
    if a == b:
        return f"{a.day}/{a.month}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day}/{a.month}"
    return f"{a.day}/{a.month}–{b.day}/{b.month}"


def format_event(e: Event, show_summary: bool = False) -> str:
    lines = [f"[{PRIORITY_LABEL.get(e.priority, 'THẤP')}] {fmt_range(e.start_date, e.end_date)} · {province_name(e.province)}",
             e.name]
    lines.append(f"Địa điểm: {e.venue}" if e.venue else "Địa điểm: chưa rõ (xem nguồn)")
    if e.start_time:
        lines.append(f"Giờ: {e.start_time}")
    scale = []
    if e.shown_crowd:
        scale.append(f"~{fmt_num(e.shown_crowd)} người")
    if e.fireworks:
        scale.append("có bắn pháo hoa")
    if e.big_concert:
        scale.append("đại nhạc hội/countdown")
    if scale:
        lines.append("Quy mô: " + " · ".join(scale))
    if show_summary and e.summary:
        lines.append(f"Tóm tắt: {e.summary}")
    if e.sources:
        extra = f" (+{len(e.sources) - 1} nguồn khác)" if len(e.sources) > 1 else ""
        lines.append(f"Nguồn: {e.sources[0]}{extra}")
    return "\n".join(lines)


def format_digest(events: list[Event], today: date, days: int) -> str:
    wd = WEEKDAYS[today.weekday()]
    head = f"BẢN TIN SỰ KIỆN MIỀN NAM — {config.digest_hour():02d}:00 {wd} {today:%d/%m/%Y}"
    if not events:
        return f"{head}\n{days} ngày tới: chưa ghi nhận sự kiện nào.\n\nGõ /sukien <từ khóa> để tra cứu."
    large = sum(1 for e in events if e.is_large)
    sub = f"{days} ngày tới: {len(events)} sự kiện" + (f" ({large} quy mô lớn)" if large else "")
    body = "\n\n".join(format_event(e) for e in events)
    foot = "Gõ /tuannay để xem đầy đủ, /tinh <tên tỉnh> để lọc."
    return f"{head}\n{sub}\n\n{body}\n\n{foot}"


def format_list(title: str, events: list[Event], limit: int = 25, show_summary: bool = False) -> str:
    if not events:
        return f"{title}\nChưa có sự kiện nào được ghi nhận."
    shown = events[:limit]
    txt = f"{title}\n\n" + "\n\n".join(format_event(e, show_summary) for e in shown)
    if len(events) > limit:
        txt += f"\n\n… và {len(events) - limit} sự kiện khác (thu hẹp bằng /tinh hoặc /sukien)."
    return txt


def format_alert(e: Event) -> str:
    why = "; ".join(e.large_reasons())
    return f"🚨 CẢNH BÁO SỰ KIỆN QUY MÔ LỚN\nLý do: {why}\n\n{format_event(e, show_summary=True)}"
