"""Trích xuất thông tin sự kiện từ văn bản tiếng Việt bằng luật (regex). Có thể bổ trợ bằng LLM (bot/llm.py)."""
import re
from datetime import date, timedelta

from .geo import PROVINCES, normalize

# ---------- Lọc tin liên quan ----------
# Từ khóa MẠNH: bài phải có ít nhất một từ mới được coi là tin sự kiện (đã bỏ hội nghị, hội thảo, khai mạc... vì quá rộng)
EVENT_KW = [
    "le hoi", "festival", "dai nhac hoi", "concert", "liveshow", "live show", "phao hoa", "countdown",
    "hoi cho", "trien lam", "marathon", "giai chay", "via ba", "ok om bok", "chol chnam thmay",
    "sen dolta", "dem nhac", "nhac hoi", "dua ghe", "dua bo", "dua thuyen", "carnival", "lien hoan",
    "giao thua", "chao nam moi", "tuan le van hoa", "tuan le du lich", "le roc", "dai le", "le cung",
]
# Cụm phủ định (xét trên TIÊU ĐỀ): hội họp, tin đã diễn ra xong, bài tổng hợp/gợi ý, tin không liên quan
NEG_KW = [
    "tai nan", "tu vong", "khoi to", "bat giu", "lua dao", "chung khoan", "gia vang", "ngoai hang anh",
    "premier league", "champions league", "hoi nghi", "hoi thao", "dai hoi", "tong ket", "be mac",
    "da dien ra", "vua dien ra", "vua ket thuc", "dem qua", "toi qua", "hom qua", "top", "diem danh",
    "goi y", "cam nang", "kinh nghiem", "nhung le hoi", "cac le hoi", "lich le hoi", "tuyen sinh",
]
_LISTICLE = re.compile(r"^\d+ (?:le hoi|su kien|dia diem|diem den|mon|cach)\b")


def event_keyword_hits(text: str) -> list[str]:
    n = f" {normalize(text)} "
    return [k for k in EVENT_KW if f" {k} " in n]


def has_negative(title: str) -> bool:
    n = normalize(title)
    return bool(_LISTICLE.match(n)) or any(f" {k} " in f" {n} " for k in NEG_KW)


def is_candidate(title: str, summary: str = "") -> bool:
    """Tin có thể là sự kiện cụ thể: có từ khóa mạnh trong tiêu đề/tóm tắt và tiêu đề không thuộc loại phủ định."""
    return bool(event_keyword_hits(f"{title} {summary}")) and not has_negative(title)


# ---------- Ngày ----------
_Y = r"(?:\s*/\s*(\d{4}))?"
_WY = r"(?:\s*(?:năm\s*)?(\d{4}))?"
_RNG = r"(?:-|–|—|đến|tới)"
R_FULL = re.compile(rf"(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}\s*{_RNG}\s*(?:ngày\s*)?(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}", re.I)
R_WORD2 = re.compile(rf"(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}\s*{_RNG}\s*(?:ngày\s*)?(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}", re.I)
R_SHORT = re.compile(rf"(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}", re.I)
R_WORD1 = re.compile(rf"(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}", re.I)
R_SINGLE = re.compile(rf"(?<![\d/])(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}(?![\d/])", re.I)
R_WSINGLE = re.compile(rf"(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}", re.I)
R_DASH = re.compile(r"\bngày\s*(\d{1,2})\s*-\s*(\d{1,2})(?![\d/]|\s*tháng)", re.I)
R_LUNAR = re.compile(r"\s*\(?\s*(?:âm lịch|âm|âl)\b", re.I)


def _mk(d: int, m: int, y: int | None, ref: date) -> date | None:
    try:
        if y:
            if not (ref.year - 1 <= y <= ref.year + 2):
                return None
            return date(y, m, d)
        cand = date(ref.year, m, d)
        if cand < ref - timedelta(days=45):
            cand = date(ref.year + 1, m, d)
        return cand
    except ValueError:
        return None


