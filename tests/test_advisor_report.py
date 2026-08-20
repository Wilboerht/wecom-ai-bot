"""测肤报告联动（enter_session → 档案写入 → 客服链接）测试"""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo
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

# 扩展后的 report-summary 响应（skin-advisor 侧 2026-08 起新增字段）
FAKE_EXTENDED_REPORT = {
    **FAKE_REPORT,
    "dimensions": [
        {"key": "waterOil", "label": "水油平衡", "score": 65},
        {"key": "firmness", "label": "皮肤弹性", "score": 90},
        {"key": "radiance", "label": "光泽度", "score": 58},
    ],
    "dataSource": "comprehensive",
    "questionnaire": {
        "ageRange": "26-30岁",
        "primaryConcerns": ["细纹/松弛", "色斑/暗沉"],
        "allergies": ["香精过敏", "酒精过敏"],
        "pregnancy": "是",
        "medicalBeauty": "光子/激光类",
        "sleepQuality": "较差（经常熬夜/失眠）",
        "stressLevel": "很大（焦虑/紧绷）",
        "menstrualCycle": "黄体期（经前一周）",
        "skincareFrequency": "每天精细护肤",
        "budget": "中等预算（单品300-1000元）",
    },
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

    def test_extended_report_questionnaire_fields(self):
        fields = advisor_report.build_profile_fields(FAKE_EXTENDED_REPORT)
        assert fields["年龄段"] == "26-30岁"
        assert fields["关注问题"] == "细纹/松弛、色斑/暗沉"
        assert fields["过敏史"] == "⚠️香精过敏、酒精过敏"
        assert fields["孕期状态"] == "⚠️是"
        assert fields["医美经历(近3月)"] == "光子/激光类"
        assert fields["睡眠质量"] == "较差（经常熬夜/失眠）"
        assert fields["压力水平"] == "很大（焦虑/紧绷）"
        assert fields["生理周期"] == "黄体期（经前一周）"
        assert fields["护肤习惯"] == "每天精细护肤"
        assert fields["护肤预算"] == "中等预算（单品300-1000元）"

    def test_extended_report_dimensions_and_source(self):
        fields = advisor_report.build_profile_fields(FAKE_EXTENDED_REPORT)
        assert fields["各维度评分"] == "水油平衡65分、皮肤弹性90分、光泽度58分"
        assert fields["测肤方式"] == "问卷+面部扫描"

    def test_safe_values_no_warning_marks(self):
        report = {
            "found": True,
            "questionnaire": {
                "allergies": ["无过敏史"],
                "pregnancy": "否",
                "medicalBeauty": "无",
                "menstrualCycle": "不适用",
            },
        }
        fields = advisor_report.build_profile_fields(report)
        assert fields["过敏史"] == "无过敏史"
        assert "孕期状态" not in fields
        assert "医美经历(近3月)" not in fields
        assert "生理周期" not in fields
        assert not any("⚠️" in v for v in fields.values())

    def test_allergies_mixed_with_no_history_still_warns(self):
        """上游返回 ["无过敏史", "花粉"] 这类组合时，含真实过敏原，仍应加 ⚠️"""
        report = {
            "found": True,
            "questionnaire": {"allergies": ["无过敏史", "花粉"]},
        }
        fields = advisor_report.build_profile_fields(report)
        assert fields["过敏史"] == "⚠️无过敏史、花粉"

    def test_allergies_all_negative_no_warning(self):
        """全部是否定表述（无过敏史/无）时不加 ⚠️"""
        report = {
            "found": True,
            "questionnaire": {"allergies": ["无过敏史", "无"]},
        }
        fields = advisor_report.build_profile_fields(report)
        assert fields["过敏史"] == "无过敏史、无"

    def test_concerns_fallback_when_no_issues(self):
        fields = advisor_report.build_profile_fields(
            {"found": True, "concerns": ["T区出油", "毛孔粗大"], "dataSource": "questionnaire"}
        )
        assert fields["重点问题"] == "T区出油、毛孔粗大"
        assert fields["测肤方式"] == "仅问卷"

    def test_issues_take_priority_over_concerns(self):
        report = {
            "found": True,
            "issues": [{"label": "色斑状况", "score": 55}],
            "concerns": ["T区出油"],
        }
        fields = advisor_report.build_profile_fields(report)
        assert fields["重点问题"] == "色斑状况（55分）"


class TestBuildWelcomeText:
    def test_welcome_text(self):
        text = advisor_report.build_welcome_text(FAKE_REPORT)
        assert "尊敬的小雨女士您好" in text
        assert "性别：女" in text
        # FAKE_REPORT 无问卷字段，对应行显示「暂未填写」
        assert "年龄段：暂未填写" in text
        assert "请确认上述信息" in text

    def test_welcome_text_with_questionnaire(self):
        text = advisor_report.build_welcome_text(FAKE_EXTENDED_REPORT)
        assert "年龄段：26-30岁" in text
        assert "关注问题：细纹/松弛、色斑/暗沉" in text
        assert "医美经历（近3月）：光子/激光类" in text
        assert "护肤习惯：每天精细护肤" in text

    def test_welcome_text_without_nickname(self):
        text = advisor_report.build_welcome_text({"found": True, "gender": "male"})
        assert "尊敬的先生您好" in text

    def test_welcome_text_uses_report_created_at(self):
        # 接口返回 createdAt 时按它显示测肤日期（UTC 转中国时区）
        text = advisor_report.build_welcome_text(
            {**FAKE_REPORT, "createdAt": "2026-08-01T02:00:00.000Z"}
        )
        assert "最近一次的肌智派测肤时间: 2026年08月01日" in text

    def test_welcome_text_created_at_fallback(self):
        # 旧版接口无 createdAt 时兜底为当天日期
        text = advisor_report.build_welcome_text(FAKE_REPORT)
        today = datetime.now(ZoneInfo("Asia/Shanghai"))
        assert f"{today.year}年{today.month:02d}月{today.day:02d}日" in text


class TestHandleEnterSession:
    def test_saves_profile_and_skips_welcome(self, monkeypatch):
        saved = {}
        monkeypatch.setattr(
            "src.advisor_report.store.profile_replace_report_keys",
            lambda open_kfid, user, delete_keys, fields: saved.update({(open_kfid, user): fields}),
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
            "src.advisor_report.store.profile_replace_report_keys", lambda a, b, c, d: None
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
        assert "小雨" in sent["content"]

    def test_invalid_scene_param_ignored(self, monkeypatch):
        # 非法 scene_param 应被直接忽略，_fetch_report 不应被调用（spy 验证）
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
            "src.advisor_report.store.profile_replace_report_keys", lambda a, b, c, d: saved.append(1)
        )
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: None)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "sess-1234567890", "welcome_code": "wcode"},
        )
        assert saved == []

    def test_second_report_clears_stale_managed_keys(self, monkeypatch):
        """第二次测肤：删旧 key 与写新字段通过单次原子替换完成，防止旧过敏史等残留"""
        deleted = []
        saved = {}
        monkeypatch.setattr(
            "src.advisor_report.store.profile_replace_report_keys",
            lambda open_kfid, user, keys, fields: (deleted.append(keys), saved.update(fields)),
        )
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_EXTENDED_REPORT)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        monkeypatch.setattr(config, "KF_SEND_WELCOME", False)

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_user_1",
            {"scene_param": "sess-1234567890"},
        )

        assert deleted == [advisor_report.REPORT_MANAGED_KEYS]
        # 报告管理的全部字段都在清理清单里
        for key in saved:
            assert key in advisor_report.REPORT_MANAGED_KEYS


