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
is_event (bool: bài có nói về MỘT sự kiện/lễ hội cụ thể sắp hoặc đang diễn ra, tập trung đông người?),
name (tên chính thức của sự kiện, bỏ cụm như 'sẵn sàng cho', 'hoàn tất công tác chuẩn bị), province (tỉnh nơi sự kiện DIỄN RA (nhận cả tên tỉnh cũ trước sáp nhập 2025: 
Kiên Giang→An Giang, Long An→Tây Ninh, Bến Tre/Trà Vinh→Vĩnh Long, Tiền Giang→Đồng Tháp, Sóc Trăng/Hậu Giang→Cần Thơ, Bạc Liêu→Cà Mau, Bình Dương/Bà Rịa-Vũng Tàu→TP.HCM, 
Bình Phước→Đồng Nai). Sự kiện ở nơi khác thì null), venue (địa điểm cụ thể: tên nơi + phường/xã, hoặc ""), start_date (YYYY-MM-DD hoặc null), end_date (YYYY-MM-DD hoặc null),
start_time (HH:MM hoặc ""), crowd (số người dự kiến, số nguyên hoặc null), fireworks (bool), big_concert (bool: đại nhạc hội/countdown/concert lớn),
summary (1 câu tóm tắt tiếng Việt).
Không bịa. Thiếu thông tin thì để null/"". Ngày âm lịch phải đổi sang dương lịch nếu bài có nêu, nếu không thì null.

SAME_PROMPT = """Hai tin dưới đây có nói về CÙNG MỘT sự kiện/lễ hội (cùng đợt tổ chức, cùng địa phương) không?
Khác tiêu đề, khác báo, ngày lệch nhau (ngày chuẩn bị / khai mạc / cả đợt) vẫn có thể là một sự kiện. Khác năm hoặc khác địa điểm thì không phải.
Trả về DUY NHẤT JSON: {{"same": true}} hoặc {{"same": false}}
A: {a}
B: {b}"""

TIÊU ĐỀ: {title}
NỘI DUNG: {body}"""


class LLM:
    def same_event(self, a: str, b: str) -> bool | None:
        if not self.enabled:
            return None
        self.calls += 1
        try:
            txt = self._call(SAME_PROMPT.format(a=a, b=b))
            return bool(json.loads(re.search(r"\{.*\}", txt, re.S).group(0)).get("same"))
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403, 429):
                self.disabled = True
            return None
        except Exception:  # noqa
            return None

    def __init__(self):
        self.provider = config.env("LLM_PROVIDER").lower()
        self.key = config.env("LLM_API_KEY")
        self.model = config.env("LLM_MODEL") or DEFAULT_MODEL.get(self.provider, "")
        self.calls = 0
        self.disabled = False

    @property
    def enabled(self) -> bool:
        return bool(self.provider in DEFAULT_MODEL and self.key and not self.disabled
                    and self.calls < config.llm_max_calls())

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
                self.disabled = True
            else:
                log.warning("LLM lỗi HTTP: %s", type(e).__name__)
            return None
        except Exception as e:  # noqa
            log.warning("LLM trả dữ liệu không hợp lệ: %s", type(e).__name__)
            return None
        return self._clean(raw)

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
