"""Trích xuất bằng LLM (tuỳ chọn). Bật bằng LLM_PROVIDER + LLM_API_KEY. Lỗi/hết hạn mức -> tự bỏ qua, quay về luật regex."""
import json
import logging
import re
import time
from datetime import date

import requests

from . import config
from .geo import PROVINCES, resolve_province

log = logging.getLogger("llm")
DEFAULT_MODEL = {"gemini": "gemini-3.8-flash", "anthropic": "claude-haiku-4-5-20251001"}

PROMPT = """Bạn trích xuất thông tin sự kiện từ bài báo tiếng Việt cho đội vận hành mạng di động.
Hôm nay là {today}. Bài đăng ngày {pub}. Chỉ quan tâm sự kiện sắp/đang diễn ra tại các tỉnh: {provinces}.
Trả về DUY NHẤT một JSON (không markdown) với các khóa:
is_event (bool: bài nói về MỘT lễ hội/sự kiện văn hoá-nghệ thuật-âm nhạc-pháo hoa-hội chợ-triển lãm-thể thao quần chúng cụ thể, sắp hoặc đang diễn ra, tập trung đông người. cũng tính: hội chợ/triển lãm/hội nghị-hội thảo quy mô lớn (từ ~2.000 người), sự kiện tại sân bay/cảng/khu du lịch đông khách, kỳ nghỉ lễ/Tết có lượng người di chuyển lớn. KHÔNG phải: họp, tổng kết, đại hội Đảng/đoàn thể, tập huấn, hội thi nghiệp vụ, tin chính trị/hành chính/pháp luật/quân sự/công an -> false. QUAN TRỌNG: nếu đoàn/đơn vị của một tỉnh đi biểu diễn, giao lưu, tham gia sự kiện Ở NƠI KHÁC (vd đoàn Cà Mau biểu diễn tại Hà Nội) thì sự kiện diễn ra ở nơi khác -> province null),
name (TÊN lễ hội/sự kiện TỔNG THỂ, ví dụ "Lễ hội Ok Om Bok", "Hội Đua bò Bảy Núi lần thứ 31"; KHÔNG phải tít bài, KHÔNG kèm hoạt động con như khai mạc/đêm nhạc/pháo hoa/đua ghe - nếu bài nói về hoạt động nằm trong một lễ hội lớn thì name là tên lễ hội lớn đó), province (một trong: {keys}; là tỉnh nơi sự kiện DIỄN RA, nhận cả tên tỉnh cũ trước sáp nhập 2025: Kiên Giang→An Giang, Long An→Tây Ninh, Bến Tre/Trà Vinh→Vĩnh Long, Tiền Giang→Đồng Tháp, Sóc Trăng/Hậu Giang→Cần Thơ, Bạc Liêu→Cà Mau, Bình Dương/Bà Rịa-Vũng Tàu→TP.HCM, Bình Phước→Đồng Nai; sự kiện ở nơi khác thì null),
venue (địa điểm cụ thể: tên nơi + phường/xã, hoặc ""), start_date (ngày BẮT ĐẦU sự kiện, YYYY-MM-DD hoặc null; không phải hạn chót chuẩn bị), end_date (YYYY-MM-DD hoặc null),
start_time (HH:MM hoặc ""), crowd (số người dự kiến, số nguyên hoặc null), fireworks (bool), big_concert (bool: đại nhạc hội/countdown/concert lớn),
summary (1 câu tóm tắt tiếng Việt).
Không bịa. Thiếu thông tin thì để null/"". Ngày âm lịch phải đổi sang dương lịch nếu bài có nêu, nếu không thì null.

TIÊU ĐỀ: {title}
NỘI DUNG: {body}"""


SAME_PROMPT = """Hai tin dưới đây có nói về CÙNG MỘT sự kiện/lễ hội (cùng đợt tổ chức, cùng địa phương) không?
Khác tiêu đề, khác báo, ngày lệch nhau (ngày chuẩn bị / khai mạc / cả đợt) vẫn có thể là một sự kiện. Khác năm hoặc khác địa điểm thì không phải.
Trả về DUY NHẤT JSON: {{"same": true}} hoặc {{"same": false}}
A: {a}
B: {b}"""


