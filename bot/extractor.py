"""Trích xuất thông tin sự kiện từ văn bản tiếng Việt bằng luật (regex). Có thể bổ trợ bằng LLM (bot/llm.py)."""
import re
import unicodedata
from datetime import date, timedelta

from .geo import PROVINCES, normalize
from .lunar import lunar_to_solar

# ---------- Lọc tin liên quan ----------
# Từ khoá MẠNH: bản thân đã là lễ hội/văn hoá/nghệ thuật/hội chợ... (tít có là nhận)
STRONG_KW = [
    "le hoi", "festival", "dai nhac hoi", "nhac hoi", "concert", "liveshow", "live show", "phao hoa", "countdown",
    "hoi cho", "trien lam", "marathon", "giai chay", "via ba", "cung dinh", "le cung", "ok om bok",
    "chol chnam thmay", "sen dolta", "dua ghe", "dua bo", "dua thuyen", "hoi dua", "giao thua", "chao nam moi",
    "carnival", "lien hoan", "tuan le van hoa", "tuan le du lich", "dem nhac", "dem hoi", "khai hoi", "le roc",
    "vu lan", "trung thu", "tet nguyen dan", "ngay hoi", "ngay hoi van hoa", "gala", "chuong trinh nghe thuat",
    "dem nghe thuat", "le dang huong", "hoi xuan", "hoi hoa", "dai le", "cho hoa", "duong hoa",
    # lễ hội truyền thống, tín ngưỡng (không cần quy mô)
    "ky yen", "nghinh ong", "cau ngu", "le via", "via ba", "le hoi dinh", "cung dinh", "le cung dinh", "le ha dien",
    "gio to", "le hoi chua", "le hoi den", "le hoi mieu", "hoi dinh", "le thanh minh", "le vu lan",
    # ngày lễ, Tết, kỳ nghỉ dài
    "nghi le", "ky nghi le", "dip le", "dip tet", "don tet", "quoc khanh", "gio to hung vuong",
    "tet duong lich", "tet trung thu", "tet am lich", "tet doan ngo",
]
# Từ khoá YẾU: chỉ nhận khi tít có kèm dấu hiệu đông người (>= 2.000 người / pháo hoa / đại nhạc hội)
WEAK_KW = [
    "khai mac", "be mac", "su kien", "hoi thi", "hoi dien", "tuan le", "bieu dien", "tranh tai", "giai dau",
    "giai the thao", "giai vo dich", "le ky niem", "khai truong", "mung dang", "ngay chay", "hoi thao",
    "ky nghi", "hoi nghi", "hoi thao", "dien dan", "san bay", "cang bien", "khu du lich",
]
EVENT_KW = STRONG_KW + WEAK_KW
# Tin hành chính / họp hội / nghiệp vụ / không phải sự kiện văn hoá: loại theo tít
NEG_KW = ["tai nan", "tu vong", "khoi to", "bat giu", "lua dao", "chung khoan", "gia vang",
          "ngoai hang anh", "premier league", "champions league",
          "hoi thao khoa hoc", "tong ket", "so ket", "ky hop", "dai hoi dang", "dai hoi dai bieu",
          "dai hoi cong doan", "tap huan", "hoi thi tay nghe", "tu van vien", "phap luat", "chuyen doi so",
          "hoi dong nhan dan", "bau cu", "huan luyen", "dien tap", "cong an", "quan su", "sinh hoat chuyen de",
          "hoc tap", "bao cao vien", "tuyen truyen", "tuyen sinh", "thi tuyen", "ban giao", "ky ket",
          "khoi cong", "nguon luc", "phat trien kinh te", "nghi quyet", "chuong trinh hanh dong", "cong nghiep van hoa",
          "doi moi sang tao", "chuyen doi", "khoa hoc cong nghe", "cong bo quyet dinh", "trao quyet dinh", "kiem tra", "giam sat"]


def strong_hits(text: str) -> list[str]:
    n = f" {normalize(text)} "
    return [k for k in STRONG_KW if f" {k} " in n]


def is_cultural_event(title: str, body: str, crowd: int | None, fireworks: bool, concert: bool) -> bool:
    """Chỉ giữ lễ hội/văn hoá/nghệ thuật/hội chợ/thể thao quần chúng. Loại tin hành chính, hội nghị, tập huấn..."""
    if has_negative(title):
        return False
    if strong_hits(title) or strong_hits((body or "")[:800]):
        return True
    weak = any(f" {k} " in f" {normalize(title)} " for k in WEAK_KW)
    return bool(weak and ((crowd or 0) >= 2000 or fireworks or concert))


