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
        """端到端：AI 回复标签里的非法日期不进档案，合法字段正常写入"""
        saved = {}
        monkeypatch.setattr(store, "profile_update", lambda k, u, f: saved.update(f))
        reply = core._save_profile_from_reply(
            "wk", "u1",
            "已记录。[档案: 当前阶段=屏障修护期（阶段1/3）, 阶段开始=两周前, 随访时间=2025-06-15]")
        assert saved["当前阶段"] == "屏障修护期（阶段1/3）"
        assert saved["随访时间"] == "2025-06-15"
        assert "阶段开始" not in saved  # 「两周前」非法已剔除
        assert "档案" not in reply

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
