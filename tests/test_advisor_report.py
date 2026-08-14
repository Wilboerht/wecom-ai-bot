"""测肤报告联动（enter_session → 档案写入 → 客服链接）测试"""

import pytest
from httpx import ASGITransport, AsyncClient

from src import advisor_report
from src.config import config
from src.main import app


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


FAKE_REPORT = {
    "found": True,
    "sessionId": "sess-1234567890",
    "nickname": "小雨",
    "gender": "female",
    "skinTypeLabel": "混干性肌肤",
    "skinAge": 28,
    "overallScore": 82,
    "percentile": 87,
    "issues": [
        {"label": "光泽度", "score": 58},
        {"label": "水油平衡", "score": 65},
    ],
}


class TestBuildProfileFields:
    def test_full_report(self):
        fields = advisor_report.build_profile_fields(FAKE_REPORT)
        assert fields["肤质"] == "混干性肌肤"
        assert fields["性别"] == "女"
        assert fields["肌肤年龄"] == "28岁"
        assert fields["素颜评分"] == "82分（超越全国87%的用户）"
        assert fields["重点问题"] == "光泽度（58分）、水油平衡（65分）"
        assert "报告ID" not in fields

    def test_partial_report(self):
        fields = advisor_report.build_profile_fields(
            {"found": True, "skinTypeLabel": "油性肌肤"}
        )
        assert fields == {"肤质": "油性肌肤"}

    def test_empty_report(self):
        assert advisor_report.build_profile_fields({"found": True}) == {}


class TestBuildWelcomeText:
    def test_welcome_text(self):
        text = advisor_report.build_welcome_text(FAKE_REPORT)
        assert "测肤报告" in text
        assert "混干性肌肤" in text
        assert "82分" in text


class TestHandleEnterSession:
    def test_saves_profile_and_skips_welcome(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            "src.advisor_report.store.profile_update",
            lambda open_kfid, user, fields: saved.update({(open_kfid, user): fields}),
        )
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_REPORT)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        monkeypatch.setattr(config, "KF_SEND_WELCOME", False)

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "sess-1234567890", "welcome_code": "wcode"},
        )

        assert ("kfc7834894b7ee2b86a", "wm_user_1") in saved
        assert saved[("kfc7834894b7ee2b86a", "wm_user_1")]["肤质"] == "混干性肌肤"

    def test_sends_welcome_when_code_present(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(
            "src.advisor_report.store.profile_update", lambda a, b, c: None
        )
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_REPORT)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        monkeypatch.setattr(config, "KF_SEND_WELCOME", True)

        def fake_send(corpid, secret, code, content):
            sent["code"] = code
            sent["content"] = content
            return True

        monkeypatch.setattr("src.wecom.wxkf.send_msg_on_event", fake_send)

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "sess-1234567890", "welcome_code": "wcode-1"},
        )

        assert sent["code"] == "wcode-1"
        assert "混干性肌肤" in sent["content"]

    def test_invalid_scene_param_ignored(self, monkeypatch):
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_REPORT)
        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "bad!param", "welcome_code": "wcode"},
        )
        # _fetch_report 不应被调用（上面的 lambda 若被调用会返回 FAKE_REPORT 但无断言点，
        # 此处通过 spy 验证）
        calls = []
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: calls.append(sid))
        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "bad!param", "welcome_code": "wcode"},
        )
        assert calls == []

    def test_report_not_found_does_not_save(self, monkeypatch):
        saved = []
        monkeypatch.setattr(
            "src.advisor_report.store.profile_update", lambda a, b, c: saved.append(1)
        )
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: None)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "sess-1234567890", "welcome_code": "wcode"},
        )
        assert saved == []


class TestKfLinkEndpoint:
    @pytest.mark.asyncio
    async def test_requires_internal_key(self, client, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "secret-key-123")
        monkeypatch.setattr(config, "KF_ADVISOR_OPEN_KFID", "kfc7834894b7ee2b86a")

        resp = await client.get("/api/kf-link", params={"session_id": "sess-1234567890"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_session_id(self, client, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "secret-key-123")
        monkeypatch.setattr(config, "KF_ADVISOR_OPEN_KFID", "kfc7834894b7ee2b86a")

        resp = await client.get(
            "/api/kf-link",
            params={"session_id": "bad!"},
            headers={"x-internal-key": "secret-key-123"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_returns_link_with_scene_param(self, client, monkeypatch):
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "secret-key-123")
        monkeypatch.setattr(config, "KF_ADVISOR_OPEN_KFID", "kfc7834894b7ee2b86a")
        monkeypatch.setattr(config, "KF_SCENE", "advisor")
        monkeypatch.setattr(
            "src.main._get_advisor_link",
            lambda open_kfid, scene: "https://work.weixin.qq.com/kf/kfc78?enc_scene=ENC123",
        )

        resp = await client.get(
            "/api/kf-link",
            params={"session_id": "sess-1234567890"},
            headers={"x-internal-key": "secret-key-123"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["url"] == (
            "https://work.weixin.qq.com/kf/kfc78?enc_scene=ENC123&scene_param=sess-1234567890"
        )
