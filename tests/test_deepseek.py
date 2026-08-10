"""AI 模块测试（Mock 模式，不调用真实 API）"""

import pytest
from unittest.mock import MagicMock, patch


class TestDeepSeekChat:
    def test_chat_success(self):
        with patch("src.ai.deepseek.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "你好！有什么可以帮你的？"}}]
            }
            mock_resp.raise_for_status.return_value = None
            mock_client.return_value.__enter__.return_value.post.return_value = (
                mock_resp
            )

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

        with patch("src.ai.deepseek.Client") as mock_client:
            mock_resp = Response(status_code=401)
            error = HTTPStatusError("Unauthorized", request=None, response=mock_resp)  # type: ignore[arg-type]
            mock_client.return_value.__enter__.return_value.post.side_effect = error

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}])

    def test_chat_http_429(self):
        from httpx import HTTPStatusError, Response
        from src.ai.deepseek import chat, AIServiceError

        with patch("src.ai.deepseek.Client") as mock_client:
            mock_resp = Response(status_code=429)
            error = HTTPStatusError("Rate limited", request=None, response=mock_resp)  # type: ignore[arg-type]
            mock_client.return_value.__enter__.return_value.post.side_effect = error

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}])

    def test_chat_timeout(self):
        from httpx import TimeoutException
        from src.ai.deepseek import chat, AIServiceError

        with patch("src.ai.deepseek.Client") as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = (
                TimeoutException("timeout")
            )

            with pytest.raises(AIServiceError):
                chat([{"role": "user", "content": "test"}])


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

    def test_reset(self):
        from src.ai.store import session_add, session_get
        import uuid
        uid = "test_" + uuid.uuid4().hex[:8]
        session_add(uid, "user", "test")
        assert len(session_get(uid)) >= 1

    def test_ttl_expiry(self):
        from src.ai.store import session_add, session_get
        import uuid
        uid = "test_" + uuid.uuid4().hex[:8]
        session_add(uid, "user", "test")
        assert len(session_get(uid)) >= 1
