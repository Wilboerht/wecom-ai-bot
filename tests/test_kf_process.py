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

    def test_ai_failure_sends_busy_notice_once(self, monkeypatch):
        """AI 异常：不主动转人工，发一次繁忙提示并保留认领——重推去重跳过不重复打扰，
        AI 恢复后客户再发新消息可正常回复"""
        sent = []
        calls = {"n": 0}

        def fake_chat(messages):
            calls["n"] += 1
            if calls["n"] == 1:
                raise core.AIServiceError("boom")
            return "AI已恢复"

        _patch_common(monkeypatch, sent)
        monkeypatch.setattr("src.core.chat", fake_chat)
        transfer_calls = []
        monkeypatch.setattr(
            "src.core._handle_kf_transfer",
            lambda *a, **k: transfer_calls.append(1) or True,
        )
        _patch_sync(monkeypatch, [_text_msg("m-ai-fail-1", euid="u-aifail")])

        process_kf_messages("t1", KFID)  # AI 失败 → 繁忙提示
        assert sent == ["护肤顾问暂时繁忙，这条消息还没能回复您，请稍后再发一次。"]
        assert transfer_calls == []  # 不主动转人工

        process_kf_messages("t2", KFID)  # 企微重推同一 msgid：已认领去重，不重复发提示
        assert len(sent) == 1
        assert calls["n"] == 1

        # AI 恢复后客户再发新消息：正常回复
        _patch_sync(monkeypatch, [_text_msg("m-ai-fail-2", euid="u-aifail")])
        process_kf_messages("t3", KFID)
        assert sent[-1] == "AI已恢复"


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
        """首次会话：先发账号欢迎语（去重标记），再发隐私声明，然后正常处理当条消息"""
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

        assert sent[0] == "您好，欢迎来到 NIHPLOD 旎柏，很高兴为您服务。请问有什么可以帮到您？如需人工客服，请回复「转人工」。"
        assert sent[1] == core.PRIVACY_NOTICE_TEXT
        assert sent[2] == "AI回复"
        assert accepted == ["u-privacy"]
        store.kv_delete(f"welcome-sent:{KFID}:u-privacy")

    def test_welcome_not_resent_when_report_welcome_already_sent(self, monkeypatch):
        """已通过测肤报告收到个性化欢迎语（welcome-sent 标记存在）：不再发通用欢迎语，只发隐私声明"""
        sent = []
        monkeypatch.setattr(
            "src.core._do_send_kf",
            lambda kfid, euid, content, msgid="": sent.append(content) or True,
        )
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: False)
        monkeypatch.setattr("src.core.store.privacy_notice_accept", lambda k, u: None)
        monkeypatch.setattr("src.core.chat", lambda messages: "AI回复")
        store.kv_set(f"welcome-sent:{KFID}:u-privacy-dup", "1", 600)
        _patch_sync(monkeypatch, [_text_msg("m-privacy-2", euid="u-privacy-dup")])
        try:
            process_kf_messages("t1", KFID)
            assert sent == [core.PRIVACY_NOTICE_TEXT, "AI回复"]
        finally:
            store.kv_delete(f"welcome-sent:{KFID}:u-privacy-dup")


class TestProcessMessage:
    def test_ai_failure_not_written_to_history(self, monkeypatch):
        """AI 失败兜底文案不写入历史；回滚认领允许重推后重试"""
        def boom(messages, **kw):
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

        def fake_chat(messages, **kw):
            calls["n"] += 1
            return "你好！"

        monkeypatch.setattr("src.core.chat", fake_chat)
        msg = TextMessage(from_user="wx-u-dup", content="你好", msg_id="wxmsg-dup-1")

        assert process_message(msg) == "你好！"
        assert process_message(msg) is None
        assert calls["n"] == 1
        assert len(store.session_get("wx-u-dup")) == 2

    def test_wx_ai_budget_args(self, monkeypatch):
        """公众号路径调 AI 带 5s 内预算：短 timeout + 不重试，避免慢响应静默丢回复"""
        captured = {}

        def fake_chat(messages, timeout=28.0, max_retries=2):
            captured["timeout"] = timeout
            captured["max_retries"] = max_retries
            return "你好！"

        monkeypatch.setattr("src.core.chat", fake_chat)
        msg = TextMessage(from_user="wx-u-budget", content="你好", msg_id="wxmsg-budget-1")

        assert process_message(msg) == "你好！"
        assert captured["timeout"] < 5
        assert captured["max_retries"] == 0

    def test_wx_budget_timeout_fallback_and_metric(self, monkeypatch):
        """AI 超出预算（timeout 类失败）：返回兜底文案、不写历史、计 wx_budget_timeout"""
        from src import metrics

        before = metrics.snapshot()

        def boom(messages, timeout=28.0, max_retries=2):
            raise core.AIServiceError("timeout")

        monkeypatch.setattr("src.core.chat", boom)
        msg = TextMessage(from_user="wx-u-bto", content="你好", msg_id="wxmsg-bto-1")

        assert process_message(msg) == "AI服务暂时不可用，请稍后再试。"
        assert store.session_get("wx-u-bto") == []
        assert metrics.snapshot().get(metrics.WX_BUDGET_TIMEOUT, 0) \
            - before.get(metrics.WX_BUDGET_TIMEOUT, 0) == 1


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


