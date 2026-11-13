"""
企业微信消息编排层 — 传输解析、回调路由和 KF 处理流程。

领域规则已拆分到 src/domain/：
- render.py       Markdown 清理、字节拆条、发送
- intent.py       转人工/政策咨询意图
- order_flow.py   订单号/渠道解析
- profile.py      档案字段、阶段状态机、复评注入
- schedule.py     人工客服服务时段

本模块保留编排逻辑，并继续 re-export 旧函数以兼容既有测试/调用方。
"""
import json
import logging
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from .config import config
from .log_utils import mask_id
from .compliance import sanitize_reply
from . import copy
from . import metrics
from .domain.intent import (
    is_policy_question as _is_policy_question,
    transfer_requested as _transfer_requested,
)
from .domain.order_flow import (
    ORDER_FLOW_CHANNEL_MAX_LEN,
    ORDER_FLOW_EXIT_WORDS,
    ORDER_FLOW_MAX_ATTEMPTS,
    ORDER_FLOW_TTL,
    _extract_order_no,
    _order_triggered,
    _split_channel_order,
)
from .domain.profile import (
    _REVIEW_DUE_PREFIX,
    _REVIEW_DUE_TTL,
    apply_stage_dates as _apply_stage_dates,
    build_profile_block,
    save_profile_from_reply as _save_profile_from_reply,
    validate_stage_fields as _validate_stage_fields,
)
from .domain.prompt import (
    _INJECTION_NOTE,
    _USER_MSG_CLOSE,
    _USER_MSG_OPEN,
    _account_prompts,
    _prompt_file_cache,
    build_system_content,
    get_system_prompt,
    load_prompt_file,
    today_line as _today_line,
    wrap_user_text,
)
from .domain.render import (
    send_kf_text as _do_send_kf,
    split_kf_content as _split_kf_content,
    strip_markdown as _strip_markdown,
)
from .domain.schedule import (
    is_within_service_hours,
    service_hour as _service_hour,
    service_hours_line as _service_hours_line,
)
from .wecom.crypto import WXBizMsgCrypt
from .wecom.handler import (
    parse_encrypted_xml,
    parse_message_xml,
    build_text_reply_xml,
    build_encrypted_reply_xml,
)
from .wecom.types import TextMessage, EventMessage
from .ai.deepseek import chat, AIServiceError
from .ai import store
from .links import shorten_text

# 兼容既有调用方/测试的 re-export（领域函数已移到 src/domain/）
__all__ = [
    "_apply_stage_dates",
    "_validate_stage_fields",
    "_split_kf_content",
    "_service_hour",
    "build_profile_block",
    "_save_profile_from_reply",
    "_strip_markdown",
    "_do_send_kf",
    "_transfer_requested",
    "_is_policy_question",
    "_extract_order_no",
    "_split_channel_order",
    "_order_triggered",
    "build_system_content",
    "get_system_prompt",
    "load_prompt_file",
    "_today_line",
    "wrap_user_text",
    "_USER_MSG_OPEN",
    "_USER_MSG_CLOSE",
    "_INJECTION_NOTE",
    "_prompt_file_cache",
    "_account_prompts",
    "is_within_service_hours",
    "process_message",
    "process_kf_messages",
    "handle_callback",
]

logger = logging.getLogger(__name__)

# 同一用户的 KF 消息串行处理（PM2 instances=1）；多 worker 部署需改 Redis 分布式锁
_user_locks: dict[tuple[str, str], list] = {}
_user_locks_guard = threading.Lock()


@contextmanager
def _user_lock(open_kfid: str, external_userid: str):
    """按 open_kfid + external_userid 串行化消息/事件处理，避免历史、档案和转人工竞态。"""
    key = (open_kfid, external_userid)
    with _user_locks_guard:
        entry = _user_locks.get(key)
        if entry is None:
            entry = [threading.Lock(), 0]
            _user_locks[key] = entry
        entry[1] += 1
        lock = entry[0]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _user_locks_guard:
            entry = _user_locks.get(key)
            if entry is not None:
                entry[1] -= 1
                if entry[1] <= 0:
                    _user_locks.pop(key, None)


# cache_key -> (知识库内容, 基础 prompt, 拼接结果)；知识库或 prompt 文件热加载变化即重建


# ---- 外部 prompt 文件（mtime 热加载）----
# filepath -> (文件 mtime, prompt 文本)；文件缺失时 mtime 记 None，只警告一次