def event_keyword_hits(text: str) -> list[str]:
    n = f" {normalize(text)} "
    return [k for k in EVENT_KW if f" {k} " in n]


def has_negative(title: str) -> bool:
    n = f" {normalize(title)} "
    return any(f" {k} " in n for k in NEG_KW)


# ---------- Ngày ----------
_Y = r"(?:\s*/\s*(\d{4}))?"
_WY = r"(?:\s*(?:năm\s*)?(\d{4}))?"
_RNG = r"(?:-|–|—|đến hết|đến|tới)"
R_FULL = re.compile(rf"(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}\s*{_RNG}\s*(?:ngày\s*)?(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}", re.I)
R_WORD2 = re.compile(rf"(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}\s*{_RNG}\s*(?:ngày\s*)?(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}", re.I)
R_SHORT = re.compile(rf"(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}", re.I)
R_WORD1 = re.compile(rf"(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}", re.I)
R_SINGLE = re.compile(rf"(?<![\d/])(\d{{1,2}})\s*/\s*(\d{{1,2}}){_Y}(?![\d/])", re.I)
R_WSINGLE = re.compile(rf"(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_WY}", re.I)
_UNITS = r"triệu|nghìn|ngàn|tỷ|vạn|%|km|kg|ha|m2|m²|tấn|điểm|người|lượt|đồng|vnd|usd|năm|tháng|tuần|lần|tuổi|giờ|phút|giây|mét|phần trăm|lít"
_DOT_END = rf"(?![\d/]|[.,]\d|\s*(?:{_UNITS})(?!\w))"
_YD = r"(?:\.(\d{4}))?"
R_DFULL = re.compile(rf"(?<![\d/.,])(\d{{1,2}})\.(\d{{1,2}}){_YD}\s*{_RNG}\s*(?:ngày\s*)?(\d{{1,2}})\.(\d{{1,2}}){_YD}{_DOT_END}", re.I)
R_DSHORT = re.compile(rf"(?<![\d/.,])(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\.(\d{{1,2}}){_YD}{_DOT_END}", re.I)
R_DSINGLE = re.compile(rf"(?<![\d/.,])(\d{{1,2}})\.(\d{{1,2}}){_YD}{_DOT_END}", re.I)
_MARK = r"\s*\(?\s*(?:âm lịch|âl\b)"
R_LUNAR = re.compile(_MARK, re.I)
_RN = r"(?:-|–|—|đến|tới)"
_NB = r"(?<![\d/])"
L_SL2 = re.compile(rf"{_NB}(\d{{1,2}})\s*/\s*(\d{{1,2}})\s*{_RN}\s*(?:ngày\s*)?(\d{{1,2}})\s*/\s*(\d{{1,2}}){_MARK}", re.I)
L_SL1 = re.compile(rf"{_NB}(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\s*/\s*(\d{{1,2}}){_MARK}", re.I)
L_SL0 = re.compile(rf"{_NB}(\d{{1,2}})\s*/\s*(\d{{1,2}}){_MARK}", re.I)
L_DL2 = re.compile(rf"{_NB}(\d{{1,2}})\.(\d{{1,2}})\s*{_RN}\s*(?:ngày\s*)?(\d{{1,2}})\.(\d{{1,2}}){_MARK}", re.I)
L_DL1 = re.compile(rf"{_NB}(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\.(\d{{1,2}}){_MARK}", re.I)
L_DL0 = re.compile(rf"{_NB}(\d{{1,2}})\.(\d{{1,2}}){_MARK}", re.I)
L_W2 = re.compile(rf"{_NB}(\d{{1,2}})\s*tháng\s*(\d{{1,2}})\s*{_RN}\s*(?:ngày\s*)?(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_MARK}", re.I)
L_W1 = re.compile(rf"{_NB}(\d{{1,2}})\s*(?:-|–|—|đến|tới|và)\s*(?:ngày\s*)?(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_MARK}", re.I)
L_W0 = re.compile(rf"{_NB}(\d{{1,2}})\s*tháng\s*(\d{{1,2}}){_MARK}", re.I)
R_DEADLINE = re.compile(r"trước\s*(?:ngày\s*)?$", re.I)


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
            if R_LUNAR.match(masked[m.end(): m.end() + 14]) or R_DEADLINE.search(masked[max(0, m.start() - 12): m.start()]):
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

    def lunar_conv(d: int, m: int, ref_y: int) -> date | None:
        a = lunar_to_solar(d, m, ref_y)
        if a is None or a < ref - timedelta(days=60):
            a = lunar_to_solar(d, m, ref_y + 1) or a
        return a

    def l_range(m):
        g = [int(x) for x in m.groups()]
        if len(g) == 4:
            d1, m1, d2, m2 = g
        else:
            d1, d2, m1 = g
            m2 = m1
        a = lunar_conv(d1, m1, ref.year)
        if not a:
            return None
        b = lunar_to_solar(d2, m2, a.year if (m2, d2) >= (m1, d1) else a.year + 1) or lunar_to_solar(d2, m2, ref.year + 1)
        # b phải sau a: thử cùng năm âm với a
        for ly in (ref.year, ref.year + 1):
            cand = lunar_to_solar(d2, m2, ly)
            if cand and cand >= a and (b is None or b < a or cand < b):
                b = cand
        return (a, b) if b and b >= a and (b - a).days < 40 else (a, a)

    def l_single(m):
        d, mo = int(m.group(1)), int(m.group(2))
        a = lunar_conv(d, mo, ref.year)
        return (a, a) if a else None

    def take_lunar(rx, build):
        nonlocal masked
        for m in list(rx.finditer(masked)):
            res = build(m)
            masked = masked[: m.start()] + " " * (m.end() - m.start()) + masked[m.end():]
            if res:
                found.append((m.start(), res[0], res[1]))

    for rx, b in ((L_SL2, l_range), (L_DL2, l_range), (L_W2, l_range), (L_SL1, l_range), (L_DL1, l_range),
                  (L_W1, l_range), (L_SL0, l_single), (L_DL0, l_single), (L_W0, l_single)):
        take_lunar(rx, b)
    take(R_FULL, full)
    take(R_WORD2, full)
    take(R_DFULL, full)
    take(R_SHORT, short)
    take(R_WORD1, short)
    take(R_DSHORT, short)
    take(R_SINGLE, single)
    take(R_WSINGLE, single)
    take(R_DSINGLE, single)
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
    r"(?:lượt hành khách|hành khách|người|lượt khách|lượt du khách|lượt người|khán giả|du khách|vận động viên|vđv|thí sinh|tín đồ|khách)",
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
_VENUE_WORDS = {"Quảng", "Sân", "Công", "Nhà", "Bến", "Đình", "Chùa", "Miếu", "Khu", "Trường", "Hoàng", "Tòa", "Cảng", "Phố",
                "Cung", "Núi", "Hồ", "Đường", "Làng", "Đền", "Thánh", "Lăng", "Khách", "Chợ", "Bảo", "Di",
                "Ga", "Sảnh", "Vườn", "Biển", "Bãi", "Cầu", "Vinpearl", "Dinh", "Rạp"}