class TestWelcomeFallback:
    """无 welcome_code 时退化为普通 send_msg 补发，同一份报告只补发一次"""

    def _setup(self, monkeypatch, sent):
        monkeypatch.setattr("src.advisor_report.store.profile_replace_report_keys", lambda a, b, c, d: None)
        # 补发去重标记用本地 dict 隔离，避免污染/依赖真实 Redis
        local_kv: dict[str, str] = {}
        monkeypatch.setattr(
            "src.advisor_report.store.kv_get", lambda key: local_kv.get(key, "")
        )
        monkeypatch.setattr(
            "src.advisor_report.store.kv_set",
            lambda key, value, ttl=600: local_kv.__setitem__(key, value),
        )
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_REPORT)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        monkeypatch.setattr(config, "KF_SEND_WELCOME", True)

        def fake_send(corpid, secret, to_user, open_kfid, msgtype, content, msgid=""):
            sent.append({"to_user": to_user, "open_kfid": open_kfid, "content": content})
            return True

        monkeypatch.setattr("src.wecom.wxkf.send_message", fake_send)

    def test_fallback_sent_when_no_welcome_code(self, monkeypatch):
        sent = []
        self._setup(monkeypatch, sent)

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_1",
            {"scene_param": "sess-fb-0001"},
        )

        assert len(sent) == 1
        assert sent[0]["to_user"] == "wm_fallback_1"
        assert "小雨" in sent[0]["content"]

    def test_fallback_not_resent_for_same_scene(self, monkeypatch):
        sent = []
        self._setup(monkeypatch, sent)

        for _ in range(3):
            advisor_report.handle_enter_session(
                "kfc7834894b7ee2b86a", "wm_fallback_2",
                {"scene_param": "sess-fb-0002"},
            )

        assert len(sent) == 1

    def test_fallback_resent_for_new_scene(self, monkeypatch):
        sent = []
        self._setup(monkeypatch, sent)

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_3",
            {"scene_param": "sess-fb-0003a"},
        )
        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_3",
            {"scene_param": "sess-fb-0003b"},
        )

        assert len(sent) == 2

    def test_welcome_code_takes_priority_over_fallback(self, monkeypatch):
        sent = []
        on_event = []
        self._setup(monkeypatch, sent)
        monkeypatch.setattr(
            "src.wecom.wxkf.send_msg_on_event",
            lambda corpid, secret, code, content: on_event.append(code) or True,
        )

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_4",
            {"scene_param": "sess-fb-0004", "welcome_code": "wcode-9"},
        )

        assert on_event == ["wcode-9"]
        assert sent == []

    def test_welcome_code_path_dedup_by_scene(self, monkeypatch):
        """welcome_code 路径同样按 scene_param 去重：同一份报告不重复发"""
        sent = []
        on_event = []
        self._setup(monkeypatch, sent)
        monkeypatch.setattr(
            "src.wecom.wxkf.send_msg_on_event",
            lambda corpid, secret, code, content: on_event.append(code) or True,
        )

        for _ in range(3):
            advisor_report.handle_enter_session(
                "kfc7834894b7ee2b86a", "wm_fallback_5",
                {"scene_param": "sess-fb-0005", "welcome_code": "wcode-10"},
            )

        assert on_event == ["wcode-10"]

    def test_welcome_code_failure_falls_back_to_send_message(self, monkeypatch):
        """welcome_code 发送失败：降级普通 send_msg 再试一次"""
        sent = []
        self._setup(monkeypatch, sent)
        monkeypatch.setattr(
            "src.wecom.wxkf.send_msg_on_event",
            lambda corpid, secret, code, content: False,
        )

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_6",
            {"scene_param": "sess-fb-0006", "welcome_code": "wcode-bad"},
        )

        assert len(sent) == 1
        assert sent[0]["to_user"] == "wm_fallback_6"

    def test_all_failed_releases_marker_allows_retry(self, monkeypatch):
        """两条路径都失败：释放去重标记，下次事件可重试"""
        sent = []
        self._setup(monkeypatch, sent)
        fail = {"on": True}

        def fake_send(corpid, secret, to_user, open_kfid, msgtype, content, msgid=""):
            if fail["on"]:
                return False
            sent.append(content)
            return True

        monkeypatch.setattr("src.wecom.wxkf.send_message", fake_send)

        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_7",
            {"scene_param": "sess-fb-0007"},
        )
        assert sent == []

        fail["on"] = False
        advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_fallback_7",
            {"scene_param": "sess-fb-0007"},
        )
        assert len(sent) == 1


