"""
NIHPLOD 旎柏 AI 智能客服 — FastAPI 服务入口

启动: uvicorn src.main:app --host 0.0.0.0 --port 8080
"""
import logging
import random
import traceback
import time
import xml.etree.ElementTree as ET
from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse, Response
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
from .ai.session import session_manager
from .ai.knowledge import load_knowledge

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="NIHPLOD AI 客服", docs_url=None, redoc_url=None)

crypto_wecom = WXBizMsgCrypt(
    token=config.WECOM_TOKEN,
    encoding_aes_key=config.WECOM_ENCODING_AES_KEY,
    corp_id=config.WECOM_CORP_ID,
)

crypto_wx = None
if config.WX_APP_ID:
    crypto_wx = WXBizMsgCrypt(
        token=config.WX_TOKEN,
        encoding_aes_key=config.WX_ENCODING_AES_KEY,
        corp_id=config.WX_APP_ID,
    )

_system_prompt = ""
_account_prompts: dict[str, str] = {}
_processed_msgids: set = set()
_transferred: set = set()
_rate_limit: dict[str, list[float]] = {}


def _get_system_prompt(open_kfid: str = "") -> str:
    """获取指定客服账号的系统提示词（含知识库）"""
    account = config.get_account(open_kfid)
    prompt = account.get("system_prompt", "") or config.SYSTEM_PROMPT
    knowledge_file = account.get("knowledge_file", "knowledge.txt")

    cache_key = f"{open_kfid}:{knowledge_file}"
    if cache_key in _account_prompts:
        return _account_prompts[cache_key]

    knowledge = load_knowledge(knowledge_file) if knowledge_file else ""
    full = prompt
    if knowledge:
        full += "\n\n" + knowledge

    _account_prompts[cache_key] = full
    return full


def _check_rate_limit(user_id: str, send_time: int = 0) -> bool:
    now = time.time()
    if send_time and now - send_time > 60:
        return False
    timestamps = _rate_limit.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < 60]
    _rate_limit[user_id] = timestamps
    if len(timestamps) >= 30:
        return True
    timestamps.append(send_time or now)
    return False


@app.middleware("http")
async def catch_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.error("未处理异常: %s", traceback.format_exc())
        return Response(content="", status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/callback")
async def verify_url(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    try:
        plain = crypto_wecom.verify_url(msg_signature, timestamp, nonce, echostr)
        return PlainTextResponse(content=plain.decode("utf-8"))
    except Exception as e:
        logger.error(f"URL验证失败: {e}")
        return Response(content="", status_code=200)


@app.post("/callback")
async def handle_callback(request: Request):
    body = await request.body()
    query = dict(request.query_params)
    return _handle_callback(body.decode("utf-8"), query, crypto_wecom)


@app.get("/wx-callback")
async def wx_verify_url(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    if not crypto_wx:
        return Response(content="", status_code=200)
    try:
        plain = crypto_wx.verify_url(msg_signature, timestamp, nonce, echostr)
        return PlainTextResponse(content=plain.decode("utf-8"))
    except Exception as e:
        logger.error(f"WX URL验证失败: {e}")
        return Response(content="", status_code=200)


@app.post("/wx-callback")
async def handle_wx_callback(request: Request):
    if not crypto_wx:
        return Response(content="", status_code=200)
    body = await request.body()
    query = dict(request.query_params)
    return _handle_callback(body.decode("utf-8"), query, crypto_wx)


def _handle_callback(body: str, query: dict, crypto: WXBizMsgCrypt) -> Response:
    """统一回调处理: 企微 / 公众号 / 微信客服"""
    try:
        encrypted_data = parse_encrypted_xml(body)
    except Exception as e:
        logger.error(f"XML解析失败: {e}")
        return Response(content="", status_code=200)

    try:
        plain_xml = crypto.decrypt_message(
            msg_signature=query.get("msg_signature", ""),
            timestamp=query.get("timestamp", ""),
            nonce=query.get("nonce", ""),
            encrypt=encrypted_data["encrypt"],
        )
    except Exception as e:
        logger.error(f"解密失败: {e}")
        return Response(content="", status_code=200)

    # 检测微信客服事件
    message = parse_message_xml(plain_xml)
    if isinstance(message, EventMessage) and message.event == "kf_msg_or_event":
        root = ET.fromstring(plain_xml)
        token = root.findtext("Token", "")
        open_kfid = root.findtext("OpenKfId", "")
        if token and open_kfid:
            _process_kf_messages(token, open_kfid)
        return Response(content="", status_code=200)

    # 普通消息处理
    reply_content = _process_message(message)
    if reply_content is None:
        return Response(content="", status_code=200)

    reply_xml = build_text_reply_xml(
        from_user=encrypted_data["to_user_name"],
        to_user=message.from_user,
        content=reply_content,
    )

    result = crypto.encrypt_message(reply_xml)
    encrypted_reply = build_encrypted_reply_xml(
        encrypt=result["encrypt"],
        msgsignature=result["msgsignature"],
        timestamp=result["timestamp"],
        nonce=result["nonce"],
    )
    return Response(content=encrypted_reply, media_type="application/xml")


def _process_message(message) -> str | None:
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
        history = session_manager.get_context(user_id)
        messages = [{"role": "system", "content": _get_system_prompt()}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})

        try:
            reply = chat(messages)
        except AIServiceError:
            reply = "已为您转接人工顾问，请稍候。"
        session_manager.add_message(user_id, "user", user_text)
        session_manager.add_message(user_id, "assistant", reply)
        return reply

    return None


def _process_kf_messages(token: str, open_kfid: str) -> None:
    """处理微信客服消息"""
    from .wecom.wxkf import sync_messages, send_message

    global _processed_msgids, _transferred

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
            if msg.get("msgtype") != "text":
                _do_send_kf(open_kfid, msg.get("external_userid", ""), "暂不支持图片/语音，请用文字描述您的问题。", msg.get("msgid", ""))
                continue

            user_text = msg.get("text", {}).get("content", "").strip()
            external_userid = msg.get("external_userid", "")
            msg_id = msg.get("msgid", "")
            send_time = int(msg.get("send_time", 0))

            if not user_text or not external_userid:
                continue
            if send_time and time.time() - send_time > 120:
                continue
            if _check_rate_limit(external_userid, send_time):
                _do_send_kf(open_kfid, external_userid, "您的消息发送过于频繁，请稍后再试。")
                continue
            if msg_id in _processed_msgids:
                continue

            _processed_msgids.add(msg_id)
            if len(_processed_msgids) > 500:
                _processed_msgids = set(list(_processed_msgids)[-200:])

            # 转人工
            transfer_keywords = ["转人工", "人工客服", "人工服务", "找人工"]
            if any(kw in user_text for kw in transfer_keywords if len(kw) >= 3) and external_userid not in _transferred:
                _transferred.add(external_userid)
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
                continue

            # 恢复AI
            if "智能回复" in user_text and external_userid in _transferred:
                _transferred.discard(external_userid)

            if external_userid in _transferred:
                continue

            # AI 回复
            history = session_manager.get_context(external_userid)
            messages = [{"role": "system", "content": _get_system_prompt(open_kfid)}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_text[:2000]})

        try:
            reply = chat(messages)
        except AIServiceError:
            reply = "已为您转接人工顾问，请稍候。"
            session_manager.add_message(external_userid, "user", user_text)
            session_manager.add_message(external_userid, "assistant", reply)
            _do_send_kf(open_kfid, external_userid, reply, msg_id)

        cursor = data.get("next_cursor", "")
        if not data.get("has_more") or not cursor:
            break


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
