"""SQLite: bài đã xử lý, sự kiện, trạng thái nguồn, metadata (offset Telegram, mốc chạy)."""
import json
import sqlite3
from datetime import date, datetime, timedelta

from . import config
from .geo import normalize
from .models import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles(url TEXT PRIMARY KEY, source TEXT, title TEXT, seen_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, province TEXT, venue TEXT,
  start_date TEXT, end_date TEXT, start_time TEXT, crowd INTEGER,
  fireworks INTEGER DEFAULT 0, big_concert INTEGER DEFAULT 0, priority TEXT, summary TEXT,
  sources TEXT, first_seen TEXT, updated_at TEXT, alerted INTEGER DEFAULT 0, search_text TEXT);
CREATE INDEX IF NOT EXISTS ix_events_date ON events(start_date, end_date);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS source_health(
  source_id TEXT PRIMARY KEY, name TEXT, fail_count INTEGER DEFAULT 0, last_error TEXT,
  last_ok TEXT, notified INTEGER DEFAULT 0, last_count INTEGER DEFAULT 0);
"""


RULES_VERSION = "3"   # tăng số này khi đổi luật lọc/trích xuất: dữ liệu cũ sẽ được làm sạch và xử lý lại


def _d(s):
    return date.fromisoformat(s) if s else None


def _iso(d):
    return d.isoformat() if d else None


class DB:
    def __init__(self, path=None):
        self.path = path or config.db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Đổi luật lọc -> xoá sự kiện/bài cũ (do luật cũ lọc lỏng) để thu thập lại sạch. Giữ offset Telegram, trạng thái nguồn."""
        if self.get("rules_version") != RULES_VERSION:
            self.conn.execute("DELETE FROM events")
            self.conn.execute("DELETE FROM articles")
            self.conn.execute("DELETE FROM meta WHERE key='last_collect'")
            self.set("rules_version", RULES_VERSION)

    def close(self):
        self.conn.commit()
        self.conn.close()

    # ---- meta ----
    def get(self, key, default=None):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set(self, key, value):
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (key, str(value)))
        self.conn.commit()

    # ---- articles ----
    def seen(self, url: str) -> bool:
        return self.conn.execute("SELECT 1 FROM articles WHERE url=?", (url,)).fetchone() is not None

    def add_article(self, url, source, title, status):
        self.conn.execute("INSERT OR REPLACE INTO articles VALUES(?,?,?,?,?)",
                          (url, source, title, config.now().isoformat(timespec="seconds"), status))

    # ---- events ----
    @staticmethod
    def _row_to_event(r) -> Event:
        return Event(id=r["id"], name=r["name"], province=r["province"], venue=r["venue"] or "",
                     start_date=_d(r["start_date"]), end_date=_d(r["end_date"]), start_time=r["start_time"] or "",
                     crowd=r["crowd"], fireworks=bool(r["fireworks"]), big_concert=bool(r["big_concert"]),
                     summary=r["summary"] or "", sources=json.loads(r["sources"] or "[]"),
                     priority=r["priority"] or "THAP", alerted=bool(r["alerted"]))

    @staticmethod
    def _search_text(e: Event) -> str:
        return normalize(f"{e.name} {e.venue} {e.summary} {e.province}")

    def insert_event(self, e: Event) -> int:
        e.priority = e.compute_priority()
        now = config.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO events(name,province,venue,start_date,end_date,start_time,crowd,fireworks,big_concert,"
            "priority,summary,sources,first_seen,updated_at,alerted,search_text) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (e.name, e.province, e.venue, _iso(e.start_date), _iso(e.end_date), e.start_time, e.crowd,
             int(e.fireworks), int(e.big_concert), e.priority, e.summary, json.dumps(e.sources),
             now, now, self._search_text(e)))
        self.conn.commit()
        e.id = cur.lastrowid
        return e.id

    def update_event(self, e: Event):
        e.priority = e.compute_priority()
        self.conn.execute(
            "UPDATE events SET name=?,venue=?,start_date=?,end_date=?,start_time=?,crowd=?,fireworks=?,big_concert=?,"
            "priority=?,summary=?,sources=?,updated_at=?,search_text=? WHERE id=?",
            (e.name, e.venue, _iso(e.start_date), _iso(e.end_date), e.start_time, e.crowd, int(e.fireworks),
             int(e.big_concert), e.priority, e.summary, json.dumps(e.sources),
             config.now().isoformat(timespec="seconds"), self._search_text(e), e.id))
        self.conn.commit()

    def candidates_for_dedupe(self, province: str, start: date | None) -> list[Event]:
        """Sự kiện cùng tỉnh, lệch ngày <= 1 hoặc chưa rõ ngày."""
        rows = self.conn.execute("SELECT * FROM events WHERE province=?", (province,)).fetchall()
        out = []
        for r in rows:
            e = self._row_to_event(r)
            if start is None or e.start_date is None or abs((e.start_date - start).days) <= 1:
                out.append(e)
        return out

    def events_between(self, start: date, end: date, province: str | None = None) -> list[Event]:
        q = ("SELECT * FROM events WHERE start_date IS NOT NULL AND start_date<=? "
             "AND COALESCE(end_date,start_date)>=?")
        args = [end.isoformat(), start.isoformat()]
        if province:
            q += " AND province=?"
            args.append(province)
        q += " ORDER BY start_date, CASE priority WHEN 'CAO' THEN 0 WHEN 'TB' THEN 1 ELSE 2 END, name"
        return [self._row_to_event(r) for r in self.conn.execute(q, args)]

    def search(self, keyword: str, today: date, limit=15) -> list[Event]:
        kw = normalize(keyword)
        if not kw:
            return []
        rows = self.conn.execute(
            "SELECT * FROM events WHERE search_text LIKE ? AND (start_date IS NULL OR COALESCE(end_date,start_date)>=?) "
            "ORDER BY start_date IS NULL, start_date LIMIT ?",
            (f"%{kw}%", (today - timedelta(days=1)).isoformat(), limit))
        return [self._row_to_event(r) for r in rows]

    def unalerted_large(self, today: date, horizon=60) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE alerted=0 AND start_date IS NOT NULL AND start_date<=? "
            "AND COALESCE(end_date,start_date)>=? ORDER BY start_date",
            ((today + timedelta(days=horizon)).isoformat(), today.isoformat()))
        return [e for e in map(self._row_to_event, rows) if e.is_large]

    def mark_alerted(self, event_id: int):
        self.conn.execute("UPDATE events SET alerted=1 WHERE id=?", (event_id,))
        self.conn.commit()

    def all_events(self) -> list[Event]:
        return [self._row_to_event(r) for r in self.conn.execute("SELECT * FROM events ORDER BY id")]

    def delete_event(self, event_id: int):
        self.conn.execute("DELETE FROM events WHERE id=?", (event_id,))
        self.conn.commit()

    def count_events(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def purge_old(self):
        cut = (config.today() - timedelta(days=90)).isoformat()
        self.conn.execute("DELETE FROM articles WHERE seen_at < ?", (cut,))
        self.conn.execute("DELETE FROM events WHERE COALESCE(end_date,start_date,substr(first_seen,1,10)) < ?", (cut,))
        self.conn.commit()

    # ---- sức khoẻ nguồn ----
    def health_ok(self, sid, name, count) -> bool:
        """Ghi nhận nguồn chạy tốt. Trả True nếu vừa hồi phục sau khi đã báo lỗi."""
        r = self.conn.execute("SELECT notified FROM source_health WHERE source_id=?", (sid,)).fetchone()
        recovered = bool(r and r["notified"])
        self.conn.execute(
            "INSERT INTO source_health(source_id,name,fail_count,last_error,last_ok,notified,last_count) VALUES(?,?,0,'',?,0,?) "
            "ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,fail_count=0,last_error='',last_ok=excluded.last_ok,"
            "notified=0,last_count=excluded.last_count",
            (sid, name, config.now().isoformat(timespec="seconds"), count))
        self.conn.commit()
        return recovered

    def health_fail(self, sid, name, error) -> int:
        self.conn.execute(
            "INSERT INTO source_health(source_id,name,fail_count,last_error) VALUES(?,?,1,?) "
            "ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,fail_count=fail_count+1,last_error=excluded.last_error",
            (sid, name, str(error)[:300]))
        self.conn.commit()
        return self.conn.execute("SELECT fail_count FROM source_health WHERE source_id=?", (sid,)).fetchone()[0]

    def health_should_notify(self, sid, threshold) -> bool:
        r = self.conn.execute("SELECT fail_count, notified FROM source_health WHERE source_id=?", (sid,)).fetchone()
        return bool(r and r["fail_count"] >= threshold and not r["notified"])

    def health_mark_notified(self, sid):
        self.conn.execute("UPDATE source_health SET notified=1 WHERE source_id=?", (sid,))
        self.conn.commit()

    def health_all(self):
        return self.conn.execute("SELECT * FROM source_health ORDER BY source_id").fetchall()