def find_dates(text: str, ref: date) -> list[tuple[int, date, date]]:
    """Trả về [(vị trí, ngày bắt đầu, ngày kết thúc)] theo thứ tự xuất hiện. Bỏ qua ngày âm lịch."""
    masked = text
    found: list[tuple[int, date, date]] = []

    def take(rx, build):
        nonlocal masked
        for m in list(rx.finditer(masked)):
            if R_LUNAR.match(masked[m.end(): m.end() + 14]):
                masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
                continue
            res = build(m)
            masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
            if res:
                found.append((m.start(), res[0], res[1]))

    def full(m):
        d1, m1, y1, d2, m2, y2 = m.groups()
        a = _mk(int(d1), int(m1), int(y1) if y1 else (int(y2) if y2 else None), ref)
        b = _mk(int(d2), int(m2), int(y2) if y2 else (int(y1) if y1 else None), ref)
        if not a or not b:
            return None
        if b < a:
            b = _mk(int(d2), int(m2), a.year + 1, ref) or b
        return (a, b) if b >= a else None

    def short(m):
        d1, d2, mo, y = m.groups()
        a, b = _mk(int(d1), int(mo), int(y) if y else None, ref), _mk(int(d2), int(mo), int(y) if y else None, ref)
        return (a, b) if a and b and b >= a else None

    def single(m):
        d, mo, y = m.groups()
        a = _mk(int(d), int(mo), int(y) if y else None, ref)
        return (a, a) if a else None

    take(R_FULL, full)
    take(R_WORD2, full)
    take(R_SHORT, short)
    take(R_WORD1, short)

    def dash(m):  # "ngày 25-9" = 25/9 (nếu tháng > 12 thì _mk trả None, tức là khoảng ngày như "ngày 17-18")
        a = _mk(int(m.group(1)), int(m.group(2)), None, ref)
        return (a, a) if a else None

    take(R_DASH, dash)
    take(R_SINGLE, single)
    take(R_WSINGLE, single)
    return sorted(found)


def choose_date(dates: list[tuple[int, date, date]], ref: date):
    """Ưu tiên mốc ngày đầu tiên chưa qua; nếu toàn mốc cũ thì lấy mốc đầu tiên."""
    if not dates:
        return None
    for _, a, b in dates:
        if b >= ref - timedelta(days=1):
            return a, b
    return dates[0][1], dates[0][2]


# ---------- Giờ ----------
R_T1 = re.compile(r"(?<![\d:/.])([01]?\d|2[0-3])h([0-5]\d)?(?![\da-zA-Zà-ỹ])")
R_T2 = re.compile(r"(?<![\d:/.])([01]?\d|2[0-3])\s*giờ(?:\s*([0-5]\d)(?!\s*/))?(?![a-zà-ỹ])", re.I)
R_T3 = re.compile(r"(?<![\d:/.])([01]?\d|2[0-3]):([0-5]\d)(?!\d)")


def find_time(text: str) -> str:
    best = None
    for rx in (R_T1, R_T2, R_T3):
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m)
    if not best:
        return ""
    h, mi = best[1].group(1), best[1].group(2) or "00"
    return f"{int(h):02d}:{mi}"


# ---------- Quy mô ----------
R_CROWD = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(nghìn|ngàn|triệu|vạn)?\s*"
    r"(?:người|lượt khách|lượt du khách|lượt người|khán giả|du khách|vận động viên|vđv|thí sinh|tín đồ|khách)",
    re.I)
R_CROWD_WORDS = re.compile(
    r"hàng\s+(chục\s+nghìn|chục\s+ngàn|vạn|trăm\s+nghìn|trăm\s+ngàn|triệu)\s*(?:người|lượt|khán giả|du khách|khách)", re.I)
_WORD_VAL = {"chục nghìn": 20000, "chục ngàn": 20000, "vạn": 10000, "trăm nghìn": 200000,
             "trăm ngàn": 200000, "triệu": 1000000}
_UNIT = {"nghìn": 1e3, "ngàn": 1e3, "triệu": 1e6, "vạn": 1e4}
R_ANNUAL = re.compile(r"^\W{0,3}(?:cả năm|mỗi năm|trong năm|năm\s*\d{4}|hằng năm|hàng năm)", re.I)


def find_crowd(text: str) -> int | None:
    best = 0
    for m in R_CROWD.finditer(text):
        if R_ANNUAL.match(text[m.end(): m.end() + 25]) or R_ANNUAL.match(text[m.end() - 1: m.end() + 25]):
            continue
        raw, unit = m.group(1), (m.group(2) or "").lower()
        try:
            if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
                num = float(re.sub(r"[.,]", "", raw))
            else:
                num = float(raw.replace(",", "."))
        except ValueError:
            continue
        val = int(num * _UNIT.get(unit, 1))
        if 100 <= val <= 3_000_000:
            best = max(best, val)
    for m in R_CROWD_WORDS.finditer(text):
        best = max(best, _WORD_VAL.get(re.sub(r"\s+", " ", m.group(1).lower()), 0))
    return best or None