# ---- 系统提示词 ----


# ---- 知识库按需路由（常驻段 + 产品FAQ段）----
# 从产品线清单提取「正装名（昵称）」别名对，如：净透温和洁面慕斯（云朵洁面）￥680/100ml




# ---- 注入防护 ----
# 历史会话与用户消息均为外部输入，用 system 声明 + 定界符包裹降低提示词注入面










# ---- 速率限制 ----
def check_rate_limit(user_id: str, send_time: int = 0) -> bool:
    return store.ratelimit_check(user_id, send_time)


# ---- 普通消息处理（企微/公众号被动回复） ----
# 微信被动回复要求 5s 内返回响应，否则放弃等待并重试；重推又被 msgid 去重拒掉，
# 慢响应会导致用户完全收不到回复。因此公众号路径给 AI 4.5s 预算且不重试：
# 超时也保证 5s 内返回兜底文案（微信收到响应即视为成功，不会重推）
_WX_AI_TIMEOUT = 4.5


def process_message(message) -> str | None:
    # 非 KF 路径默认关闭客户文本 AI：该路径没有隐私声明/限流/被动回复长度保护。
    # 事件欢迎语保留，便于公众号关注、企微进入应用等场景。
    if not isinstance(message, EventMessage) and not config.NON_KF_AUTO_REPLY_ENABLED:
        return None

    if isinstance(message, EventMessage):
        event = message.event.lower()
        if event in ("enter_agent", "subscribe"):
            # 公众号事件类消息无 MsgId：企微超时重推会重复发欢迎语，用合成 key 去重
            claim_key = f"wx:event:{message.from_user}:{event}:{message.create_time}"
            if not store.msgid_claim(claim_key):
                return None
            return copy.WX_WELCOME_TEXT
        return None

    if isinstance(message, TextMessage):
        user_text = message.content.strip()
        if not user_text:
            return None
        if len(user_text) > 2000:
            user_text = user_text[:2000]

        metrics.inc(metrics.WX_MSG_RECEIVED)

        # 微信 5s 超时重推去重：wx: 前缀避免与客服消息 msgid 冲突
        msg_id = getattr(message, "msg_id", "") or ""
        claim_key = f"wx:{msg_id}" if msg_id else ""
        if claim_key and not store.msgid_claim(claim_key):
            return None

        user_id = message.from_user
        history = store.session_get(user_id)
        # 注入防护：system 声明 + 用户消息定界符包裹；今天日期供随访承诺计算
        messages = [{"role": "system", "content": get_system_prompt() + "\n\n" + _today_line() + "\n\n" + _service_hours_line() + "\n\n" + _INJECTION_NOTE}]
        messages.extend(history)
        messages.append({"role": "user", "content": wrap_user_text(user_text)})

        try:
            reply = chat(messages, timeout=_WX_AI_TIMEOUT, max_retries=0)
        except AIServiceError as e:
            # 兜底文案不写入历史（避免污染上下文）；回滚认领允许重推后重试
            metrics.inc(metrics.AI_FAILURE)
            if "timeout" in str(e).lower():
                metrics.inc(metrics.WX_BUDGET_TIMEOUT)
                logger.warning("公众号 AI 超出 %.1fs 预算返回兜底: user=%s", _WX_AI_TIMEOUT, mask_id(user_id))
            if claim_key:
                store.msgid_release(claim_key)
            return copy.WX_AI_UNAVAILABLE

        # 广告法禁语后置兜底：命中替换为合规表述
        reply, banned_hits = sanitize_reply(reply)
        if banned_hits:
            metrics.inc(metrics.BANNED_SANITIZED)
            logger.warning("命中广告法禁语已替换: user=%s words=%s", mask_id(user_id), banned_hits)

        metrics.inc(metrics.AI_REPLY_OK)

        store.session_add(user_id, "user", user_text)
        store.session_add(user_id, "assistant", reply)
        return reply

    return copy.WX_MEDIA_UNSUPPORTED


