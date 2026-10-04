"""Trích xuất bằng LLM (tuỳ chọn). Bật bằng LLM_PROVIDER + LLM_API_KEY. Lỗi/hết hạn mức -> tự bỏ qua, quay về luật regex."""
import json
import logging
import re
from datetime import date

import requests

from . import config
from .geo import PROVINCES, resolve_province

log = logging.getLogger("llm")
DEFAULT_MODEL = {"gemini": "gemini-2.5-flash", "anthropic": "claude-haiku-4-5-20251001"}

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


class LLM:
    def __init__(self):
        self.provider = config.env("LLM_PROVIDER").lower()
        self.key = config.env("LLM_API_KEY")
        self.model = config.env("LLM_MODEL") or DEFAULT_MODEL.get(self.provider, "")
        self.calls = 0
        self.disabled = False
        self.last_error = ""

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
        return f"BẬT ({self.provider}/{self.model}), đã gọi {self.calls}/{config.llm_max_calls()}"

    def _call(self, prompt: str) -> str:
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
