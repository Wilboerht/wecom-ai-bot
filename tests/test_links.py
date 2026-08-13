"""短链服务测试"""

from src.links import shorten_text, load_links, get_target


FAKE_LINKS = {
    "bm-xhs": "https://www.xiaohongshu.hk/goods-detail/6503f9e66b07180001092c06?xsec_token=XBjyVlzQDwulxteO3IUldQLAGwtvzGmiFoRb4cCLl2lps=",
    "bm-tm": "https://e.tb.cn/h.86E63c4xE9IR1rw?tk=9ZuQTZHH4ki",
}


def test_shorten_replaces_long_urls(monkeypatch):
    monkeypatch.setattr("src.links._links", FAKE_LINKS)
    text = "购买链接：https://www.xiaohongshu.hk/goods-detail/6503f9e66b07180001092c06?xsec_token=XBjyVlzQDwulxteO3IUldQLAGwtvzGmiFoRb4cCLl2lps="
    out = shorten_text(text)
    assert out == "购买链接：https://kf.nihplod.cn/l/bm-xhs"


def test_shorten_replaces_multiple(monkeypatch):
    monkeypatch.setattr("src.links._links", FAKE_LINKS)
    text = f"天猫：{FAKE_LINKS['bm-tm']} 小红书：{FAKE_LINKS['bm-xhs']}"
    out = shorten_text(text)
    assert "https://kf.nihplod.cn/l/bm-tm" in out
    assert "https://kf.nihplod.cn/l/bm-xhs" in out
    assert "e.tb.cn" not in out


def test_shorten_keeps_unknown_urls(monkeypatch):
    monkeypatch.setattr("src.links._links", FAKE_LINKS)
    text = "随便一个 https://example.com/page 不换"
    assert shorten_text(text) == text


def test_shorten_empty_text(monkeypatch):
    monkeypatch.setattr("src.links._links", FAKE_LINKS)
    assert shorten_text("") == ""
