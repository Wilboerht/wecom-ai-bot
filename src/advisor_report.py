"""
测肤报告场景联动 — 微信客服 enter_session 事件处理

流程：用户从测肤结果页点击「打开护肤顾问」（链接带 scene_param=sessionId）
→ 微信客服回调进入会话事件 → 用 scene_param 调 skin-advisor 内部接口拉取报告摘要
→ 写入该用户的护肤档案（profile），AI 顾问后续回复自带报告上下文
→ 可选：用 welcome_code 主动发送个性化欢迎语
"""
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import config
from .ai import store

logger = logging.getLogger(__name__)

# sessionId 白名单格式（UUID 或 8-128 位字母数字与连字符）
SCENE_PARAM_RE = re.compile(r"^[0-9A-Za-z\-]{8,128}$")

_TIMEOUT = 5.0

# 由测肤报告管理的档案字段（build_profile_fields 可能产生的全部 key）。
# 新报告到达时先清理这些 key 再写入，防止第二次测肤后旧报告的字段残留
#（如第一次有过敏史、第二次未作答，旧 ⚠️ 过敏史不应继续留在档案里）。
# 用户聊天中 AI 通过 [档案: ...] 标记积累的其他字段不受影响。
REPORT_MANAGED_KEYS = [
    "肤质", "性别", "肌肤年龄", "素颜评分", "重点问题", "各维度评分", "测肤方式",
    "年龄段", "关注问题", "过敏史", "孕期状态", "医美经历(近3月)",
    "睡眠质量", "压力水平", "生理周期", "饮水习惯", "运动频率",
    "饮食习惯", "日晒程度", "护肤习惯", "护肤预算",
]


def _fetch_report(session_id: str) -> dict | None:
    """调 skin-advisor 内部接口拉取报告摘要，失败返回 None"""
    if not config.INTERNAL_API_KEY or not config.ADVISOR_REPORT_API_URL:
        return None
    try:
        resp = httpx.get(
            config.ADVISOR_REPORT_API_URL,
            params={"sessionId": session_id},
            headers={"x-internal-key": config.INTERNAL_API_KEY},
            timeout=_TIMEOUT,
        )
    except Exception as e:
        logger.warning("report-summary 请求失败: %s", e)
        return None
    if resp.status_code != 200:
        logger.warning("report-summary 返回 %s: user session=%s", resp.status_code, session_id)
        return None
    try:
        data = resp.json()
    except Exception as e:
        logger.warning("report-summary 解析失败: %s", e)
        return None
    if not data.get("found"):
        return None
    return data


