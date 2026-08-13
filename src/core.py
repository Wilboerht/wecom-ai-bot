"""
公共业务逻辑 — src/main.py (FastAPI) 和 dist/main.py (SCF) 共享

所有核心处理逻辑在此模块中，入口文件只负责路由和 HTTP 适配。
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

logger = logging.getLogger(__name__)

_account_prompts: dict[str, str] = {}


# ---- 系统提示词 ----
def get_system_prompt(open_kfid: str = "") -> str:
    """获取指定客服账号的系统提示词（含知识库），结果缓存"""
    account = config.get_account(open_kfid)
    prompt = account.get("system_prompt", "") or config.SYSTEM_PROMPT
    knowledge_file = account.get("knowledge_file", "knowledge.txt")

    cache_key = f"{open_kfid}:{knowledge_file}"
    if cache_key in _account_prompts:
        return _account_prompts[cache_key]

    if knowledge_file:
        knowledge = load_knowledge(knowledge_file)
        if knowledge:
            result = prompt + "\n\n" + knowledge
            _account_prompts[cache_key] = result
            return result

    _account_prompts[cache_key] = prompt
    return prompt


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

        user_id = message.from_user
        history = store.session_get(user_id)
        messages = [{"role": "system", "content": get_system_prompt()}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        try:
            reply = chat(messages)
        except AIServiceError:
            reply = "AI服务暂时不可用，请稍后再试。"

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
def process_kf_messages(token: str, open_kfid: str) -> None:
    """拉取并处理微信客服消息，支持分页"""
    from .wecom.wxkf import sync_messages, get_session_state

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
            _do_send_kf(open_kfid, euid, "好的，帮您查询订单。请问您是在哪个平台购买的？（微信小铺、小红书、天猫、抖音商城、其它）")
            return True
        return False

    cursor = ""
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
            if msg.get("origin") != 3:
                continue

            external_userid = msg.get("external_userid", "")
            msg_id = msg.get("msgid", "")

            # 非文本消息 — 只记录元信息不存内容，仅对2分钟内的发一次引导
            if msg.get("msgtype") != "text":
                msgtype = msg.get("msgtype", "unknown")
                send_time = int(msg.get("send_time", 0))
                # 安全层：永不上报图片/语音到 Redis 或日志，只记类型
                logger.info("KF media received: type=%s user=%s (content NOT stored)", msgtype, external_userid)
                if not send_time or time.time() - send_time < 120:
                    if not store.msgid_is_duplicate(msg_id, open_kfid):
                        _do_send_kf(open_kfid, external_userid, "暂不支持图片/语音，请用文字描述您的问题。如需人工顾问，请回复「转人工」。")
                continue

            user_text = msg.get("text", {}).get("content", "").strip()
            send_time = int(msg.get("send_time", 0))

            if not user_text or not external_userid:
                continue
            if send_time and time.time() - send_time > 120:
                continue
            if check_rate_limit(external_userid, send_time):
                _do_send_kf(open_kfid, external_userid, "您的消息发送过于频繁，请稍后再试。")
                continue
            if store.msgid_is_duplicate(msg_id, open_kfid):
                continue

            # 转人工关键词（账号级配置，品牌客服含纠纷场景词）
            transfer_keywords = account.get("transfer_keywords") or ["转人工", "人工客服", "人工服务", "找人工", "人工", "真人", "转接", "有人吗"]

            # 查微信原生会话状态: 0=新会话 1=API可处理 2=排队 3=人工接待 4=已结束
            state = _get_state(external_userid)
            logger.info("KF state=%s user=%s text=%s", state, external_userid, user_text[:30])

            if not store.privacy_notice_accepted(open_kfid, external_userid):
                store.privacy_notice_accept(open_kfid, external_userid)

            if external_userid in _transferred_in_batch:
                continue

            if state == 3:
                # 人工接待中。用户发普通消息→先发提示再结束会话→下次消息AI接管
                if not any(kw in user_text for kw in transfer_keywords):
                    _do_send_kf(open_kfid, external_userid, "人工会话已结束，请重新发送您的问题。")
                    from .wecom.wxkf import transfer_session
                    transfer_session(
                        corpid=config.WECOM_CORP_ID, secret=config.WECOM_SECRET,
                        open_kfid=open_kfid, external_userid=external_userid,
                        service_state=4,
                    )
                continue

            if state in (0, 1):
                # AI 可处理
                if any(kw in user_text for kw in transfer_keywords):
                    logger.info("KF transfer triggered: user=%s text=%s", external_userid, user_text)
                    _transferred_in_batch.add(external_userid)
                    _handle_kf_transfer(open_kfid, external_userid, candidates, _merge_flow_context(external_userid, user_text))
                    continue

                # 订单查询引导流程：先问购买渠道，再问订单号，收集齐转人工
                if _handle_order_inquiry(external_userid, user_text):
                    continue

                # AI 回复
                session_key = f"{open_kfid}:{external_userid}"
                history = store.session_get(session_key)

                # 注入用户档案
                profile = store.profile_get(open_kfid, external_userid)
                profile_text = ""
                if profile:
                    parts = []
                    for k, v in profile.items():
                        parts.append(f"{k}: {v}")
                    profile_text = "【该用户的护肤档案】" + "；".join(parts) + "。依据档案个性化推荐，如档案信息变化请在回复末尾建议更新。"

                messages = [{"role": "system", "content": get_system_prompt(open_kfid) + profile_text}]
                messages.extend(history)
                messages.append({"role": "user", "content": user_text[:2000]})

                try:
                    reply = chat(messages)
                except AIServiceError:
                    _transferred_in_batch.add(external_userid)
                    _handle_kf_transfer(open_kfid, external_userid, candidates)
                    continue

                store.session_add(session_key, "user", user_text)
                store.session_add(session_key, "assistant", reply)

                # 提取并保存用户档案
                reply = _save_profile_from_reply(open_kfid, external_userid, reply)

                # 硬性清理 Markdown 语法
                reply = _strip_markdown(reply)

                _do_send_kf(open_kfid, external_userid, reply, msg_id)

            # state 2/4 不做处理，等下次回调

        cursor = data.get("next_cursor", "")
        if not data.get("has_more") or not cursor:
            break


def _pick_agent(external_userid: str, open_kfid: str, candidates: list) -> str:
    """选择转接目标：优先熟人 > 最少接待量均衡。仅1人时直接返回"""
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    last = store.get_last_agent(external_userid) if len(candidates) > 1 else ""
    return store.transfer_pick_agent(open_kfid, candidates, last)


def is_within_service_hours() -> bool:
    """检查当前是否在人工服务时间（默认周一至周六 10:00-20:00，中国时区）"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    # 周一(0)到周六(5)，周日(6)休息
    if now.weekday() >= 6:
        return False
    # 10:00 到 20:00，可通过环境变量调整
    start_hour = int(getattr(config, "SERVICE_START_HOUR", 10) or 10)
    end_hour = int(getattr(config, "SERVICE_END_HOUR", 20) or 20)
    if now.hour < start_hour or now.hour >= end_hour:
        return False
    return True