OLD_NAMES = {"hcm": "Bình Dương, Bà Rịa - Vũng Tàu", "dongnai": "Bình Phước", "tayninh": "Long An",
             "angiang": "Kiên Giang (Phú Quốc, Rạch Giá, Hà Tiên)", "dongthap": "Tiền Giang", "vinhlong": "Bến Tre, Trà Vinh",
             "cantho": "Sóc Trăng, Hậu Giang", "camau": "Bạc Liêu"}

DISCOVER_PROMPT = """Hôm nay là {today}. Dùng Google Search tìm các sự kiện SẮP HOẶC ĐANG diễn ra từ {a} đến {b} tại {name} (sau sáp nhập 2025, gồm cả {old}).
Loại sự kiện: lễ hội truyền thống/tín ngưỡng (vía, cúng đình, Ok Om Bok, Sene Dolta, Nghinh Ông, Giỗ...), sự kiện văn hóa/du lịch/kỷ niệm/khai mạc cấp tỉnh,
thể thao/marathon/ca nhạc/đại nhạc hội/pháo hoa/countdown, hội chợ/triển lãm/hội nghị lớn, sự kiện tại sân bay/cảng/khu du lịch đông khách, kỳ nghỉ lễ/Tết.
Chỉ nêu sự kiện DIỄN RA tại {name} (không phải đoàn của tỉnh này đi biểu diễn nơi khác) và có ngày rõ ràng trong nguồn. Không bịa.
Trả về DUY NHẤT một mảng JSON (không markdown, không giải thích). Mỗi phần tử có các khóa:
name (tên lễ hội/sự kiện, ngắn gọn), venue (địa điểm cụ thể + phường/xã, hoặc ""), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD),
start_time (HH:MM hoặc ""), crowd (số người dự kiến hoặc null; lễ hội truyền thống không cần), fireworks (bool), big_concert (bool), summary (1 câu tiếng Việt).
Nếu không có sự kiện nào thì trả []."""

LOOKUP_PROMPT = """Hôm nay là {today}. Dùng Google Search tìm thông tin sự kiện "{name}" tại {pname} (gồm cả {old}) sắp diễn ra.
Trả về DUY NHẤT một JSON object (không markdown): start_date (YYYY-MM-DD hoặc null), end_date (YYYY-MM-DD hoặc null),
venue (địa điểm cụ thể + phường/xã, hoặc ""), start_time (HH:MM hoặc ""), crowd (số hoặc null), fireworks (bool), big_concert (bool),
summary (1 câu tiếng Việt). Chỉ lấy đợt tổ chức sắp tới/đang diễn ra; không chắc thì null/"". Không bịa."""

LOOKUP_MANY_PROMPT = """Hôm nay là {today}. Dùng Google Search tìm ngày tổ chức SẮP TỚI hoặc ĐANG diễn ra của từng sự kiện sau, được báo là ở {pname} (gồm cả {old}):
{names}
Trả về DUY NHẤT một mảng JSON (không markdown), mỗi phần tử ứng với một sự kiện trong danh sách, giữ NGUYÊN tên đã cho ở khóa name, kèm các khóa:
in_province (bool: sự kiện có thật sự diễn ra ở {pname} không), start_date (YYYY-MM-DD hoặc null), end_date (YYYY-MM-DD hoặc null),
venue (địa điểm cụ thể + phường/xã, hoặc ""), start_time (HH:MM hoặc ""), crowd (số hoặc null), fireworks (bool), big_concert (bool), summary (1 câu tiếng Việt).
Chỉ lấy đợt tổ chức sắp tới/đang diễn ra trong năm nay; không chắc thì null. Không bịa."""