class TestOrderFlowAccountGate:
    """订单查询流程按账号门控：仅 order_flow=true 的账号（品牌客服）进入流程；
    护肤顾问账号的订单类问题交给 AI（由其 prompt 引导至品牌人工客服）"""

    SK_KFID = "kfc7834894b7ee2b86a"

    def test_advisor_account_skips_order_flow(self, monkeypatch):
        sent = []
        calls = {"n": 0}

        def fake_chat(messages):
            calls["n"] += 1
            return "售后问题麻烦您联系品牌人工客服处理哦~"

        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.chat", fake_chat)
        _patch_sync(monkeypatch, [_text_msg("m-of-gate-1", content="查订单", euid="u-of-gate")])

        process_kf_messages("t1", self.SK_KFID)

        # 未进入订单流程：不问购买渠道，直接走 AI
        assert not any("哪个平台购买" in s for s in sent)
        assert calls["n"] == 1
        assert sent == ["售后问题麻烦您联系品牌人工客服处理哦~"]
        assert store.kv_get(f"orderflow:{self.SK_KFID}:u-of-gate") == ""

    def test_brand_account_enters_order_flow(self, monkeypatch):
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.chat", lambda messages: "AI回复")
        _patch_sync(monkeypatch, [_text_msg("m-of-gate-2", content="查订单", euid="u-of-gate-b")])

        process_kf_messages("t1", KFID)

        assert any("哪个平台购买" in s for s in sent)
        assert store.kv_get(f"orderflow:{KFID}:u-of-gate-b")


class TestNativeLengthSplit:
    """微信原生约束：企微客服 send_msg 文本上限 2048 字节，超长自动拆条发送"""

    def test_short_content_single_message(self, monkeypatch):
        from src.core import _do_send_kf

        calls = []
        monkeypatch.setattr(
            "src.wecom.wxkf.send_message",
            lambda **kw: calls.append(kw) or True,
        )
        assert _do_send_kf(KFID, "u1", "短回复", msgid="m-1") is True
        assert len(calls) == 1
        assert calls[0]["content"] == "短回复" and calls[0]["msgid"] == "m-1"

    def test_long_content_split_with_msgid_first_only(self, monkeypatch):
        from src.core import _do_send_kf

        calls = []
        monkeypatch.setattr(
            "src.wecom.wxkf.send_message",
            lambda **kw: calls.append(kw) or True,
        )
        # 3000 中文字 ≈ 9000 字节，远超 2000 字节阈值
        long_reply = "长" * 3000
        assert _do_send_kf(KFID, "u1", long_reply, msgid="m-2") is True

        assert len(calls) > 1
        # 每条都不超原生上限（预留 buffer 后 2000 字节）
        assert all(len(c["content"].encode("utf-8")) <= 2000 for c in calls)
        # 内容不丢：拼回等于原文
        assert "".join(c["content"] for c in calls) == long_reply
        # msgid 只挂第一条，后续不带引用
        assert calls[0]["msgid"] == "m-2"
        assert all(c["msgid"] == "" for c in calls[1:])

    def test_split_prefers_line_boundary(self):
        from src.core import _split_kf_content

        content = "\n".join([f"第{i}行内容" for i in range(200)])  # 多行超长文本
        parts = _split_kf_content(content)
        assert len(parts) > 1
        assert all(len(p.encode("utf-8")) <= 2000 for p in parts)
        # 按行拆：拼回后与原文一致（含换行）
        assert "\n".join(parts) == content

    def test_single_huge_line_hard_cut(self):
        from src.core import _split_kf_content

        content = "字" * 5000  # 单行 15000 字节，无换行可拆
        parts = _split_kf_content(content)
        assert len(parts) > 1
        assert all(len(p.encode("utf-8")) <= 2000 for p in parts)
        assert "".join(parts) == content

    def test_split_failure_returns_false(self, monkeypatch):
        """任一分片发送失败即返回 False，由调用方 msgid_release 回滚"""
        from src.core import _do_send_kf

        n = {"i": 0}

        def fake_send(**kw):
            n["i"] += 1
            return n["i"] == 1  # 第二条失败

        monkeypatch.setattr("src.wecom.wxkf.send_message", fake_send)
        assert _do_send_kf(KFID, "u1", "长" * 3000, msgid="m-3") is False


