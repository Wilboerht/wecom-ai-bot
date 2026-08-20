"""微信客服 API 模块测试（token 刷新 / 限频退避 / 网络重试，Mock httpx）"""

import httpx
import pytest
from src.wecom import wxkf
from src.wecom.wxkf import WecomApiError


CORPID = "ww_test_corp"
SECRET = "test_secret"
TOKEN_OK = {"errcode": 0, "access_token": "token_1", "expires_in": 7200}
TOKEN_OK_2 = {"errcode": 0, "access_token": "token_2", "expires_in": 7200}


class FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeClient:
    """按队列依次返回 get/post 响应；队列元素为 dict 或 Exception（直接抛出）"""

    def __init__(self, get_resps=None, post_resps=None):
        self.get_resps = list(get_resps or [])
        self.post_resps = list(post_resps or [])
        self.get_calls = 0
        self.post_calls = 0

    def _next(self, queue):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return FakeResp(item)

    def get(self, url, params=None):
        self.get_calls += 1
        return self._next(self.get_resps)

    def post(self, url, params=None, json=None):
        self.post_calls += 1
        return self._next(self.post_resps)


@pytest.fixture(autouse=True)
def _reset_token_cache(monkeypatch):
    """每个用例清空 token 缓存，并跳过真实 sleep 退避"""
    wxkf._access_tokens.clear()
    monkeypatch.setattr(wxkf.time, "sleep", lambda *a, **k: None)
    yield
    wxkf._access_tokens.clear()


class TestAccessToken:
    def test_cache_per_corpid(self, monkeypatch):
        """token 缓存按 corpid 分键，互不串用"""
        client = FakeClient(get_resps=[TOKEN_OK, TOKEN_OK_2])
        monkeypatch.setattr(wxkf, "_client", client)

        t1 = wxkf.get_access_token("corp_a", SECRET)
        t2 = wxkf.get_access_token("corp_b", SECRET)
        assert (t1, t2) == ("token_1", "token_2")
        assert client.get_calls == 2

        # 命中缓存，不再请求
        assert wxkf.get_access_token("corp_a", SECRET) == "token_1"
        assert wxkf.get_access_token("corp_b", SECRET) == "token_2"
        assert client.get_calls == 2

    def test_network_error_exhausted_raises(self, monkeypatch):
        """gettoken 网络异常重试耗尽后抛 WecomApiError"""
        err = httpx.ConnectError("conn refused")
        client = FakeClient(get_resps=[err, err, err])
        monkeypatch.setattr(wxkf, "_client", client)

        with pytest.raises(WecomApiError):
            wxkf.get_access_token(CORPID, SECRET)
        assert client.get_calls == 3  # 1 次原始 + 2 次重试

    def test_api_error_raises(self, monkeypatch):
        """gettoken 返回非 0 errcode 抛 WecomApiError（不再抛裸 RuntimeError）"""
        client = FakeClient(get_resps=[{"errcode": 40013, "errmsg": "invalid corpid"}])
        monkeypatch.setattr(wxkf, "_client", client)

        with pytest.raises(WecomApiError):
            wxkf.get_access_token(CORPID, SECRET)


