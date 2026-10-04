"""Cấu hình đọc từ biến môi trường (không bao giờ ghi token vào mã nguồn)."""
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _load_dotenv() -> None:
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else default


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except ValueError:
        return default


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def token() -> str:
    return env("TELEGRAM_BOT_TOKEN")


def _ids(name: str) -> list[str]:
    return [x.strip() for x in env(name).replace(";", ",").split(",") if x.strip()]


def chat_ids() -> list[str]:
    return _ids("TELEGRAM_CHAT_IDS")


def admin_ids() -> list[str]:
    return _ids("ADMIN_CHAT_IDS") or chat_ids()


def join_code() -> str:
    """Mã tham gia (tuỳ chọn). Đặt BOT_JOIN_CODE để chỉ người có mã mới tự đăng ký được; để trống = ai cũng đăng ký được."""
    return env("BOT_JOIN_CODE")


def db_path() -> Path:
    p = Path(env("DB_PATH", "data/events.db"))
    return p if p.is_absolute() else ROOT / p


def lookahead_days() -> int:
    return env_int("LOOKAHEAD_DAYS", 7)


def large_crowd() -> int:
    return env_int("LARGE_CROWD", 10000)


def digest_hour() -> int:
    return env_int("DIGEST_HOUR", 7)


def collect_interval_min() -> int:
    return env_int("COLLECT_INTERVAL_MIN", 120)


def fail_threshold() -> int:
    return env_int("FAIL_ALERT_THRESHOLD", 3)


def max_article_fetch() -> int:
    return env_int("MAX_ARTICLE_FETCH", 40)


def enrich_max() -> int:
    """Số sự kiện tối đa được tìm lại bài báo trong mỗi lượt thu thập."""
    return env_int("ENRICH_MAX", 6)


def enrich_budget_sec() -> int:
    return env_int("ENRICH_BUDGET_SEC", 70)


def llm_max_calls() -> int:
    return env_int("LLM_MAX_CALLS", 30)
