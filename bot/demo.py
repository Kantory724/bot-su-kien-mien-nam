"""Chạy thử toàn bộ luồng xử lý bằng dữ liệu GIẢ (không cần mạng, không cần token) để xem định dạng bản tin."""
from datetime import timedelta

from . import config
from .collector import Item
from .db import DB
from .formatter import format_alert, format_digest
from .pipeline import ingest


def sample_items(today):
    d = lambda n: today + timedelta(days=n)
    f = lambda a, b=None: f"{a.day}-{b.day}/{a.month}" if b else f"{a.day}/{a.month}"
    return [
        (Item("demo-1", "Báo A", "Đại nhạc hội Mùa Hè Xanh tại Cần Thơ thu hút 20.000 khán giả", "https://demo.local/a1", "", today, "cantho"),
         f"Đại nhạc hội Mùa Hè Xanh diễn ra ngày {f(d(5), d(6))} tại Bến Ninh Kiều, phường Ninh Kiều, dự kiến 20.000 người tham dự. "
         "Chương trình bắn pháo hoa lúc 21h30."),
        (Item("demo-2", "Báo B", "Cần Thơ: Mùa Hè Xanh - đại nhạc hội lớn nhất năm", "https://demo.local/b1", "", today, "cantho"),
         f"Đại nhạc hội Mùa Hè Xanh sẽ diễn ra từ ngày {d(5).day} đến {d(6).day}/{d(6).month} tại Bến Ninh Kiều."),  # trùng sự kiện trên
        (Item("demo-3", "Báo C", "Lễ hội Vía Bà Chúa Xứ Núi Sam khai mạc", "https://demo.local/c1", "", today, "angiang"),
         f"Lễ hội khai mạc ngày {d(3).day} tháng {d(3).month} tại Khu du lịch Núi Sam, phường Núi Sam, Châu Đốc. Dự kiến đón hơn 15 nghìn lượt khách."),
        (Item("demo-4", "Báo D", "Hội chợ triển lãm hàng Việt tại Tây Ninh", "https://demo.local/d1", "", today, "tayninh"),
         f"Hội chợ diễn ra {f(d(2), d(4))} tại Trung tâm Hội chợ Tây Ninh. Khoảng 3.000 người mỗi ngày."),
        (Item("demo-5", "Báo E", "Giải chạy bộ cộng đồng Cà Mau", "https://demo.local/e1", "", today, "camau"),
         f"Giải chạy khởi tranh lúc 5h30 ngày {d(30).day}/{d(30).month} tại Quảng trường Hùng Vương."),  # ngoài 7 ngày
        (Item("demo-6", "Báo F", "Lễ hội xưa đã qua ở Đồng Nai", "https://demo.local/f1", "", today, "dongnai"),
         f"Lễ hội diễn ra ngày {(today - timedelta(days=40)).day}/{(today - timedelta(days=40)).month}."),  # đã qua
        (Item("demo-7", "Báo G", "Lễ hội Hà Nội mùa thu", "https://demo.local/g1", "", today, None),
         "Lễ hội tổ chức tại Hà Nội, Hoàn Kiếm ngày 20/11."),  # ngoài địa bàn
    ]


def run_demo() -> None:
    today = config.today()
    db = DB(":memory:")  # type: ignore[arg-type]
    print("=== DEMO: dữ liệu giả, không gọi mạng ===\n")
    for item, body in sample_items(today):
        print(f"- {item.title[:70]:70s} → {ingest(db, item, body, None, today)}")
    print()
    print(format_digest(db.events_between(today, today + timedelta(days=7)), today, 7))
    print("\n=== Cảnh báo sự kiện lớn sẽ gửi ===\n")
    for e in db.unalerted_large(today):
        print(format_alert(e), "\n")
