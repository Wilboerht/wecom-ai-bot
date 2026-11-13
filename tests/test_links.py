"""短链服务测试"""

import json
import logging
import os

import pytest

from src.links import shorten_text, load_links, get_target


FAKE_LINKS = {
    "bm-xhs": "https://www.xiaohongshu.hk/goods-detail/6503f9e66b07180001092c06?xsec_token=XBjyVlzQDwulxteO3IUldQLAGwtvzGmiFoRb4cCLl2lps=",
    "bm-tm": "https://e.tb.cn/h.86E63c4xE9IR1rw?tk=9ZuQTZHH4ki",
}

FAKE_DY = {
    "jm-dy": "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=3824659499508170825&origin_type=604",
}


@pytest.fixture(autouse=True)
def _reset_links_state(monkeypatch):
    """每个用例前重置模块缓存，避免热加载状态在用例间泄漏"""
    monkeypatch.setattr("src.links._links", {})
    monkeypatch.setattr("src.links._links_path", "")
    monkeypatch.setattr("src.links._links_fingerprint", None)


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


class TestLoadLinks:
    def test_hot_reload_on_mtime_change(self, tmp_path):
        """mtime 变化后重新加载，无需重启"""
        f = tmp_path / "links.json"
        _write(f, {"a": "https://example.com/a"})
        os.utime(f, (1000, 1000))
        assert load_links(str(f)) == {"a": "https://example.com/a"}

        _write(f, {"b": "https://example.com/b"})
        os.utime(f, (2000, 2000))
        assert load_links(str(f)) == {"b": "https://example.com/b"}

    def test_mtime_unchanged_keeps_cache(self, tmp_path):
        """mtime 未变时不重新解析（即使内容被改动）"""
        f = tmp_path / "links.json"
        _write(f, {"a": "https://example.com/a"})
        os.utime(f, (1000, 1000))
        load_links(str(f))

        f.write_text("not json", encoding="utf-8")
        os.utime(f, (1000, 1000))
        assert load_links(str(f)) == {"a": "https://example.com/a"}

    def test_load_failure_keeps_last_good_cache(self, tmp_path, caplog):
        """加载失败保留上一份成功加载的缓存并记 error"""
        f = tmp_path / "links.json"
        _write(f, {"a": "https://example.com/a"})
        os.utime(f, (1000, 1000))
        load_links(str(f))

        f.write_text("{bad json", encoding="utf-8")
        os.utime(f, (2000, 2000))
        with caplog.at_level(logging.ERROR, logger="src.links"):
            result = load_links(str(f))
        assert result == {"a": "https://example.com/a"}
        assert any("加载失败" in r.message for r in caplog.records)

    def test_non_dict_json_keeps_last_good_cache(self, tmp_path, caplog):
        """合法 JSON 但顶层非对象（如数组）按加载失败处理：保旧缓存、不更新 mtime"""
        f = tmp_path / "links.json"
        _write(f, {"a": "https://example.com/a"})
        os.utime(f, (1000, 1000))
        load_links(str(f))

        f.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
        os.utime(f, (2000, 2000))
        with caplog.at_level(logging.ERROR, logger="src.links"):
            result = load_links(str(f))
        assert result == {"a": "https://example.com/a"}
        assert any("加载失败" in r.message for r in caplog.records)
        # mtime 未更新：修复文件后仍可热加载
        _write(f, {"b": "https://example.com/b"})
        os.utime(f, (3000, 3000))
        assert load_links(str(f)) == {"b": "https://example.com/b"}

    def test_invalid_url_scheme_ignored(self, tmp_path, caplog):
        """非 http/https 目标不进入短链映射，避免开放重定向到 javascript:。"""
        f = tmp_path / "links.json"
        _write(f, {"bad": "javascript:alert(1)", "good": "https://example.com/a"})
        with caplog.at_level(logging.WARNING, logger="src.links"):
            result = load_links(str(f))
        assert result == {"good": "https://example.com/a"}
        assert any("条目非法" in r.message for r in caplog.records)

    def test_get_target_triggers_hot_reload(self, tmp_path):
        """读取路径上自动热加载：改文件后 get_target 立即生效"""
        f = tmp_path / "links.json"
        _write(f, {"a": "https://example.com/a"})
        os.utime(f, (1000, 1000))
        load_links(str(f))
        assert get_target("a") == "https://example.com/a"

        _write(f, {"b": "https://example.com/b"})
        os.utime(f, (2000, 2000))
        assert get_target("a") == ""
        assert get_target("b") == "https://example.com/b"


