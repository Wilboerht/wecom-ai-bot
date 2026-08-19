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
