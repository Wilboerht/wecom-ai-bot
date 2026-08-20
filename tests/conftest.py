import os

import pytest

# 在任何 src 模块 import 之前注入测试用占位配置：
# src/config.py 在 import 时即校验环境变量（缺失或占位值直接 SystemExit），
# 而 .env 不进仓库 —— 不注入的话新克隆/CI 环境跑 pytest 会直接退出。
# 注意：_require 会拦截「请替换」/「your_」前缀的占位值，AES key 必须恰好 43 位。
CORP_ID = "ww1234567890test"
TOKEN = "TestToken123"
AES_KEY = "abcDefGhijKlmnOpqRstUvwXyz12345678901234567"

os.environ.setdefault("WECOM_CORP_ID", CORP_ID)
os.environ.setdefault("WECOM_SECRET", "test_secret_1234567890")
os.environ.setdefault("WECOM_TOKEN", TOKEN)
os.environ.setdefault("WECOM_ENCODING_AES_KEY", AES_KEY)
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-1234567890")

from src.wecom.crypto import WXBizMsgCrypt


@pytest.fixture(autouse=True)
def _force_memory_store(monkeypatch):
    """测试一律走内存存储，不连真实 Redis——避免测试数据污染本地 Redis
    （如 welcome-fallback 去重标记 TTL 7 天，残留会导致后续测试被误判为已发送）。
    Redis 真实路径的覆盖见 test_store_redis.py（fakeredis）。"""
    monkeypatch.setattr("src.ai.store._get_redis", lambda: None)


@pytest.fixture
def crypto():
    return WXBizMsgCrypt(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=CORP_ID)
