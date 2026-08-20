"""
公共业务逻辑 — 消息处理的核心入口

所有核心处理逻辑在此模块中，src/main.py (FastAPI) 只负责路由和 HTTP 适配。
"""
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import config
from .wecom.crypto import WXBizMsgCrypt, SignatureError, DecryptError
from .wecom.handler import (
    parse_encrypted_xml,
    parse_message_xml,
    build_text_reply_xml,
    build_encrypted_reply_xml,
)
from .wecom.types import TextMessage, EventMessage
from .ai.deepseek import chat, AIServiceError
from .ai.knowledge import load_knowledge
from .ai import store
from .links import shorten_text, load_links

logger = logging.getLogger(__name__)

# cache_key -> (知识库内容, 系统提示词)；知识库热加载后内容变化即重建
_account_prompts: dict[str, tuple[str, str]] = {}


# ---- 系统提示词 ----
def get_system_prompt(open_kfid: str = "") -> str:
    """获取指定客服账号的系统提示词（含知识库）

    每次调用都经 load_knowledge 热加载（内部按 mtime 缓存，开销可忽略），
    知识库内容变化时重建 prompt，文件不变时命中 (知识库内容, prompt) 缓存
    """
    account = config.get_account(open_kfid)
    prompt = account.get("system_prompt", "") or config.SYSTEM_PROMPT
    knowledge_file = account.get("knowledge_file", "knowledge.txt")

    cache_key = f"{open_kfid}:{knowledge_file}"
    knowledge = load_knowledge(knowledge_file) if knowledge_file else ""

    cached = _account_prompts.get(cache_key)
    if cached and cached[0] == knowledge:
        return cached[1]

    result = prompt + "\n\n" + knowledge if knowledge else prompt
    _account_prompts[cache_key] = (knowledge, result)
    return result


# ---- 速率限制 ----
def check_rate_limit(user_id: str, send_time: int = 0) -> bool:
    return store.ratelimit_check(user_id, send_time)


