"""广告法禁语输出兜底 — 替换 / 长词优先 / 序数词保护 / 链路接入"""

import time

from src.compliance import sanitize_reply
from src.core import process_kf_messages


class TestSanitizeReply:
    def test_banned_word_replaced(self):
        text, hits = sanitize_reply("这款面霜可以美白祛斑")
        assert "美白" not in text and "祛斑" not in text
        assert text == "这款面霜可以提亮提亮"
        assert hits == ["祛斑", "美白"] or set(hits) == {"祛斑", "美白"}

    def test_no_hit_returns_original(self):
        text = "这款面霜适合干性肌肤"
        result, hits = sanitize_reply(text)
        assert result is text and hits == []

    def test_empty_text(self):
        assert sanitize_reply("") == ("", [])

    def test_long_word_first(self):
        """「当天见效」整体替换，不被「见效快」先切碎"""
        text, hits = sanitize_reply("当天见效")
        assert text == "坚持使用有助于看到变化"
        assert "见效快" not in hits

    def test_efficacy_claims_replaced(self):
        text, _ = sanitize_reply("根治痘痘，永不反弹，无效退款")
        assert "根治" not in text and "永不反弹" not in text and "无效退款" not in text

    def test_ordinal_diyi_preserved(self):
        """「第一步/第一次」等序数用法不误伤"""
        text, hits = sanitize_reply("第一步先洁面，第一次使用建议少量")
        assert "第一步" in text and "第一次" in text
        assert "第一" not in hits

    def test_claim_diyi_replaced(self):
        text, hits = sanitize_reply("销量第一的品牌")
        assert "第一" not in text and "前列" in text

    def test_medical_terms_replaced(self):
        text, hits = sanitize_reply("消炎杀菌，治疗敏感肌")
        assert "消炎" not in text and "杀菌" not in text and "治疗" not in text

    def test_diyi_time_preserved(self):
        """「第一时间」是惯常用法（尤其医疗红线「请第一时间就医」），不替换"""
        text, hits = sanitize_reply("情况严重请第一时间就医")
        assert "第一时间" in text
        assert "第一" not in hits

    def test_juedui_negation_preserved(self):
        """「绝对不要/绝对不能」等告诫语气不替换，避免病句与语义削弱"""
        text, hits = sanitize_reply("红肿痘痘绝对不要挤，也绝对不能熬夜")
        assert "绝对不要" in text and "绝对不能" in text
        assert "绝对" not in hits

    def test_juedui_claim_replaced(self):
        text, hits = sanitize_reply("这款产品绝对有效")
        assert "绝对" not in text and "非常有效" in text

    def test_zhiliao_medical_advice_preserved(self):
        """医嘱语境（就医/医院/就诊/接受治疗）的「治疗」不替换"""
        for text in ("建议及时就医治疗", "去医院治疗更稳妥", "建议就诊治疗", "接受治疗后观察"):
            result, hits = sanitize_reply(text)
            assert "治疗" in result, text
            assert "治疗" not in hits, text

    def test_zhiyu_objective_statement_preserved(self):
        """「无法治愈」「难以治愈」是客观陈述，不替换"""
        text, hits = sanitize_reply("医生说这种情况无法治愈，只能控制")
        assert "无法治愈" in text
        assert "治愈" not in hits


class TestKfPipelineIntegration:
    """kf 链路：AI 回复发出前必须经过禁语清洗"""

    def test_ai_reply_sanitized_before_send(self, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [{
                    "msgid": "m-banned-1",
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": "u-banned",
                    "send_time": int(time.time()),
                    "text": {"content": "这个能美白吗"},
                }],
                "next_cursor": "",
                "has_more": 0,
            },
        )
        monkeypatch.setattr(
            "src.core._do_send_kf",
            lambda kfid, euid, content, msgid="": sent.append(content) or True,
        )
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: True)
        monkeypatch.setattr("src.core.chat", lambda messages: "可以的，美白效果很好")

        process_kf_messages("t1", "wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ")

        assert sent == ["可以的，提亮效果很好"]

    def test_sanitized_reply_stored_in_history(self, monkeypatch):
        """写入会话历史的是清洗后的文本，避免禁语污染后续上下文"""
        from src.ai import store

        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [{
                    "msgid": "m-banned-2",
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": "u-banned-h",
                    "send_time": int(time.time()),
                    "text": {"content": "效果怎么样"},
                }],
                "next_cursor": "",
                "has_more": 0,
            },
        )
        monkeypatch.setattr(
            "src.core._do_send_kf",
            lambda kfid, euid, content, msgid="": True,
        )
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: True)
        monkeypatch.setattr("src.core.chat", lambda messages: "百分百好用")

        process_kf_messages("t1", "wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ")

        history = store.session_get("wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ:u-banned-h")
        assistant_msgs = [m["content"] for m in history if m["role"] == "assistant"]
        assert assistant_msgs == ["很大程度好用"]
