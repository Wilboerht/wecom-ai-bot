import pytest
from src.wecom.crypto import WXBizMsgCrypt


CORP_ID = "ww1234567890test"
TOKEN = "TestToken123"
AES_KEY = "abcDefGhijKlmnOpqRstUvwXyz12345678901234567"


@pytest.fixture(autouse=True)
def _force_memory_store(monkeypatch):
    """测试一律走内存存储，不连真实 Redis——避免测试数据污染本地 Redis
    （如 welcome-fallback 去重标记 TTL 7 天，残留会导致后续测试被误判为已发送）"""
    monkeypatch.setattr("src.ai.store._get_redis", lambda: None)


@pytest.fixture
def crypto():
    return WXBizMsgCrypt(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=CORP_ID)
