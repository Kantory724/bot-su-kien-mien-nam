from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlparse

from . import config
from .geo import normalize

# Địa điểm nhạy cảm với mạng (dễ nghẽn): dùng để nâng mức ưu tiên
SENSITIVE = ["san bay", "cang", "khu du lich", "quang truong", "san van dong", "nha thi dau",
             "pho di bo", "nui ba den", "nui sam", "phu quoc", "ben ninh kieu", "trung tam hoi cho",
             "ben xe", "cong vien"]
MID_KW = ["le hoi", "hoi cho", "trien lam", "marathon", "giai chay", "via ba", "ok om bok",
          "chol chnam thmay", "sen dolta", "khai mac", "festival", "le ky niem", "dua ghe",
          "dua bo", "giao thua", "tet", "ky yen", "nghinh ong", "cau ngu", "le via", "cung dinh", "gio to", "vu lan", "nghi le", "quoc khanh", "hoi nghi", "dien dan"]

# Lễ hội truyền thống / tín ngưỡng: không cần quy mô (luôn tối thiểu mức TB)
TRADITIONAL_KW = ["ooc om boc", "kate", "dieu tri cung", "trung thu", "trang ram", "via ba", "ba chua xu", "ok om bok", "chol chnam thmay", "sen dolta", "ky yen", "nghinh ong", "cau ngu",
                  "cung dinh", "le via", "gio to", "vu lan", "le hoi dinh", "le hoi chua", "le hoi den", "le hoi mieu",
                  "le dang huong", "le roc", "dua ghe", "dua bo", "hoi dua", "le ha dien", "le thanh minh",
                  "nguyen trung truc", "nguyen dinh chieu", "thoai ngoc hau", "tran hung dao", "ba den", "quan the am",
                  "nguyen huu canh", "le van duyet", "ong dia", "ba thien hau", "quan de", "le hoi nghinh"]

# Loại sự kiện đong đếm được quy mô: ca nhạc, hội chợ (pháo hoa xét riêng qua cờ fireworks)
SCALE_KW = ["ca nhac", "dem nhac", "nhac hoi", "dai nhac hoi", "concert", "liveshow", "live show",
            "countdown", "music festival", "hoi cho"]

# Nguồn dạng video (đài truyền hình, mạng xã hội) -> xếp sau bài văn bản khi chọn link hiển thị
VIDEO_HOSTS = ("canthotv.vn", "youtube.com", "youtu.be", "facebook.com", "fb.watch", "tiktok.com", "vimeo.com")
VIDEO_PATHS = ("/video", "/clip", "/truyen-hinh")


def source_rank(url: str) -> int:
    """0 = bài văn bản (ưu tiên nhất), 1 = link Google News chưa giải mã, 2 = video."""
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS) or any(x in p.path.lower() for x in VIDEO_PATHS):
        return 2
    return 1 if host == "news.google.com" else 0


PRIORITY_LABEL = {"CAO": "CAO", "TB": "TB", "THAP": "THẤP"}
PRIORITY_RANK = {"CAO": 0, "TB": 1, "THAP": 2}


@dataclass
class Event:
    name: str
    province: str
    venue: str = ""
    start_date: date | None = None
    end_date: date | None = None
    start_time: str = ""
    crowd: int | None = None
    fireworks: bool = False
    big_concert: bool = False
    summary: str = ""
    sources: list[str] = field(default_factory=list)
    priority: str = "THAP"
    id: int | None = None
    alerted: bool = False

    @property
    def last_date(self) -> date | None:
        return self.end_date or self.start_date

    @property
    def main_source(self) -> str:
        """Link hiển thị: ưu tiên bài văn bản, video để sau cùng."""
        return min(self.sources, key=source_rank, default="")

    @property
    def is_large(self) -> bool:
        """Tiêu chí cảnh báo riêng: >= 10.000 người, hoặc có pháo hoa, hoặc đại nhạc hội."""
        return bool((self.crowd or 0) >= config.large_crowd() or self.fireworks or self.big_concert)

    @property
    def is_traditional(self) -> bool:
        n = " " + normalize(f"{self.name} {self.venue}") + " "
        return any(f" {k} " in n for k in TRADITIONAL_KW)

    @property
    def has_scale_kind(self) -> bool:
        """Chỉ sự kiện có ca nhạc / hội chợ / pháo hoa mới có quy mô đong đếm được."""
        if self.fireworks or self.big_concert:
            return True
        n = " " + normalize(f"{self.name} {self.summary}") + " "
        return any(f" {k} " in n for k in SCALE_KW)

    @property
    def shown_crowd(self) -> int | None:
        """Số người dự kiến theo bài báo (chỉ khi >= LARGE_CROWD, mặc định 10.000; nhỏ hơn hoặc không nêu -> None -> hiển thị 'Không rõ')."""
        return self.crowd if (self.crowd or 0) >= config.large_crowd() else None

    def large_reasons(self) -> list[str]:
        r = []
        if (self.crowd or 0) >= config.large_crowd():
            r.append(f"quy mô ≥ {config.large_crowd():,}".replace(",", ".") + " người")
        if self.fireworks:
            r.append("có bắn pháo hoa")
        if self.big_concert:
            r.append("đại nhạc hội/countdown")
        return r

    def compute_priority(self) -> str:
        n = " " + normalize(f"{self.name} {self.venue}") + " "
        sensitive = any(f" {k}" in n for k in SENSITIVE)
        crowd = self.crowd or 0
        if self.is_large or (crowd >= 5000 and sensitive):
            return "CAO"
        if self.is_traditional:
            return "TB"
        if crowd >= 1000 or sensitive or any(f" {k} " in n for k in MID_KW):
            return "TB"
        return "THAP"