_PROV_NORM = {normalize(PROVINCES[k][0]) for k in PROVINCES} | {a for k in PROVINCES for a in PROVINCES[k][1]}


_VENUE_OK = re.compile(r"\b(?:phường|xã|thị trấn|đặc khu|đường|số\s*\d)", re.I)


def valid_venue(v: str) -> bool:
    """Chỉ nhận tên nơi chốn cụ thể (quảng trường, sân, chùa, phường/xã...), không nhận tên tỉnh/thành/quốc gia."""
    v = (v or "").strip(" ,:")
    n = normalize(v)
    if len(v) < 3 or n in _PROV_NORM or n.startswith(("ho chi minh", "cong an", "ubnd", "uy ban", "quan doi", "bo chi huy")):
        return False
    return v.split()[0] in _VENUE_WORDS or n.startswith("trung tam") or bool(_VENUE_OK.search(v))


def find_venue(text: str) -> str:
    m = re.search(r"địa điểm\s*[:：-]\s*([^\n.;]{3,100})", text, re.I)
    if m and valid_venue(m.group(1)):
        return m.group(1).strip(" ,:")[:100]
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
        if valid_venue(v):
            return v[:100]
    return ""


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



# ---------- TÊN SỰ KIỆN (không phải tiêu đề bài báo) ----------
# Hạng 0 = sự kiện "mẹ" (lễ hội, hội chợ...). Hạng 1 = sự kiện/hoạt động có thể nằm trong một sự kiện mẹ.
_P0 = (r"Lễ\s+hội|Festival|Ngày\s+hội|Hội\s+chợ|Lễ\s+Vía|Vía\s+Bà|Ok\s+Om\s+Bok|Chol\s+Chnam\s+Thmay|"
       r"Sen\s+Dolta|Lễ\s+Dolta|Lễ\s+cúng\s+Trăng|Lễ\s+Kỳ\s+Yên|Lễ\s+Nghinh\s+Ông|Lễ\s+Cầu\s+Ngư|Lễ\s+Hạ\s+điền|"
       r"Lễ\s+cúng\s+đình|Cúng\s+đình|Lễ\s+Giỗ\s+Tổ|Giỗ\s+Tổ|Lễ\s+Vu\s+Lan|Lễ\s+hội\s+Kỳ\s+Yên")
