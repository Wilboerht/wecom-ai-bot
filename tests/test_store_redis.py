"""store.py Redis 真实路径测试（fakeredis 内存实现，不连真实 Redis）

conftest 的 autouse fixture 强制 _get_redis → None（其他测试依赖内存模式）；
本模块用自己的 fixture 在其之后重新注入 fakeredis 客户端，覆盖 Redis 分支。
"""

import logging
import time

import fakeredis
import pytest
import redis.exceptions

from src.ai import store
from src.config import config


@pytest.fixture
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    # 后于 conftest 的 autouse fixture 执行，覆盖其强制 None 的注入
    monkeypatch.setattr(store, "_get_redis", lambda: client)
    yield client
    # _redis_down 会直接改模块全局状态，降级路径也可能写内存字典，统一复原
    store._redis = None
    store._redis_retry_at = 0.0
    store._inmem_msgids.clear()
    store._inmem_sessions.clear()
    store._inmem_session_ttl.clear()
    store._inmem_ratelimit.clear()
    store._inmem_kv.clear()


class TestMsgidClaim:
    def test_claim_release_semantics(self, fake_redis):
        # SET NX：首次认领成功，重复认领失败；release 后可重新认领
        assert store.msgid_claim("m1") is True
        assert store.msgid_claim("m1") is False
        store.msgid_release("m1")
        assert store.msgid_claim("m1") is True

    def test_claim_sets_ttl(self, fake_redis):
        store.msgid_claim("m2")
        ttl = fake_redis.ttl(f"{store.MSGID_PREFIX}m2")
        assert 0 < ttl <= store.MSGID_TTL


class TestSession:
    def test_add_get_and_ltrim(self, fake_redis):
        # LIST 原子追加 + LTRIM 只保留最近 20 条
        for i in range(25):
            store.session_add("u1", "user", f"m{i}")
        ctx = store.session_get("u1")
        assert len(ctx) == 20
        assert ctx[0]["content"] == "m5"
        assert ctx[-1] == {"role": "user", "content": "m24"}

    def test_session_ttl_set(self, fake_redis):
        store.session_add("u2", "user", "hello")
        ttl = fake_redis.ttl(f"{store.SESSION_PREFIX}u2")
        assert 0 < ttl <= config.SESSION_TTL


class TestKv:
    def test_set_get_with_ttl(self, fake_redis):
        store.kv_set("k1", "v1", ttl=60)
        assert store.kv_get("k1") == "v1"
        assert 0 < fake_redis.ttl("k1") <= 60

    def test_get_missing_returns_empty(self, fake_redis):
        assert store.kv_get("no-such-key") == ""

    def test_kv_set_nx_atomic(self, fake_redis):
        """SET NX EX：首次占用成功，重复失败，带 TTL"""
        assert store.kv_set_nx("nx1", "v", ttl=60) is True
        assert store.kv_set_nx("nx1", "v", ttl=60) is False
        assert store.kv_get("nx1") == "v"
        assert 0 < fake_redis.ttl("nx1") <= 60


class TestRatelimit:
    def test_limited_after_threshold(self, fake_redis):
        assert store.ratelimit_check("ru1", max_per_window=2) is False
        assert store.ratelimit_check("ru1", max_per_window=2) is False
        assert store.ratelimit_check("ru1", max_per_window=2) is True

    def test_stale_send_time_not_counted(self, fake_redis):
        old = int(time.time()) - 3600
        assert store.ratelimit_check("ru2", send_time=old, max_per_window=1) is False
        assert store.ratelimit_check("ru2", max_per_window=1) is False

    def test_same_second_messages_all_counted(self, fake_redis):
        """同秒多条消息：member 带唯一后缀，不互相覆盖，计数正确"""
        now = int(time.time())
        assert store.ratelimit_check("ru3", send_time=now, max_per_window=2) is False
        assert store.ratelimit_check("ru3", send_time=now, max_per_window=2) is False
        assert store.ratelimit_check("ru3", send_time=now, max_per_window=2) is True


class TestRedisDownFallback:
    """Redis 运行期抛异常时降级内存模式，不炸且语义保持"""

    def test_msgid_claim_falls_back_to_memory(self, fake_redis, monkeypatch):
        def boom(*a, **kw):
            raise redis.exceptions.RedisError("connection lost")

        monkeypatch.setattr(fake_redis, "set", boom)
        assert store.msgid_claim("merr") is True   # 降级内存，首次认领成功
        assert store.msgid_claim("merr") is False  # 内存去重同样生效

    def test_kv_falls_back_to_memory(self, fake_redis, monkeypatch):
        def boom(*a, **kw):
            raise redis.exceptions.RedisError("connection lost")

        monkeypatch.setattr(fake_redis, "set", boom)
        monkeypatch.setattr(fake_redis, "get", boom)
        store.kv_set("kerr", "verr", ttl=60)  # 写入降级内存
        assert store.kv_get("kerr") == "verr"


