"""
测肤报告场景联动 — 微信客服 enter_session 事件处理

流程：用户从测肤结果页点击「打开护肤顾问」（链接带 scene_param=sessionId）
→ 微信客服回调进入会话事件 → 用 scene_param 调 skin-advisor 内部接口拉取报告摘要
→ 写入该用户的护肤档案（profile），AI 顾问后续回复自带报告上下文
→ 可选：用 welcome_code 主动发送个性化欢迎语
"""
import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import config
from .ai import store
from .log_utils import mask_id

logger = logging.getLogger(__name__)

# sessionId 白名单格式（UUID 或 8-128 位字母数字与连字符）
SCENE_PARAM_RE = re.compile(r"^[0-9A-Za-z\-]{8,128}$")

_TIMEOUT = 5.0


class ReportFetchRetryableError(Exception):
    """报告拉取的可重试失败（网络异常/5xx）：上层按失败处理，允许企微重推后重试"""

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


_no_api_key_warned = False


def _mask_session(session_id: str) -> str:
    """sessionId 是拉取报告的凭证，日志只打前 8 位"""
    return session_id[:8] + "..."


def _fetch_report(session_id: str) -> dict | None:
    """调 skin-advisor 内部接口拉取报告摘要，失败返回 None。
    超时/网络异常/5xx 重试 1 次（退避 1s），重试耗尽抛 ReportFetchRetryableError；
    404 等客户端错误不重试，返回 None（终态）。"""
    global _no_api_key_warned
    if not config.INTERNAL_API_KEY or not config.ADVISOR_REPORT_API_URL:
        if not _no_api_key_warned:
            _no_api_key_warned = True
            logger.warning("INTERNAL_API_KEY/ADVISOR_REPORT_API_URL 未配置，测肤报告拉取已停用")
        return None
    resp = None
    for attempt in range(2):
        try:
            resp = httpx.get(
                config.ADVISOR_REPORT_API_URL,
                params={"sessionId": session_id},
                headers={"x-internal-key": config.INTERNAL_API_KEY},
                timeout=_TIMEOUT,
            )
        except Exception as e:
            # 不记录异常原文：httpx 异常可能带含 sessionId 的请求 URL
            if attempt == 0:
                logger.warning("report-summary 请求失败，1s 后重试: %s", type(e).__name__)
                time.sleep(1)
                continue
            logger.warning("report-summary 请求失败: %s", type(e).__name__)
            raise ReportFetchRetryableError(type(e).__name__)
        if resp.status_code == 200:
            break
        # 5xx 重试一次；4xx（如 404 报告不存在）不重试
        if resp.status_code >= 500 and attempt == 0:
            logger.warning("report-summary 返回 %s，1s 后重试", resp.status_code)
            time.sleep(1)
            continue
        logger.warning("report-summary 返回 %s: user session=%s", resp.status_code, _mask_session(session_id))
        if resp.status_code >= 500:
            raise ReportFetchRetryableError(f"status {resp.status_code}")
        return None
    try:
        data = resp.json()
    except Exception as e:
        logger.warning("report-summary 解析失败: %s", e)
        return None
    if not isinstance(data, dict):
        # 上游异常返回数组/标量等非对象 JSON：按失败处理但不崩
        logger.warning("report-summary 返回非对象 JSON: type=%s", type(data).__name__)
        return None
    if not data.get("found"):
        return None
    return data


def _as_str_list(value) -> list[str]:
    """上游列表字段归一：若误返回字符串（如 "敏感肌"）按单元素处理，避免被逐字符迭代成 "敏、感、肌\""""
    if isinstance(value, list):
        return [str(x) for x in value if x]
    if value:
        return [str(value)]
    return []