_P1 = (r"Đại\s+nhạc\s+hội|Hội\s+đua|Hội\s+xuân|Hội\s+hoa|Liên\s+hoan|Tuần\s+lễ|Carnival|Countdown|"
       r"Marathon|Giải\s+chạy|Triển\s+lãm|Chợ\s+hoa|Đường\s+hoa|Đêm\s+hội|Lễ\s+rước|Lễ\s+đón|"
       r"Kỳ\s+nghỉ\s+lễ|Dịp\s+nghỉ\s+lễ|Nghỉ\s+lễ|Tết\s+Nguyên\s+đán|Tết\s+Dương\s+lịch|Tết\s+Trung\s+thu|"
       r"Quốc\s+khánh|Giỗ\s+Tổ\s+Hùng\s+Vương|Hội\s+nghị|Hội\s+thảo|Diễn\s+đàn")
_RX0 = re.compile(rf"(?<!\w)(?:{_P0})(?!\w)", re.I)
_RX1 = re.compile(rf"(?<!\w)(?:{_P1})(?!\w)", re.I)
_STOP1 = set("""đã sẽ đang sắp tại ở trong với để nhằm từ vào lúc sau trước có được bị là đón hoặc của cùng như khi nơi
bởi do theo gồm cho về trên dưới đến tới qua giữa vẫn còn thuộc nhưng mà thì rằng vừa""".split())
# cụm từ chỉ hành động/mô tả (so khớp theo cụm, KHÔNG tách từng âm tiết để không cắt nhầm tên như "Thành Hoàng", "Mùa Đông")
_STOP2 = {("khai", "mạc"), ("bế", "mạc"), ("diễn", "ra"), ("thu", "hút"), ("quy", "tụ"), ("hứa", "hẹn"),
          ("chính", "thức"), ("hấp", "dẫn"), ("sôi", "động"), ("rộn", "ràng"), ("nhộn", "nhịp"), ("dự", "kiến"),
          ("tổ", "chức"), ("khuôn", "khổ"), ("chào", "mừng"), ("hoàn", "tất"), ("khẩn", "trương"), ("tất", "bật"),
          ("sẵn", "sàng"), ("chuẩn", "bị"), ("công", "tác"), ("thành", "công"), ("trải", "nghiệm"),
          ("thưởng", "thức"), ("đổ", "về"), ("bắt", "đầu"), ("kéo", "dài"), ("mở", "cửa"), ("sắp", "diễn"),
          ("hàng", "nghìn"), ("hàng", "chục"), ("hàng", "ngàn"), ("hàng", "vạn"), ("hơn", "nghìn")}
_ORD = {"nhất", "nhì", "hai", "ba", "tư", "bốn", "năm", "sáu", "bảy", "tám", "chín", "mười"}
_ROMAN = re.compile(r"^(?:[IVXLC]+|\d{1,3})$", re.I)
_LISTHEAD = "Thể|Du|Văn|Ẩm|Thương|Nghệ"  # "Văn hóa, Thể thao và Du lịch" là MỘT tên, không cắt ở dấu phẩy/gạch/'và'