# ---- 统一回调处理 ----
def handle_callback(body: str, query: dict, crypto: WXBizMsgCrypt) -> dict:
    """统一回调处理，返回 {reply_xml: str|None, is_kf: bool}"""
    try:
        encrypted_data = parse_encrypted_xml(body)
    except Exception as e:
        logger.error(f"XML解析失败: {e}")
        return {"reply_xml": None}

    try:
        plain_xml = crypto.decrypt_message(
            msg_signature=query.get("msg_signature", ""),
            timestamp=query.get("timestamp", ""),
            nonce=query.get("nonce", ""),
            encrypt=encrypted_data["encrypt"],
        )
    except Exception as e:
        logger.error(f"解密失败: {e}")
        return {"reply_xml": None}

    # 解密后的明文解析并入 try：明文畸形（上游 bug 或 key 泄露才能构造）时留定位日志而非中间件笼统兜底
    try:
        message = parse_message_xml(plain_xml)
    except Exception as e:
        logger.error(f"解密后明文 XML 解析失败: {e}")
        return {"reply_xml": None}

    # 微信客服事件 — 异步处理
    if isinstance(message, EventMessage) and message.event == "kf_msg_or_event":
        try:
            root = ET.fromstring(plain_xml)
        except Exception as e:
            logger.error(f"kf 事件 XML 二次解析失败: {e}")
            return {"reply_xml": None}
        token = root.findtext("Token", "")
        open_kfid = root.findtext("OpenKfId", "")
        if token and open_kfid:
            return {"reply_xml": None, "kf_token": token, "kf_open_kfid": open_kfid}
        # 缺 Token/OpenKfId 无法拉取消息，记 warning（只记字段存在性）便于排查静默丢弃
        logger.warning(
            "kf_msg_or_event 缺 Token/OpenKfId，事件已丢弃: has_token=%s has_open_kfid=%s",
            bool(token), bool(open_kfid),
        )
        return {"reply_xml": None}

    # 普通消息
    reply_content = process_message(message)
    if reply_content is None:
        return {"reply_xml": None}

    reply_xml = build_text_reply_xml(
        from_user=encrypted_data["to_user_name"],
        to_user=message.from_user,
        content=reply_content,
    )

    result = crypto.encrypt_message(reply_xml)
    return {
        "reply_xml": build_encrypted_reply_xml(
            encrypt=result["encrypt"],
            msgsignature=result["msgsignature"],
            timestamp=result["timestamp"],
            nonce=result["nonce"],
        )
    }


# ---- 微信客服消息处理 ----
# 用户可见固定文案集中在 src/copy.py（文案术语表见 docs/copy-style-guide.md）；
# 别名保持 core.PRIVACY_NOTICE_TEXT 可引用（测试与既有调用方）
PRIVACY_NOTICE_TEXT = copy.PRIVACY_NOTICE_TEXT


