"""微信客服消息处理 — 去重/失败回滚/隐私告知/人工接待 行为测试"""

import time

from src import core
from src.ai import store
from src.core import process_kf_messages, process_message
from src.wecom.types import TextMessage

KFID = "wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ"


def _text_msg(msgid: str, content: str = "你好", euid: str = "u1") -> dict:
    return {
        "msgid": msgid,
        "msgtype": "text",
        "origin": 3,
        "external_userid": euid,
        "send_time": int(time.time()),
        "text": {"content": content},
    }


def _patch_sync(monkeypatch, msgs: list) -> None:
    monkeypatch.setattr(
        "src.wecom.wxkf.sync_messages",
        lambda **kw: {"msg_list": msgs, "next_cursor": "", "has_more": 0},
    )


def _patch_common(monkeypatch, sent: list, state: int = 1) -> None:
    """公共 mock：发送记录、会话状态、已接受隐私声明"""
    monkeypatch.setattr(
        "src.core._do_send_kf",
        lambda kfid, euid, content, msgid="": sent.append(content) or True,
    )
    monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: state)
    monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: True)


class TestDedupBeforeRateLimit:
    def test_replay_does_not_resend_ratelimit_prompt(self, monkeypatch):
        """去重先于限流：重放消息不再重复发限流提示"""
        sent = []
        _patch_common(monkeypatch, sent)
        monkeypatch.setattr("src.core.check_rate_limit", lambda uid, t=0: True)
        _patch_sync(monkeypatch, [_text_msg("m-rl-1")])

        process_kf_messages("t1", KFID)
        process_kf_messages("t2", KFID)

        assert sent == ["您的消息发送过于频繁，请稍后再试。"]


class TestFailureRelease:
    def test_send_failure_release_allows_retry(self, monkeypatch):
        """发送失败回滚 msgid，企微重推后可重试成功"""
        sent = []
        flag = {"fail": True}

        def fake_send(kfid, euid, content, msgid=""):
            if flag["fail"]:
                return False
            sent.append(content)
            return True

        monkeypatch.setattr("src.core._do_send_kf", fake_send)
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: True)
        monkeypatch.setattr("src.core.chat", lambda messages: "这是AI回复")
        _patch_sync(monkeypatch, [_text_msg("m-fail-1", euid="u-fail")])

        process_kf_messages("t1", KFID)  # 发送失败 → release
        assert sent == []

        flag["fail"] = False
        process_kf_messages("t2", KFID)  # 重推重试成功
        assert sent == ["这是AI回复"]

    def test_ai_failure_release_allows_retry(self, monkeypatch):
        """AI 异常回滚 msgid 并转人工兜底；重推后 AI 恢复可正常回复"""
        sent = []
        calls = {"n": 0}

        def fake_chat(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                raise core.AIServiceError("boom")
            return "AI已恢复"

        _patch_common(monkeypatch, sent)
        monkeypatch.setattr("src.core.chat", fake_chat)
        monkeypatch.setattr("src.core._handle_kf_transfer", lambda *a, **k: None)
        _patch_sync(monkeypatch, [_text_msg("m-ai-fail-1", euid="u-aifail")])

        process_kf_messages("t1", KFID)
        process_kf_messages("t2", KFID)

        assert calls["n"] == 2
        assert "AI已恢复" in sent


class TestHumanSessionMedia:
    def test_state3_media_no_bot_reply(self, monkeypatch):
        """人工接待中（state=3）媒体消息：机器人不回复"""
        sent = []
        _patch_common(monkeypatch, sent, state=3)
        _patch_sync(monkeypatch, [{
            "msgid": "m-media-st3",
            "msgtype": "image",
            "origin": 3,
            "external_userid": "u-media3",
            "send_time": int(time.time()),
        }])

        process_kf_messages("t1", KFID)
        assert sent == []

    def test_state1_media_sends_prompt(self, monkeypatch):
        """非人工接待时媒体消息仍发一次引导"""
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        _patch_sync(monkeypatch, [{
            "msgid": "m-media-st1",
            "msgtype": "image",
            "origin": 3,
            "external_userid": "u-media1",
            "send_time": int(time.time()),
        }])

        process_kf_messages("t1", KFID)
        assert len(sent) == 1
        assert "暂不支持图片/语音" in sent[0]


class TestPrivacyNotice:
    def test_first_session_sends_privacy_notice(self, monkeypatch):
        """首次会话先发隐私声明文本并标记，再继续正常处理当条消息"""
        sent = []
        accepted = []
        monkeypatch.setattr(
            "src.core._do_send_kf",
            lambda kfid, euid, content, msgid="": sent.append(content) or True,
        )
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: False)
        monkeypatch.setattr("src.core.store.privacy_notice_accept", lambda k, u: accepted.append(u))
        monkeypatch.setattr("src.core.chat", lambda messages: "AI回复")
        _patch_sync(monkeypatch, [_text_msg("m-privacy-1", euid="u-privacy")])

        process_kf_messages("t1", KFID)

        assert sent[0] == core.PRIVACY_NOTICE_TEXT
        assert sent[1] == "AI回复"
        assert accepted == ["u-privacy"]


class TestProcessMessage:
    def test_ai_failure_not_written_to_history(self, monkeypatch):
        """AI 失败兜底文案不写入历史；回滚认领允许重推后重试"""
        def boom(messages):
            raise core.AIServiceError("boom")

        monkeypatch.setattr("src.core.chat", boom)
        msg = TextMessage(from_user="wx-u-fail", content="你好", msg_id="wxmsg-fail-1")

        reply = process_message(msg)

        assert reply == "AI服务暂时不可用，请稍后再试。"
        assert store.session_get("wx-u-fail") == []
        # 认领已回滚，重推可再次处理
        assert store.msgid_claim("wx:wxmsg-fail-1") is True

    def test_duplicate_msgid_skipped(self, monkeypatch):
        """微信 5s 超时重推：同 MsgId 不重复调 AI、不重复写历史"""
        calls = {"n": 0}

        def fake_chat(messages):
            calls["n"] += 1
            return "你好！"

        monkeypatch.setattr("src.core.chat", fake_chat)
        msg = TextMessage(from_user="wx-u-dup", content="你好", msg_id="wxmsg-dup-1")

        assert process_message(msg) == "你好！"
        assert process_message(msg) is None
        assert calls["n"] == 1
        assert len(store.session_get("wx-u-dup")) == 2
