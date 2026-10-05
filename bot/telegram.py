"""Gọi Telegram Bot API trực tiếp bằng requests (nhẹ, ít phụ thuộc, chạy tốt trên GitHub Actions)."""
import logging
import time

import requests

from . import config

log = logging.getLogger("telegram")
LIMIT = 3900


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Tách theo khối (dòng trống) để không cắt giữa một sự kiện."""
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for block in text.split("\n\n"):
        while len(block) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(block[:limit])
            block = block[limit:]
        if len(cur) + len(block) + 2 > limit and cur:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts


class Telegram:
    def __init__(self, token: str | None = None, dry_run: bool = False):
        self.token = token or config.token()
        self.dry = dry_run
        if not self.dry and not self.token:
            raise RuntimeError("Thiếu TELEGRAM_BOT_TOKEN (đặt trong .env hoặc GitHub Secrets).")
        self.s = requests.Session()
        self.dead: set[str] = set()  # chat đã chặn bot/không còn tồn tại

    def _call(self, method: str, data=None, files=None, timeout=40):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(3):
            try:
                r = self.s.post(url, data=data, files=files, timeout=timeout)
            except requests.RequestException as e:
                # Không để lộ token trong log
                raise RuntimeError(f"Lỗi mạng khi gọi Telegram {method}: {str(e).replace(self.token, '***')}") from None
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)) + 1)
                continue
            j = r.json()
            if not j.get("ok"):
                raise RuntimeError(f"Telegram {method} lỗi: {j.get('description')}")
            return j["result"]
        raise RuntimeError(f"Telegram {method}: quá nhiều lần bị giới hạn tốc độ")

    def send(self, chat_id: str, text: str) -> None:
        for part in split_message(text):
            if self.dry:
                print(f"\n----- [DRY-RUN → {chat_id}] -----\n{part}\n")
                continue
            self._call("sendMessage", {"chat_id": chat_id, "text": part, "disable_web_page_preview": "true"})
            time.sleep(0.05)

    def broadcast(self, text: str, chat_ids: list[str] | None = None) -> int:
        ok = 0
        for cid in (chat_ids if chat_ids is not None else config.chat_ids()):
            try:
                self.send(cid, text)
                ok += 1
            except Exception as e:  # noqa - 1 người chặn bot không được làm hỏng cả đợt gửi
                log.error("Gửi tới %s thất bại: %s", cid, e)
                if any(k in str(e).lower() for k in ("blocked", "chat not found", "deactivated", "kicked")):
                    self.dead.add(str(cid))
        return ok

    def send_document(self, chat_id: str, path, caption: str = "") -> None:
        if self.dry:
            print(f"\n----- [DRY-RUN → {chat_id}] gửi file {path} | {caption}\n")
            return
        with open(path, "rb") as f:
            self._call("sendDocument", {"chat_id": chat_id, "caption": caption}, files={"document": f}, timeout=90)

    def get_updates(self, offset: int | None, timeout: int = 0) -> list[dict]:
        if self.dry:
            return []
        data = {"timeout": timeout, "allowed_updates": '["message"]'}
        if offset:
            data["offset"] = offset
        return self._call("getUpdates", data, timeout=timeout + 20)
    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        if self.dry:
            print(commands)
            return
        self._call("setMyCommands", {"commands": json.dumps(
            [{"command": c, "description": d} for c, d in commands], ensure_ascii=False)})
    def set_short_description(self, text: str) -> None:
        if self.dry:
            print(text)
            return
        self._call("setMyShortDescription", {"short_description": text[:120]})
