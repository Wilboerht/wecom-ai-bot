"""AI 模块测试（Mock 模式，不调用真实 API）"""

import pytest
from unittest.mock import MagicMock, patch


class TestDeepSeekChat:
    def test_chat_success(self):
        with patch("src.ai.deepseek._get_client") as mock_get_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "你好！有什么可以帮你的？"}}]
            }
            mock_resp.raise_for_status.return_value = None
            mock_get_client.return_value.post.return_value = mock_resp

            from src.ai.deepseek import chat

            messages = [
                {"role": "system", "content": "你是一个客服"},
                {"role": "user", "content": "你好"},
            ]
            reply = chat(messages)
            assert "你好" in reply

    def test_chat_http_401(self):
        from httpx import HTTPStatusError, Response
        from src.ai.deepseek import chat, AIServiceError

        with patch("src.ai.deepseek._get_client") as mock_get_client:
            mock_resp = Response(status_code=401)
            error = HTTPStatusError("Unauthorized", request=None, response=mock_resp)  # type: ignore[arg-type]
            mock_get_client.return_value.post.side_effect = error

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}])
            # 4xx（非 429）不重试
            assert mock_get_client.return_value.post.call_count == 1

    def test_chat_http_429(self):
        from httpx import HTTPStatusError, Response
        from src.ai.deepseek import chat, AIServiceError

        with patch("src.ai.deepseek._get_client") as mock_get_client, \
                patch("src.ai.deepseek.time.sleep"):
            mock_resp = Response(status_code=429)
            error = HTTPStatusError("Rate limited", request=None, response=mock_resp)  # type: ignore[arg-type]
            mock_get_client.return_value.post.side_effect = error

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}])
            # 429 重试 2 次后耗尽：共 3 次调用
            assert mock_get_client.return_value.post.call_count == 3

    def test_chat_timeout(self):
        from httpx import TimeoutException
        from src.ai.deepseek import chat, AIServiceError

        with patch("src.ai.deepseek._get_client") as mock_get_client, \
                patch("src.ai.deepseek.time.sleep"):
            mock_get_client.return_value.post.side_effect = (
                TimeoutException("timeout")
            )

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}])
            # 超时重试 2 次后耗尽：共 3 次调用
            assert mock_get_client.return_value.post.call_count == 3

    def test_chat_429_retry_then_success(self):
        """429 重试后成功：第一次 429，第二次返回正常内容"""
        from httpx import HTTPStatusError, Response
        from src.ai.deepseek import chat

        with patch("src.ai.deepseek._get_client") as mock_get_client, \
                patch("src.ai.deepseek.time.sleep"):
            err_resp = Response(status_code=429)
            error = HTTPStatusError("Rate limited", request=None, response=err_resp)  # type: ignore[arg-type]
            ok_resp = MagicMock()
            ok_resp.status_code = 200
            ok_resp.json.return_value = {
                "choices": [{"message": {"content": "重试后的回复"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
            ok_resp.raise_for_status.return_value = None
            mock_get_client.return_value.post.side_effect = [error, ok_resp]

            reply = chat([{"role": "user", "content": "test"}])
            assert reply == "重试后的回复"
            assert mock_get_client.return_value.post.call_count == 2

    def test_chat_max_retries_zero_no_retry(self):
        """公众号 5s 预算场景：max_retries=0 时超时/5xx 只调一次不重试"""
        from httpx import TimeoutException
        from src.ai.deepseek import chat, AIServiceError

        with patch("src.ai.deepseek._get_client") as mock_get_client:
            mock_get_client.return_value.post.side_effect = (
                TimeoutException("timeout")
            )

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}], timeout=4.5, max_retries=0)
            assert mock_get_client.return_value.post.call_count == 1
            # timeout 参数透传给 httpx
            assert mock_get_client.return_value.post.call_args.kwargs["timeout"] == 4.5

    def test_chat_payload_uses_config_temperature(self):
        """payload 的 temperature 取自 config（.env 可配，默认 0.3），不再硬编码"""
        from src.ai.deepseek import chat
        from src.config import config

        with patch("src.ai.deepseek._get_client") as mock_get_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {},
            }
            mock_resp.raise_for_status.return_value = None
            mock_get_client.return_value.post.return_value = mock_resp

            chat([{"role": "user", "content": "hi"}])
            payload = mock_get_client.return_value.post.call_args.kwargs["json"]
            assert payload["temperature"] == config.DEEPSEEK_TEMPERATURE
            assert payload["max_tokens"] == config.DEEPSEEK_MAX_TOKENS


class TestSessionManager:
    def test_get_context_empty(self):
        from src.ai.store import session_get
        import uuid
        ctx = session_get("test_" + uuid.uuid4().hex[:8])
        assert ctx == []

    def test_add_and_get_context(self):
        from src.ai.store import session_add, session_get
        import uuid
        uid = "test_" + uuid.uuid4().hex[:8]
        session_add(uid, "user", "msg1")
        session_add(uid, "assistant", "reply1")
        ctx = session_get(uid)
        assert len(ctx) == 2

    def test_context_isolation(self):
        from src.ai.store import session_add, session_get
        import uuid
        u1 = "test_" + uuid.uuid4().hex[:8]
        u2 = "test_" + uuid.uuid4().hex[:8]
        session_add(u1, "user", "u1")
        session_add(u2, "user", "u2")
        assert len(session_get(u1)) == 1
        assert len(session_get(u2)) == 1

    def test_context_trim(self):
        from src.ai.store import session_add, session_get
        import uuid
        uid = "test_" + uuid.uuid4().hex[:8]
        for i in range(30):
            session_add(uid, "user", f"m{i}", max_msgs=20)
        ctx = session_get(uid)
        assert len(ctx) <= 20

    # 注：旧 SessionManager.reset() 已随 session.py 删除（commit 4b1726e），
    # store.py 无 reset 等价接口，原 test_reset 只是重复 add/get 假断言，已移除。

    def test_ttl_expiry(self):
        """内存模式 TTL 过期：把会话最后活跃时间回拨到 SESSION_TTL 之前，
        session_get 触发惰性清理后应返回空"""
        import time
        import uuid
        from src.ai import store
        from src.config import config

        uid = "test_" + uuid.uuid4().hex[:8]
        store.session_add(uid, "user", "test")
        assert len(store.session_get(uid)) == 1

        store._inmem_session_ttl[uid] = time.time() - config.SESSION_TTL - 1
        assert store.session_get(uid) == []
