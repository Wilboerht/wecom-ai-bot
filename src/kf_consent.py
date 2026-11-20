"""KF → 主站同意事件/身份映射的 best-effort 客户端。

主站内部接口使用 HMAC 签名；未配置 key/secret 时完全跳过，不影响 KF 主流程。
失败事件写入本地 outbox，后续事件/定时任务会重试。
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Mapping
from typing import Any

import httpx

from .ai import store
from .config import config

logger = logging.getLogger(__name__)

_TIMEOUT = 3.0
_OUTBOX_KEY = "kf-consent-outbox"
_OUTBOX_MAX = 1000
_inmem_outbox: list[str] = []


def _headers(method: str, path: str, body: bytes) -> dict[str, str] | None:
    key = config.KF_MAIN_INTERNAL_KEY
    secret = config.KF_MAIN_INTERNAL_SECRET
    if not key or not secret:
        return None

    timestamp = int(time.time())
    nonce = secrets.token_hex(16)
    body_hash = hashlib.sha256(body).hexdigest()
    payload = f"{method.upper()}|{path}||{timestamp}|{nonce}|{body_hash}"
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Internal-API-Key": key,
        "X-Internal-API-Timestamp": str(timestamp),
        "X-Internal-API-Nonce": nonce,
        "X-Internal-API-Signature": signature,
    }


def verify_main_request(headers: Mapping[str, str], path: str, body: bytes) -> bool:
    """校验主站调用 KF 内部接口的 HMAC（主站 PII 删除等）。"""
    key = config.KF_MAIN_INTERNAL_KEY
    secret = config.KF_MAIN_INTERNAL_SECRET
    if not key or not secret:
        return False

    provided_key = headers.get("x-internal-api-key", "")
    timestamp = headers.get("x-internal-api-timestamp", "")
    nonce = headers.get("x-internal-api-nonce", "")
    signature = headers.get("x-internal-api-signature", "")
    if not (
        hmac.compare_digest(provided_key, key)
        and timestamp.isdigit()
        and nonce
        and signature
    ):
        return False
    if abs(int(time.time()) - int(timestamp)) > 300:
        return False

    body_hash = hashlib.sha256(body).hexdigest()
    payload = f"POST|{path}||{timestamp}|{nonce}|{body_hash}"
    expected = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def _post(path: str, payload: dict[str, Any]) -> bool:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = _headers("POST", path, body)
    if not headers:
        return False
    try:
        response = httpx.post(
            f"{config.KF_MAIN_API_BASE}{path}",
            content=body,
            headers=headers,
            timeout=_TIMEOUT,
        )
        if response.status_code >= 300:
            logger.warning(
                "KF consent API failed: path=%s status=%s",
                path,
                response.status_code,
            )
            return False
        return True
    except Exception as exc:
        logger.warning(
            "KF consent API unavailable: path=%s error=%s",
            path,
            type(exc).__name__,
        )
        return False


def _enqueue(path: str, payload: dict[str, Any]) -> None:
    raw = json.dumps({"path": path, "payload": payload}, ensure_ascii=False, separators=(",", ":"))
    redis = store._get_redis()
    if redis:
        try:
            redis.rpush(_OUTBOX_KEY, raw)
            redis.ltrim(_OUTBOX_KEY, -_OUTBOX_MAX, -1)
            return
        except Exception as exc:
            logger.warning("KF consent outbox Redis failed: %s", type(exc).__name__)
    _inmem_outbox.append(raw)
    del _inmem_outbox[:-_OUTBOX_MAX]


def flush_outbox(limit: int = 50) -> int:
    """重试 outbox；返回成功条数。失败项保留，等下一次重试。"""
    items: list[str] = []
    redis = store._get_redis()
    if redis:
        try:
            items = list(redis.lrange(_OUTBOX_KEY, 0, limit - 1))
        except Exception as exc:
            logger.warning("KF consent outbox read failed: %s", type(exc).__name__)
    if not items:
        items = _inmem_outbox[:limit]

    sent = 0
    for raw in items:
        try:
            item = json.loads(raw)
            path = str(item["path"])
            payload = item["payload"]
            if not isinstance(payload, dict):
                raise ValueError("payload")
            ok = _post(path, payload)
        except Exception:
            ok = False
        if not ok:
            continue
        sent += 1
        if redis:
            try:
                redis.lrem(_OUTBOX_KEY, 0, raw)
            except Exception:
                pass
        else:
            try:
                _inmem_outbox.remove(raw)
            except ValueError:
                pass
    return sent


def link_identity(open_kfid: str, external_userid: str, user_id: str) -> None:
    payload = {
        "openKfid": open_kfid,
        "externalUserId": external_userid,
        "userId": user_id,
        "source": "advisor_report",
    }
    if not _post("/api/internal/kf/identity-link", payload):
        _enqueue("/api/internal/kf/identity-link", payload)


def record_event(open_kfid: str, external_userid: str, action: str, version: str) -> None:
    payload = {
        "openKfid": open_kfid,
        "externalUserId": external_userid,
        "action": action,
        "version": version,
    }
    if not _post("/api/internal/kf/consent-event", payload):
        _enqueue("/api/internal/kf/consent-event", payload)
    else:
        flush_outbox(limit=10)