class TestSyncMessages:
    def test_token_invalid_refresh_and_retry(self, monkeypatch):
        """errcode=40014 token 失效：强制刷新 token 并重试一次后成功"""
        client = FakeClient(
            get_resps=[TOKEN_OK, TOKEN_OK_2],
            post_resps=[
                {"errcode": 40014, "errmsg": "invalid access_token"},
                {"errcode": 0, "msg_list": [{"msgid": "m1"}], "next_cursor": "c1", "has_more": 0},
            ],
        )
        monkeypatch.setattr(wxkf, "_client", client)

        data = wxkf.sync_messages(CORPID, SECRET, token="event_token", open_kfid="kf1")
        assert data["msg_list"] == [{"msgid": "m1"}]
        assert client.get_calls == 2  # 初次取 token + 失效后强制刷新
        assert client.post_calls == 2
        # 重试时使用的是新 token
        assert wxkf._access_tokens[CORPID]["value"] == "token_2"

    def test_token_invalid_retry_exhausted_raises(self, monkeypatch):
        """刷新后仍 42001，抛 WecomApiError"""
        client = FakeClient(
            get_resps=[TOKEN_OK, TOKEN_OK_2],
            post_resps=[
                {"errcode": 42001, "errmsg": "expired"},
                {"errcode": 42001, "errmsg": "expired"},
            ],
        )
        monkeypatch.setattr(wxkf, "_client", client)

        with pytest.raises(WecomApiError):
            wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="kf1")
        assert client.post_calls == 2

    def test_rate_limit_backoff_retry(self, monkeypatch):
        """errcode=45009 限频：退避重试一次后成功"""
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[
                {"errcode": 45009, "errmsg": "api freq out of limit"},
                {"errcode": 0, "msg_list": [], "next_cursor": "", "has_more": 0},
            ],
        )
        monkeypatch.setattr(wxkf, "_client", client)

        data = wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="kf1")
        assert data["errcode"] == 0
        assert client.post_calls == 2

    def test_rate_limit_retry_exhausted_raises(self, monkeypatch):
        """限频重试后仍 45009，抛 WecomApiError"""
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[
                {"errcode": 45009, "errmsg": "limited"},
                {"errcode": 45009, "errmsg": "limited"},
            ],
        )
        monkeypatch.setattr(wxkf, "_client", client)

        with pytest.raises(WecomApiError):
            wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="kf1")

    def test_network_error_exhausted_raises(self, monkeypatch):
        """网络异常重试耗尽后抛 WecomApiError（不再静默返回空）"""
        err = httpx.ReadTimeout("timeout")
        client = FakeClient(get_resps=[TOKEN_OK], post_resps=[err, err, err])
        monkeypatch.setattr(wxkf, "_client", client)

        with pytest.raises(WecomApiError):
            wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="kf1")
        assert client.post_calls == 3  # 1 次原始 + 2 次重试

    def test_network_error_retry_success(self, monkeypatch):
        """网络异常后重试成功"""
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[
                httpx.ConnectError("conn refused"),
                {"errcode": 0, "msg_list": [], "next_cursor": "", "has_more": 0},
            ],
        )
        monkeypatch.setattr(wxkf, "_client", client)

        data = wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="kf1")
        assert data["errcode"] == 0

    def test_business_error_raises(self, monkeypatch):
        """非 token/限频类 errcode 直接抛 WecomApiError"""
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[{"errcode": 95000, "errmsg": "invalid open_kfid"}],
        )
        monkeypatch.setattr(wxkf, "_client", client)

        with pytest.raises(WecomApiError):
            wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="bad_kf")

    def test_default_limit_is_1000(self, monkeypatch):
        """sync_msg 默认 limit=1000"""
        captured = {}

        class CaptureClient(FakeClient):
            def post(self, url, params=None, json=None):
                captured.update(json)
                return FakeResp({"errcode": 0, "msg_list": [], "next_cursor": "", "has_more": 0})

        monkeypatch.setattr(wxkf, "_client", CaptureClient(get_resps=[TOKEN_OK]))
        wxkf.sync_messages(CORPID, SECRET, token="t", open_kfid="kf1")
        assert captured["limit"] == 1000


class TestGetSessionState:
    def test_success(self, monkeypatch):
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[{"errcode": 0, "service_state": 3}],
        )
        monkeypatch.setattr(wxkf, "_client", client)
        assert wxkf.get_session_state(CORPID, SECRET, "kf1", "user1") == 3

    def test_failure_raises(self, monkeypatch):
        """失败不再返回 -1，重试耗尽后抛 WecomApiError"""
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[{"errcode": 95002, "errmsg": "bad"}],
        )
        monkeypatch.setattr(wxkf, "_client", client)
        with pytest.raises(WecomApiError):
            wxkf.get_session_state(CORPID, SECRET, "kf1", "user1")


class TestSendMessage:
    def test_success(self, monkeypatch):
        client = FakeClient(get_resps=[TOKEN_OK], post_resps=[{"errcode": 0}])
        monkeypatch.setattr(wxkf, "_client", client)
        assert wxkf.send_message(CORPID, SECRET, "user1", "kf1", "text", "你好") is True

    def test_failure_returns_false(self, monkeypatch):
        """重试耗尽返回 False，不抛异常"""
        err = httpx.ConnectError("conn refused")
        client = FakeClient(get_resps=[TOKEN_OK], post_resps=[err, err, err])
        monkeypatch.setattr(wxkf, "_client", client)
        assert wxkf.send_message(CORPID, SECRET, "user1", "kf1", "text", "你好") is False

    def test_token_invalid_retry_success(self, monkeypatch):
        """token 失效刷新重试后成功"""
        client = FakeClient(
            get_resps=[TOKEN_OK, TOKEN_OK_2],
            post_resps=[{"errcode": 40014}, {"errcode": 0}],
        )
        monkeypatch.setattr(wxkf, "_client", client)
        assert wxkf.send_message(CORPID, SECRET, "user1", "kf1", "text", "你好") is True

    def test_non_text_msgtype_rejected(self):
        """仅支持 text，其他 msgtype 直接断言失败"""
        with pytest.raises(AssertionError):
            wxkf.send_message(CORPID, SECRET, "user1", "kf1", "image", "x")


class TestTransferSession:
    def test_failure_returns_false(self, monkeypatch):
        client = FakeClient(
            get_resps=[TOKEN_OK],
            post_resps=[{"errcode": 95000, "errmsg": "bad"}],
        )
        monkeypatch.setattr(wxkf, "_client", client)
        assert wxkf.transfer_session(CORPID, SECRET, "kf1", "user1") is False


class TestSendAgentNotice:
    def test_invalid_agent_id_returns_false(self):
        """agent_id 非数字：记 error 返回 False，不抛 ValueError"""
        assert wxkf.send_agent_notice(CORPID, SECRET, "abc", "servicer1", "通知") is False