def _name_from(text: str, start: int) -> str:
    """Lấy cụm tên bắt đầu từ vị trí `start`: dừng ở dấu câu hoặc cụm chỉ hành động (khai mạc, tại, thu hút...)."""
    seg = re.split(rf"[;:()|\n]|\.\s|,\s+(?!(?:{_LISTHEAD})\b)|\s[-–—]\s+(?!(?:{_LISTHEAD})\b)",
                   text[start:start + 170], maxsplit=1)[0]
    seg = re.sub("[“”\"‘’']", "", seg)
    toks = seg.split()
    out, i = [], 0
    while i < len(toks) and len(out) < 14:
        low = toks[i].lower().strip(".,")
        nxt = toks[i + 1].lower() if i + 1 < len(toks) else ""
        if i > 0:
            if low == "lần" and nxt == "thứ" and i + 2 < len(toks) and (
                    _ROMAN.match(toks[i + 2]) or toks[i + 2].lower().strip(".,") in _ORD):
                out += toks[i:i + 3]
                i += 3
                continue
            if low == "năm" and re.fullmatch(r"20\d\d", toks[i + 1].strip(".,") if i + 1 < len(toks) else ""):
                out += toks[i:i + 2]
                i += 2
                continue
            if low == "và" and i + 1 < len(toks) and re.match(rf"(?:{_LISTHEAD})", toks[i + 1]):
                out.append(toks[i])
                i += 1
                continue
            if low in _STOP1 or (low, nxt) in _STOP2 or (low == "ngày" and nxt[:1].isdigit()) or low == "và":
                break
        out.append(toks[i])
        i += 1
    while out and (out[-1].lower().strip(".,") in _STOP1 | {"lần", "thứ", "năm", "và", "-", "–", "—"}):
        out.pop()
    return " ".join(out).strip(" ,;-–—")


def _cap_after_kw(n: str, kw: str) -> bool:
    """Từ ngay sau loại hình (Lễ hội/Hội chợ...) phải viết hoa hoặc là số: 'Lễ hội Sen...' ok, 'đại nhạc hội lớn nhất' không."""
    toks = n.split()[len(kw.split()):]
    return any(t[:1].isupper() or t[:1].isdigit() for t in toks)


def _valid_event_name(n: str) -> bool:
    core = [t for t in n.split()
            if not re.fullmatch(r"20\d\d|[IVXLC]+|\d+", t, re.I) and t.lower() not in ("lần", "thứ", "năm")]
    return len(core) >= 2 and len(n) <= 120


def _strip_prov_prefix(n: str) -> str:
    m = re.match(r"^([^:]{2,20}):\s*(.{6,})$", n or "")
    return m.group(2) if m and normalize(m.group(1)) in _PROV_NORM else n


def event_name(title: str, body: str = "") -> str:
    """Tên lễ hội/sự kiện (vd 'Lễ hội Ok Om Bok'), bỏ phần mô tả hoạt động như 'Khai mạc', 'Đêm nhạc', 'Pháo hoa'.
    Nhiều hoạt động của cùng một lễ hội nhờ vậy cùng một tên -> được gộp thành 1 sự kiện. '' nếu không nhận ra."""
    t = unicodedata.normalize("NFC", re.sub(r"\s+", " ", title or ""))
    lead = unicodedata.normalize("NFC", re.sub(r"\s+", " ", body or ""))[:700]
    # 1) bài nói hoạt động "trong khuôn khổ / thuộc / nằm trong <lễ hội mẹ>" -> lấy lễ hội mẹ
    for src in (t, lead):
        for m in _RX0.finditer(src):
            if re.search(r"(?:khuôn khổ|nằm trong|thuộc|trong)\s+(?:của\s+)?$", src[max(0, m.start() - 25):m.start()], re.I):
                n = _name_from(src, m.start())
                if _valid_event_name(n) and _cap_after_kw(n, m.group(0)):
                    return n
    # 2) tít có lễ hội mẹ -> lấy; rồi đến hạng 1 trong tít; rồi lễ hội mẹ trong đoạn đầu bài
    for rx, src in ((_RX0, t), (_RX1, t), (_RX0, lead)):
        for m in rx.finditer(src):
            n = _name_from(src, m.start())
            if _valid_event_name(n) and _cap_after_kw(n, m.group(0)):
                return n
    return _strip_prov_prefix(_event_name_old(title, body))


# ---------- Tên, tóm tắt ----------
def clean_title(t: str) -> str:
    t = re.sub(r"\s+", " ", t or "").strip()
    t = re.sub(r"\s+[-|–—]\s+[^-|–—]{2,40}$", "", t) if re.search(r"\s[-|–—]\s", t) and len(t) > 40 else t
    t = re.sub(r"^(ảnh|video|clip|infographic|trực tiếp|photo)\s*[:\-]\s*", "", t, flags=re.I)
    return t.strip(" \"'“”")[:160]


