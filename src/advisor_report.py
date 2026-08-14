"""
测肤报告场景联动 — 微信客服 enter_session 事件处理

流程：用户从测肤结果页点击「打开护肤顾问」（链接带 scene_param=sessionId）
→ 微信客服回调进入会话事件 → 用 scene_param 调 skin-advisor 内部接口拉取报告摘要
→ 写入该用户的护肤档案（profile），AI 顾问后续回复自带报告上下文
→ 可选：用 welcome_code 主动发送个性化欢迎语
"""
import logging
import re

import httpx

from .config import config
from .ai import store

logger = logging.getLogger(__name__)

# sessionId 白名单格式（UUID 或 8-128 位字母数字与连字符）
SCENE_PARAM_RE = re.compile(r"^[0-9A-Za-z\-]{8,128}$")

_TIMEOUT = 5.0


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
    """报告摘要 → 用户护肤档案字段（与 AI 顾问档案标签一致：肤质/性别/年龄/诉求等）"""
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
    # 注意：不写「报告ID」等内部字段——档案会注入 AI 系统提示词，内部标识对模型是噪音
    return fields


def build_welcome_text(report: dict) -> str:
    """个性化欢迎语（纯文本、符合护肤顾问语气规范）"""
    lines = ["您好呀，我看到您刚在肌智派完成了测肤报告"]
    parts = []
    if report.get("skinTypeLabel"):
        parts.append(f"肤质：{report['skinTypeLabel']}")
    score = report.get("overallScore")
    if isinstance(score, (int, float)):
        parts.append(f"素颜评分：{int(score)}分")
    if parts:
        lines.append("，".join(parts))
    lines.append("我已记下您的报告，接下来可以直接告诉我您最想改善的肌肤问题，或对报告里任何一项提问哦。")
    return "\n".join(lines)


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

    store.profile_update(open_kfid, external_userid, fields)
    logger.info("Report profile saved: user=%s session=%s fields=%s", external_userid, scene_param, list(fields))

    # 欢迎语：仅在微信判定满足条件（48h 内未收过欢迎语且未发过消息）时才有 welcome_code
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