class TestHandleEnterSessionReturn:
    """handle_enter_session 返回 bool：终态 True，可重试失败 False"""

    def _setup(self, monkeypatch):
        monkeypatch.setattr("src.advisor_report.store.profile_replace_report_keys", lambda a, b, c, d: None)
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        monkeypatch.setattr(config, "KF_SEND_WELCOME", False)

    def test_retryable_fetch_failure_returns_false(self, monkeypatch):
        """网络/5xx 等可重试拉取失败返回 False（配合 msgid release 重试）"""
        self._setup(monkeypatch)

        def boom(sid):
            raise advisor_report.ReportFetchRetryableError("status 500")

        monkeypatch.setattr(advisor_report, "_fetch_report", boom)
        assert advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_ret_1", {"scene_param": "sess-ret-0001"}
        ) is False

    def test_report_not_found_returns_true(self, monkeypatch):
        """报告不存在（404 终态）返回 True，不重试"""
        self._setup(monkeypatch)
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: None)
        assert advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_ret_2", {"scene_param": "sess-ret-0002"}
        ) is True

    def test_invalid_scene_param_returns_true(self):
        """无/非法 scene_param 明确无需处理，返回 True"""
        assert advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_ret_3", {"scene_param": "bad!"}
        ) is True

    def test_success_returns_true(self, monkeypatch):
        self._setup(monkeypatch)
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_REPORT)
        assert advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_ret_4", {"scene_param": "sess-ret-0004"}
        ) is True

    def test_profile_write_failure_returns_false(self, monkeypatch):
        """档案写入异常返回 False"""
        self._setup(monkeypatch)

        def boom(a, b, c, d):
            raise RuntimeError("redis boom")

        monkeypatch.setattr("src.advisor_report.store.profile_replace_report_keys", boom)
        monkeypatch.setattr(advisor_report, "_fetch_report", lambda sid: FAKE_REPORT)
        assert advisor_report.handle_enter_session(
            "kfc7834894b7ee2b86a", "wm_ret_5", {"scene_param": "sess-ret-0005"}
        ) is False


