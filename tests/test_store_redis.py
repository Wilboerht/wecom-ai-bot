"""store.py Redis 真实路径测试（fakeredis 内存实现，不连真实 Redis）

conftest 的 autouse fixture 强制 _get_redis → None（其他测试依赖内存模式）；
本模块用自己的 fixture 在其之后重新注入 fakeredis 客户端，覆盖 Redis 分支。
"""

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


class TestRatelimit:
    def test_limited_after_threshold(self, fake_redis):
        assert store.ratelimit_check("ru1", max_per_window=2) is False
        assert store.ratelimit_check("ru1", max_per_window=2) is False
        assert store.ratelimit_check("ru1", max_per_window=2) is True

    def test_stale_send_time_not_counted(self, fake_redis):
        old = int(time.time()) - 3600
        assert store.ratelimit_check("ru2", send_time=old, max_per_window=1) is False
        assert store.ratelimit_check("ru2", max_per_window=1) is False


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