# ---- 普通消息处理（企微/公众号被动回复） ----
def process_message(message) -> str | None:
    if isinstance(message, EventMessage):
        event = message.event.lower()
        if event in ("enter_agent", "subscribe"):
            return "您好，欢迎来到 NIHPLOD 旎柏，很高兴为您服务。请问有什么可以帮到您？如需人工客服，请回复「转人工」。"
        return None

    if isinstance(message, TextMessage):
        user_text = message.content.strip()
        if not user_text:
            return None
        if len(user_text) > 2000:
            user_text = user_text[:2000]

        # 微信 5s 超时重推去重：wx: 前缀避免与客服消息 msgid 冲突
        msg_id = getattr(message, "msg_id", "") or ""
        claim_key = f"wx:{msg_id}" if msg_id else ""
        if claim_key and not store.msgid_claim(claim_key):
            return None

        user_id = message.from_user
        history = store.session_get(user_id)
        messages = [{"role": "system", "content": get_system_prompt()}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        try:
            reply = chat(messages)
        except AIServiceError:
            # 兜底文案不写入历史（避免污染上下文）；回滚认领允许重推后重试
            if claim_key:
                store.msgid_release(claim_key)
            return "AI服务暂时不可用，请稍后再试。"

        store.session_add(user_id, "user", user_text)
        store.session_add(user_id, "assistant", reply)
        return reply

    return "暂不支持图片/语音，请用文字描述您的问题。"


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

    message = parse_message_xml(plain_xml)

    # 微信客服事件 — 异步处理
    if isinstance(message, EventMessage) and message.event == "kf_msg_or_event":
        root = ET.fromstring(plain_xml)
        token = root.findtext("Token", "")
        open_kfid = root.findtext("OpenKfId", "")
        if token and open_kfid:
            return {"reply_xml": None, "kf_token": token, "kf_open_kfid": open_kfid}
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
# 隐私声明：首次会话先发送，继续咨询即视为同意
PRIVACY_NOTICE_TEXT = (
    "【隐私告知】为向您提供护肤咨询服务，我们会收集您的咨询内容及肤质相关信息"
    "（其中可能包含过敏史、孕期状态等敏感个人信息），并会将咨询内容发送给第三方 AI 服务（DeepSeek）进行处理。"
    "继续咨询即视为您同意上述处理；如需人工客服，请回复「人工」。"
)


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
        """转人工时把订单查询流程里收集到的渠道/订单号并入上下文"""
        key = _order_flow_key(open_kfid, euid)
        info = store.kv_get(key)
        if not info:
            return user_text
        store.kv_delete(key)
        try:
            d = json.loads(info)
        except Exception:
            return user_text
        parts = [user_text]
        if d.get("channel"):
            parts.append(f"购买渠道：{d['channel']}")
        if d.get("order_no"):
            parts.append(f"订单号：{d['order_no']}")
        return "，".join(parts)

    def _handle_order_inquiry(euid: str, user_text: str) -> bool:
        """订单查询两步引导：先问购买渠道，再问订单号，收集齐转人工。返回 True 表示已处理"""
        key = _order_flow_key(open_kfid, euid)
        info = store.kv_get(key)
        if info:
            try:
                d = json.loads(info)
            except Exception:
                d = {}
            if d.get("step") == "orderno":
                if not _extract_order_no(user_text):
                    _do_send_kf(open_kfid, euid, "这个看起来不像是订单号，麻烦再发一下订单号哦。")
                    return True
                order_no = _extract_order_no(user_text)
                store.kv_delete(key)
                _transferred_in_batch.add(euid)
                _handle_kf_transfer(
                    open_kfid, euid, candidates,
                    f"查询订单（购买渠道：{d.get('channel', '')}，订单号：{order_no}）",
                )
                return True
            # step == channel
            channel, order_no = _split_channel_order(user_text)
            if order_no:
                store.kv_delete(key)
                _transferred_in_batch.add(euid)
                _handle_kf_transfer(
                    open_kfid, euid, candidates,
                    f"查询订单（购买渠道：{channel}，订单号：{order_no}）",
                )
                return True
            d["channel"] = channel
            d["step"] = "orderno"
            store.kv_set(key, json.dumps(d, ensure_ascii=False), ORDER_FLOW_TTL)
            _do_send_kf(open_kfid, euid, "好的，请问您的订单号是多少呢？")
            return True

        if _order_triggered(user_text):
            store.kv_set(key, json.dumps({"step": "channel"}, ensure_ascii=False), ORDER_FLOW_TTL)
            _do_send_kf(open_kfid, euid, "好的，帮您查询订单。请问您是在哪个平台购买的？（小红书、天猫国际、抖音商城、其它）")
            return True
        return False

    def _process_one(msg: dict) -> None:
        # 事件消息（origin=4）：目前只处理「用户进入会话」事件（测肤报告场景联动）
        if msg.get("msgtype") == "event":
            event = msg.get("event") or {}
            if event.get("event_type") == "enter_session":
                # 与文本消息一致做 msgid 去重：sync_msg 每次从头拉取，
                # 不去重会随后续回调反复重放该事件（重复拉报告/写档案/发欢迎语）
                msg_id = msg.get("msgid", "")
                if msg_id and store.msgid_claim(msg_id):
                    _handle_enter_session_event(open_kfid, event)
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
            logger.error("KF state 查询失败: user=%s err=%s", external_userid, e)
            store.msgid_release(msg_id)
            return

        # 转人工跨批次标记: 转接成功后 state=3 生效有延迟, 300s 内按人工接待对待
        transferred = bool(store.kv_get(f"kf:transferred:{open_kfid}:{external_userid}"))

        # 非文本消息 — 只记录元信息不存内容，仅对2分钟内的发一次引导
        if msg.get("msgtype") != "text":
            msgtype = msg.get("msgtype", "unknown")
            # 安全层：永不上报图片/语音到 Redis 或日志，只记类型
            logger.info("KF media received: type=%s user=%s (content NOT stored)", msgtype, external_userid)
            if state != 3 and not transferred and (not send_time or time.time() - send_time < 120):
                if not _do_send_kf(open_kfid, external_userid, "暂不支持图片/语音，请用文字描述您的问题。如需人工顾问，请回复「转人工」。"):
                    store.msgid_release(msg_id)
            return

        user_text = msg.get("text", {}).get("content", "").strip()
        if not user_text:
            return
        # 统一截断：发 LLM 与存历史用同一份文本
        user_text = user_text[:2000]

        if send_time and time.time() - send_time > 120:
            return
        if check_rate_limit(external_userid, send_time):
            if not _do_send_kf(open_kfid, external_userid, "您的消息发送过于频繁，请稍后再试。"):
                store.msgid_release(msg_id)
            return

        # 转人工关键词（账号级配置，品牌客服含纠纷场景词）；裸「人工」不做子串匹配，避免误伤「人工智能/人工合成」
        transfer_keywords = account.get("transfer_keywords") or ["转人工", "人工客服", "人工服务", "真人客服", "找人工", "真人", "转接", "有人吗"]

        logger.info("KF state=%s user=%s text_len=%d", state, external_userid, len(user_text))

        if external_userid in _transferred_in_batch:
            return

        if state == 3 or transferred:
            # 人工接待中（或刚转接成功、state 尚未生效）：机器人不回复、不结束会话、不打断
            return

        if state in (0, 1):
            # 隐私告知：首次会话先发送隐私声明并标记，继续咨询即视为同意
            if not store.privacy_notice_accepted(open_kfid, external_userid):
                _do_send_kf(open_kfid, external_userid, PRIVACY_NOTICE_TEXT)
                store.privacy_notice_accept(open_kfid, external_userid)

            # AI 可处理
            if _transfer_requested(user_text, transfer_keywords) and not _is_policy_question(user_text):
                logger.info("KF transfer triggered: user=%s text_len=%d", external_userid, len(user_text))
                _transferred_in_batch.add(external_userid)
                _handle_kf_transfer(open_kfid, external_userid, candidates, _merge_flow_context(external_userid, user_text))
                return

            # 订单查询引导流程：先问购买渠道，再问订单号，收集齐转人工
            if _handle_order_inquiry(external_userid, user_text):
                return

            # AI 回复
            session_key = f"{open_kfid}:{external_userid}"
            history = store.session_get(session_key)

            # 注入用户档案
            profile = store.profile_get(open_kfid, external_userid)
            profile_text = ""
            if profile:
                parts = []
                for k, v in profile.items():
                    # 档案值来自 AI 回复的 [档案:...] 标签（间接注入面）：
                    # 拼进 prompt 前去换行并截断，防止伪造 system 指令
                    v = " ".join(str(v).split())[:100]
                    parts.append(f"{k}: {v}")
                profile_text = "【该用户的护肤档案】" + "；".join(parts) + "。依据档案个性化推荐，如档案信息变化请在回复末尾建议更新。"

            messages = [{"role": "system", "content": get_system_prompt(open_kfid) + profile_text}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_text})

            try:
                reply = chat(messages)
            except AIServiceError:
                # AI 失败：回滚认领允许重推后重试，同时转人工兜底
                store.msgid_release(msg_id)
                _transferred_in_batch.add(external_userid)
                _handle_kf_transfer(open_kfid, external_userid, candidates)
                return

            # 原始回复（含档案标记）写入历史，与既往上下文格式保持一致
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
            store.session_add(session_key, "user", user_text)
            store.session_add(session_key, "assistant", history_reply)

        # state 2/4 不做处理，等下次回调

    cursor = ""
    try:
        for _ in range(5):
            data = sync_messages(
                corpid=config.WECOM_CORP_ID,
                secret=config.WECOM_SECRET,
                token=token,
                open_kfid=open_kfid,
                cursor=cursor,
                limit=100,
            )

            for msg in data.get("msg_list", []):
                try:
                    _process_one(msg)
                except Exception as e:
                    # 单条失败不拖垮整批；回滚认领允许企微重推后重试
                    logger.error("KF 单条消息处理异常: msgid=%s err=%s", msg.get("msgid", ""), e)
                    mid = msg.get("msgid", "")
                    if mid:
                        store.msgid_release(mid)

            cursor = data.get("next_cursor", "")
            if not data.get("has_more") or not cursor:
                break
        else:
            # 达到页数上限仍有 has_more：可能有消息积压被丢弃，需人工关注
            logger.warning("KF 消息分页达到上限仍有 has_more: open_kfid=%s, 可能有消息积压未处理", open_kfid)
    except Exception as e:
        # sync_messages 重试耗尽抛 WecomApiError 等：记 error 优雅结束，等下次回调
        logger.error("KF 消息拉取/处理失败: open_kfid=%s err=%s", open_kfid, e)