def _as_dict_list(value) -> list[dict]:
    """上游 issues/dimensions 归一：只接受对象数组，畸形元素忽略，避免 .get 崩溃。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


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
    # 白名单：male→男、female→女，其他值不写字段
    gender_label = {"male": "男", "female": "女"}.get(gender)
    if gender_label:
        fields["性别"] = gender_label
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
    issues = _as_dict_list(report.get("issues"))
    if issues:
        parts = []
        for item in issues:
            label = item.get("label") or ""
            item_score = item.get("score")
            if label and isinstance(item_score, (int, float)):
                parts.append(f"{label}（{int(item_score)}分）")
            elif label:
                parts.append(label)
        if parts:
            fields["重点问题"] = "、".join(parts)
    elif report.get("concerns"):
        # 纯问卷模式没有维度评分，用 AI 分析得出的关注点兜底
        concerns = _as_str_list(report["concerns"])
        if concerns:
            fields["重点问题"] = "、".join(concerns)

    dimensions = _as_dict_list(report.get("dimensions"))
    dim_parts = []
    for item in dimensions:
        label = item.get("label") or ""
        item_score = item.get("score")
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
        primary = _as_str_list(q.get("primaryConcerns"))
        if primary:
            fields["关注问题"] = "、".join(primary)

        # 安全相关字段：过敏史 / 孕期状态直接影响产品成分推荐，加 ⚠️ 让 AI 优先注意
        allergies = _as_str_list(q.get("allergies"))
        if allergies:
            text = "、".join(allergies)
            # 按是否含真实过敏原判断：上游可能返回 ["无过敏史", "花粉"] 这类组合，
            # 此时仍应加 ⚠️；全部是否定表述（无过敏史/无）才不加
            has_real_allergen = any(a not in ("无过敏史", "无") for a in allergies)
            fields["过敏史"] = f"⚠️{text}" if has_real_allergen else text
        pregnancy = q.get("pregnancy")
        if pregnancy and pregnancy not in ("否", "不适用"):
            fields["孕期状态"] = f"⚠️{pregnancy}"

        medical = q.get("medicalBeauty")
        if medical and medical not in ("无", "不适用"):
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
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
        else:
            # naive 时间（无时区偏移）按中国时区解释，再统一转中国时区
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            return dt.astimezone(ZoneInfo("Asia/Shanghai"))
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def build_welcome_text(report: dict) -> str:
    """个性化欢迎语（纯文本、符合护肤顾问语气规范）

    用户刚从测肤结果页点进来，欢迎语确认问卷收集到的基础信息；
    测肤时间优先用接口返回的 createdAt，旧版接口没有该字段时兜底为当天日期。
    问卷字段缺失时显示「暂未填写」，引导用户补充。
    医美经历等敏感字段不回显具体内容，只显示「已记录」。
    """
    # nickname 清洗：去换行、截断 32 字符，防空/超长昵称撑爆企微 2048 字节上限
    nickname = re.sub(r"[\r\n]+", "", str(report.get("nickname") or "")).strip()[:32]
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

    concerns = _as_str_list(q.get("primaryConcerns"))
    gender_label = {"male": "男", "female": "女"}.get(gender, "暂未填写")
    tested_at = _report_date(report)

    medical = q.get("medicalBeauty")
    medical_recorded = bool(medical) and medical not in ("无", "不适用")

    lines = [
        f"尊敬的{name}您好，我是 NIHPLOD 肌智派专属护肤顾问，很高兴为您服务。"
        "为了给您更贴合的护肤建议，想和您确认以下基础信息：",
        "",
        f"性别：{gender_label}",
        f"年龄段：{_q('ageRange')}",
        f"关注问题：{'、'.join(concerns) if concerns else '暂未填写'}",
        # 医美经历属敏感个人信息，聊天中不回显具体内容，只显示是否已记录
        f"医美经历（近3月）：{'已记录' if medical_recorded else '暂未填写'}",
        f"护肤习惯：{_q('skincareFrequency')}",
        f"最近一次的肌智派测肤时间：{tested_at.year}年{tested_at.month:02d}月{tested_at.day:02d}日",
        "",
        "请确认上述信息，如需调整，直接告诉我就好哦~",
    ]
    return "\n".join(lines)


# 欢迎语去重标记 TTL：7 天（远长于测肤会话的有效期，同一份报告不会重复发送）
_WELCOME_FALLBACK_TTL = 7 * 24 * 3600
# 标记 key 前缀与 store 的 PII 导出/删除扫描保持一致（按用户键控，可随用户数据清除）
_WELCOME_FALLBACK_PREFIX = "welcome-fallback:"


def _send_welcome(open_kfid: str, external_userid: str, scene_param: str, event: dict, report: dict) -> None:
    """个性化欢迎语：优先用 welcome_code（微信事件响应消息，仅 48h 内未收过欢迎语且
    用户未发过消息时才下发），缺失或发送失败时降级普通 send_msg 再试一次——
    降级路径仅当用户在 48h 会话窗口内（之前发过消息）才能送达，送达失败属平台限制。

    去重：两条路径共用同一个 per-(用户, scene_param) 标记，发送前 kv_set_nx 原子 claim，
    任一路径成功即占用；全部失败释放标记，允许下次事件重试。
    """
    try:
        content = build_welcome_text(report)
    except Exception as e:
        # 内容构建失败不占用 7 天去重标记，允许下一次事件重试
        logger.warning("Welcome 内容构建失败: %s", e)
        return
    key = f"{_WELCOME_FALLBACK_PREFIX}{open_kfid}:{external_userid}:{scene_param}"
    if not store.kv_set_nx(key, "1", _WELCOME_FALLBACK_TTL):
        # 同一份报告已发过，避免用户重复点链接被刷屏
        return
    ok = False
    welcome_code = (event.get("welcome_code") or "").strip()
    if welcome_code:
        try:
            from .wecom.wxkf import send_msg_on_event

            ok = send_msg_on_event(
                corpid=config.WECOM_CORP_ID,
                secret=config.WECOM_SECRET,
                code=welcome_code,
                content=content,
            )
        except Exception as e:
            logger.warning("Welcome 发送失败: %s", type(e).__name__)
    if not ok:
        # welcome_code 缺失或发送失败：降级普通 send_msg 再试一次
        try:
            from .wecom.wxkf import send_message

            ok = send_message(
                corpid=config.WECOM_CORP_ID,
                secret=config.WECOM_SECRET,
                to_user=external_userid,
                open_kfid=open_kfid,
                msgtype="text",
                content=content,
            )
        except Exception as e:
            logger.warning("Welcome 补发失败: %s", type(e).__name__)
    if ok:
        logger.info("Welcome sent: user=%s session=%s", mask_id(external_userid), _mask_session(scene_param))
        # 与通用欢迎语（首条消息时发送，见 core.process_kf_messages）共用去重标记：
        # 已收到报告个性化欢迎语的用户，不再收到账号通用欢迎语
        store.kv_set(f"welcome-sent:{open_kfid}:{external_userid}", "1", 86400 * 365)
    else:
        # 全部失败：释放标记，允许下次事件重试
        store.kv_delete(key)


def handle_enter_session(open_kfid: str, external_userid: str, event: dict) -> bool:
    """进入会话事件：拉取报告写档案，可选发欢迎语。

    返回 True 表示处理完成或明确无需处理（无 scene_param、报告不存在等终态——
    404 是终态不算失败）；返回 False 表示可重试失败（网络/5xx 拉取失败、
    档案写入异常），调用方应回滚 msgid 认领，允许企微重推后重试。
    """
    scene_param = (event.get("scene_param") or "").strip()
    if not SCENE_PARAM_RE.match(scene_param):
        return True

    try:
        report = _fetch_report(scene_param)
    except ReportFetchRetryableError:
        return False
    if not report:
        return True

    fields = build_profile_fields(report)
    if not fields:
        return True

    # 先清理报告管理的旧字段再合并（单次 SET 原子完成）：同一用户第二次测肤时，
    # 旧报告中存在而新报告缺失的字段（过敏史/重点问题等）不应残留
    try:
        store.profile_replace_report_keys(open_kfid, external_userid, REPORT_MANAGED_KEYS, fields)
    except Exception as e:
        logger.warning("报告档案写入失败: %s", e)
        return False
    logger.info("Report profile saved: user=%s session=%s fields=%s", mask_id(external_userid), _mask_session(scene_param), list(fields))

    # 欢迎语：报告存在才发送；发送失败不视为处理失败（标记已释放，下次事件可重试）
    if config.KF_SEND_WELCOME:
        _send_welcome(open_kfid, external_userid, scene_param, event, report)
    return True
