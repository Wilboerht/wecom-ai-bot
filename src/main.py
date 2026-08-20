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
from .ai import store

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="NIHPLOD AI 客服", docs_url=None, redoc_url=None)

load_links()

# 未配置 Redis 时去重/限流/会话全部落在进程内存，多 worker 部署下各进程状态不一致
if not config.REDIS_URL:
    logger.warning("未配置 REDIS_URL：去重/限流/会话为进程内存模式，仅适用于单 worker 部署；多 worker 部署请配置 Redis")


@app.get("/l/{code}")
async def short_link(code: str, request: Request):
    """短链跳转: /l/{code} → 真实购买链接"""
    url = get_target(code)
    if not url:
        logger.info("短链未命中: code=%s", code)
        return Response(content="链接不存在", status_code=404)
    # 转化统计：只记 code 与 UA，不记 IP 等 PII（nginx 已做 real_ip）
    logger.info("短链点击: code=%s ua=%s", code, request.headers.get("user-agent", "-"))
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


# ---- PII 导出/删除（个人信息保护法 查阅权/删除权，仅供服务端间调用） ----
# 与 /api/kf-link 相同的 x-internal-key 鉴权；出错返回真实状态码，不走全局 200 兜底
_PII_ID_RE = re.compile(r"[0-9A-Za-z_\-]{1,128}")


def _pii_check(open_kfid: str, external_userid: str, x_internal_key: str):
    """PII 管理端点公共校验：鉴权 + 参数白名单。通过返回 None，否则返回错误响应"""
    if not config.INTERNAL_API_KEY or not hmac.compare_digest(x_internal_key, config.INTERNAL_API_KEY):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if not _PII_ID_RE.fullmatch(open_kfid) or not _PII_ID_RE.fullmatch(external_userid):
        return JSONResponse({"error": "invalid open_kfid or external_userid"}, status_code=400)
    return None


@app.get("/api/pii/export")
async def pii_export(
    open_kfid: str = Query(...),
    external_userid: str = Query(...),
    x_internal_key: str = Header("", alias="x-internal-key"),
):
    """导出用户全部数据（查阅权）"""
    err = _pii_check(open_kfid, external_userid, x_internal_key)
    if err:
        return err
    try:
        data = store.export_user_data(open_kfid, external_userid)
    except Exception as e:
        logger.error("PII 导出失败: open_kfid=%s err=%s", open_kfid, e)
        return JSONResponse({"error": "export failed"}, status_code=500)
    # 审计日志：只记标识与动作，不记数据内容；euid 只记前 8 位
    logger.info("PII 导出: open_kfid=%s euid=%s***", open_kfid, external_userid[:8])
    return data


@app.post("/api/pii/delete")
async def pii_delete(
    open_kfid: str = Query(...),
    external_userid: str = Query(...),
    x_internal_key: str = Header("", alias="x-internal-key"),
):
    """删除用户全部数据（删除权，破坏性操作仅 POST，不用 GET）"""
    err = _pii_check(open_kfid, external_userid, x_internal_key)
    if err:
        return err
    try:
        deleted = store.delete_user_data(open_kfid, external_userid)
    except Exception as e:
        logger.error("PII 删除失败: open_kfid=%s err=%s", open_kfid, e)
        return JSONResponse({"error": "delete failed"}, status_code=500)
    categories = [k.split(":")[0] for k in deleted]
    logger.info("PII 删除: open_kfid=%s euid=%s*** 删除类别=%s", open_kfid, external_userid[:8], categories)
    return {"deleted": deleted}


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