class TestFetchReportRobustness:
    def test_non_dict_json_returns_none(self, monkeypatch):
        """上游返回非对象 JSON（数组/标量）：按失败处理但不崩"""
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")

        class Resp:
            status_code = 200

            def json(self):
                return ["not", "a", "dict"]

        monkeypatch.setattr(advisor_report.httpx, "get", lambda *a, **kw: Resp())
        assert advisor_report._fetch_report("sess-nd-0001") is None

    def test_5xx_raises_retryable(self, monkeypatch):
        """5xx 重试耗尽抛 ReportFetchRetryableError"""
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        monkeypatch.setattr(advisor_report.time, "sleep", lambda s: None)

        class Resp:
            status_code = 500

            def json(self):
                return {}

        monkeypatch.setattr(advisor_report.httpx, "get", lambda *a, **kw: Resp())
        with pytest.raises(advisor_report.ReportFetchRetryableError):
            advisor_report._fetch_report("sess-nd-0002")

    def test_404_returns_none_without_retry(self, monkeypatch):
        """404 是终态：不重试、返回 None"""
        monkeypatch.setattr(config, "INTERNAL_API_KEY", "k")
        monkeypatch.setattr(config, "ADVISOR_REPORT_API_URL", "https://x")
        calls = {"n": 0}

        class Resp:
            status_code = 404

            def json(self):
                return {}

        def fake_get(*a, **kw):
            calls["n"] += 1
            return Resp()

        monkeypatch.setattr(advisor_report.httpx, "get", fake_get)
        assert advisor_report._fetch_report("sess-nd-0003") is None
        assert calls["n"] == 1


class TestGenderAndNickname:
    def test_unknown_gender_no_profile_field(self):
        """gender 白名单：未知值不写「性别」字段"""
        fields = advisor_report.build_profile_fields({"found": True, "gender": "other"})
        assert "性别" not in fields

    def test_unknown_gender_welcome_falls_back(self):
        """未知 gender：称呼回退「顾客」，性别行显示暂未填写"""
        text = advisor_report.build_welcome_text({"found": True, "gender": "other"})
        assert "尊敬的顾客您好" in text
        assert "性别：暂未填写" in text

    def test_nickname_sanitized(self):
        """nickname 去换行、截断 32 字符"""
        text = advisor_report.build_welcome_text(
            {"found": True, "gender": "female", "nickname": "小\n雨" + "x" * 100}
        )
        first_line = text.split("\n", 1)[0]
        assert "小雨" in first_line
        # 称呼部分（昵称+女士）不超过 32+2 字符
        name = first_line.split("您好")[0].replace("尊敬的", "")
        assert len(name) <= 34

    def test_naive_created_at_as_shanghai(self):
        """naive createdAt 按中国时区解释"""
        text = advisor_report.build_welcome_text(
            {**FAKE_REPORT, "createdAt": "2026-08-01T10:00:00"}
        )
        assert "最近一次的肌智派测肤时间: 2026年08月01日" in text


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
