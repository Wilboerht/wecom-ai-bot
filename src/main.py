"""
NIHPLOD 旎柏 AI 智能客服 — FastAPI 服务入口

启动: uvicorn src.main:app --host 0.0.0.0 --port 8080
"""
import logging
import threading
import traceback
from fastapi import FastAPI, Request, Query, BackgroundTasks
from fastapi.responses import PlainTextResponse, Response
from .config import config
from .wecom.crypto import WXBizMsgCrypt
from .core import handle_callback, process_kf_messages

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
async def handle_callback_route(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()
    query = dict(request.query_params)
    return _route(body.decode("utf-8"), query, crypto_wecom, background_tasks)


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
async def handle_wx_callback_route(request: Request, background_tasks: BackgroundTasks):
    if not crypto_wx:
        return Response(content="", status_code=200)
    body = await request.body()
    query = dict(request.query_params)
    return _route(body.decode("utf-8"), query, crypto_wx, background_tasks)


def _route(body: str, query: dict, crypto: WXBizMsgCrypt, bg: BackgroundTasks) -> Response:
    result = handle_callback(body, query, crypto)

    # 微信客服消息 — 异步处理，立即返回 200
    if result.get("kf_token"):
        bg.add_task(process_kf_messages, result["kf_token"], result["kf_open_kfid"])
        return Response(content="", status_code=200)

    # 普通消息 — 同步返回加密 XML
    reply_xml = result.get("reply_xml")
    if reply_xml:
        return Response(content=reply_xml, media_type="application/xml")

    return Response(content="", status_code=200)