class TestShortenText:
    def test_shorten_replaces_long_urls(self, monkeypatch):
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        text = "购买链接：https://www.xiaohongshu.hk/goods-detail/6503f9e66b07180001092c06?xsec_token=XBjyVlzQDwulxteO3IUldQLAGwtvzGmiFoRb4cCLl2lps="
        out = shorten_text(text)
        assert out == "购买链接：https://kf.nihplod.cn/l/bm-xhs"

    def test_shorten_replaces_multiple(self, monkeypatch):
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        text = f"天猫：{FAKE_LINKS['bm-tm']} 小红书：{FAKE_LINKS['bm-xhs']}"
        out = shorten_text(text)
        assert "https://kf.nihplod.cn/l/bm-tm" in out
        assert "https://kf.nihplod.cn/l/bm-xhs" in out
        assert "e.tb.cn" not in out

    def test_shorten_keeps_unknown_urls(self, monkeypatch):
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        text = "随便一个 https://example.com/page 不换"
        assert shorten_text(text) == text

    def test_oil_placeholder_does_not_replace_official_homepage(self, monkeypatch):
        """占位链接带独立 query，不能把普通官网链接误替换成护理油短链。"""
        monkeypatch.setattr(
            "src.links._links",
            {"oil-dy": "https://nihplod.cn/?from=oil-dy"},
        )
        text = "官网 https://nihplod.cn 看看"
        assert shorten_text(text) == text

    def test_shorten_empty_text(self, monkeypatch):
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        assert shorten_text("") == ""

    def test_shorten_xhs_time_params_changed(self, monkeypatch):
        """小红书时效参数（xsec_token/apptime/share_id）被 LLM 改写后仍能匹配"""
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        url = (
            "https://www.xiaohongshu.hk/goods-detail/6503f9e66b07180001092c06"
            "?xsec_token=REWRITTEN&apptime=9999999999&share_id=abc123"
        )
        out = shorten_text(f"购买：[小红书:{url}]")
        assert out == "购买：[小红书:https://kf.nihplod.cn/l/bm-xhs]"

    def test_shorten_xhs_query_dropped(self, monkeypatch):
        """小红书链接 query 被整体截掉也能按商品 id 路径匹配"""
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        text = "看这里 https://www.xiaohongshu.hk/goods-detail/6503f9e66b07180001092c06 速抢"
        out = shorten_text(text)
        assert out == "看这里 https://kf.nihplod.cn/l/bm-xhs 速抢"

    def test_shorten_xhs_other_goods_not_replaced(self, monkeypatch):
        """不同商品 id 的小红书链接不误配"""
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        text = "https://www.xiaohongshu.hk/goods-detail/64b7a0c4c142780001f7e41a?xsec_token=x"
        assert shorten_text(text) == text

    def test_shorten_tmall_tk_changed(self, monkeypatch):
        """天猫短链 tk 参数被改写后仍按路径匹配"""
        monkeypatch.setattr("src.links._links", FAKE_LINKS)
        text = "天猫 https://e.tb.cn/h.86E63c4xE9IR1rw?tk=CHANGED123"
        out = shorten_text(text)
        assert out == "天猫 https://kf.nihplod.cn/l/bm-tm"

    def test_shorten_douyin_by_goods_id(self, monkeypatch):
        """抖音链接按 query 中的商品 id 匹配，其余参数任意"""
        monkeypatch.setattr("src.links._links", FAKE_DY)
        text = "抖音 https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=3824659499508170825 速达"
        out = shorten_text(text)
        assert out == "抖音 https://kf.nihplod.cn/l/jm-dy 速达"

    def test_shorten_douyin_other_id_not_replaced(self, monkeypatch):
        """不同商品 id 的抖音链接不误配（路径相同仅靠 id 区分）"""
        monkeypatch.setattr("src.links._links", FAKE_DY)
        text = "https://haohuo.jinritemai.com/ecommerce/trade/detail/index.html?id=3577875397465002136&origin_type=604"
        assert shorten_text(text) == text