def build_profile_fields(report: dict) -> dict:
    """报告摘要 → 用户护肤档案字段（与 AI 顾问档案标签一致：肤质/性别/年龄/诉求等）

    兼容扩展后的 report-summary 响应：questionnaire（问卷档案，上游已标签化为
    中文文本，此处只做透传与拼接）、dimensions（完整十维）、concerns（AI 分析
    关注点，作为 issues 为空时的兜底）、dataSource（测肤方式）。
    """
    fields = {}
    if report.get("skinTypeLabel"):
        fields["肤质"] = str(report["skinTypeLabel"])
    gender = report.get("gender")
    if gender:
        fields["性别"] = "男" if gender == "male" else "女"
    skin_age = report.get("skinAge")
    if isinstance(skin_age, (int, float)):
        fields["肌肤年龄"] = f"{int(skin_age)}岁"
    score = report.get("overallScore")
    if isinstance(score, (int, float)):
        percentile = report.get("percentile")
        if isinstance(percentile, (int, float)):
            fields["素颜评分"] = f"{int(score)}分（超越全国{int(percentile)}%的用户）"
        else:
            fields["素颜评分"] = f"{int(score)}分"
    issues = report.get("issues") or []
    if issues:
        parts = []
        for item in issues:
            label = (item or {}).get("label") or ""
            item_score = (item or {}).get("score")
            if label and isinstance(item_score, (int, float)):
                parts.append(f"{label}（{int(item_score)}分）")
            elif label:
                parts.append(label)
        if parts:
            fields["重点问题"] = "、".join(parts)
    elif report.get("concerns"):
        # 纯问卷模式没有维度评分，用 AI 分析得出的关注点兜底
        concerns = [str(c) for c in report["concerns"] if c]
        if concerns:
            fields["重点问题"] = "、".join(concerns)

    dimensions = report.get("dimensions") or []
    dim_parts = []
    for item in dimensions:
        label = (item or {}).get("label") or ""
        item_score = (item or {}).get("score")
        if label and isinstance(item_score, (int, float)):
            dim_parts.append(f"{label}{int(item_score)}分")
    if dim_parts:
        fields["各维度评分"] = "、".join(dim_parts)

    data_source = report.get("dataSource")
    if data_source:
        source_labels = {
            "comprehensive": "问卷+面部扫描",
            "questionnaire": "仅问卷",
            "hybrid": "问卷+部分扫描",
        }
        fields["测肤方式"] = source_labels.get(str(data_source), str(data_source))

    q = report.get("questionnaire") or {}
    if isinstance(q, dict):
        if q.get("ageRange"):
            fields["年龄段"] = str(q["ageRange"])
        primary = [str(c) for c in (q.get("primaryConcerns") or []) if c]
        if primary:
            fields["关注问题"] = "、".join(primary)

        # 安全相关字段：过敏史 / 孕期状态直接影响产品成分推荐，加 ⚠️ 让 AI 优先注意
        allergies = [str(a) for a in (q.get("allergies") or []) if a]
        if allergies:
            text = "、".join(allergies)
            fields["过敏史"] = text if allergies == ["无过敏史"] else f"⚠️{text}"
        pregnancy = q.get("pregnancy")
        if pregnancy and pregnancy != "否":
            fields["孕期状态"] = f"⚠️{pregnancy}"

        medical = q.get("medicalBeauty")
        if medical and medical != "无":
            fields["医美经历(近3月)"] = str(medical)

        lifestyle_map = [
            ("sleepQuality", "睡眠质量"),
            ("stressLevel", "压力水平"),
            ("menstrualCycle", "生理周期"),
            ("waterIntake", "饮水习惯"),
            ("exerciseFrequency", "运动频率"),
            ("dietaryHabits", "饮食习惯"),
            ("sunExposure", "日晒程度"),
            ("skincareFrequency", "护肤习惯"),
            ("budget", "护肤预算"),
        ]
        for key, label in lifestyle_map:
            value = q.get(key)
            if value and value != "不适用":
                fields[label] = str(value)
    # 注意：不写「报告ID」等内部字段——档案会注入 AI 系统提示词，内部标识对模型是噪音
    return fields


def _report_date(report: dict) -> datetime:
    """测肤时间：优先用接口返回的 createdAt（ISO 8601），缺失时兜底为当前时间（中国时区）"""
    raw = report.get("createdAt")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(
                ZoneInfo("Asia/Shanghai")
            )
        except ValueError:
            pass
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def build_welcome_text(report: dict) -> str:
    """个性化欢迎语（纯文本、符合护肤顾问语气规范）

    用户刚从测肤结果页点进来，欢迎语确认问卷收集到的基础信息；
    测肤时间优先用接口返回的 createdAt，旧版接口没有该字段时兜底为当天日期。
    问卷字段缺失时显示「暂未填写」，引导用户补充。
    """
    nickname = str(report.get("nickname") or "").strip()
    gender = report.get("gender")
    title = {"male": "先生", "female": "女士"}.get(gender, "")
    if nickname:
        name = f"{nickname}{title}"
    else:
        name = title or "顾客"

    q = report.get("questionnaire")
    if not isinstance(q, dict):
        q = {}

    def _q(key: str) -> str:
        value = q.get(key)
        return str(value) if value else "暂未填写"

    concerns = [str(c) for c in (q.get("primaryConcerns") or []) if c]
    gender_label = {"male": "男", "female": "女"}.get(gender, "暂未填写")
    tested_at = _report_date(report)

    lines = [
        f"尊敬的{name}您好, 我是你的专属顾问NIHPLOD肌智派护肤顾问, 很高兴成为您的专属护肤顾问, 为了将来给您带来更好的服务体验, 想和您确认以下的基础信息:",
        "",
        f"性别：{gender_label}",
        f"年龄段：{_q('ageRange')}",
        f"关注问题：{'、'.join(concerns) if concerns else '暂未填写'}",
        f"医美经历（近3月）：{_q('medicalBeauty')}",
        f"护肤习惯：{_q('skincareFrequency')}",
        f"最近一次的肌智派测肤时间: {tested_at.year}年{tested_at.month:02d}月{tested_at.day:02d}日",
        "",
        "请确认上述信息，如果需要调整，请马上告诉我哦~",
    ]
    return "\n".join(lines)


