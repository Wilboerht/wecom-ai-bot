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
        from src.ai.session import SessionManager

        sm = SessionManager()
        ctx = sm.get_context("user1")
        assert ctx == []

    def test_add_and_get_context(self):
        from src.ai.session import SessionManager

        sm = SessionManager()
        sm.add_message("user1", "user", "消息1")
        sm.add_message("user1", "assistant", "回复1")
        ctx = sm.get_context("user1")
        assert len(ctx) == 2
        assert ctx[0]["role"] == "user"
        assert ctx[0]["content"] == "消息1"

    def test_context_isolation(self):
        from src.ai.session import SessionManager

        sm = SessionManager()
        sm.add_message("user1", "user", "用户1的消息")
        sm.add_message("user2", "user", "用户2的消息")

        ctx1 = sm.get_context("user1")
        ctx2 = sm.get_context("user2")

        assert len(ctx1) == 1
        assert len(ctx2) == 1
        assert ctx1[0]["content"] != ctx2[0]["content"]

    def test_context_trim(self):
        from src.ai.session import SessionManager

        sm = SessionManager()
        for i in range(25):
            sm.add_message("user1", "user", f"msg{i}")
            sm.add_message("user1", "assistant", f"reply{i}")

        ctx = sm.get_context("user1")
        assert len(ctx) <= 20

    def test_reset(self):
        from src.ai.session import SessionManager

        sm = SessionManager()
        sm.add_message("user1", "user", "test")
        sm.reset("user1")
        assert sm.get_context("user1") == []

    def test_ttl_expiry(self, monkeypatch):
        import time as time_module
        from src.ai.session import SessionManager

        original_time = time_module.time

        sm = SessionManager()
        sm.add_message("user1", "user", "test")

        monkeypatch.setattr(time_module, "time", lambda: original_time() + 3600)
        ctx = sm.get_context("user1")
        assert ctx == []