_NAME_PREFIX = re.compile(
    r"^(?:(?:hoàn tất|khẩn trương|tất bật|rộn ràng|nhộn nhịp|háo hức)\s+)?(?:(?:công tác|việc)\s+)?"
    r"(?:chuẩn bị|sẵn sàng|chờ đón|đón chờ)(?:\s+cho)?\s+", re.I)
_NAME_SUFFIX = re.compile(r"\s*[,:\-–]?\s*(?:trước|vào|từ|diễn ra|sẽ diễn ra|sắp diễn ra)\s+(?:ngày\s+)?\d.*$", re.I)


def canonical_name(name: str) -> str:
    """Bỏ cụm 'hoàn tất công tác chuẩn bị', 'sẵn sàng cho'... và đuôi ngày để còn lại tên sự kiện."""
    n = _NAME_SUFFIX.sub("", _NAME_PREFIX.sub("", (name or "").strip())).strip(" ,:-–")
    return (n[:1].upper() + n[1:]) if len(n) >= 8 else (name or "").strip()



# ---------- Tên SỰ KIỆN / LỄ HỘI (không phải tít bài báo) ----------
_T1 = ["le hoi", "festival", "dai nhac hoi", "nhac hoi", "hoi cho", "ngay hoi", "tuan le van hoa", "tuan le du lich",
       "lien hoan", "carnival", "hoi dua", "ok om bok", "chol chnam thmay", "sen dolta", "le via", "via ba",
       "hoi xuan", "hoi hoa", "cho hoa", "duong hoa", "countdown", "le roc"]
_T2 = ["giai chay", "marathon", "trien lam", "dem nhac", "concert", "liveshow", "live show", "giao thua",
       "le dang huong", "le cung", "cung dinh", "dai le"]
_TAIL_STOP = {"tai", "o", "trong", "dien", "se", "sap", "khai", "mac", "be", "don", "voi", "de", "nham", "tu", "vao",
              "luc", "va", "cua", "cho", "dang", "da", "duoc", "co", "la", "gom", "hang", "gan", "hon", "khoang",
              "ngay", "thang", "lon", "nhat", "hoanh", "chuan", "quy", "tung", "bung", "ron", "nhon", "tuy", "nhu",
              "mang", "hap", "ket", "thanh", "dau", "chinh", "bat", "so", "toi", "den", "sang", "tiep"}
_DASH = {"-", "–", "—", "|"}
_PUNCT = "\"'“”‘’()[]{} ,.;:!?-–—"


def _extend(tokens: list[str], nt: list[str], i: int, L: int) -> list[str]:
    out = tokens[i:i + L]
    if tokens[i + L - 1][-1:] in ",;:!?":
        return out
    j, n_all = i + L, len(tokens)
    while j < n_all and len(out) < 11:
        t, n = tokens[j], nt[j]
        if t in _DASH or t.startswith("("):
            break
        if t[0] in '“"':  # tên nằm trong ngoặc kép
            seg = []
            while j < n_all and len(seg) < 9:
                seg.append(tokens[j])
                j += 1
                if seg[-1][-1:] in '”"':
                    break
            out += seg
            break
        if n == "tinh" or n == "tp" or (n == "thanh" and nt[j + 1:j + 2] == ["pho"]):
            k = j + (2 if n == "thanh" else 1)
            hit = next((m for m in (3, 2, 1) if " ".join(nt[k:k + m]) in _PROV_NORM), 0)
            if hit:  # bỏ cụm "tỉnh An Giang", "TP. Cần Thơ" khỏi tên
                j = k + hit
                continue
            break
        if n == "lan" and nt[j + 1:j + 2] == ["thu"] and j + 2 < n_all:
            out += tokens[j:j + 3]
            j += 3
            continue
        if n == "nam" and j + 1 < n_all and re.fullmatch(r"\d{4}", nt[j + 1]):
            out += tokens[j:j + 2]
            break
        if n in _TAIL_STOP or (n == "thu" and nt[j + 1:j + 2] == ["hut"]):
            break
        if any(c.isdigit() for c in t) and not re.fullmatch(r"\d{4}", n):
            break
        out.append(t)
        if t[-1:] in ",;:!?.":
            break
        j += 1
    return out


def _finalize(parts: list[str]) -> str:
    s = re.sub(r"\s+", " ", " ".join(parts)).strip(_PUNCT)
    return (s[:1].upper() + s[1:]) if s else ""


