from datetime import date

import pytest

from bot import collector, config
from bot.telegram import Telegram

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>T</title>
<item><title>Lễ hội Vía Bà tại Tây Ninh - Báo X</title><link>https://b.vn/1</link>
<description>&lt;p&gt;Khai mạc ngày 20/10&lt;/p&gt;</description><pubDate>{pub}</pubDate></item>
<item><title>Tin cũ</title><link>https://b.vn/old</link><pubDate>Mon, 01 Jan 2024 00:00:00 +0700</pubDate></item>
</channel></rss>"""


class Resp:
    def __init__(self, content):
        self.content = content.encode() if isinstance(content, str) else content


def test_rss(monkeypatch):
    pub = config.now().strftime("%a, %d %b %Y 08:00:00 +0700")
    monkeypatch.setattr(collector, "_get", lambda url: Resp(RSS.format(pub=pub)))
    items = collector.fetch_source({"id": "x", "name": "X", "type": "rss", "url": "u", "province_hint": "tayninh"})
    assert len(items) == 1 and items[0].summary == "Khai mạc ngày 20/10" and items[0].province_hint == "tayninh"


def test_empty_rss_raises(monkeypatch):
    monkeypatch.setattr(collector, "_get", lambda url: Resp("<html>blocked</html>"))
    with pytest.raises(RuntimeError):
        collector.fetch_source({"id": "x", "name": "X", "type": "rss", "url": "u"})


def test_html_source(monkeypatch):
    html = '<div><h3><a href="/tin/le-hoi-ok-om-bok-2026">Lễ hội Ok Om Bok 2026 sắp diễn ra</a></h3><h3><a href="/x">ngắn</a></h3></div>'
    monkeypatch.setattr(collector, "_get", lambda url: Resp(html))
    src = {"id": "h", "name": "H", "type": "html", "url": "https://so.gov.vn/tin", "item_selector": "h3 a", "province_hint": "vinhlong"}
    items = collector.fetch_source(src)
    assert len(items) == 1 and items[0].link == "https://so.gov.vn/tin/le-hoi-ok-om-bok-2026"
    monkeypatch.setattr(collector, "_get", lambda url: Resp("<html></html>"))
    with pytest.raises(RuntimeError, match="Selector"):
        collector.fetch_source(src)


def test_token_not_leaked():
    import requests
    tg = Telegram(token="SECRET123")
    def boom(*a, **k):
        raise requests.ConnectionError("failed https://api.telegram.org/botSECRET123/sendMessage")
    tg.s.post = boom
    with pytest.raises(RuntimeError) as e:
        tg.send("1", "hi")
    assert "SECRET123" not in str(e.value)


def test_missing_token():
    import os
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        Telegram(token="")