def _handle_enter_session_event(open_kfid: str, event: dict) -> None:
    """enter_session 事件：仅对开启测肤报告联动的客服账号生效"""
    account = config.get_account(open_kfid)
    if not account.get("advisor_report"):
        return
    external_userid = (event.get("external_userid") or "").strip()
    if not external_userid:
        return
    try:
        from .advisor_report import handle_enter_session

        handle_enter_session(open_kfid, external_userid, event)
    except Exception as e:
        logger.exception("enter_session 处理异常: %s", e)


def _pick_agent(external_userid: str, open_kfid: str, candidates: list) -> str:
    """选择转接目标：优先熟人 > 最少接待量均衡。仅1人时直接返回"""
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    last = store.get_last_agent(external_userid) if len(candidates) > 1 else ""
    return store.transfer_pick_agent(open_kfid, candidates, last)


def is_within_service_hours() -> bool:
    """检查当前是否在人工服务时间（默认周一至周五 10:00-20:00，中国时区）"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # 周一(0)到周五(4)，周六(5)周日(6)休息
    if now.weekday() >= 5:
        return False
    # 10:00 到 20:00，可通过环境变量调整
    start_hour = int(getattr(config, "SERVICE_START_HOUR", 10) or 10)
    end_hour = int(getattr(config, "SERVICE_END_HOUR", 20) or 20)
    if now.hour < start_hour or now.hour >= end_hour:
        return False
    return True


def _order_transfer_candidates(candidates: list, last: str) -> list:
    """转接候选顺序：上次服务过的客服优先，其余保持原顺序"""
    if last and last in candidates:
        return [last] + [c for c in candidates if c != last]
    return list(candidates)


def _handle_kf_transfer(open_kfid: str, external_userid: str, candidates: list, user_text: str = "") -> None:
    """转人工：依次尝试候选客服，失败自动换下一位；全部失败提示客户。服务时间外不转人工"""
    if not is_within_service_hours():
        _do_send_kf(
            open_kfid, external_userid,
            "您反馈的问题需要人工客服跟进处理，人工客服服务时间为周一至周五 10:00-20:00。建议您在该时间段内再来联系我们，客服会第一时间为您处理。",
        )
        return

    if not candidates:
        _do_send_kf(open_kfid, external_userid, "人工客服暂时不可用，请稍后再试。")
        return

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
        logger.warning("转接失败，尝试下一位: agent=%s user=%s", agent, external_userid)

    if not chosen:
        _do_send_kf(open_kfid, external_userid, "人工客服暂时不可用，请稍后再试。")
        return

    store.set_last_agent(external_userid, chosen)
    # 跨批次标记: state=3 生效有延迟, TTL 内命中则机器人不再回复（消息处理开头检查）
    store.kv_set(f"kf:transferred:{open_kfid}:{external_userid}", "1", TRANSFERRED_MARK_TTL)
    # 客户侧只看到转接提示；问题上下文通过企业微信应用消息单独发给客服
    _do_send_kf(open_kfid, external_userid, "正在为您转接人工顾问，请稍候...")
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


# ---- 订单查询引导流程 ----
ORDER_FLOW_TTL = 600
# 转人工跨批次标记 TTL（秒）：覆盖 state=3 生效前的延迟窗口
TRANSFERRED_MARK_TTL = 300
ORDER_TRIGGERS = (
    "查订单", "查询订单", "查单", "订单号", "订单进度",
    "查物流", "物流信息", "快递单号", "快递到哪", "物流到哪",
)


def _order_flow_key(open_kfid: str, external_userid: str) -> str:
    return f"orderflow:{open_kfid}:{external_userid}"


def _extract_order_no(text: str) -> str:
    """从文本提取订单号：带「订单号/单号」前缀的6-32位字母数字，或10位以上纯数字"""
    m = re.search(r"(?:订单号|单号)[:：\s]*([A-Za-z0-9\-]{6,32})", text)
    if m:
        return m.group(1)
    m = re.search(r"\d{10,}", text)
    if m:
        return m.group(0)
    return ""


def _split_channel_order(text: str) -> tuple[str, str]:
    """渠道回复里可能同时带订单号，拆成 (渠道, 订单号)"""
    order_no = _extract_order_no(text)
    if not order_no:
        return text.strip()[:50], ""
    m = re.search(r"(?:订单号|单号)[:：\s]*([A-Za-z0-9\-]{6,32})", text) or re.search(r"\d{10,}", text)
    channel = (text[:m.start()] + text[m.end():]).strip(" ,，。.")
    return channel[:50], order_no


def _order_triggered(text: str) -> bool:
    if any(t in text for t in ORDER_TRIGGERS):
        return True
    # 「帮我查询我的订单」「订单在哪看」这类短语
    p1 = re.search(r"(查询|查|看|找|跟踪).{0,8}(订单|物流|快递)", text)
    p2 = re.search(r"(订单|物流|快递).{0,8}(查询|查|看|找)", text)
    return bool(p1 or p2)


# ---- 转人工判定 ----
def _transfer_requested(text: str, keywords: list) -> bool:
    """转人工判定：关键词子串匹配；消息整体就是「人工」也触发（隐私声明里写的触发词）

    裸「人工」不参与子串匹配（即使账号配置里带了也过滤掉），
    避免误伤「人工智能」「人工合成」等正常咨询
    """
    if text.strip() == "人工":
        return True
    return any(kw in text for kw in keywords if kw.strip() != "人工")


# ---- 政策咨询（不触发转人工） ----
POLICY_QUESTION_PATTERNS = (
    "政策", "规则", "流程", "运费", "条件", "标准", "范围",
    "能不能", "可以吗", "能吗", "怎么退", "如何退", "怎么换", "如何换",
    "会过敏吗", "会不会过敏", "容易过敏吗",
)


def _is_policy_question(text: str) -> bool:
    """退换货/过敏等政策咨询不应触发转人工，让 AI 按知识库回答"""
    if not any(k in text for k in ("退", "换", "过敏")):
        return False
    if any(p in text for p in POLICY_QUESTION_PATTERNS):
        return True
    # 「能退货吗」「可以换吗」这类疑问句
    return bool(re.search(r"[退换][^。！？]{0,8}吗", text))


def _strip_markdown(text: str) -> str:
    """硬性清理 Markdown 语法，保证发给微信的是纯文本"""
    # 去掉 **加粗**、*斜体*、__下划线__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    # 去掉 # 标题符号（保留文字）
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # 去掉 - 列表符号（保留文字）
    text = re.sub(r'^[-*]\s+', '', text, flags=re.MULTILINE)
    # 去掉数字列表符号 1. 2. 3.
    text = re.sub(r'^\d+[.、]\s*', '', text, flags=re.MULTILINE)
    # 去掉反引号代码块
    text = re.sub(r'`{1,3}', '', text)
    # 压缩多余空行，保留单行换行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _save_profile_from_reply(open_kfid: str, external_userid: str, reply: str) -> str:
    """从AI回复中提取[档案: key=value, ...]标记（兼容全角【档案：…】），存入Redis，返回清理后的文本"""
    match = re.search(r'[\[【]档案[:：]\s*([^\]】]+)[\]】]', reply)
    if match:
        fields_str = match.group(1)
        fields = {}
        for part in re.split(r'[,;，；]', fields_str):
            pair = part.split('=', 1)
            if len(pair) == 2:
                fields[pair[0].strip()] = pair[1].strip()
        if fields:
            store.profile_update(open_kfid, external_userid, fields)
            logger.info("Profile saved: user=%s fields=%s", external_userid, list(fields))
        # 从回复中移除档案标记，不显示给用户
        reply = reply[:match.start()] + reply[match.end():]
        reply = reply.strip()
    return reply


def _do_send_kf(open_kfid: str, to_user: str, content: str, msgid: str = "") -> bool:
    """发送客服文本消息，返回是否成功（失败由调用方决定是否 msgid_release 回滚）"""
    from .wecom.wxkf import send_message
    ok = send_message(
        corpid=config.WECOM_CORP_ID,
        secret=config.WECOM_SECRET,
        to_user=to_user,
        open_kfid=open_kfid,
        msgtype="text",
        content=content,
        msgid=msgid,
    )
    if not ok:
        logger.error("send_msg 失败: open_kfid=%s to=%s", open_kfid, to_user)
    return ok
