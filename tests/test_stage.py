"""阶段目标管理单测：日期字段校验、档案注入块（阶段状态块）、复评到期检测"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src import core, metrics
from src.ai import store


def _future_date(days: int) -> str:
    return (datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=days)).isoformat()


class TestStageDateValidation:
    def test_valid_dates_kept(self):
        fields = {"阶段开始": "2025-06-01", "随访时间": "2025-06-15", "肤质": "干性"}
        core._validate_stage_fields(fields)
        assert fields == {"阶段开始": "2025-06-01", "随访时间": "2025-06-15", "肤质": "干性"}

    @pytest.mark.parametrize("bad", ["两周后", "2025/01/01", "2025-13-99", "20250101", ""])
    def test_invalid_date_dropped_and_counted(self, bad):
        """非法日期剔除并计数，同批其他字段（含阶段名）不受影响"""
        metrics.snapshot(reset=True)
        fields = {"随访时间": bad, "当前阶段": "屏障修护期（阶段1/3）"}
        core._validate_stage_fields(fields)
        assert "随访时间" not in fields
        assert fields["当前阶段"] == "屏障修护期（阶段1/3）"
        assert metrics.snapshot()[metrics.STAGE_FIELD_INVALID] == 1

    def test_save_profile_drops_invalid_stage_date(self, monkeypatch):
        """端到端：AI 回复标签里的非法日期不进档案，合法字段正常写入
        （阶段名无 N/3 编号时代码不重算日期，模型写的日期走格式校验）"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        reply = core._save_profile_from_reply(
            "wk", "u1",
            "已记录。[档案: 当前阶段=屏障修护期, 阶段开始=两周前, 随访时间=2025-06-15]")
        assert saved["当前阶段"] == "屏障修护期"
        assert saved["随访时间"] == "2025-06-15"
        assert "阶段开始" not in saved  # 「两周前」非法已剔除
        assert "档案" not in reply

    def test_stage_dates_computed_by_code(self, monkeypatch):
        """写「当前阶段=…（阶段N/3）」时随访时间/阶段开始由代码计算，覆盖模型手算值"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        monkeypatch.setattr(store, "profile_get", lambda k, u: {})
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        core._save_profile_from_reply(
            "wk", "u1",
            "方案已出。[档案: 当前阶段=屏障修护期（阶段1/3）, 上次方案=精简修护, 随访时间=2030-01-01]")
        assert saved["随访时间"] == (today + timedelta(days=14)).isoformat()
        assert saved["阶段开始"] == today.isoformat()

    def test_stage_dates_follow_stage_number(self, monkeypatch):
        """进阶期（阶段2/3）随访 +28 天；阶段名变化时阶段开始重置为今天"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        monkeypatch.setattr(
            store, "profile_get",
            lambda k, u: {"当前阶段": "屏障修护期（阶段1/3）", "阶段开始": "2025-01-01"},
        )
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        core._save_profile_from_reply(
            "wk", "u1", "晋级。[档案: 当前阶段=提亮进阶期（阶段2/3）]")
        assert saved["随访时间"] == (today + timedelta(days=28)).isoformat()
        assert saved["阶段开始"] == today.isoformat()

    def test_stage_hold_keeps_original_start_date(self, monkeypatch):
        """留级（阶段名不变）：随访时间按新一轮重算，但保留原阶段开始日期"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        monkeypatch.setattr(
            store, "profile_get",
            lambda k, u: {"当前阶段": "屏障修护期（阶段1/3）", "阶段开始": "2025-01-01"},
        )
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        core._save_profile_from_reply(
            "wk", "u1", "再走一轮。[档案: 当前阶段=屏障修护期（阶段1/3）, 阶段开始=2026-01-01]")
        assert saved["随访时间"] == (today + timedelta(days=14)).isoformat()
        assert "阶段开始" not in saved  # 模型重写的开始日期被丢弃，档案保留 2025-01-01

    def test_comma_in_value_truncation_warned(self, monkeypatch, caplog):
        """字段值含中文逗号会被拆断（标签按逗号拆字段）：截断行为存在且留告警痕迹可观测"""
        import logging
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        with caplog.at_level(logging.WARNING, logger="src.core"):
            core._save_profile_from_reply(
                "wk", "u1", "已记录。[档案: 晋级条件=连续一周无泛红，可进入提亮进阶]")
        assert saved.get("晋级条件") == "连续一周无泛红"  # 逗号后内容被截断丢失
        assert any("逗号截断" in r.getMessage() for r in caplog.records)


class TestBuildProfileBlock:
    def test_empty_profile_returns_empty(self):
        assert core.build_profile_block(None) == ""
        assert core.build_profile_block({}) == ""

    def test_report_key_with_parentheses_injected(self):
        """报告字段「医美经历(近3月)」带括号，应通过 key 白名单正常注入（安全相关信息不能丢）"""
        text = core.build_profile_block({"肤质": "干性", "医美经历(近3月)": "光子激光"})
        assert "医美经历(近3月): 光子激光" in text

    def test_basic_fields_without_stage(self):
        """无阶段字段：只有基础档案块，无状态块与复评标记"""
        text = core.build_profile_block({"肤质": "干性"})
        assert "【该用户的护肤档案】" in text
        assert "肤质: 干性" in text
        assert "【当前阶段状态】" not in text
        assert "阶段复评日" not in text

    def test_stage_block_rendered(self):
        profile = {
            "当前阶段": "屏障修护期（阶段1/3）",
            "阶段目标": "两颊泛红消退",
            "阶段开始": "2025-06-01",
            "晋级条件": "连续一周无泛红",
            "随访时间": _future_date(10),
        }
        text = core.build_profile_block(profile)
        assert "【当前阶段状态】" in text
        assert "当前阶段=屏障修护期（阶段1/3）" in text
        assert "晋级条件=连续一周无泛红" in text
        assert "阶段复评日" not in text  # 随访未到期
        # 阶段字段只在状态块渲染一次，基础档案块不重复注入（省 token）
        assert text.count("当前阶段=") == 1

    def test_review_due_marker_when_followup_past(self):
        """随访时间已到期：注入确定性复评指令（代码算日期，不靠模型比较）"""
        text = core.build_profile_block({"上次方案": "打底修护", "随访时间": "2025-01-01"})
        assert "今天是阶段复评日" in text

    def test_review_due_marker_today(self):
        text = core.build_profile_block({"随访时间": _future_date(0)})
        assert "今天是阶段复评日" in text

    def test_no_marker_when_followup_future(self):
        text = core.build_profile_block({"随访时间": _future_date(14)})
        assert "阶段复评日" not in text

    def test_invalid_followup_date_no_marker_no_crash(self):
        text = core.build_profile_block({"随访时间": "两周后"})
        assert "阶段复评日" not in text

    def test_due_marker_without_stage_fields(self):
        """老客档案只有上次方案+随访时间（无当前阶段）时，复评标记同样注入——老客迁移触发入口"""
        text = core.build_profile_block({"上次方案": "先修护屏障两周", "随访时间": "2025-01-01"})
        assert "【当前阶段状态】" not in text
        assert "今天是阶段复评日" in text


class TestReviewDueDedup:
    """复评指令每个随访周期只注入一次：客户「不想谈复评」时模型不写新随访时间，
    不去重会导致之后每条消息都被强制复评裁决，与 prompt「督促一次不再追问」冲突"""

    def test_marker_not_reinjected_after_touch(self, monkeypatch):
        """触达标记存在时不再注入；新随访时间（新周期）则重新触发"""
        monkeypatch.setattr(
            store, "kv_get",
            lambda k: "1" if k == "review-due:kf1:u1:2025-01-01" else "",
        )
        profile = {"上次方案": "打底修护", "随访时间": "2025-01-01"}
        assert "阶段复评日" not in core.build_profile_block(profile, "kf1:u1")
        # 模型写了新随访时间 → 新周期的 key 无标记 → 到期后正常注入
        profile["随访时间"] = _future_date(0)
        assert "今天是阶段复评日" in core.build_profile_block(profile, "kf1:u1")

    def test_marked_after_successful_send(self, monkeypatch):
        """端到端：复评指令随回复成功发出后落标记；同周期下一轮不再注入"""
        import time

        sent = []
        kfid, euid = "kfc7834894b7ee2b86a", "u-review-dedup"
        profile = {"上次方案": "打底修护", "随访时间": "2025-01-01"}
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [{
                    "msgid": "m-review-1",
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": euid,
                    "send_time": int(time.time()),
                    "text": {"content": "你好"},
                }],
                "next_cursor": "",
                "has_more": 0,
            },
        )
        monkeypatch.setattr(
            "src.core._do_send_kf",
            lambda k, u, content, msgid="": sent.append(content) or True,
        )
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: True)
        monkeypatch.setattr("src.core.store.profile_get", lambda k, u: dict(profile))
        monkeypatch.setattr("src.core.store.profile_update", lambda *a, **k: None)
        monkeypatch.setattr("src.core.chat", lambda messages: "好的，先做复评。")

        key = f"review-due:{kfid}:{euid}:2025-01-01"
        try:
            core.process_kf_messages("t1", kfid)
            assert sent and store.kv_get(key) == "1"
            # 标记落库后，同周期不再注入复评指令
            assert "阶段复评日" not in core.build_profile_block(profile, f"{kfid}:{euid}")
        finally:
            store.kv_delete(key)

    def test_not_marked_when_send_fails(self, monkeypatch):
        """发送失败不落标记（msgid 回滚允许重推），本周期复评仍会触发"""
        import time

        kfid, euid = "kfc7834894b7ee2b86a", "u-review-fail"
        monkeypatch.setattr(
            "src.wecom.wxkf.sync_messages",
            lambda **kw: {
                "msg_list": [{
                    "msgid": "m-review-2",
                    "msgtype": "text",
                    "origin": 3,
                    "external_userid": euid,
                    "send_time": int(time.time()),
                    "text": {"content": "你好"},
                }],
                "next_cursor": "",
                "has_more": 0,
            },
        )
        monkeypatch.setattr("src.core._do_send_kf", lambda *a, **k: False)
        monkeypatch.setattr("src.wecom.wxkf.get_session_state", lambda *a, **k: 1)
        monkeypatch.setattr("src.core.store.privacy_notice_accepted", lambda k, u: True)
        monkeypatch.setattr(
            "src.core.store.profile_get",
            lambda k, u: {"上次方案": "打底修护", "随访时间": "2025-01-01"},
        )
        monkeypatch.setattr("src.core.store.profile_update", lambda *a, **k: None)
        monkeypatch.setattr("src.core.chat", lambda messages: "好的，先做复评。")

        core.process_kf_messages("t1", kfid)
        assert store.kv_get(f"review-due:{kfid}:{euid}:2025-01-01") == ""
