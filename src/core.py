"""
公共业务逻辑 — src/main.py (FastAPI) 和 dist/main.py (SCF) 共享

所有核心处理逻辑在此模块中，入口文件只负责路由和 HTTP 适配。
"""
import logging
import random
import time
import xml.etree.ElementTree as ET
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
            reply = "已为您转接人工顾问，请稍候。"

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
    from .wecom.wxkf import sync_messages

    account = config.get_account(open_kfid)
    candidates = account.get("service_userid", "") or config.WECOM_SERVICE_USERID
    if isinstance(candidates, list):
        service_userid = random.choice(candidates)
    else:
        service_userid = candidates

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

            if msg.get("msgtype") != "text":
                _handle_kf_non_text(open_kfid, external_userid, service_userid)
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

            # 转人工
            transfer_keywords = ["转人工", "人工客服", "人工服务", "找人工"]
            if any(kw in user_text for kw in transfer_keywords if len(kw) >= 3) and not store.transfer_is_active(open_kfid, external_userid):
                _handle_kf_transfer(open_kfid, external_userid, service_userid)
                continue

            # 恢复 AI
            if "智能回复" in user_text and store.transfer_is_active(open_kfid, external_userid):
                store.transfer_reset(open_kfid, external_userid)

            if store.transfer_is_active(open_kfid, external_userid):
                continue

            # AI 回复
            history = store.session_get(external_userid)
            messages = [{"role": "system", "content": get_system_prompt(open_kfid)}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_text[:2000]})

            try:
                reply = chat(messages)
            except AIServiceError:
                _handle_kf_transfer(open_kfid, external_userid, service_userid)
                continue

            store.session_add(external_userid, "user", user_text)
            store.session_add(external_userid, "assistant", reply)
            _do_send_kf(open_kfid, external_userid, reply, msg_id)

        cursor = data.get("next_cursor", "")
        if not data.get("has_more") or not cursor:
            break


def _handle_kf_transfer(open_kfid: str, external_userid: str, service_userid: str) -> None:
    """标记转人工 + 发提示 + 调转移 API"""
    store.transfer_mark(open_kfid, external_userid)
    _do_send_kf(open_kfid, external_userid, "正在为您转接人工顾问，请稍候...")
    if service_userid:
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


def _handle_kf_non_text(open_kfid: str, external_userid: str, service_userid: str) -> None:
    """非文本消息 → 自动转人工"""
    store.transfer_mark(open_kfid, external_userid)
    _do_send_kf(open_kfid, external_userid, "已为您转接人工顾问，请稍候。")
    if service_userid:
        try:
            from .wecom.wxkf import transfer_session
            transfer_session(
                corpid=config.WECOM_CORP_ID, secret=config.WECOM_SECRET,
                open_kfid=open_kfid, external_userid=external_userid,
                service_userid=service_userid,
            )
        except Exception:
            pass


def _do_send_kf(open_kfid: str, to_user: str, content: str, msgid: str = "") -> None:
    from .wecom.wxkf import send_message
    send_message(
        corpid=config.WECOM_CORP_ID,
        secret=config.WECOM_SECRET,
        to_user=to_user,
        open_kfid=open_kfid,
        msgtype="text",
        content=content,
        msgid=msgid,
    )
