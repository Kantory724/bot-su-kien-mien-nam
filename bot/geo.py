"""Danh sách tỉnh/thành, bí danh (gồm tên địa phương cũ sau sáp nhập 2025) và nhận diện tỉnh."""
import re
import unicodedata


def normalize(s: str) -> str:
    """Bỏ dấu, chữ thường, chỉ giữ chữ-số, gộp khoảng trắng."""
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# key -> (tên hiển thị, [bí danh đã chuẩn hoá])
# 8 tỉnh/thành theo đề bài; bí danh gồm các địa danh/tỉnh cũ đã sáp nhập vào (từ 01/07/2025).
PROVINCES: dict[str, tuple[str, list[str]]] = {
    "hcm": ("TP. Hồ Chí Minh", [
        "tp hcm", "tphcm", "hcmc", "tp ho chi minh", "thanh pho ho chi minh", "sai gon",
        "thu duc", "can gio", "binh duong", "thu dau mot", "vung tau", "ba ria", "long hai", "ho tram"]),
    "dongnai": ("Đồng Nai", [
        "dong nai", "bien hoa", "long thanh", "nhon trach", "binh phuoc", "dong xoai", "binh long"]),
    "tayninh": ("Tây Ninh", [
        "tay ninh", "nui ba den", "long an", "tan an", "kien tuong", "moc bai", "hoa thanh"]),
    "angiang": ("An Giang", [
        "an giang", "chau doc", "long xuyen", "nui sam", "ha tien", "kien giang", "rach gia",
        "phu quoc", "tri ton", "nui cam", "tinh bien", "kien luong", "bay nui", "that son",
        "tan chau", "an phu", "thoai son", "chau phu"]),
    "dongthap": ("Đồng Tháp", [
        "dong thap", "cao lanh", "sa dec", "tien giang", "my tho", "go cong", "cai be",
        "tram chim", "vinh trang"]),
    "vinhlong": ("Vĩnh Long", ["vinh long", "ben tre", "tra vinh"]),
    "cantho": ("Cần Thơ", [
        "can tho", "ninh kieu", "soc trang", "hau giang", "vi thanh", "nga bay", "cai rang", "o mon"]),
    "camau": ("Cà Mau", ["ca mau", "bac lieu", "nam can", "dat mui"]),
}

# Địa danh ngoài 8 tỉnh (đã chuẩn hoá): chỉ dùng làm chốt chặn khi TÍT nhắc tới nơi này mà không nhắc tỉnh nào trong 8 tỉnh
OTHER_AREAS = [
    "ha noi", "hue", "da nang", "hai phong", "quang ninh", "nghe an", "thanh hoa", "da lat", "lam dong",
    "khanh hoa", "nha trang", "hoi an", "quang nam", "ninh binh", "lao cai", "gia lai", "dak lak",
    "quy nhon", "binh dinh", "quang tri", "ha long", "sa pa", "ha tinh", "cao bang", "lang son", "son la",
    "dien bien", "tuyen quang", "thai nguyen", "phu tho", "bac ninh", "hung yen", "quang ngai", "kon tum",
    "dong hoi", "vinh phuc", "hai duong", "nam dinh", "thai binh", "ha giang", "yen bai", "bac giang",
    "trung quoc", "campuchia", "thai lan", "nhat ban", "han quoc",
    # địa danh/địa điểm nổi tiếng ở Hà Nội & nơi khác (bài viết về sự kiện ở đó dù tít không ghi tên thành phố)
    "thang long", "hoang thanh", "hoan kiem", "ba dinh", "my dinh", "trung tam hoi nghi quoc gia", "tay ho",
    "cau giay", "hai ba trung", "long bien", "ha dong", "buon ma thuot", "pleiku", "co do hue",
    "thu do", "thu do ha noi",  # "Ngày hội Thủ đô" = Hà Nội
    "hoa binh", "bac kan", "lai chau", "dak nong", "gia nghia", "ninh thuan", "phan rang", "binh thuan", "phan thiet",
    "mui ne", "cam ranh", "tam ky", "sam son", "cua lo", "tuy hoa", "bai chay", "cat ba", "tam dao", "mu cang chai",
    "phu yen", "kon tum", "ha nam", "phu ly", "tay bac", "tay nguyen", "mien bac", "mien trung", "bac bo", "trung bo",
    "dong bang song hong", "phnom penh", "singapore", "malaysia", "indonesia",
]