def process_kf_messages(token: str, open_kfid: str) -> None:
    """拉取并处理微信客服消息，支持分页

    可靠性约定：
    - 整体 try/except 兜底（sync_messages 重试耗尽会抛 WecomApiError），记 error 后优雅结束
    - 每条消息先 msgid_claim 去重，企微重推直接跳过（不重发限流/媒体提示）
    - 处理失败一律 msgid_release 回滚，允许企微重推后重试；成功路径不 release
    - 单条消息异常不拖垮整批
    """
    from .wecom.wxkf import sync_messages, get_session_state, WecomApiError

    account = config.get_account(open_kfid)
    candidates = account.get("service_userid", "") or config.WECOM_SERVICE_USERID
    if not isinstance(candidates, list):
        candidates = [candidates] if candidates else []

    _state_cache: dict[str, int] = {}
    _transferred_in_batch: set = set()  # 本批次已转人工的用户，后续消息跳过

    def _get_state(euid: str) -> int:
        if euid not in _state_cache:
            _state_cache[euid] = get_session_state(
                config.WECOM_CORP_ID, config.WECOM_SECRET, open_kfid, euid
            )
        return _state_cache[euid]

    def _merge_flow_context(euid: str, user_text: str) -> str:
        """转人工时把订单查询流程里收集到的渠道/订单号并入上下文。
        只读不删：转接成功后由调用方 kv_delete，失败时上下文保留可重试"""
        key = _order_flow_key(open_kfid, euid)
        info = store.kv_get(key)
        if not info:
            return user_text
        try:
            d = json.loads(info)
        except Exception:
            return user_text
        if not isinstance(d, dict):
            return user_text
        parts = [user_text]
        if d.get("channel"):
            parts.append(f"购买渠道：{d['channel']}")
        if d.get("order_no"):
            parts.append(f"订单号：{d['order_no']}")
        return "，".join(parts)

    def _handle_order_inquiry(euid: str, user_text: str) -> bool:
        """订单查询两步引导：先问购买渠道，再问订单号，收集齐转人工。返回 True 表示已处理"""
        # 账号级门控：仅开启 order_flow 的账号（品牌客服）进入流程；
        # 护肤顾问等账号的订单类问题由其 prompt 引导至品牌人工客服，不走本流程
        if not account.get("order_flow"):
            return False
        key = _order_flow_key(open_kfid, euid)
        info = store.kv_get(key)
        if info:
            # 流程内退出关键词：命中即删除流程状态并确认
            if any(w in user_text for w in ORDER_FLOW_EXIT_WORDS):
                store.kv_delete(key)
                _do_send_kf(open_kfid, euid, copy.ORDER_FLOW_CANCELLED)
                return True
            try:
                d = json.loads(info)
            except Exception:
                d = {}
            if not isinstance(d, dict):
                # 坏状态不采信，删除后按首次触发重新开始
                store.kv_delete(key)
                d = {}
            if d.get("step") == "orderno":
                order_no = _extract_order_no(user_text)
                if not order_no:
                    # 重试上限：多次未识别到有效订单号自动退出，避免用户卡死在流程里
                    attempts = int(d.get("attempts", 0)) + 1
                    if attempts >= ORDER_FLOW_MAX_ATTEMPTS:
                        store.kv_delete(key)
                        _do_send_kf(open_kfid, euid, copy.ORDER_FLOW_GIVE_UP)
                        return True
                    d["attempts"] = attempts
                    store.kv_set(key, json.dumps(d, ensure_ascii=False), ORDER_FLOW_TTL)
                    _do_send_kf(open_kfid, euid, copy.ORDER_FLOW_INVALID_ORDER_NO)
                    return True
                # 转接成功才删除流程上下文；全员失败/非服务时间时保留，稍后可重试
                if _handle_kf_transfer(
                    open_kfid, euid, candidates,
                    f"查询订单（购买渠道：{d.get('channel', '')}，订单号：{order_no}）",
                ):
                    metrics.inc(metrics.ORDERFLOW_COMPLETED)
                    store.kv_delete(key)
                    _transferred_in_batch.add(euid)
                return True
            # step == channel
            channel, order_no = _split_channel_order(user_text)
            if not order_no:
                order_no = str(d.get("order_no") or "")
            if order_no:
                if _handle_kf_transfer(
                    open_kfid, euid, candidates,
                    f"查询订单（购买渠道：{channel}，订单号：{order_no}）",
                ):
                    metrics.inc(metrics.ORDERFLOW_COMPLETED)
                    store.kv_delete(key)
                    _transferred_in_batch.add(euid)
                return True
            # 渠道输入长度校验：超长输入不采信，按无效输入处理
            if len(user_text) > ORDER_FLOW_CHANNEL_MAX_LEN:
                _do_send_kf(open_kfid, euid, copy.ORDER_FLOW_INVALID_CHANNEL)
                return True
            d["channel"] = channel
            d["step"] = "orderno"
            store.kv_set(key, json.dumps(d, ensure_ascii=False), ORDER_FLOW_TTL)
            _do_send_kf(open_kfid, euid, copy.ORDER_FLOW_ASK_ORDER_NO)
            return True

        if _order_triggered(user_text):
            metrics.inc(metrics.ORDERFLOW_STARTED)
            channel, order_no = _split_channel_order(user_text)
            # 首条消息可能已同时给出渠道+订单号，能直接转接就不再多问一轮；
            # 「查订单 1234567890」这类只有触发器+订单号的文本不能当成渠道
            if (
                order_no
                and channel
                and len(channel) <= ORDER_FLOW_CHANNEL_MAX_LEN
                and not _order_triggered(channel)
            ):
                if _handle_kf_transfer(
                    open_kfid, euid, candidates,
                    f"查询订单（购买渠道：{channel}，订单号：{order_no}）",
                ):
                    metrics.inc(metrics.ORDERFLOW_COMPLETED)
                    _transferred_in_batch.add(euid)
                return True
            state = {"step": "channel"}
            if order_no:
                state["order_no"] = order_no
            store.kv_set(key, json.dumps(state, ensure_ascii=False), ORDER_FLOW_TTL)
            _do_send_kf(open_kfid, euid, copy.ORDER_FLOW_ASK_CHANNEL)
            return True
        return False

    def _process_one(msg: dict) -> None:
        # 事件消息（origin=4）：目前只处理「用户进入会话」事件（测肤报告场景联动）
        if msg.get("msgtype") == "event":
            event = msg.get("event") or {}
            if event.get("event_type") == "enter_session":
                # 与文本消息一致做 msgid 去重：sync_msg 每次从头拉取，
                # 不去重会随后续回调反复重放该事件（重复拉报告/写档案/发欢迎语）；
                # 处理失败（如报告拉取失败）回滚认领，允许企微重推后重试
                msg_id = msg.get("msgid", "")
                if msg_id and store.msgid_claim(msg_id):
                    if not _handle_enter_session_event(open_kfid, event):
                        store.msgid_release(msg_id)
            return

        if msg.get("origin") != 3:
            return

        external_userid = msg.get("external_userid", "")
        msg_id = msg.get("msgid", "")
        if not external_userid or not msg_id:
            return

        # 去重提前：先认领再处理，重放消息直接跳过（不重发限流/媒体提示）
        if not store.msgid_claim(msg_id):
            return

        send_time = int(msg.get("send_time", 0))

        # 先查会话状态: 0=新会话 1=API可处理 2=排队 3=人工接待 4=已结束
        # 人工接待中（state=3）所有机器人回复都跳过（含媒体提示）
        try:
            state = _get_state(external_userid)
        except WecomApiError as e:
            logger.error("KF state 查询失败: user=%s err=%s", mask_id(external_userid), e)
            store.msgid_release(msg_id)
            return

        # 转人工跨批次标记: 转接成功后 state=3 生效有延迟, 300s 内按人工接待对待
        transferred = bool(store.kv_get(f"kf:transferred:{open_kfid}:{external_userid}"))

        # 非文本消息 — 只记录元信息不存内容，仅对2分钟内的发一次引导
        if msg.get("msgtype") != "text":
            msgtype = msg.get("msgtype", "unknown")
            # 安全层：永不上报图片/语音到 Redis 或日志，只记类型
            logger.info("KF media received: type=%s user=%s (content NOT stored)", msgtype, mask_id(external_userid))
            if state in (0, 1) and not transferred and (not send_time or time.time() - send_time < 120):
                if not _do_send_kf(open_kfid, external_userid, copy.KF_MEDIA_UNSUPPORTED):
                    store.msgid_release(msg_id)
            return

        user_text = msg.get("text", {}).get("content", "").strip()
        if not user_text:
            return
        # 统一截断：发 LLM 与存历史用同一份文本
        user_text = user_text[:2000]
        metrics.inc(metrics.KF_MSG_RECEIVED)

        # 转人工关键词（账号级配置，品牌客服含纠纷场景词）；裸「人工」不做子串匹配，避免误伤「人工智能/人工合成」
        transfer_keywords = account.get("transfer_keywords") or ["转人工", "人工客服", "人工服务", "真人客服", "找人工", "真人", "转接", "有人吗"]

        logger.info("KF state=%s user=%s text_len=%d", state, mask_id(external_userid), len(user_text))

        if external_userid in _transferred_in_batch:
            return

        if state == 3 or transferred:
            # 人工接待中（或刚转接成功、state 尚未生效）：机器人不回复、不结束会话、不打断；
            # 该判断必须先于限流，否则人工接待中机器人会插嘴限流提示
            return

        if send_time and time.time() - send_time > 120:
            return

        # 排队（2）/已结束（4）状态机器人完全静默，不发限流/媒体提示
        if state not in (0, 1):
            return

        # 转人工诉求豁免限流：先判转人工，命中直接转，避免被限流拦截
        transfer_hit = _transfer_requested(user_text, transfer_keywords) and not _is_policy_question(user_text)

        if not transfer_hit and check_rate_limit(external_userid, send_time):
            metrics.inc(metrics.RATELIMIT_HIT)
            if not _do_send_kf(open_kfid, external_userid, copy.RATELIMIT_HINT):
                store.msgid_release(msg_id)
            return

        if state in (0, 1):
            # 首次会话：先发账号欢迎语（welcome-sent 标记与测肤报告个性化欢迎语共用去重），
            # 再发隐私声明并标记，继续咨询即视为同意
            if not store.privacy_notice_accepted(open_kfid, external_userid):
                welcome = (account.get("welcome") or "").strip()
                welcome_key = f"welcome-sent:{open_kfid}:{external_userid}"
                if welcome and not store.kv_get(welcome_key):
                    if _do_send_kf(open_kfid, external_userid, welcome):
                        store.kv_set(welcome_key, "1", 86400 * 365)
                # 只有隐私声明真正送达才记录同意；发送失败下一条消息会重试
                if _do_send_kf(open_kfid, external_userid, PRIVACY_NOTICE_TEXT):
                    store.privacy_notice_accept(open_kfid, external_userid)

            # AI 可处理
            if transfer_hit:
                metrics.inc(metrics.TRANSFER_REQUESTED)
                logger.info("KF transfer triggered: user=%s text_len=%d", mask_id(external_userid), len(user_text))
                ctx = _merge_flow_context(external_userid, user_text)
                # 转接成功才打标记并清理订单流程上下文；失败保留，本批次后续消息也不应被静默丢弃
                if _handle_kf_transfer(open_kfid, external_userid, candidates, ctx):
                    _transferred_in_batch.add(external_userid)
                    store.kv_delete(_order_flow_key(open_kfid, external_userid))
                return

            # 订单查询引导流程：先问购买渠道，再问订单号，收集齐转人工
            if _handle_order_inquiry(external_userid, user_text):
                return

            # AI 回复
            session_key = f"{open_kfid}:{external_userid}"
            history = store.session_get(session_key)

            # 注入用户档案（key 白名单/截断防护 + 阶段状态块 + 复评日检测，见 build_profile_block；
            # 与 golden 测试用例共用同一函数保证行为一致）
            profile = store.profile_get(open_kfid, external_userid)
            profile_text = build_profile_block(profile, session_key)

            # 品牌客服知识库按需路由 + 档案定界符 + 注入防护（声明放在最后）
            messages = [{"role": "system", "content": build_system_content(open_kfid, user_text, history, profile_text)}]
            messages.extend(history)
            messages.append({"role": "user", "content": wrap_user_text(user_text)})

            try:
                reply = chat(messages)
            except AIServiceError:
                # AI 失败：不主动转人工（客户没有提出该诉求），发一次繁忙提示并保留 msgid 认领——
                # 企微重推因已认领而跳过，不会重复发提示；AI 恢复后客户再发一条即可正常咨询。
                # 提示语发送失败才回滚认领，允许重推后重试
                metrics.inc(metrics.AI_FAILURE)
                if not _do_send_kf(open_kfid, external_userid, copy.KF_AI_BUSY):
                    store.msgid_release(msg_id)
                return

            # 广告法禁语后置兜底：命中替换为合规表述（在提取档案/写历史之前，
            # 让历史记录与档案值同样是合规文本）
            reply, banned_hits = sanitize_reply(reply)
            if banned_hits:
                metrics.inc(metrics.BANNED_SANITIZED)
                logger.warning("命中广告法禁语已替换: user=%s words=%s", mask_id(external_userid), banned_hits)

            # 清洗后的回复（含档案标签）写入历史，与既往上下文格式保持一致
            history_reply = reply

            # 提取并保存用户档案
            reply = _save_profile_from_reply(open_kfid, external_userid, reply)

            # 硬性清理 Markdown 语法
            reply = _strip_markdown(reply)

            # 长购买链接替换为短链
            reply = shorten_text(reply)

            # 先发送成功再写历史：发送失败回滚 msgid 允许重推，避免历史重复堆叠同一提问
            if not _do_send_kf(open_kfid, external_userid, reply, msg_id):
                store.msgid_release(msg_id)
                return
            metrics.inc(metrics.AI_REPLY_OK)
            # 复评指令触达落库：仅在回复成功发出后标记（同一随访周期不再注入复评指令）；
            # AI/发送失败不标记，企微重推后本周期复评仍会触发
            if "今天是阶段复评日" in profile_text:
                follow = str(profile.get("随访时间") or "").strip()
                store.kv_set(f"{_REVIEW_DUE_PREFIX}{session_key}:{follow}", "1", _REVIEW_DUE_TTL)
                metrics.inc(metrics.REVIEW_TRIGGERED)
                logger.info("复评指令已触达: user=%s", mask_id(external_userid))
            store.session_add(session_key, "user", user_text, ttl=account.get("session_ttl", 0))
            store.session_add(session_key, "assistant", history_reply, ttl=account.get("session_ttl", 0))

        # state 2/4 不做处理，等下次回调

    # 游标持久化到 Redis/内存：积压超过单次页数时，下一次回调从上次位置继续，
    # 避免每次从头拉取导致第 500 条以后的消息长期饥饿。
    cursor_key = f"kf:cursor:{open_kfid}"
    cursor = store.kv_get(cursor_key)
    try:
        for _ in range(50):
            try:
                data = sync_messages(
                    corpid=config.WECOM_CORP_ID,
                    secret=config.WECOM_SECRET,
                    token=token,
                    open_kfid=open_kfid,
                    cursor=cursor,
                    limit=1000,
                )
            except WecomApiError:
                if cursor:
                    # cursor 可能已过期/失效：清空后从头拉取，msgid 去重保证不重复处理
                    logger.warning("KF cursor 拉取失败，清空后重试: open_kfid=%s", open_kfid)
                    store.kv_delete(cursor_key)
                    cursor = ""
                    continue
                raise

            for msg in data.get("msg_list", []):
                euid = msg.get("external_userid") or ""
                event = msg.get("event")
                if not euid and isinstance(event, dict):
                    euid = event.get("external_userid") or ""
                try:
                    # 同一用户的消息/事件串行处理，避免历史、档案、转人工竞态
                    if euid:
                        with _user_lock(open_kfid, euid):
                            _process_one(msg)
                    else:
                        _process_one(msg)
                except Exception as e:
                    # 单条失败不拖垮整批；回滚认领允许企微重推后重试
                    logger.error("KF 单条消息处理异常: msgid=%s err=%s", msg.get("msgid", ""), e)
                    mid = msg.get("msgid", "")
                    if mid:
                        store.msgid_release(mid)

            next_cursor = data.get("next_cursor", "")
            if next_cursor:
                cursor = next_cursor
                store.kv_set(cursor_key, cursor, 86400)
            if not data.get("has_more") or not cursor:
                break
        else:
            # 达到页数上限仍有 has_more：cursor 已持久化，下一次回调继续
            logger.warning(
                "KF 消息分页达到上限仍有 has_more: open_kfid=%s，cursor 已保存，下一次回调继续",
                open_kfid,
            )
    except Exception as e:
        # sync_messages 重试耗尽抛 WecomApiError 等：交给可靠队列重试；
        # 直接调用方（测试/运维）会看到异常。
        logger.error("KF 消息拉取/处理失败: open_kfid=%s err=%s", open_kfid, e)
        raise


