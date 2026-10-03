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
        "phu quoc", "tri ton", "nui cam", "tinh bien", "kien luong"]),
    "dongthap": ("Đồng Tháp", [
        "dong thap", "cao lanh", "sa dec", "tien giang", "my tho", "go cong", "cai be",
        "tram chim", "vinh trang"]),
    "vinhlong": ("Vĩnh Long", ["vinh long", "ben tre", "tra vinh"]),
    "cantho": ("Cần Thơ", [
        "can tho", "ninh kieu", "soc trang", "hau giang", "vi thanh", "nga bay", "cai rang", "o mon"]),
    "camau": ("Cà Mau", ["ca mau", "bac lieu", "nam can", "dat mui"]),
}

# Tên người dùng gõ cho lệnh /tinh
EXTRA_NAMES = {"hcm": ["hcm", "sg", "tp hcm", "sai gon", "ho chi minh"], "tayninh": ["tn"], "cantho": ["ct"]}


def _pat(alias: str) -> re.Pattern:
    return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")


_PATTERNS: dict[str, list[re.Pattern]] = {
    k: [_pat(a) for a in dict.fromkeys(aliases + [normalize(name)])]
    for k, (name, aliases) in PROVINCES.items()
}


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


# Địa danh NGOÀI 8 tỉnh/thành: nếu bài nói về chúng nhiều hơn địa bàn thì loại
OUT_OF_SCOPE = [
    "ha noi", "da nang", "hue", "thua thien hue", "hai phong", "nha trang", "khanh hoa", "da lat", "dalat",
    "lam dong", "bao loc", "quang ninh", "ha long", "sa pa", "lao cai", "nghe an", "thanh hoa", "quang nam",
    "hoi an", "ninh binh", "phan thiet", "mui ne", "binh thuan", "gia lai", "quy nhon", "dak lak",
    "buon ma thuot", "quang binh", "quang tri", "ha giang", "cao bang", "lang son", "son la", "dien bien",
    "thai nguyen", "bac ninh", "hung yen", "phu tho", "vinh phuc", "nam dinh", "thai binh", "ha tinh",
    "phu yen", "ninh thuan", "phan rang", "kon tum", "pleiku", "dong hoi",
]
_OUT = [_pat(a) for a in OUT_OF_SCOPE]


def out_of_scope_score(title: str, body: str) -> int:
    nt, nb = normalize(title), normalize(body)
    return sum(3 * len(p.findall(nt)) + len(p.findall(nb)) for p in _OUT)


def detect_province(title: str, body: str = "", hint: str | None = None) -> str | None:
    """Chọn tỉnh có điểm cao nhất (tiêu đề x3), cần >= 2 điểm. Gợi ý từ nguồn chỉ được dùng khi bài CÓ nhắc tỉnh đó.
    Bài nói về địa danh ngoài địa bàn nhiều hơn địa bàn -> loại (trả None)."""
    scores = score_provinces(title, body)
    out = out_of_scope_score(title, body)
    if scores:
        best = max(scores, key=scores.get)
        if scores[best] >= 2:
            return best if out <= scores[best] else None
        if hint in scores and out <= scores[hint]:
            return hint
    return None


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
