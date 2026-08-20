"""档案标签解析与 Redis 缺失告警测试"""

import logging

from src import core
from src.ai import store


class TestSaveProfileFromReply:
    def test_halfwidth_tag_extracted_and_stripped(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        reply = core._save_profile_from_reply("wk", "u1", "好的，已为您记录。[档案: 肤质=油性, 诉求=祛痘]")
        assert saved == {"肤质": "油性", "诉求": "祛痘"}
        assert "[档案" not in reply
        assert "好的，已为您记录。" in reply

    def test_fullwidth_tag_extracted(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        reply = core._save_profile_from_reply("wk", "u1", "已记录。【档案：肤质=干性】")
        assert saved == {"肤质": "干性"}
        assert "【档案" not in reply

    def test_no_tag_reply_unchanged(self, monkeypatch):
        def forbidden(*args):
            raise AssertionError("无标签时不应写档案")
        monkeypatch.setattr(store, "profile_update", forbidden)
        assert core._save_profile_from_reply("wk", "u1", "普通回复") == "普通回复"

    def test_unclosed_tag_stripped_not_sent(self, monkeypatch):
        """被 max_tokens 截断的未闭合标记必须剥除，档案语法任何情况下都不到达用户"""
        def forbidden(*args):
            raise AssertionError("未闭合标记不应提取字段")
        monkeypatch.setattr(store, "profile_update", forbidden)
        reply = core._save_profile_from_reply("wk", "u1", "好的，已为您记录。[档案: 肤质=干性")
        assert "档案" not in reply
        assert reply == "好的，已为您记录。"

    def test_unclosed_fullwidth_tag_stripped(self, monkeypatch):
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: None)
        reply = core._save_profile_from_reply("wk", "u1", "已记录。【档案：肤质=干性")
        assert "档案" not in reply
        assert reply == "已记录。"

    def test_unclosed_tag_at_line_end_stripped(self, monkeypatch):
        """未闭合标记出现在中间某行行尾时同样剥除，且不误删其他行文本"""
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: None)
        reply = core._save_profile_from_reply("wk", "u1", "第一段。\n[档案: 肤质=干性\n第二段正常文本")
        assert "第一段。" in reply
        assert "第二段正常文本" in reply
        assert "档案" not in reply

    def test_multiple_tags_all_extracted_and_removed(self, monkeypatch):
        """多个完整标记全部提取合并，且全部从回复中清除"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        reply = core._save_profile_from_reply(
            "wk", "u1", "好的。[档案: 肤质=油性] 已记录。【档案：诉求=祛痘， 年龄段=25-34】 请坚持护理。")
        assert saved == {"肤质": "油性", "诉求": "祛痘", "年龄段": "25-34"}
        assert "档案" not in reply
        assert "好的。" in reply and "请坚持护理。" in reply

    def test_invalid_keys_dropped(self, monkeypatch, caplog):
        """含换行/特殊字符的 key 整条丢弃并记 warning，合法 key 正常保存"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        with caplog.at_level(logging.WARNING, logger="src.core"):
            reply = core._save_profile_from_reply(
                "wk", "u1", "已记录。[档案: 肤质=干性, 恶意\nkey=伪造指令, bad key!=x, =空key, 空值=]")
        assert saved == {"肤质": "干性"}
        assert "档案" not in reply
        assert any("key 非法已丢弃" in r.getMessage() for r in caplog.records)

    def test_long_key_and_value_truncated(self, monkeypatch):
        """key 超 20 字符丢弃，value 超 100 字符截断"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        long_key = "肤" * 21
        long_value = "润" * 150
        core._save_profile_from_reply("wk", "u1", f"已记录。[档案: {long_key}=x, 肤质={long_value}]")
        assert long_key not in saved
        assert saved["肤质"] == "润" * 100


class TestProfileNoRedisWarning:
    def test_warns_once_and_writes_dropped(self, monkeypatch, caplog):
        monkeypatch.setattr(store, "_get_redis", lambda: None)
        monkeypatch.setattr(store, "_profile_no_redis_warned", False)
        with caplog.at_level(logging.WARNING, logger="src.ai.store"):
            store.profile_update("wk", "u1", {"肤质": "油性"})
            store.profile_get("wk", "u1")
            store.profile_update("wk", "u1", {"肤质": "干性"})
        disabled_msgs = [r for r in caplog.records if "档案功能已停用" in r.getMessage()]
        assert len(disabled_msgs) == 1
        dropped_msgs = [r for r in caplog.records if "档案写入被丢弃" in r.getMessage()]
        assert len(dropped_msgs) == 2