# ---------- Cờ pháo hoa / đại nhạc hội ----------
def _flag(rx: str, text: str) -> bool:
    for m in re.finditer(rx, text, re.I):
        pre = text[max(0, m.start() - 25): m.start()].lower()
        if re.search(r"không|cấm|dừng|hủy|bỏ|thay (?:bằng|thế)", pre):
            continue
        return True
    return False


def has_fireworks(text: str) -> bool:
    return _flag(r"pháo hoa|bắn pháo|trình diễn pháo|màn pháo", text)


def has_big_concert(text: str) -> bool:
    return _flag(r"đại nhạc hội|concert|countdown|music festival|lễ hội âm nhạc|đại hội âm nhạc|đêm nhạc hội", text)


# ---------- Địa điểm ----------
_STOP = {"vào", "từ", "lúc", "ngày", "với", "để", "nhằm", "trong", "và", "do", "theo", "của", "sẽ", "đã",
         "đang", "có", "khi", "sau", "trước", "cùng", "nơi", "bởi", "như", "được"}
_VENUE_WORDS = {"Quảng", "Sân", "Công", "Nhà", "Bến", "Đình", "Chùa", "Miếu", "Khu", "Trung", "Cảng", "Phố",
                "Cung", "Núi", "Hồ", "Đường", "Làng", "Đền", "Thánh", "Lăng", "Khách", "Chợ", "Bảo", "Di",
                "Ga", "Sảnh", "Vườn", "Biển", "Bãi", "Cầu", "Vinpearl", "Dinh", "Rạp"}
_PROV_NORM = {normalize(PROVINCES[k][0]) for k in PROVINCES} | {a for k in PROVINCES for a in PROVINCES[k][1]}


def find_venue(text: str) -> str:
    cands = []
    m = re.search(r"địa điểm\s*[:：-]\s*([^\n.;]{3,100})", text, re.I)
    if m:
        cands.append((0, m.group(1).strip(" ,:")))
    for m in re.finditer(r"\btại\s+([^,.;\n()]{3,100})", text):
        toks = m.group(1).split()
        if not toks or not toks[0][0].isupper():
            continue
        out = []
        for t in toks:
            if t in _STOP:
                break
            out.append(t)
            if len(out) >= 8:
                break
        v = " ".join(out).strip()
        if len(v) < 3 or normalize(v) in _PROV_NORM:
            continue
        cands.append((0 if toks[0] in _VENUE_WORDS else 1, v))
    if not cands:
        return ""
    return sorted(cands, key=lambda x: x[0])[0][1][:100]


def find_ward(text: str) -> str:
    for m in re.finditer(r"\b(phường|xã|thị trấn|đặc khu|Phường|Xã)\s+([^\s,.;()]+(?:\s+[^\s,.;()]+){0,2})", text):
        words = m.group(2).split()
        keep = []
        for w in words:
            if w[0].isupper() or w[0].isdigit():
                keep.append(w)
            else:
                break
        if keep:
            return f"{m.group(1).lower()} {' '.join(keep)}"
    return ""


# ---------- Tên, tóm tắt ----------
def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", t) if re.search(r"\s[-|–—]\s", t) and len(t) > 40 else t
    t = re.sub(r"^(ảnh|video|clip|infographic|trực tiếp|photo)\s*[:\-]\s*", "", t, flags=re.I)
    return t.strip(" \"'“”")[:160]


def make_summary(text: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = s.strip()
        if len(s) >= 40:
            return s[:220]
    return (text or "").strip()[:220]


def extract_rules(title: str, body: str, pub: date | None, ref: date) -> dict:
    """Trích xuất bằng luật. `ref` = ngày làm mốc suy luận năm (thường là ngày đăng bài)."""
    r = pub or ref
    full = f"{title}\n{body}"
    dates = find_dates(title, r) or find_dates(body, r)
    chosen = choose_date(dates, r)
    venue, ward = find_venue(full), find_ward(full)
    if venue and ward and normalize(ward) not in normalize(venue):
        venue = f"{venue}, {ward}"
    elif not venue:
        venue = ward
    return {
        "name": clean_title(title),
        "venue": venue,
        "start_date": chosen[0] if chosen else None,
        "end_date": chosen[1] if chosen else None,
        "start_time": find_time(full),
        "crowd": find_crowd(full),
        "fireworks": has_fireworks(full),
        "big_concert": has_big_concert(full),
        "summary": make_summary(body or clean_title(title)),
    }
