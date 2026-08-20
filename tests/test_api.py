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


class TestDoctypeRejection:
    @pytest.mark.asyncio
    async def test_doctype_body_rejected(self, client, caplog):
        """含 DOCTYPE 的 body 在解析前被拒：记 warning、返回 200 空（防十亿 laughs）"""
        import logging

        body = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE foo [<!ENTITY lol "lollollol">]>'
            "<xml><ToUserName><![CDATA[x]]></ToUserName>"
            "<Encrypt><![CDATA[&lol;]]></Encrypt></xml>"
        )
        with caplog.at_level(logging.WARNING, logger="src.main"):
            resp = await client.post(
                "/callback",
                params={"msg_signature": "x", "timestamp": "1", "nonce": "y"},
                content=body,
                headers={"Content-Type": "text/xml"},
            )
        assert resp.status_code == 200
        assert resp.text == ""
        assert any("DOCTYPE" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_doctype_case_insensitive(self, client):
        """DOCTYPE 检测大小写不敏感"""
        body = "<!doctype foo><xml><Encrypt><![CDATA[x]]></Encrypt></xml>"
        resp = await client.post(
            "/callback",
            params={"msg_signature": "x", "timestamp": "1", "nonce": "y"},
            content=body,
            headers={"Content-Type": "text/xml"},
        )
        assert resp.status_code == 200
        assert resp.text == ""


class TestShortLinkLogSanitize:
    @pytest.mark.asyncio
    async def test_code_with_newline_sanitized(self, client, monkeypatch, caplog):
        """code 含 %0A 解码后的换行时日志被清洗，不能伪造日志行"""
        import logging

        monkeypatch.setattr("src.links._links", {"abc": "https://example.com/buy"})
        with caplog.at_level(logging.INFO, logger="src.main"):
            resp = await client.get("/l/abc%0AFORGED:injected")
        assert resp.status_code == 404
        miss_logs = [r.message for r in caplog.records if "短链未命中" in r.message]
        assert miss_logs
        for m in miss_logs:
            # 非白名单字符（换行、冒号）被替换为 ?，无法伪造日志行
            assert m == "短链未命中: code=abc?FORGED?injected"


class TestHealthMiddleware:
    @pytest.mark.asyncio
    async def test_health_error_returns_500(self, caplog):
        """/health 异常返回 500 而非空 200，避免监控误判存活"""
        import logging
        from types import SimpleNamespace

        from src.main import catch_errors

        request = SimpleNamespace(url=SimpleNamespace(path="/health"))

        async def boom(_):
            raise RuntimeError("health broken")

        with caplog.at_level(logging.ERROR, logger="src.main"):
            resp = await catch_errors(request, boom)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_other_route_error_returns_200(self):
        """其余路由维持 200 兜底（回调防企微重试风暴是有意的）"""
        from types import SimpleNamespace

        from src.main import catch_errors

        request = SimpleNamespace(url=SimpleNamespace(path="/callback"))

        async def boom(_):
            raise RuntimeError("callback broken")

        resp = await catch_errors(request, boom)
        assert resp.status_code == 200