# 补发去重标记 TTL：7 天（远长于测肤会话的有效期，同一份报告不会重复补发）
_WELCOME_FALLBACK_TTL = 7 * 24 * 3600


def _welcome_fallback_marked(open_kfid: str, external_userid: str, scene_param: str) -> bool:
    """同一份报告（scene_param）只补发一次欢迎语；首次调用写入标记并返回 False"""
    key = f"welcome-fallback:{open_kfid}:{external_userid}:{scene_param}"
    if store.kv_get(key):
        return True
    store.kv_set(key, "1", _WELCOME_FALLBACK_TTL)
    return False


def handle_enter_session(open_kfid: str, external_userid: str, event: dict) -> None:
    """进入会话事件：拉取报告写档案，可选发欢迎语"""
    scene_param = (event.get("scene_param") or "").strip()
    if not SCENE_PARAM_RE.match(scene_param):
        return

    report = _fetch_report(scene_param)
    if not report:
        return

    fields = build_profile_fields(report)
    if not fields:
        return

    # 先清理报告管理的旧字段再合并：同一用户第二次测肤时，旧报告中存在而
    # 新报告缺失的字段（过敏史/重点问题等）不应残留
    store.profile_delete_keys(open_kfid, external_userid, REPORT_MANAGED_KEYS)
    store.profile_update(open_kfid, external_userid, fields)
    logger.info("Report profile saved: user=%s session=%s fields=%s", external_userid, scene_param, list(fields))

    # 欢迎语：优先用 welcome_code（微信事件响应消息，仅 48h 内未收过欢迎语且
    # 用户未发过消息时才下发）。无 welcome_code 时退化为普通 send_msg 补发——
    # 仅当用户在 48h 会话窗口内（之前发过消息）才能送达，送达失败属平台限制，不重试。
    # 同一份报告（scene_param）只补发一次，避免用户重复点链接被刷屏。
    if config.KF_SEND_WELCOME:
        welcome_code = (event.get("welcome_code") or "").strip()
        if welcome_code:
            try:
                from .wecom.wxkf import send_msg_on_event

                ok = send_msg_on_event(
                    corpid=config.WECOM_CORP_ID,
                    secret=config.WECOM_SECRET,
                    code=welcome_code,
                    content=build_welcome_text(report),
                )
                if ok:
                    logger.info("Welcome sent: user=%s", external_userid)
            except Exception as e:
                logger.warning("Welcome 发送失败: %s", e)
        elif not _welcome_fallback_marked(open_kfid, external_userid, scene_param):
            try:
                from .wecom.wxkf import send_message

                ok = send_message(
                    corpid=config.WECOM_CORP_ID,
                    secret=config.WECOM_SECRET,
                    to_user=external_userid,
                    open_kfid=open_kfid,
                    msgtype="text",
                    content=build_welcome_text(report),
                )
                if ok:
                    logger.info("Welcome fallback sent: user=%s session=%s", external_userid, scene_param)
            except Exception as e:
                logger.warning("Welcome 补发失败: %s", e)