class LLM:
    def __init__(self):
        self.provider = config.env("LLM_PROVIDER").lower()
        self.key = config.env("LLM_API_KEY")
        self.model = config.env("LLM_MODEL") or DEFAULT_MODEL.get(self.provider, "")
        self.calls = 0
        self.disabled = False
        self.last_error = ""
        self.search_calls = 0
        self.disc_off = False
        self._last = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.provider in DEFAULT_MODEL and self.key and not self.disabled
                    and self.calls < config.llm_max_calls())

    def status(self) -> str:
        if not self.provider:
            return "TẮT - chưa có secret LLM_PROVIDER"
        if self.provider not in DEFAULT_MODEL:
            return f"TẮT - LLM_PROVIDER='{self.provider}' không hợp lệ (cần gemini hoặc anthropic)"
        if not self.key:
            return "TẮT - chưa có secret LLM_API_KEY"
        if self.disabled:
            return f"TỰ TẮT do bị từ chối/hết hạn mức ({self.last_error}); đã gọi {self.calls}"
        s = f"BẬT ({self.provider}/{self.model}), đã gọi {self.calls}/{config.llm_max_calls()}"
        if self.provider == "gemini":
            s += f"; tìm kiếm Google {self.search_calls}/{config.llm_search_max()}" + (f" (TẮT: {self.last_error})" if self.disc_off else "")
        return s

    def _throttle(self, gap: float = 13.0) -> None:
        """Giãn cách các lệnh gọi để không vượt hạn mức RPM của gói free (5 RPM -> >= 12 giây/lần)."""
        wait = gap - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def _call(self, prompt: str) -> str:
        self._throttle()
        if self.provider == "gemini":
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}},
                timeout=60)
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": self.model, "max_tokens": 700, "messages": [{"role": "user", "content": prompt}]},
            timeout=60)
        r.raise_for_status()
        return r.json()["content"][0]["text"]

    def can_search(self) -> bool:
        return bool(self.provider == "gemini" and self.key and not self.disc_off
                    and self.search_calls < config.llm_search_max())

    def _search_call(self, prompt: str) -> str | None:
        """Gọi Gemini kèm công cụ Google Search (đọc web trực tiếp, không phụ thuộc link Google News)."""
        self._throttle()
        self.search_calls += 1
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}],
                      "generationConfig": {"temperature": 0}},
                timeout=120)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            log.warning("Gemini tìm kiếm lỗi HTTP %s: %s", code, (e.response.text[:1500] if e.response is not None else ""))
            if code in (400, 401, 403, 429):
                self.last_error = f"HTTP {code}"
                self.disc_off = True
            return None
        except Exception as e:  # noqa
            log.warning("Gemini tìm kiếm lỗi: %s", type(e).__name__)
            return None

    def discover(self, pkey: str, today: date, days: int = 21) -> list[dict] | None:
        """Hỏi Gemini (có Google Search) các sự kiện của một tỉnh trong `days` ngày tới. None = lỗi gọi."""
        from datetime import timedelta
        if not self.can_search():
            return None
        txt = self._search_call(DISCOVER_PROMPT.format(
            today=today.isoformat(), a=today.strftime("%d/%m/%Y"), b=(today + timedelta(days=days)).strftime("%d/%m/%Y"),
            name=PROVINCES[pkey][0], old=OLD_NAMES.get(pkey, "")))
        if txt is None:
            return None
        m = re.search(r"\[.*\]", txt, re.S)
        try:
            arr = json.loads(m.group(0)) if m else []
        except ValueError:
            return []
        out = []
        for raw in arr if isinstance(arr, list) else []:
            if isinstance(raw, dict):
                c = self._clean({**raw, "is_event": True, "province": pkey})
                if c["name"] and c["start_date"]:
                    out.append(c)
        return out

    def lookup(self, name: str, pkey: str, today: date) -> dict | None:
        """Tra ngày/địa điểm của MỘT sự kiện đã biết tên bằng Gemini + Google Search."""
        if not self.can_search():
            return None
        txt = self._search_call(LOOKUP_PROMPT.format(today=today.isoformat(), name=name, pname=PROVINCES[pkey][0],
                                                     old=OLD_NAMES.get(pkey, "")))
        m = re.search(r"\{.*\}", txt or "", re.S)
        try:
            raw = json.loads(m.group(0)) if m else None
        except ValueError:
            return None
        if not isinstance(raw, dict):
            return None
        c = self._clean({**raw, "is_event": True, "province": pkey, "name": name})
        return c if c["start_date"] else None

    def lookup_many(self, names: list[str], pkey: str, today: date) -> list[dict] | None:
        """Tra ngày/địa điểm cho NHIỀU sự kiện của một tỉnh bằng một lệnh Gemini + Google Search."""
        if not names or not self.can_search():
            return None
        txt = self._search_call(LOOKUP_MANY_PROMPT.format(
            today=today.isoformat(), pname=PROVINCES[pkey][0], old=OLD_NAMES.get(pkey, ""),
            names="\n".join(f"- {n}" for n in names)))
        if txt is None:
            return None
        clean = re.sub(r"```(?:json)?", "", txt)
        i = clean.find("[")
        try:
            arr = json.JSONDecoder().raw_decode(clean[i:])[0] if i >= 0 else []
        except ValueError:
            return []
        out = []
        for raw in arr if isinstance(arr, list) else []:
            if isinstance(raw, dict) and raw.get("name"):
                c = self._clean({**raw, "is_event": True, "province": pkey})
                c["name"] = str(raw["name"]).strip()
                c["in_province"] = raw.get("in_province") is not False
                out.append(c)
        return out

    def extract(self, title: str, body: str, pub: date | None, today: date) -> dict | None:
        if not self.enabled:
            return None
        self.calls += 1
        prompt = PROMPT.format(today=today.isoformat(), pub=(pub or today).isoformat(),
                               provinces=", ".join(v[0] for v in PROVINCES.values()),
                               keys=", ".join(PROVINCES), title=title, body=body[:5000])
        try:
            txt = self._call(prompt)
            m = re.search(r"\{.*\}", txt, re.S)
            raw = json.loads(m.group(0))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403, 429):
                log.warning("LLM từ chối (%s) - tắt LLM trong phiên này", e.response.status_code)
                self.last_error = f"HTTP {e.response.status_code}"
                self.disabled = True
            else:
                log.warning("LLM lỗi HTTP: %s", type(e).__name__)
            return None
        except Exception as e:  # noqa
            log.warning("LLM trả dữ liệu không hợp lệ: %s", type(e).__name__)
            return None
        return self._clean(raw)

    def same_event(self, a: str, b: str) -> bool | None:
        if not self.enabled:
            return None
        self.calls += 1
        try:
            txt = self._call(SAME_PROMPT.format(a=a, b=b))
            return bool(json.loads(re.search(r"\{.*\}", txt, re.S).group(0)).get("same"))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403, 429):
                self.last_error = f"HTTP {e.response.status_code}"
                self.disabled = True
            return None
        except Exception:  # noqa
            return None

    @staticmethod
    def _clean(raw: dict) -> dict:
        def d(v):
            try:
                return date.fromisoformat(v) if v else None
            except (ValueError, TypeError):
                return None
        crowd = raw.get("crowd")
        out = {
            "is_event": bool(raw.get("is_event")),
            "name": (raw.get("name") or "").strip()[:160],
            "province": raw.get("province") if raw.get("province") in PROVINCES else resolve_province(str(raw.get("province") or "")),
            "venue": (raw.get("venue") or "").strip()[:100],
            "start_date": d(raw.get("start_date")), "end_date": d(raw.get("end_date")),
            "start_time": (raw.get("start_time") or "").strip()[:5],
            "crowd": int(crowd) if isinstance(crowd, (int, float)) and crowd > 0 else None,
            "fireworks": bool(raw.get("fireworks")), "big_concert": bool(raw.get("big_concert")),
            "summary": (raw.get("summary") or "").strip()[:220],
        }
        if out["start_date"] and not out["end_date"]:
            out["end_date"] = out["start_date"]
        return out