def _handle_enter_session_event(open_kfid: str, event: dict) -> bool:
    """enter_session 事件：仅对开启测肤报告联动的客服账号生效。
    返回 True 表示处理完成或明确无需处理；False 表示可重试失败，调用方应 msgid_release"""
    account = config.get_account(open_kfid)
    if not account.get("advisor_report"):
        return True
    external_userid = (event.get("external_userid") or "").strip()
    if not external_userid:
        return True
    try:
        from .advisor_report import handle_enter_session

        return bool(handle_enter_session(open_kfid, external_userid, event))
    except Exception as e:
        logger.exception("enter_session 处理异常: %s", e)
        return False






def _order_transfer_candidates(candidates: list, last: str) -> list:
    """转接候选顺序：上次服务过的客服优先，其余保持原顺序"""
    if last and last in candidates:
        return [last] + [c for c in candidates if c != last]
    return list(candidates)


def _handle_kf_transfer(open_kfid: str, external_userid: str, candidates: list, user_text: str = "") -> bool:
    """转人工：依次尝试候选客服，失败自动换下一位；全部失败提示客户。服务时间外不转人工。
    返回 True 表示转接成功；False 表示未转接（服务时间外/无候选/全员失败），
    调用方可据此决定是否保留订单流程等上下文"""
    if not is_within_service_hours():
        metrics.inc(metrics.TRANSFER_FAILED)
        _do_send_kf(
            open_kfid,
            external_userid,
            copy.transfer_off_hours_text(config.SERVICE_START_HOUR, config.SERVICE_END_HOUR),
        )
        return False

    if not candidates:
        metrics.inc(metrics.TRANSFER_FAILED)
        _do_send_kf(open_kfid, external_userid, copy.TRANSFER_UNAVAILABLE)
        return False

    from .wecom.wxkf import transfer_session, send_agent_notice

    last = store.get_last_agent(external_userid) if len(candidates) > 1 else ""
    ordered = _order_transfer_candidates(candidates, last)
    if not (last and last in candidates):
        # 无历史客服时按接待量均衡选第一位
        first = store.transfer_pick_agent(open_kfid, candidates, "")
        if first and first in ordered:
            ordered.remove(first)
            ordered.insert(0, first)

    chosen = ""
    for agent in ordered:
        try:
            ok = transfer_session(
                corpid=config.WECOM_CORP_ID,
                secret=config.WECOM_SECRET,
                open_kfid=open_kfid,
                external_userid=external_userid,
                service_userid=agent,
            )
        except Exception as e:
            logger.error(f"转接失败 agent={agent}: {e}")
            ok = False
        if ok:
            chosen = agent
            break
        logger.warning("转接失败，尝试下一位: agent=%s user=%s", agent, mask_id(external_userid))

    if not chosen:
        metrics.inc(metrics.TRANSFER_FAILED)
        _do_send_kf(open_kfid, external_userid, copy.TRANSFER_UNAVAILABLE)
        return False

    metrics.inc(metrics.TRANSFER_SUCCESS)
    store.set_last_agent(external_userid, chosen)
    # 跨批次标记: state=3 生效有延迟, TTL 内命中则机器人不再回复（消息处理开头检查）
    store.kv_set(f"kf:transferred:{open_kfid}:{external_userid}", "1", TRANSFERRED_MARK_TTL)
    # 客户侧只看到转接提示；问题上下文通过企业微信应用消息单独发给客服
    _do_send_kf(open_kfid, external_userid, copy.TRANSFER_IN_PROGRESS)
    if user_text:
        try:
            send_agent_notice(
                corpid=config.WECOM_CORP_ID,
                secret=config.WECOM_SECRET,
                agent_id=config.WECOM_AGENT_ID,
                to_user=chosen,
                content=f"【微信客服转人工】\n客户问题：{user_text.strip()[:200]}",
            )
        except Exception as e:
            logger.error(f"客服通知发送失败: {e}")
    return True