# Báo/cổng thông tin địa phương (kể cả tỉnh cũ đã sáp nhập) - dùng để ưu tiên khi tìm lại bài về một sự kiện.
# Tên miền sai chỉ khiến truy vấn không ra kết quả và bot tự chuyển sang tìm không giới hạn nguồn.
LOCAL_SITES: dict[str, list[str]] = {
    "hcm": ["sggp.org.vn", "baobinhduong.vn", "baobariavungtau.com.vn", "hcmcpv.org.vn"],
    "dongnai": ["baodongnai.com.vn", "dnrtv.vn", "baobinhphuoc.com.vn"],
    "tayninh": ["baotayninh.vn", "baolongan.vn"],
    "angiang": ["baoangiang.com.vn", "angiang.gov.vn", "baokiengiang.vn"],
    "dongthap": ["baodongthap.vn", "dongthap.gov.vn", "baoapbac.vn"],
    "vinhlong": ["baovinhlong.com.vn", "baobentre.vn", "baotravinh.vn"],
    "cantho": ["baocantho.com.vn", "cantho.gov.vn", "baosoctrang.org.vn", "baohaugiang.com.vn"],
    "camau": ["baocamau.vn", "baobaclieu.vn"],
}

# Tên người dùng gõ cho lệnh /tinh
EXTRA_NAMES = {"hcm": ["hcm", "sg", "tp hcm", "sai gon", "ho chi minh"], "tayninh": ["tn"], "cantho": ["ct"]}


def _pat(alias: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")


_PATTERNS: dict[str, list[re.Pattern]] = {
    k: [_pat(a) for a in dict.fromkeys(aliases + [normalize(name)])]
    for k, (name, aliases) in PROVINCES.items()
}


_OTHER_PATS = [_pat(a) for a in OTHER_AREAS]


def title_elsewhere(title: str) -> bool:
    """Tít nhắc tới nơi ngoài 8 tỉnh và không nhắc tỉnh nào trong 8 tỉnh."""
    nt = normalize(title)
    if any(p.search(nt) for pats in _PATTERNS.values() for p in pats):
        return False
    return any(p.search(nt) for p in _OTHER_PATS)


def province_name(key: str) -> str:
    return PROVINCES[key][0] if key in PROVINCES else key


def score_provinces(title: str, body: str) -> dict[str, int]:
    nt, nb = normalize(title), normalize(body)
    scores = {}
    for key, pats in _PATTERNS.items():
        s = sum(3 * len(p.findall(nt)) + len(p.findall(nb)) for p in pats)
        if s:
            scores[key] = s
    return scores


def detect_province(title: str, body: str = "", hint: str | None = None) -> str | None:
    """Chọn tỉnh có điểm cao nhất (tiêu đề x3). Cần >= 2 điểm, hoặc dùng gợi ý từ nguồn."""
    scores = score_provinces(title, body)
    if scores:
        best = max(scores, key=scores.get)
        if scores[best] >= 2:
            return best
    return hint if hint in PROVINCES else None


def resolve_province(text: str) -> str | None:
    """Đổi chuỗi người dùng gõ (/tinh can tho, /tinh HCM...) thành key tỉnh."""
    n = normalize(text)
    if not n:
        return None
    for key, (name, aliases) in PROVINCES.items():
        names = [normalize(name)] + EXTRA_NAMES.get(key, [])
        if n in names or n in aliases:
            return key
    for key, pats in _PATTERNS.items():
        if any(p.search(n) for p in pats):
            return key
    return None


def other_area_score(title: str, body: str = "") -> int:
    """Số lần nhắc tới nơi NGOÀI 8 tỉnh (tít tính x3), cùng thang điểm với score_provinces."""
    nt, nb = normalize(title), normalize(body)
    return sum(3 * len(p.findall(nt)) + len(p.findall(nb)) for p in _OTHER_PATS)
