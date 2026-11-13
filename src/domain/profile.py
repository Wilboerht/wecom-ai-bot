"""用户护肤档案与阶段状态的领域规则。

AI 通过 [档案: key=value] 标签提出档案/阶段变更；本模块负责：
- key/value 结构校验（白名单、长度、日期格式）
- 阶段状态机（禁止 1→3 跳级，允许 3→1 回退）
- 随访时间/阶段开始由代码计算，不信任模型手算日期
- 档案注入文本（含复评到期确定性指令）
"""
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import metrics
from ..ai import store
from ..log_utils import mask_id

logger = logging.getLogger(__name__)

PROFILE_SCHEMA_VERSION = 1
PROFILE_VALUE_MAX_LEN = 100

# 档案字段 key 白名单：中英文、数字、下划线及全/半角括号（兼容报告字段「医美经历(近3月)」），
# 最长 20 字符（防注入伪造 system 指令）
_PROFILE_KEY_RE = re.compile(r'^[\w一-龥（）()]{1,20}$')
# 完整闭合的档案标记（兼容全角【档案：…】）
_PROFILE_TAG_RE = re.compile(r'[\[【]档案[:：]\s*([^\]】]+)[\]】]')
# 行尾/文末残留的未闭合档案标记片段（如被 max_tokens 截断的 "[档案: 肤质=干性"）。
_PROFILE_TAG_TAIL_RE = re.compile(r'[\[【]档案[:：][^\]】\n]*$', re.MULTILINE)

# 阶段字段由模型提出，代码做日期与状态机把关
_STAGE_KEYS = ("当前阶段", "阶段目标", "阶段开始", "晋级条件")
_STAGE_DATE_KEYS = ("阶段开始", "随访时间")
_STAGE_REVIEW_DAYS = {1: 14, 2: 28, 3: 90}
_STAGE_NO_RE = re.compile(r"阶段\s*(\d)\s*/\s*3")

# 阶段状态机：允许 1→1/2、2→1/2/3、3→1/2/3；禁止 1→3 跳级
_ALLOWED_STAGE_TRANSITIONS = {
    1: {1, 2},
    2: {1, 2, 3},
    3: {1, 2, 3},
}

# 每个随访周期只注入一次复评指令；标记在回复成功发出后由 core 写入
_REVIEW_DUE_PREFIX = "review-due:"
_REVIEW_DUE_TTL = 86400 * 90


def _valid_stage_date(value: str) -> bool:
    """日期字段必须是真实的 YYYY-MM-DD（2025-13-99 也拒绝）。"""
    try:
        datetime.strptime(value.strip(), "%Y-%m-%d")
        return True
    except (ValueError, AttributeError):
        return False


def _parse_stage_number(value) -> int | None:
    m = _STAGE_NO_RE.search(str(value or ""))
    if not m:
        return None
    number = int(m.group(1))
    return number if 1 <= number <= 3 else None


def _stage_transition_allowed(old_number: int | None, new_number: int) -> bool:
    if old_number is None:
        return 1 <= new_number <= 3
    return new_number in _ALLOWED_STAGE_TRANSITIONS.get(old_number, set())


def normalize_profile_fields(fields: dict) -> dict:
    """结构化档案字段：白名单 key、去换行、截断；未知 key 仍允许但必须通过白名单正则。"""
    normalized = {}
    for key, value in fields.items():
        key = str(key).strip()
        if not _PROFILE_KEY_RE.match(key):
            logger.warning("档案字段 key 非法已丢弃: key=%r", key)
            continue
        text = " ".join(str(value).split())[:PROFILE_VALUE_MAX_LEN]
        if text:
            normalized[key] = text
    return normalized


def validate_stage_fields(fields: dict) -> dict:
    """写入档案前校验阶段日期：非法日期剔除并计数，其余字段原样放行。"""
    for key in _STAGE_DATE_KEYS:
        value = fields.get(key)
        if value is not None and not _valid_stage_date(str(value)):
            logger.warning("阶段日期字段非法已剔除: key=%r len=%d", key, len(str(value)))
            metrics.inc(metrics.STAGE_FIELD_INVALID)
            fields.pop(key)
    return fields