# ---- 订单查询引导流程 ----
# 流程内退出关键词：命中即退出订单查询流程
# 订单号重试上限：超过自动退出流程，提示稍后可重试
# 购买渠道输入长度上限：超长不采信，按无效输入处理
# 转人工跨批次标记 TTL（秒）：覆盖 state=3 生效前的延迟窗口
TRANSFERRED_MARK_TTL = 300


def _order_flow_key(open_kfid: str, external_userid: str) -> str:
    return f"orderflow:{open_kfid}:{external_userid}"


# 独立成号的 11 位手机号（1[3-9] 开头，前后无其他数字）：
# 兜底匹配长数字前先剔除，避免手机号被误判为订单号










# ---- 转人工判定 ----




# ---- 政策咨询（不触发转人工） ----






# 档案字段 key 白名单：中英文、数字、下划线及全/半角括号（兼容报告字段「医美经历(近3月)」），
# 最长 20 字符（防注入伪造 system 指令）
# 完整闭合的档案标记（兼容全角【档案：…】）
# 行尾/文末残留的未闭合档案标记片段（如被 max_tokens 截断的 "[档案: 肤质=干性"）。
# 只匹配以档案标记开头、无闭合括号、直到行尾的片段（字符类排除换行，不会跨行误删正常文本）


# ---- 阶段目标管理 ----
# 阶段字段由模型通过 [档案: ...] 标签写入，代码层做两道把关：日期格式校验（非法剔除）+
# 随访时间/阶段开始按阶段自动重算（见 _apply_stage_dates，模型手算日期不可靠）；
# 阶段名不做词表白名单（阶段名随客户诉求变化，写死会僵化）；晋级/留级/回退裁决归模型 prompt 规则。

# 复评触达标记 kv：key 含随访日期（review-due:{kfid}:{euid}:{随访时间}），
# 每个随访周期只注入一次复评指令——客户「没执行/不想谈」时模型不写新随访时间，
# 不去重会导致之后每条消息都被强制复评裁决，与 prompt「督促一次不再追问」冲突。
# 标记在回复成功发出后才写入（见 _process_one），AI/发送失败不占用，下个周期仍可复评。






# 阶段 N → 复评间隔天数（与 prompt 三段式节奏一致：打底 2 周 / 进阶 4 周 / 巩固 90 天）








# 企微客服 send_msg 文本上限 2048 字节（UTF-8 中文 3 字节≈680 字）；
# 预留 buffer 避免临界抖动，超过则按行边界拆分多条发送
