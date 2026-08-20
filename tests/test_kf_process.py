"""微信客服消息处理 — 去重/失败回滚/隐私告知/人工接待 行为测试"""

import json
import time

from src import core
from src.ai import store
from src.core import process_kf_messages, process_message
from src.wecom.types import TextMessage, EventMessage

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


class TestRateLimitOrder:
    """限流顺序：人工接待判断先于限流；转人工诉求豁免限流"""

    def test_state3_no_ratelimit_prompt(self, monkeypatch):
        """人工接待中（state=3）触发限流也不发限流提示（机器人不插嘴）"""
        sent = []
        _patch_common(monkeypatch, sent, state=3)
        monkeypatch.setattr("src.core.check_rate_limit", lambda uid, t=0: True)
        _patch_sync(monkeypatch, [_text_msg("m-rl-st3", euid="u-rl-st3")])

        process_kf_messages("t1", KFID)
        assert sent == []

    def test_ratelimited_user_can_transfer(self, monkeypatch):
        """被限流用户发「转人工」：豁免限流直接转，不被限流提示拦截"""
        sent, transfers = [], []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.check_rate_limit", lambda uid, t=0: True)
        monkeypatch.setattr(
            "src.core._handle_kf_transfer",
            lambda kfid, uid, candidates, ctx="": transfers.append(uid) or True,
        )
        _patch_sync(monkeypatch, [_text_msg("m-rl-tr", content="转人工", euid="u-rl-tr")])

        process_kf_messages("t1", KFID)
        assert transfers == ["u-rl-tr"]
        assert "您的消息发送过于频繁，请稍后再试。" not in sent


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


class TestTransferKeywords:
    """转人工关键词：精确「人工」触发，「人工智能」不误触发"""

    def _run(self, monkeypatch, text: str, euid: str):
        """跑一条文本消息，返回 (发送记录, 转人工记录, AI调用次数)"""
        sent, transfers, calls = [], [], {"n": 0}
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr(
            "src.core._handle_kf_transfer",
            lambda kfid, uid, candidates, ctx="": transfers.append(uid),
        )

        def fake_chat(messages):
            calls["n"] += 1
            return "AI回复"

        monkeypatch.setattr("src.core.chat", fake_chat)
        _patch_sync(monkeypatch, [_text_msg(f"m-kw-{euid}", content=text, euid=euid)])
        process_kf_messages("t1", KFID)
        return sent, transfers, calls["n"]

    def test_ai_keyword_does_not_trigger(self, monkeypatch):
        """「人工智能」「人工合成」等含「人工」的正常咨询不触发转人工"""
        sent, transfers, chat_calls = self._run(monkeypatch, "这款产品有人工合成成分吗，跟人工智能有关系吗", "u-kw-ai")
        assert transfers == []
        assert chat_calls == 1
        assert sent == ["AI回复"]

    def test_exact_rengong_triggers(self, monkeypatch):
        """消息整体就是「人工」时触发（与隐私声明文案一致）"""
        sent, transfers, chat_calls = self._run(monkeypatch, "人工", "u-kw-exact")
        assert transfers == ["u-kw-exact"]
        assert chat_calls == 0

    def test_zhuanrengong_triggers(self, monkeypatch):
        """「转人工」关键词子串触发"""
        sent, transfers, chat_calls = self._run(monkeypatch, "我要转人工", "u-kw-zhuan")
        assert transfers == ["u-kw-zhuan"]
        assert chat_calls == 0