class TestProfileRobustness:
    """档案上限、脏数据容错与报告字段原子替换"""

    def test_max_keys_drops_new_but_updates_existing(self, fake_redis, caplog):
        # 先塞满 50 个 key
        store.profile_update("kf1", "cap1", {f"key{i:02d}": "v" for i in range(store.PROFILE_MAX_KEYS)})
        assert len(store.profile_get("kf1", "cap1")) == store.PROFILE_MAX_KEYS
        # 已有 key 可更新，新增 key 被丢弃并记 warning
        with caplog.at_level(logging.WARNING, logger="src.ai.store"):
            store.profile_update("kf1", "cap1", {"key00": "updated", "new_key": "nope"})
        profile = store.profile_get("kf1", "cap1")
        assert profile["key00"] == "updated"
        assert "new_key" not in profile
        assert len(profile) == store.PROFILE_MAX_KEYS
        assert any("超上限" in r.getMessage() for r in caplog.records)

    def test_corrupted_json_returns_empty_without_redis_down(self, fake_redis, monkeypatch, caplog):
        down_calls = []
        monkeypatch.setattr(store, "_redis_down", lambda e: down_calls.append(e))
        fake_redis.set(f"{store._PROFILE_PREFIX}kf1:bad1", "{not-json")
        with caplog.at_level(logging.ERROR, logger="src.ai.store"):
            assert store.profile_get("kf1", "bad1") == {}
        assert down_calls == []  # 脏数据不触发全局降级
        assert fake_redis.get(f"{store._PROFILE_PREFIX}kf1:bad1") is None  # 坏 key 已被清除
        assert any("JSON 损坏" in r.getMessage() for r in caplog.records)

    def test_corrupted_json_then_update_recovers(self, fake_redis, monkeypatch):
        monkeypatch.setattr(store, "_redis_down", lambda e: (_ for _ in ()).throw(AssertionError("不应降级")))
        fake_redis.set(f"{store._PROFILE_PREFIX}kf1:bad2", "}{")
        store.profile_update("kf1", "bad2", {"肤质": "干性"})
        assert store.profile_get("kf1", "bad2") == {"肤质": "干性"}

    def test_replace_report_keys_single_set(self, fake_redis, monkeypatch):
        """删旧增新一步到位：一次 read→合并→单次 SET"""
        set_calls = []
        orig_set = fake_redis.set
        monkeypatch.setattr(fake_redis, "set", lambda *a, **kw: (set_calls.append(a), orig_set(*a, **kw))[1])
        store.profile_update("kf1", "rep1", {"过敏史": "花粉", "肤质": "油性", "诉求": "祛痘"})
        set_calls.clear()

        store.profile_replace_report_keys("kf1", "rep1", ["过敏史", "肤质"], {"肤质": "干性"})

        assert len(set_calls) == 1
        assert store.profile_get("kf1", "rep1") == {"肤质": "干性", "诉求": "祛痘"}


class TestPiiExportDeleteRedis:
    """PII 导出/删除的 Redis 路径（含只落 Redis 的 profile/privacy/last_agent）"""

    def test_export_full_structure(self, fake_redis):
        uid = "kf1:eu1"
        store.session_add(uid, "user", "hi")
        store.profile_update("kf1", "eu1", {"过敏史": "花粉"})
        store.privacy_notice_accept("kf1", "eu1")
        store.set_last_agent("eu1", "agent01")
        store.kv_set("orderflow:kf1:eu1", '{"step":"channel"}', 600)
        store.kv_set("welcome-fallback:kf1:eu1:sc1", "1", 600)
        store.ratelimit_check("eu1")

        data = store.export_user_data("kf1", "eu1")
        assert data["session"][0]["content"] == "hi"
        assert data["profile"] == {"过敏史": "花粉"}
        assert data["privacy_notice_accepted"] is True
        assert data["last_agent"] == "agent01"
        assert data["kv"]["orderflow:kf1:eu1"] == '{"step":"channel"}'
        assert data["kv"]["welcome-fallback:kf1:eu1:sc1"] == "1"
        assert "kf:transferred:kf1:eu1" not in data["kv"]  # 未写入的不出现
        assert len(data["ratelimit"]) == 1

    def test_delete_clears_all_keys(self, fake_redis):
        uid = "kf1:eu2"
        store.session_add(uid, "user", "hi")
        store.profile_update("kf1", "eu2", {"肤质": "干性"})
        store.privacy_notice_accept("kf1", "eu2")
        store.set_last_agent("eu2", "agent01")
        store.kv_set("orderflow:kf1:eu2", "{}", 600)
        store.kv_set("kf:transferred:kf1:eu2", "1", 300)
        store.kv_set("welcome-fallback:kf1:eu2:sc1", "1", 600)
        store.ratelimit_check("eu2")

        deleted = store.delete_user_data("kf1", "eu2")
        assert set(deleted) == {
            "sess:kf1:eu2", "profile:kf1:eu2", "privacy:kf1:eu2",
            "agent:eu2", "rl:eu2",
            "orderflow:kf1:eu2", "kf:transferred:kf1:eu2",
            "welcome-fallback:kf1:eu2:sc1",
        }
        # 二次导出应为空
        again = store.export_user_data("kf1", "eu2")
        assert again["session"] is None
        assert again["profile"] is None
        assert again["privacy_notice_accepted"] is False
        assert again["last_agent"] is None
        assert again["kv"] is None
        assert again["ratelimit"] is None

    def test_delete_does_not_touch_other_users(self, fake_redis):
        store.profile_update("kf1", "other", {"肤质": "油性"})
        store.delete_user_data("kf1", "eu3")
        assert store.profile_get("kf1", "other") == {"肤质": "油性"}