def apply_stage_dates(open_kfid: str, external_userid: str, fields: dict) -> None:
    """阶段状态机 + 随访时间/阶段开始计算（Asia/Shanghai）。

    AI 只提出阶段；代码拒绝非法跳级（如 1→3），保留旧阶段并丢弃本次阶段字段。
    允许回退（3→1/2，例如过敏/爆痘恶化），符合 prompt 的「无条件回退打底期」。
    """
    stage = fields.get("当前阶段")
    if not stage:
        return
    new_no = _parse_stage_number(stage)
    if new_no is None:
        logger.warning(
            "阶段名缺少 阶段N/3 编号，日期按模型原值处理: user=%s stage_len=%d",
            mask_id(external_userid), len(str(stage)),
        )
        return

    existing = store.profile_get(open_kfid, external_userid)
    old_stage = existing.get("当前阶段")
    old_no = _parse_stage_number(old_stage)
    if old_no is not None and not _stage_transition_allowed(old_no, new_no):
        logger.warning(
            "非法阶段跳变已拒绝: user=%s old=%s new=%s",
            mask_id(external_userid), old_no, new_no,
        )
        metrics.inc(metrics.STAGE_TRANSITION_INVALID)
        for key in (*_STAGE_KEYS, "随访时间"):
            fields.pop(key, None)
        return

    days = _STAGE_REVIEW_DAYS[new_no]
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    fields["随访时间"] = (today + timedelta(days=days)).isoformat()

    if old_no is None:
        metrics.inc(metrics.STAGE_STARTED)
    elif new_no > old_no:
        metrics.inc(metrics.STAGE_PROMOTED)
    elif new_no < old_no:
        metrics.inc(metrics.STAGE_REGRESSED)
    else:
        metrics.inc(metrics.STAGE_HELD)

    if existing.get("当前阶段") == stage:
        # 留级/阶段内调整：保留原阶段开始日期，不重置阶段时钟
        fields.pop("阶段开始", None)
    else:
        fields["阶段开始"] = today.isoformat()


def build_profile_block(profile: dict | None, user_id: str = "") -> str:
    """组装档案注入文本：基础字段 + 阶段状态块 + 复评日确定性指令。"""
    if not profile:
        return ""
    parts = []
    for key, value in profile.items():
        key = str(key).strip()
        if not _PROFILE_KEY_RE.match(key):
            logger.warning("档案 key 非法已跳过: user=%s key=%r", mask_id(user_id), key)
            continue
        if key in _STAGE_KEYS:
            continue
        value = " ".join(str(value).split())[:PROFILE_VALUE_MAX_LEN]
        if value:
            parts.append(f"{key}: {value}")

    blocks: list[str] = []
    if parts:
        blocks.append(
            "【该用户的护肤档案】" + "；".join(parts)
            + "。依据档案个性化推荐，如档案信息变化请在回复末尾建议更新。"
        )

    stage_parts = []
    for key in _STAGE_KEYS:
        value = profile.get(key)
        value = " ".join(str(value).split())[:PROFILE_VALUE_MAX_LEN] if value is not None else ""
        if value:
            stage_parts.append(f"{key}={value}")
    if stage_parts:
        blocks.append("【当前阶段状态】" + "；".join(stage_parts))

    # 复评到期检测：代码算日期，随访时间不晚于今天则输出确定性指令。
    follow = str(profile.get("随访时间") or "").strip()
    if _valid_stage_date(follow):
        due = datetime.strptime(follow, "%Y-%m-%d").date()
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if due <= today and (
            not user_id or not store.kv_get(f"{_REVIEW_DUE_PREFIX}{user_id}:{follow}")
        ):
            blocks.append(
                "【系统指令，优先级最高，覆盖寒暄规则】今天是阶段复评日："
                "即使用户只是打招呼，也必须先执行阶段复评裁决（晋级/留级/回退），"
                "禁止只回复寒暄。"
            )
    return "\n".join(blocks)


def save_profile_from_reply(open_kfid: str, external_userid: str, reply: str) -> str:
    """从 AI 回复提取所有 [档案: key=value] 标记，合并入存储，并返回清理后的文本。"""
    fields: dict = {}

    def _collect(match: re.Match) -> str:
        for part in re.split(r'[,;，；]', match.group(1)):
            pair = part.split('=', 1)
            if len(pair) != 2:
                if part.strip():
                    logger.warning(
                        "档案标签残片疑似值内逗号截断: user=%s part_len=%d",
                        mask_id(external_userid), len(part.strip()),
                    )
                continue
            key, value = pair[0].strip(), pair[1].strip()
            if not key or not value:
                continue
            if not _PROFILE_KEY_RE.match(key):
                logger.warning("档案字段 key 非法已丢弃: user=%s key=%r", mask_id(external_userid), key)
                continue
            fields[key] = value[:PROFILE_VALUE_MAX_LEN]
        return ""

    reply = _PROFILE_TAG_RE.sub(_collect, reply)
    reply = _PROFILE_TAG_TAIL_RE.sub("", reply)
    fields = normalize_profile_fields(fields)
    validate_stage_fields(fields)
    apply_stage_dates(open_kfid, external_userid, fields)
    if fields:
        store.profile_update(open_kfid, external_userid, fields)
        logger.info("Profile saved: user=%s fields=%s", mask_id(external_userid), list(fields))
    return reply.strip()
