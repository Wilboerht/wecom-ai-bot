"""
公共业务逻辑 — src/main.py (FastAPI) 和 dist/main.py (SCF) 共享

所有核心处理逻辑在此模块中，入口文件只负责路由和 HTTP 适配。
"""
import logging
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

            # 非文本消息 — 只对2分钟内的发一次引导
            if msg.get("msgtype") != "text":
                send_time = int(msg.get("send_time", 0))
                if not send_time or time.time() - send_time < 120:
                    if not store.msgid_is_duplicate(msg_id, open_kfid):
                        _do_send_kf(open_kfid, external_userid, "暂不支持图片/语音，请用文字描述您的问题。")
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

            # 转人工关键词
            transfer_keywords = ["转人工", "人工客服", "人工服务", "找人工", "人工", "真人", "转接", "有人吗"]

            # 查微信原生会话状态: 0=新会话 1=API可处理 2=排队 3=人工接待 4=已结束
            state = _get_state(external_userid)
            logger.info("KF state=%s user=%s text=%s", state, external_userid, user_text[:30])

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
                    _handle_kf_transfer(open_kfid, external_userid, candidates)
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


def _handle_kf_transfer(open_kfid: str, external_userid: str, candidates: list) -> None:
    """先发转接消息，再调 transfer_session 转到人工接待"""
    service_userid = _pick_agent(external_userid, open_kfid, candidates)
    if service_userid:
        store.set_last_agent(external_userid, service_userid)
        _do_send_kf(open_kfid, external_userid, "正在为您转接人工顾问，请稍候...")
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
