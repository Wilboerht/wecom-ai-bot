"""
NIHPLOD 旎柏 AI 智能客服 — FastAPI 服务入口

启动: uvicorn src.main:app --host 0.0.0.0 --port 8080
"""
import hmac
import logging
import re
import threading
import time
import traceback
from urllib.parse import quote

from fastapi import FastAPI, Header, Request, Query, BackgroundTasks
from fastapi.responses import JSONResponse, PlainTextResponse, Response, RedirectResponse
from starlette.concurrency import run_in_threadpool
from .config import config
from .wecom.crypto import WXBizMsgCrypt
from .core import handle_callback, process_kf_messages
from .links import load_links, get_target

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="NIHPLOD AI 客服", docs_url=None, redoc_url=None)

load_links()


@app.get("/l/{code}")
async def short_link(code: str):
    """短链跳转: /l/{code} → 真实购买链接"""
    url = get_target(code)
    if not url:
        return Response(content="链接不存在", status_code=404)
    return RedirectResponse(url=url, status_code=302)

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


# ---- 客服链接生成（供 advisor 子站服务端代理调用） ----
# 官方要求：scene_param 必须拼接在「获取客服账号链接」接口返回的 url 之后，
# 返回的链接不允许修改或复制参数到其他链接使用。
# key 含 scene：scene 变化不能返回旧链接；加锁防并发首次请求重复调 add_contact_way
_kf_link_cache: dict[tuple[str, str], str] = {}
_kf_link_cache_time: dict[tuple[str, str], float] = {}
_kf_link_lock = threading.Lock()
_KF_LINK_TTL = 12 * 3600


def _get_advisor_link(open_kfid: str, scene: str) -> str:
    key = (open_kfid, scene)
    now = time.time()
    with _kf_link_lock:
        cached = _kf_link_cache.get(key)
        if cached and now - _kf_link_cache_time.get(key, 0) < _KF_LINK_TTL:
            return cached

        from .wecom.wxkf import get_account_link

        url = get_account_link(config.WECOM_CORP_ID, config.WECOM_SECRET, open_kfid, scene)
        if not url:
            raise RuntimeError("get_account_link 返回空链接")
        _kf_link_cache[key] = url
        _kf_link_cache_time[key] = now
        return url


@app.get("/api/kf-link")
async def advisor_kf_link(
    session_id: str = Query(..., alias="session_id"),
    x_internal_key: str = Header("", alias="x-internal-key"),
):
    """生成带 scene_param 的护肤顾问客服链接（x-internal-key 校验，仅供服务端间调用）"""
    if not config.INTERNAL_API_KEY or not hmac.compare_digest(x_internal_key, config.INTERNAL_API_KEY):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not re.fullmatch(r"[0-9A-Za-z\-]{8,128}", session_id):
        return JSONResponse({"error": "invalid session_id"}, status_code=400)

    if not config.KF_ADVISOR_OPEN_KFID:
        return JSONResponse({"error": "KF_ADVISOR_OPEN_KFID not configured"}, status_code=501)

    try:
        base_url = _get_advisor_link(config.KF_ADVISOR_OPEN_KFID, config.KF_SCENE)
    except Exception as e:
        logger.error("生成客服链接失败: %s", e)
        return JSONResponse({"error": "link generation failed"}, status_code=502)

    sep = "&" if "?" in base_url else "?"
    url = f"{base_url}{sep}scene_param={quote(session_id, safe='')}"
    return {"url": url}


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
    # 普通消息被动回复会同步调 AI（最长 28s+重试），移入线程池避免卡死事件循环
    return await run_in_threadpool(_route, body.decode("utf-8"), query, crypto_wx, background_tasks)


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