def _handle_kf_transfer(open_kfid: str, external_userid: str, candidates: list, user_text: str = "") -> None:
    """先发转接消息，再调 transfer_session 转到人工接待。服务时间外不转人工"""
    if not is_within_service_hours():
        _do_send_kf(
            open_kfid, external_userid,
            "您反馈的问题需要人工客服跟进处理，人工客服服务时间为周一至周六 10:00-20:00。建议您在该时间段内再来联系我们，客服会第一时间为您处理。",
        )
        return

    service_userid = _pick_agent(external_userid, open_kfid, candidates)
    if service_userid:
        store.set_last_agent(external_userid, service_userid)
        # 微信客服转人工后，人工侧看不到转接前由 API 处理的消息，
        # 因此把客户原话带进转接通知，保证人工客服能看到问题内容
        notice = "正在为您转接人工顾问，请稍候..."
        if user_text:
            quoted = user_text.strip()[:150]
            notice += f"\n客户问题：{quoted}"
        _do_send_kf(open_kfid, external_userid, notice)
        try:
            from .wecom.wxkf import transfer_session
            transfer_session(
                corpid=config.WECOM_CORP_ID,
                secret=config.WECOM_SECRET,
                open_kfid=open_kfid,
                external_userid=external_userid,
                service_userid=service_userid,
            )
        except Exception as e:
            logger.error(f"转接失败: {e}")
    else:
        _do_send_kf(open_kfid, external_userid, "人工客服暂时不可用，请稍后再试。")


# ---- 订单查询引导流程 ----
ORDER_FLOW_TTL = 600
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
    return text.strip()


def _save_profile_from_reply(open_kfid: str, external_userid: str, reply: str) -> str:
    """从AI回复中提取[档案: key=value, ...]标记，存入Redis，返回清理后的文本"""
    match = re.search(r'\[档案[:：]\s*([^\]]+)\]', reply)
    if match:
        fields_str = match.group(1)
        fields = {}
        for part in re.split(r'[,;，；]', fields_str):
            pair = part.split('=', 1)
            if len(pair) == 2:
                fields[pair[0].strip()] = pair[1].strip()
        if fields:
            store.profile_update(open_kfid, external_userid, fields)
            logger.info("Profile saved: user=%s fields=%s", external_userid, fields)
        # 从回复中移除档案标记，不显示给用户
        reply = reply[:match.start()] + reply[match.end():]
        reply = reply.strip()
    return reply


def _do_send_kf(open_kfid: str, to_user: str, content: str, msgid: str = "") -> None:
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