def _name_from_old(text: str) -> str:
    tokens = (text or "").split()
    if not tokens:
        return ""
    nt = [normalize(t) for t in tokens]
    cands = []
    for prio, phrases in enumerate((_T1, _T2)):
        for ph in phrases:
            pw = ph.split()
            for i in range(len(nt) - len(pw) + 1):
                if nt[i:i + len(pw)] == pw:
                    cands.append((prio, i, len(pw)))
    for prio, i, L in sorted(cands):
        out = _extend(tokens, nt, i, L)
        if len(out) > L and len(" ".join(out)) >= 8:
            return _finalize(out)
        # tên đứng TRƯỚC loại hình: "Mùa Hè Xanh - đại nhạc hội lớn nhất", "Mùa Hè Xanh: đại nhạc hội"
        k = i - 1
        if k >= 0 and (tokens[k] in _DASH or tokens[k].endswith(":")):
            back = [tokens[k].rstrip(":")] if tokens[k].endswith(":") and tokens[k] not in _DASH else []
            k -= 0 if back else 1
            while k >= 0 and len(back) < 5 and tokens[k] not in _DASH:
                back.insert(0, tokens[k])
                if tokens[k].endswith(":") and len(back) > 1:
                    back.pop(0)
                    break
                k -= 1
            name_part = " ".join(back).strip(_PUNCT)
            if len(back) >= 2 and normalize(name_part) not in _PROV_NORM:
                return _finalize(tokens[i:i + L] + back)
    return ""


def _event_name_old(title: str, body: str = "") -> str:
    """Tên của lễ hội/sự kiện (vd 'Lễ hội Sen Đồng Tháp lần thứ 3'), không phải cả tít bài. Thử tít trước, rồi đoạn mở đầu bài."""
    name = _name_from_old(clean_title(title))
    if not name:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", (body or "")[:600])[:4]:
            name = _name_from_old(sent)
            if name:
                break
    return name if any(t[:1].isupper() for t in name.split()[1:]) else ""


def make_summary(text: str) -> str:
    for s in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = s.strip()
        if len(s) >= 40:
            return s[:220]
    return (text or "").strip()[:220]


PREP_KW = ["chuan bi", "san sang", "hoan tat", "khan truong", "tat bat"]


def is_prep_title(title: str) -> bool:
    """Tít kiểu 'hoàn tất công tác chuẩn bị / sẵn sàng cho...': ngày trong tít là hạn chót, không phải ngày diễn ra."""
    n = f" {normalize(title)} "
    return any(f" {k} " in n for k in PREP_KW)


_STAMP = re.compile(r"\b\d{1,2}[:h]\d{2}\s*(?:,|-|–)?\s*(?:ngày\s*)?\d{1,2}/\d{1,2}/\d{4}\b"
                    r"|\b\d{1,2}/\d{1,2}/\d{4}\s*(?:,|-|–)?\s*\d{1,2}[:h]\d{2}\b", re.I)


def strip_stamps(text: str) -> str:
    """Bỏ dấu thời gian đăng bài ('05:52, 04/10/2026') để không bị nhận nhầm là ngày/giờ của sự kiện."""
    return _STAMP.sub(" ", text or "")


def extract_rules(title: str, body: str, pub: date | None, ref: date) -> dict:
    """Trích xuất bằng luật. `ref` = ngày làm mốc suy luận năm (thường là ngày đăng bài)."""
    title, body = strip_stamps(title), strip_stamps(body)
    r = pub or ref
    full = f"{title}\n{body}"
    dates = ([] if is_prep_title(title) else find_dates(title, r)) or find_dates(body, r)
    chosen = choose_date(dates, r)
    venue, ward = find_venue(full), find_ward(full)
    if venue and ward and normalize(ward) not in normalize(venue):
        venue = f"{venue}, {ward}"
    elif not venue:
        venue = ward
    return {
        "name": event_name(title, body) or canonical_name(clean_title(title)),
        "name_ok": bool(event_name(title, body)),  # False = chưa rút ra được TÊN lễ hội/sự kiện cụ thể (tin chung chung)
        "venue": venue,
        "start_date": chosen[0] if chosen else None,
        "end_date": chosen[1] if chosen else None,
        "start_time": find_time(full),
        "crowd": find_crowd(full),
        "fireworks": has_fireworks(full),
        "big_concert": has_big_concert(full),
        "summary": make_summary(body or title),
    }