class TestMetricsInstrumentation:
    """业务指标埋点：接待/限流/AI成败/转人工/orderflow 端到端计数"""

    @staticmethod
    def _delta(name: str, before: dict) -> int:
        from src import metrics

        return metrics.snapshot().get(name, 0) - before.get(name, 0)

    def test_normal_reply_counts_received_and_ok(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        sent = []
        _patch_common(monkeypatch, sent)
        monkeypatch.setattr("src.core.chat", lambda messages: "AI回复")
        _patch_sync(monkeypatch, [_text_msg("m-mt-ok", euid="u-mt-ok")])

        process_kf_messages("t1", KFID)

        assert sent == ["AI回复"]
        assert self._delta(metrics.KF_MSG_RECEIVED, before) == 1
        assert self._delta(metrics.AI_REPLY_OK, before) == 1
        assert self._delta(metrics.AI_FAILURE, before) == 0

    def test_ratelimit_hit_counted(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        sent = []
        _patch_common(monkeypatch, sent)
        monkeypatch.setattr("src.core.check_rate_limit", lambda uid, t=0: True)
        _patch_sync(monkeypatch, [_text_msg("m-mt-rl", euid="u-mt-rl")])

        process_kf_messages("t1", KFID)

        assert self._delta(metrics.RATELIMIT_HIT, before) == 1
        # 限流消息不调 AI
        assert self._delta(metrics.AI_REPLY_OK, before) == 0

    def test_transfer_requested_and_success_counted(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.is_within_service_hours", lambda: True)
        monkeypatch.setattr("src.wecom.wxkf.transfer_session", lambda **kw: True)
        monkeypatch.setattr("src.wecom.wxkf.send_agent_notice", lambda **kw: True)
        _patch_sync(monkeypatch, [_text_msg("m-mt-tr", content="转人工", euid="u-mt-tr")])

        process_kf_messages("t1", KFID)

        assert self._delta(metrics.TRANSFER_REQUESTED, before) == 1
        assert self._delta(metrics.TRANSFER_SUCCESS, before) == 1
        assert self._delta(metrics.TRANSFER_FAILED, before) == 0

    def test_transfer_outside_hours_counted_failed(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.is_within_service_hours", lambda: False)
        _patch_sync(monkeypatch, [_text_msg("m-mt-tr2", content="转人工", euid="u-mt-tr2")])

        process_kf_messages("t1", KFID)

        assert self._delta(metrics.TRANSFER_REQUESTED, before) == 1
        assert self._delta(metrics.TRANSFER_FAILED, before) == 1
        assert self._delta(metrics.TRANSFER_SUCCESS, before) == 0

    def test_ai_failure_counted(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        sent = []
        _patch_common(monkeypatch, sent)

        def boom(messages):
            raise core.AIServiceError("boom")

        monkeypatch.setattr("src.core.chat", boom)
        monkeypatch.setattr("src.core._handle_kf_transfer", lambda *a, **k: False)
        _patch_sync(monkeypatch, [_text_msg("m-mt-af", euid="u-mt-af")])

        process_kf_messages("t1", KFID)

        assert self._delta(metrics.AI_FAILURE, before) == 1
        assert self._delta(metrics.AI_REPLY_OK, before) == 0

    def test_orderflow_started_counted(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        sent = []
        _patch_common(monkeypatch, sent, state=1)
        monkeypatch.setattr("src.core.chat", lambda messages: "AI回复")
        _patch_sync(monkeypatch, [_text_msg("m-mt-of", content="查订单", euid="u-mt-of")])

        process_kf_messages("t1", KFID)  # 品牌账号 order_flow: true → 进入流程

        assert self._delta(metrics.ORDERFLOW_STARTED, before) == 1

    def test_wx_path_counts_received_and_ok(self, monkeypatch):
        from src import metrics

        before = metrics.snapshot()
        monkeypatch.setattr("src.core.chat", lambda messages, **kw: "你好！")
        msg = TextMessage(from_user="wx-u-mt", content="你好", msg_id="wxmsg-mt-1")

        assert process_message(msg) == "你好！"

        assert self._delta(metrics.WX_MSG_RECEIVED, before) == 1
        assert self._delta(metrics.AI_REPLY_OK, before) == 1