class TestTransferredMarker:
    """转人工跨批次标记：state=3 生效前机器人不再回复"""

    def test_marker_hit_skips_bot_reply(self, monkeypatch):
        """预置 kf:transferred 标记后，state 仍为 1 的消息也跳过机器人回复"""
        store.kv_set(f"kf:transferred:{KFID}:u-marked", "1", 300)
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        calls = {"n": 0}

        def fake_chat(messages):
            calls["n"] += 1
            return "AI回复"

        monkeypatch.setattr("src.core.chat", fake_chat)
        _patch_sync(monkeypatch, [_text_msg("m-marked-1", euid="u-marked")])

        process_kf_messages("t1", KFID)
        assert sent == []
        assert calls["n"] == 0

    def test_transfer_success_writes_marker(self, monkeypatch):
        """转接成功后写入跨批次标记，下一批次同用户消息被跳过"""
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.is_within_service_hours", lambda: True)
        monkeypatch.setattr("src.wecom.wxkf.transfer_session", lambda **kw: True)
        monkeypatch.setattr("src.wecom.wxkf.send_agent_notice", lambda **kw: True)
        _patch_sync(monkeypatch, [_text_msg("m-tmark-1", content="转人工", euid="u-tmark")])

        process_kf_messages("t1", KFID)
        assert store.kv_get(f"kf:transferred:{KFID}:u-tmark") == "1"

        # 下一批次 state 仍为 1（转接生效有延迟）：机器人不回复
        calls = {"n": 0}
        monkeypatch.setattr(
            "src.core.chat",
            lambda messages: calls.__setitem__("n", calls["n"] + 1) or "AI回复",
        )
        _patch_sync(monkeypatch, [_text_msg("m-tmark-2", content="还在吗", euid="u-tmark")])
        process_kf_messages("t2", KFID)
        assert calls["n"] == 0


class TestEventMessageDedup:
    def test_enter_agent_replay_dedup(self):
        """公众号事件无 MsgId：同 create_time 的重推不重复发欢迎语"""
        msg = EventMessage(from_user="wx-ev-1", event="enter_agent", create_time="1700000000")
        assert process_message(msg) is not None
        assert process_message(msg) is None

    def test_different_create_time_still_replies(self):
        """不同 create_time 视为新事件，仍回复欢迎语"""
        first = EventMessage(from_user="wx-ev-2", event="enter_agent", create_time="1700000000")
        second = EventMessage(from_user="wx-ev-2", event="enter_agent", create_time="1700000060")
        assert process_message(first) is not None
        assert process_message(second) is not None


class TestServiceHour:
    def test_zero_start_hour_is_valid(self, monkeypatch):
        """SERVICE_START_HOUR=0 是合法值，不应被 or 兜底吞掉"""
        monkeypatch.setattr(core.config, "SERVICE_START_HOUR", 0)
        assert core._service_hour("SERVICE_START_HOUR", 10) == 0

    def test_invalid_value_falls_back(self, monkeypatch):
        """非法配置值落默认值"""
        monkeypatch.setattr(core.config, "SERVICE_START_HOUR", "abc")
        assert core._service_hour("SERVICE_START_HOUR", 10) == 10

    def test_missing_uses_default(self, monkeypatch):
        monkeypatch.delattr(core.config, "SERVICE_START_HOUR", raising=False)
        assert core._service_hour("SERVICE_START_HOUR", 10) == 10


