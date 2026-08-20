"""FastAPI 端点测试"""

import pytest
from httpx import ASGITransport, AsyncClient
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestShortLink:
    @pytest.mark.asyncio
    async def test_redirect_and_click_log(self, client, monkeypatch, caplog):
        """命中短链 302 跳转并记 info 点击日志（含 code 与 UA，不含 IP）"""
        import logging

        monkeypatch.setattr("src.links._links", {"abc": "https://example.com/buy"})
        with caplog.at_level(logging.INFO, logger="src.main"):
            resp = await client.get("/l/abc", headers={"User-Agent": "test-agent"})
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://example.com/buy"
        click_logs = [r.message for r in caplog.records if "短链点击" in r.message]
        assert any("abc" in m and "test-agent" in m for m in click_logs)

    @pytest.mark.asyncio
    async def test_not_found(self, client, monkeypatch):
        monkeypatch.setattr("src.links._links", {"abc": "https://example.com/buy"})
        resp = await client.get("/l/unknown")
        assert resp.status_code == 404


class TestStartupWarning:
    def test_warn_when_redis_not_configured(self, monkeypatch, caplog):
        """未配置 REDIS_URL 启动时告警：内存模式仅适用单 worker"""
        import importlib
        import logging

        import src.main

        monkeypatch.setattr("src.main.config.REDIS_URL", "")
        with caplog.at_level(logging.WARNING, logger="src.main"):
            importlib.reload(src.main)
        assert any(
            "REDIS_URL" in r.message and "单 worker" in r.message
            for r in caplog.records
        )

    def test_no_warn_when_redis_configured(self, monkeypatch, caplog):
        import importlib
        import logging

        import src.main

        monkeypatch.setattr("src.main.config.REDIS_URL", "redis://localhost:6379/0")
        with caplog.at_level(logging.WARNING, logger="src.main"):
            importlib.reload(src.main)
        assert not any("单 worker" in r.message for r in caplog.records)


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestCallbackVerify:
    @pytest.mark.asyncio
    async def test_verify_url_success(self, client):
        """测试 URL 验证 - 使用真实的加密参数"""
        from src.main import crypto_wecom

        plaintext = "test_verify_12345"
        result = crypto_wecom.encrypt_message(plaintext)

        resp = await client.get(
            "/callback",
            params={
                "msg_signature": result["msgsignature"],
                "timestamp": result["timestamp"],
                "nonce": result["nonce"],
                "echostr": result["encrypt"],
            },
        )
        assert resp.status_code == 200
        assert resp.text == plaintext

    @pytest.mark.asyncio
    async def test_verify_url_bad_signature(self, client):
        resp = await client.get(
            "/callback",
            params={
                "msg_signature": "bad_signature",
                "timestamp": "123",
                "nonce": "abc",
                "echostr": "xxxx",
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_verify_url_missing_params(self, client):
        resp = await client.get("/callback")
        assert resp.status_code == 422


class TestCallbackMessage:
    def _build_encrypted_post_body(self, plain_xml: str) -> tuple[dict, dict]:
        from src.main import crypto_wecom

        result = crypto_wecom.encrypt_message(plain_xml)
        body = (
            "<xml>"
            f"<ToUserName><![CDATA[{crypto_wecom.corp_id}]]></ToUserName>"
            "<AgentID><![CDATA[1000004]]></AgentID>"
            f"<Encrypt><![CDATA[{result['encrypt']}]]></Encrypt>"
            "</xml>"
        )
        params = {
            "msg_signature": result["msgsignature"],
            "timestamp": result["timestamp"],
            "nonce": result["nonce"],
        }
        return body, params

    @pytest.mark.asyncio
    async def test_text_message(self, client):
        plain_xml = (
            "<xml>"
            "<ToUserName><![CDATA[ww1234567890test]]></ToUserName>"
            "<FromUserName><![CDATA[test_user]]></FromUserName>"
            "<CreateTime>1408091189</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[你好]]></Content>"
            "<MsgId>123</MsgId>"
            "<AgentID>1000001</AgentID>"
            "</xml>"
        )

        body, params = self._build_encrypted_post_body(plain_xml)

        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DEEPSEEK_API_KEY", "sk-test-key")

            from unittest.mock import AsyncMock, patch

            with patch("src.core.chat") as mock_chat:
                mock_chat.return_value = "你好！有什么可以帮你的？"

                resp = await client.post(
                    "/callback",
                    params=params,
                    content=body,
                    headers={"Content-Type": "text/xml"},
                )

        assert resp.status_code == 200
        # 响应应包含加密的 XML
        assert "<Encrypt>" in resp.text
        assert "<MsgSignature>" in resp.text

    @pytest.mark.asyncio
    async def test_event_enter_agent(self, client):
        plain_xml = (
            "<xml>"
            "<ToUserName><![CDATA[ww1234567890test]]></ToUserName>"
            "<FromUserName><![CDATA[test_user]]></FromUserName>"
            "<CreateTime>1408091200</CreateTime>"
            "<MsgType><![CDATA[event]]></MsgType>"
            "<Event><![CDATA[enter_agent]]></Event>"
            "<EventKey><![CDATA[]]></EventKey>"
            "<AgentID>1000001</AgentID>"
            "</xml>"
        )

        body, params = self._build_encrypted_post_body(plain_xml)

        resp = await client.post(
            "/callback",
            params=params,
            content=body,
            headers={"Content-Type": "text/xml"},
        )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_empty_text(self, client):
        plain_xml = (
            "<xml>"
            "<ToUserName><![CDATA[ww1234567890test]]></ToUserName>"
            "<FromUserName><![CDATA[test_user]]></FromUserName>"
            "<CreateTime>1408091189</CreateTime>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[   ]]></Content>"
            "<MsgId>123</MsgId>"
            "<AgentID>1000001</AgentID>"
            "</xml>"
        )

        body, params = self._build_encrypted_post_body(plain_xml)

        resp = await client.post(
            "/callback",
            params=params,
            content=body,
            headers={"Content-Type": "text/xml"},
        )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_image_message(self, client):
        plain_xml = (
            "<xml>"
            "<ToUserName><![CDATA[ww1234567890test]]></ToUserName>"
            "<FromUserName><![CDATA[test_user]]></FromUserName>"
            "<CreateTime>1408091300</CreateTime>"
            "<MsgType><![CDATA[image]]></MsgType>"
            "<PicUrl><![CDATA[http://example.com/pic.jpg]]></PicUrl>"
            "<MediaId><![CDATA[media_id]]></MediaId>"
            "<MsgId>124</MsgId>"
            "<AgentID>1000001</AgentID>"
            "</xml>"
        )

        body, params = self._build_encrypted_post_body(plain_xml)

        resp = await client.post(
            "/callback",
            params=params,
            content=body,
            headers={"Content-Type": "text/xml"},
        )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_xml_body(self, client):
        resp = await client.post(
            "/callback",
            params={"msg_signature": "x", "timestamp": "1", "nonce": "y"},
            content="not valid xml",
            headers={"Content-Type": "text/xml"},
        )
        assert resp.status_code == 200
