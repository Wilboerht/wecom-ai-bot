"""微信客服消息处理 — enter_session 事件分支测试"""

import time

from src.core import process_kf_messages

KFID_ADVISOR = "kfc7834894b7ee2b86a"
KFID_BRAND = "wkBpRSVwAA_pArJA-t5WEOLTlS1PB1FQ"
ENTER_SESSION_MSG = {
    "msgid": "evt-msg-1",
    "msgtype": "event",
    "origin": 4,
    "event": {
        "event_type": "enter_session",
        "external_userid": "wm_user_1",
        "scene_param": "sess-1234567890",
        "welcome_code": "wcode-1",
    },
}


class TestEnterSessionEvent:
    @staticmethod
    def _local_dedup(monkeypatch):
        """用本地集合替代 Redis 去重，避免跨测试运行的残留键影响结果"""
        seen = set()

        def fake_claim(msg_id):
            if msg_id in seen:
                return False
            seen.add(msg_id)
            return True

        monkeypatch.setattr("src.core.store.msgid_claim", fake_claim)
        monkeypatch.setattr("src.core.store.msgid_release", lambda msg_id: seen.discard(msg_id))

    def test_processed_once_due_to_msgid_dedup(self, monkeypatch):
        """sync_msg 每次从头拉取，同一 enter_session 事件只应处理一次"""
        self._local_dedup(monkeypatch)
        handled = []
        monkeypatch.setattr(
            "src.core._handle_enter_session_event",
            lambda kfid, event: handled.append(event) or True,
        )
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [dict(ENTER_SESSION_MSG)],
                "next_cursor": "",
                "has_more": 0,
            },
        )

        process_kf_messages("token-1", KFID_ADVISOR)
        process_kf_messages("token-2", KFID_ADVISOR)

        assert len(handled) == 1
        assert handled[0]["scene_param"] == "sess-1234567890"

    def test_other_event_types_ignored(self, monkeypatch):
        handled = []
        monkeypatch.setattr(
            "src.core._handle_enter_session_event",
            lambda kfid, event: handled.append(event),
        )
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [
                    {
                        "msgid": "evt-msg-2",
                        "msgtype": "event",
                        "origin": 4,
                        "event": {
                            "event_type": "servicer_status_change",
                            "servicer_userid": "s1",
                            "status": 1,
                        },
                    }
                ],
                "next_cursor": "",
                "has_more": 0,
            },
        )

        process_kf_messages("token-1", KFID_ADVISOR)
        assert handled == []

    def test_new_event_msgid_processed(self, monkeypatch):
        """不同 msgid 的进入会话事件应再次处理（用户重新通过链接进入）"""
        self._local_dedup(monkeypatch)
        handled = []
        monkeypatch.setattr(
            "src.core._handle_enter_session_event",
            lambda kfid, event: handled.append(event) or True,
        )

        def fake_sync(**kw):
            return {
                "msg_list": [
                    {**ENTER_SESSION_MSG, "msgid": "evt-msg-new", "event": {**ENTER_SESSION_MSG["event"], "scene_param": "sess-9999999999"}}
                ],
                "next_cursor": "",
                "has_more": 0,
            }

        monkeypatch.setattr("src.wecom.wxkf.sync_messages", fake_sync)

        process_kf_messages("token-1", KFID_ADVISOR)
        assert len(handled) == 1
        assert handled[0]["scene_param"] == "sess-9999999999"

    def test_failure_releases_msgid_allows_retry(self, monkeypatch):
        """处理失败（返回 False）回滚 msgid 认领，企微重推后可重试成功"""
        self._local_dedup(monkeypatch)
        results = iter([False, True])
        handled = []
        monkeypatch.setattr(
            "src.core._handle_enter_session_event",
            lambda kfid, event: handled.append(event) or next(results),
        )
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [dict(ENTER_SESSION_MSG)],
                "next_cursor": "",
                "has_more": 0,
            },
        )

        process_kf_messages("token-1", KFID_ADVISOR)  # 失败 → release
        process_kf_messages("token-2", KFID_ADVISOR)  # 重推重试成功

        assert len(handled) == 2

    def test_success_does_not_release(self, monkeypatch):
        """处理成功（返回 True）不 release，重推被去重跳过"""
        self._local_dedup(monkeypatch)
        handled = []
        monkeypatch.setattr(
            "src.core._handle_enter_session_event",
            lambda kfid, event: handled.append(event) or True,
        )
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [dict(ENTER_SESSION_MSG)],
                "next_cursor": "",
                "has_more": 0,
            },
        )

        process_kf_messages("token-1", KFID_ADVISOR)
        process_kf_messages("token-2", KFID_ADVISOR)

        assert len(handled) == 1


class TestHumanSessionNotInterrupted:
    def test_state3_does_not_send_or_end(self, monkeypatch):
        """人工接待中（state=3）客户消息：机器人不回复、不结束会话"""
        TestEnterSessionEvent._local_dedup(monkeypatch)
        sent = []
        ended = []
        monkeypatch.setattr(
            "src.core._do_send_kf",
            lambda kfid, euid, content, msgid="": sent.append(content),
        )
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [
                    {
                        "msgid": "m-state3-1",
                        "msgtype": "text",
                        "origin": 3,
                        "external_userid": "u1",
                        "send_time": int(time.time()),
                        "text": {"content": "11111"},
                    }
                ],
                "next_cursor": "",
                "has_more": 0,
            },
        )
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 3)
        monkeypatch.setattr(
            "src.wecom.wxkf.transfer_session",
            lambda **kw: ended.append(kw) or True,
        )

        process_kf_messages("token-1", KFID_BRAND)

        assert sent == []
        assert ended == []
