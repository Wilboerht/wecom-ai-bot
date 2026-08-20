"""PII 导出/删除通道测试（内存模式；Redis 真实路径见 test_store_redis.py::TestPiiExportDeleteRedis）"""

import pytest
from httpx import ASGITransport, AsyncClient

from src.ai import store
from src.main import app

KFID = "wkTestKfid123"
EUID = "wmExternalUser456"
KEY = "test-internal-key"


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def internal_key(monkeypatch):
    monkeypatch.setattr("src.main.config.INTERNAL_API_KEY", KEY)
    return KEY


@pytest.fixture
def seeded_user():
    """预置该用户内存侧的各类数据（会话/kv/限流），结束后清理"""
    uid = f"{KFID}:{EUID}"
    store.session_add(uid, "user", "你好")
    store.session_add(uid, "assistant", "您好，请问有什么可以帮到您？")
    store.kv_set(f"orderflow:{KFID}:{EUID}", '{"step":"channel"}', 600)
    store.kv_set(f"kf:transferred:{KFID}:{EUID}", "1", 300)
    store.kv_set(f"welcome-fallback:{KFID}:{EUID}:scene123", "1", 600)
    store.ratelimit_check(EUID)
    yield
    store._inmem_sessions.pop(uid, None)
    store._inmem_session_ttl.pop(uid, None)
    store._inmem_ratelimit.pop(EUID, None)
    for k in (f"orderflow:{KFID}:{EUID}", f"kf:transferred:{KFID}:{EUID}",
              f"welcome-fallback:{KFID}:{EUID}:scene123"):
        store._inmem_kv.pop(k, None)


class TestAuth:
    @pytest.mark.asyncio
    async def test_no_key_rejected(self, client, internal_key):
        resp = await client.get("/api/pii/export", params={"open_kfid": KFID, "external_userid": EUID})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_key_rejected(self, client, internal_key):
        resp = await client.get("/api/pii/export", params={"open_kfid": KFID, "external_userid": EUID},
                                headers={"x-internal-key": "wrong"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_no_key_rejected(self, client, internal_key):
        resp = await client.post("/api/pii/delete", params={"open_kfid": KFID, "external_userid": EUID})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_key_not_configured_rejected(self, client, monkeypatch):
        """INTERNAL_API_KEY 未配置时一律拒绝（与 kf-link 一致）"""
        monkeypatch.setattr("src.main.config.INTERNAL_API_KEY", "")
        resp = await client.get("/api/pii/export", params={"open_kfid": KFID, "external_userid": EUID},
                                headers={"x-internal-key": KEY})
        assert resp.status_code == 401


class TestParamValidation:
    @pytest.mark.asyncio
    async def test_invalid_open_kfid(self, client, internal_key):
        resp = await client.get("/api/pii/export",
                                params={"open_kfid": "bad;drop", "external_userid": EUID},
                                headers={"x-internal-key": KEY})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_external_userid(self, client, internal_key):
        resp = await client.post("/api/pii/delete",
                                 params={"open_kfid": KFID, "external_userid": "a b"},
                                 headers={"x-internal-key": KEY})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_params(self, client, internal_key):
        resp = await client.get("/api/pii/export", headers={"x-internal-key": KEY})
        assert resp.status_code == 422


class TestExport:
    @pytest.mark.asyncio
    async def test_export_structure(self, client, internal_key, seeded_user):
        resp = await client.get("/api/pii/export",
                                params={"open_kfid": KFID, "external_userid": EUID},
                                headers={"x-internal-key": KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data["open_kfid"] == KFID
        assert data["external_userid"] == EUID
        assert [m["role"] for m in data["session"]] == ["user", "assistant"]
        assert data["kv"][f"orderflow:{KFID}:{EUID}"] == '{"step":"channel"}'
        assert data["kv"][f"kf:transferred:{KFID}:{EUID}"] == "1"
        assert data["kv"][f"welcome-fallback:{KFID}:{EUID}:scene123"] == "1"
        assert len(data["ratelimit"]) == 1
        # 内存模式下 profile/privacy/last_agent 只落 Redis，应为空值
        assert data["profile"] is None
        assert data["privacy_notice_accepted"] is False
        assert data["last_agent"] is None

    @pytest.mark.asyncio
    async def test_export_empty_user(self, client, internal_key):
        resp = await client.get("/api/pii/export",
                                params={"open_kfid": "wkNobody", "external_userid": "wmNobody"},
                                headers={"x-internal-key": KEY})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session"] is None
        assert data["kv"] is None
        assert data["ratelimit"] is None


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_clears_all(self, client, internal_key, seeded_user):
        resp = await client.post("/api/pii/delete",
                                 params={"open_kfid": KFID, "external_userid": EUID},
                                 headers={"x-internal-key": KEY})
        assert resp.status_code == 200
        deleted = resp.json()["deleted"]
        assert set(deleted) == {
            f"sess:{KFID}:{EUID}",
            f"rl:{EUID}",
            f"orderflow:{KFID}:{EUID}",
            f"kf:transferred:{KFID}:{EUID}",
            f"welcome-fallback:{KFID}:{EUID}:scene123",
        }
        # 二次导出应为空
        data = store.export_user_data(KFID, EUID)
        assert data["session"] is None
        assert data["kv"] is None
        assert data["ratelimit"] is None

    @pytest.mark.asyncio
    async def test_delete_unknown_user_empty_list(self, client, internal_key):
        resp = await client.post("/api/pii/delete",
                                 params={"open_kfid": "wkNobody", "external_userid": "wmNobody"},
                                 headers={"x-internal-key": KEY})
        assert resp.status_code == 200
        assert resp.json() == {"deleted": []}

    @pytest.mark.asyncio
    async def test_delete_via_get_not_allowed(self, client, internal_key):
        """破坏性操作不允许 GET"""
        resp = await client.get("/api/pii/delete",
                                params={"open_kfid": KFID, "external_userid": EUID},
                                headers={"x-internal-key": KEY})
        assert resp.status_code == 405