class TestOrderFlowProcess:
    """订单查询引导流程：退出关键词/重试上限/渠道长度/转接失败上下文保留"""

    def _setup(self, monkeypatch, sent: list, transfer_ok: bool = True) -> list:
        """公共 mock；返回转人工调用记录（元素为并入的上下文文本）"""
        transfers = []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr(
            "src.core._handle_kf_transfer",
            lambda kfid, uid, candidates, ctx="": transfers.append(ctx) or transfer_ok,
        )
        return transfers

    def _flow_key(self, euid: str) -> str:
        return f"orderflow:{KFID}:{euid}"

    def test_exit_keyword_cancels_flow(self, monkeypatch):
        """流程中回复「算了」：删除流程状态并确认退出"""
        sent = []
        self._setup(monkeypatch, sent)
        euid = "u-of-exit"
        _patch_sync(monkeypatch, [_text_msg("m-of-e1", content="查订单", euid=euid)])
        process_kf_messages("t1", KFID)
        assert store.kv_get(self._flow_key(euid))

        _patch_sync(monkeypatch, [_text_msg("m-of-e2", content="算了", euid=euid)])
        process_kf_messages("t2", KFID)
        assert store.kv_get(self._flow_key(euid)) == ""
        assert any("已取消订单查询" in s for s in sent)

    def test_orderno_retry_limit_exits(self, monkeypatch):
        """订单号 3 次未识别：自动退出流程并提示可重试"""
        sent = []
        self._setup(monkeypatch, sent)
        euid = "u-of-retry"
        _patch_sync(monkeypatch, [
            _text_msg("m-of-r1", content="查订单", euid=euid),
            _text_msg("m-of-r2", content="小红书", euid=euid),
        ])
        process_kf_messages("t1", KFID)
        assert json.loads(store.kv_get(self._flow_key(euid)))["step"] == "orderno"

        for i in range(3):
            _patch_sync(monkeypatch, [_text_msg(f"m-of-rb{i}", content="不知道", euid=euid)])
            process_kf_messages(f"tb{i}", KFID)

        assert store.kv_get(self._flow_key(euid)) == ""
        assert any("重新开始" in s for s in sent)

    def test_channel_too_long_rejected(self, monkeypatch):
        """渠道输入超长（>20 字符）不采信，按无效输入处理、流程不推进"""
        sent = []
        self._setup(monkeypatch, sent)
        euid = "u-of-long"
        _patch_sync(monkeypatch, [
            _text_msg("m-of-l1", content="查订单", euid=euid),
            _text_msg("m-of-l2", content="我在一个特别特别长的平台名字上面买的这个东西啊", euid=euid),
        ])
        process_kf_messages("t1", KFID)

        assert json.loads(store.kv_get(self._flow_key(euid)))["step"] == "channel"
        assert any("没看懂您说的购买渠道" in s for s in sent)

    def test_transfer_failure_keeps_context(self, monkeypatch):
        """收集齐后转人工失败：订单流程上下文保留，稍后可重试"""
        sent = []
        transfers = self._setup(monkeypatch, sent, transfer_ok=False)
        euid = "u-of-keep"
        _patch_sync(monkeypatch, [
            _text_msg("m-of-k1", content="查订单", euid=euid),
            _text_msg("m-of-k2", content="小红书", euid=euid),
            _text_msg("m-of-k3", content="订单号1234567890", euid=euid),
        ])
        process_kf_messages("t1", KFID)

        assert len(transfers) == 1
        assert "1234567890" in transfers[0]
        assert store.kv_get(self._flow_key(euid))

    def test_transfer_success_clears_context(self, monkeypatch):
        """收集齐后转人工成功：订单流程上下文清理"""
        sent = []
        transfers = self._setup(monkeypatch, sent, transfer_ok=True)
        euid = "u-of-clear"
        _patch_sync(monkeypatch, [
            _text_msg("m-of-c1", content="查订单", euid=euid),
            _text_msg("m-of-c2", content="小红书", euid=euid),
            _text_msg("m-of-c3", content="订单号1234567890", euid=euid),
        ])
        process_kf_messages("t1", KFID)

        assert len(transfers) == 1
        assert store.kv_get(self._flow_key(euid)) == ""

    def test_keyword_transfer_failure_keeps_flow_context(self, monkeypatch):
        """流程中直接发「转人工」且转接失败：流程上下文同样保留"""
        sent = []
        transfers = self._setup(monkeypatch, sent, transfer_ok=False)
        euid = "u-of-kw"
        _patch_sync(monkeypatch, [
            _text_msg("m-of-w1", content="查订单", euid=euid),
            _text_msg("m-of-w2", content="小红书", euid=euid),
            _text_msg("m-of-w3", content="转人工", euid=euid),
        ])
        process_kf_messages("t1", KFID)

        assert len(transfers) == 1
        assert "购买渠道：小红书" in transfers[0]
        assert store.kv_get(self._flow_key(euid))
