"""
Redis 持久化存储 — 会话、去重、限流、转接状态

若未配置 REDIS_URL，自动回退到内存模式（兼容单机部署）。
Redis 运行期故障时自动降级内存模式，并每 60 秒尝试重连恢复。
"""
import json
import logging
import time
from ..config import config

logger = logging.getLogger(__name__)

_redis = None
_redis_retry_at = 0.0  # Redis 故障后下次尝试重连的时间戳
REDIS_RETRY_INTERVAL = 60  # 故障后每 60 秒尝试重连一次


def _sanitize_redis_url(url: str) -> str:
    """连接串脱敏：只保留 host:port/db，去掉 userinfo，避免密码进日志"""
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        host = parts.hostname or "unknown"
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}{parts.path}"
    except Exception:
        return "<redacted>"


def _redis_down(e: Exception) -> None:
    """Redis 运行期故障：记 error 并降级内存模式，60 秒后自动重试"""
    global _redis, _redis_retry_at
    logger.error("Redis 操作失败，降级到内存模式: %s", e)
    _redis = False
    _redis_retry_at = time.time() + REDIS_RETRY_INTERVAL


def _get_redis():
    """获取 Redis 连接（懒加载 + 连接池）；故障降级内存模式并定时重连"""
    global _redis, _redis_retry_at
    if _redis:
        return _redis
    if _redis is False:
        # 故障冷却期：每 60 秒尝试一次重连，恢复后自动切回 Redis
        if time.time() < _redis_retry_at:
            return None
        _redis = None

    url = getattr(config, "REDIS_URL", "") or ""
    if not url:
        return None

    try:
        import redis
        client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3, decode_responses=True)
        client.ping()
        _redis = client
        logger.info("Redis 连接成功: %s", _sanitize_redis_url(url))
        return _redis
    except Exception as e:
        logger.warning("Redis 连接失败，使用内存模式: %s", e)
        _redis = False
        _redis_retry_at = time.time() + REDIS_RETRY_INTERVAL
        return None


# ---- 会话存储 ----
_inmem_sessions: dict[str, list[dict]] = {}
_inmem_session_ttl: dict[str, float] = {}

SESSION_PREFIX = "sess:"


def session_get(user_id: str) -> list[dict]:
    """获取会话历史"""
    r = _get_redis()
    if r:
        try:
            items = r.lrange(f"{SESSION_PREFIX}{user_id}", 0, -1)
            return [json.loads(item) for item in items]
        except Exception as e:
            _redis_down(e)

    # 内存模式
    _cleanup_sessions()
    return [dict(m) for m in _inmem_sessions.get(user_id, [])]


def session_add(user_id: str, role: str, content: str, max_msgs: int = 20) -> None:
    """追加消息到会话。媒体类内容（图片/语音）应只传元信息，不传原始二进制"""
    # 安全校验：拒绝超长内容（可能是误传的 base64 图片数据）
    if len(content) > 4096:
        logger.warning("session_add rejected: content too long (%d chars) — possible media data leak", len(content))
        content = content[:200] + "...[truncated]"
    r = _get_redis()
    if r:
        key = f"{SESSION_PREFIX}{user_id}"
        try:
            # LIST 原子追加：RPUSH + LTRIM 保留最近 max_msgs 条 + 刷新过期时间
            r.rpush(key, json.dumps({"role": role, "content": content}, ensure_ascii=False))
            r.ltrim(key, -max_msgs, -1)
            r.expire(key, config.SESSION_TTL)
            return
        except Exception as e:
            _redis_down(e)

    # 内存模式（单进程 GIL 下 list.append 原子，够用）
    if user_id not in _inmem_sessions:
        _inmem_sessions[user_id] = []
    _inmem_sessions[user_id].append({"role": role, "content": content})
    _inmem_session_ttl[user_id] = time.time()
    if len(_inmem_sessions[user_id]) > max_msgs:
        _inmem_sessions[user_id] = _inmem_sessions[user_id][-max_msgs:]


def _cleanup_sessions():
    """清理过期内存会话"""
    now = time.time()
    for uid in list(_inmem_session_ttl.keys()):
        if now - _inmem_session_ttl[uid] > config.SESSION_TTL:
            _inmem_sessions.pop(uid, None)
            _inmem_session_ttl.pop(uid, None)


# ---- 消息去重（原子 claim/release 语义） ----
_inmem_msgids: dict[str, float] = {}  # msgid -> 过期时间戳
MSGID_PREFIX = "kf:msgid:"
MSGID_TTL = 1800  # 30分钟


def msgid_claim(msgid: str) -> bool:
    """原子认领消息：True=首次认领（可处理），False=重复（应跳过）。
    处理失败时应调用 msgid_release 回滚，允许企业微信重推后再次处理。"""
    r = _get_redis()
    if r:
        try:
            return bool(r.set(f"{MSGID_PREFIX}{msgid}", 1, nx=True, ex=MSGID_TTL))
        except Exception as e:
            _redis_down(e)

    # 内存模式：dict 存过期时间，惰性清理
    _cleanup_msgids()
    if msgid in _inmem_msgids:
        return False
    _inmem_msgids[msgid] = time.time() + MSGID_TTL
    return True


def msgid_release(msgid: str) -> None:
    """回滚 msgid_claim（处理失败时调用，允许重试）"""
    r = _get_redis()
    if r:
        try:
            r.delete(f"{MSGID_PREFIX}{msgid}")
            return
        except Exception as e:
            _redis_down(e)
    _inmem_msgids.pop(msgid, None)


def _cleanup_msgids():
    """惰性清理过期的内存 msgid"""
    now = time.time()
    for mid in list(_inmem_msgids.keys()):
        if _inmem_msgids[mid] <= now:
            _inmem_msgids.pop(mid, None)


def msgid_is_duplicate(msg_id: str, open_kfid: str) -> bool:
    """[兼容旧接口] 检查消息是否已处理，未处理则标记。
    新代码应使用 msgid_claim/msgid_release（处理失败可回滚）。"""
    return not msgid_claim(msg_id)


# ---- 速率限制 ----
_inmem_ratelimit: dict[str, list[float]] = {}
RATELIMIT_PREFIX = "rl:"


def ratelimit_check(user_id: str, send_time: int = 0, max_per_window: int = 30, window_secs: int = 60) -> bool:
    """检查是否超限，返回 True 表示被限制"""
    now = time.time()
    if send_time and now - send_time > window_secs:
        return False

    r = _get_redis()
    if r:
        try:
            key = f"{RATELIMIT_PREFIX}{user_id}"
            cutoff = now - window_secs
            r.zremrangebyscore(key, "-inf", cutoff)
            count = r.zcard(key)
            if count >= max_per_window:
                return True
            r.zadd(key, {str(send_time or now): send_time or now})
            r.expire(key, window_secs * 2)
            return False
        except Exception as e:
            _redis_down(e)

    # 内存模式
    timestamps = _inmem_ratelimit.get(user_id, [])
    timestamps = [t for t in timestamps if now - t < window_secs]
    _inmem_ratelimit[user_id] = timestamps
    if len(timestamps) >= max_per_window:
        return True
    timestamps.append(send_time or now)
    return False


# ---- 轮流分配计数器 ----
_RR_PREFIX = "rr:"


def transfer_next_index(open_kfid: str, total: int) -> int:
    """获取下次转接应该分配给第几个人（轮轮流），Redis原子递增"""
    if total <= 1:
        return 0
    r = _get_redis()
    if r:
        try:
            idx = r.incr(f"{_RR_PREFIX}{open_kfid}") % total
            if idx < 0:
                idx = 0
            return idx
        except Exception as e:
            _redis_down(e)
    # 内存回退：简单轮转
    import random
    return random.randrange(total)


# ---- 接待工作量均衡（最少接待优先） ----
_LOAD_PREFIX = "load:"


def transfer_pick_agent(open_kfid: str, candidates: list, preferred: str = "") -> str:
    """选择转接目标：优先熟人 > 最少接待量均衡"""
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]

    # 优先上次服务过的同事
    if preferred and preferred in candidates:
        return preferred

    # 均衡：选当前接待量最少的
    r = _get_redis()
    if r:
        try:
            best = candidates[0]
            best_load = _get_agent_load(r, open_kfid, best)
            for agent in candidates[1:]:
                load = _get_agent_load(r, open_kfid, agent)
                if load < best_load:
                    best_load = load
                    best = agent
            r.incr(f"{_LOAD_PREFIX}{open_kfid}:{best}")
            return best
        except Exception as e:
            _redis_down(e)

    # 内存回退：简单轮转
    idx = transfer_next_index(open_kfid, len(candidates))
    return candidates[idx]


def _get_agent_load(r, open_kfid: str, agent: str) -> int:
    """获取某接待人员的当前接待量"""
    try:
        return int(r.get(f"{_LOAD_PREFIX}{open_kfid}:{agent}") or 0)
    except Exception:
        return 0


def transfer_complete(open_kfid: str, agent: str) -> None:
    """接待完成，减少该接待人员的负载计数"""
    r = _get_redis()
    if r:
        try:
            r.decr(f"{_LOAD_PREFIX}{open_kfid}:{agent}")
        except Exception as e:
            _redis_down(e)


# ---- 用户→接待人绑定（优先上次服务过的同事） ----
_AGENT_PREFIX = "agent:"


def get_last_agent(external_userid: str) -> str:
    """获取用户上次转接的接待人员"""
    r = _get_redis()
    if r:
        try:
            return r.get(f"{_AGENT_PREFIX}{external_userid}") or ""
        except Exception as e:
            _redis_down(e)
    return ""


def set_last_agent(external_userid: str, service_userid: str) -> None:
    """记录用户转接到的接待人员"""
    r = _get_redis()
    if r:
        try:
            r.set(f"{_AGENT_PREFIX}{external_userid}", service_userid, ex=86400 * 30)
        except Exception as e:
            _redis_down(e)
    # 内存模式不记录（冷启动丢失可接受）


# ---- 用户护肤档案（长期记忆） ----
_PROFILE_PREFIX = "profile:"

_profile_no_redis_warned = False


def _warn_profile_no_redis() -> None:
    """Redis 不可用时档案功能静默失效——打一次告警日志，避免刷屏"""
    global _profile_no_redis_warned
    if not _profile_no_redis_warned:
        _profile_no_redis_warned = True
        logger.warning("Redis 未配置或不可用，用户档案功能已停用（写入将被丢弃）。请配置 REDIS_URL 启用。")


def profile_get(open_kfid: str, external_userid: str) -> dict:
    """获取用户护肤档案"""
    r = _get_redis()
    if r:
        try:
            data = r.get(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}")
            return json.loads(data) if data else {}
        except Exception as e:
            _redis_down(e)
    _warn_profile_no_redis()
    return {}


def profile_update(open_kfid: str, external_userid: str, fields: dict) -> None:
    """更新用户护肤档案，合并新字段"""
    r = _get_redis()
    if r:
        try:
            current = profile_get(open_kfid, external_userid)
            current.update(fields)
            r.set(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}", json.dumps(current, ensure_ascii=False), ex=86400 * 90)
            return
        except Exception as e:
            _redis_down(e)
    _warn_profile_no_redis()
    logger.warning("档案写入被丢弃: user=%s fields=%s", external_userid, list(fields))


def profile_delete_keys(open_kfid: str, external_userid: str, keys: list) -> None:
    """删除用户护肤档案中的指定字段（用于新报告到达前清理报告管理的旧字段，防止残留过期信息）"""
    if not keys:
        return
    r = _get_redis()
    if r:
        try:
            current = profile_get(open_kfid, external_userid)
            if not current:
                return
            changed = False
            for k in keys:
                if k in current:
                    current.pop(k)
                    changed = True
            if changed:
                r.set(f"{_PROFILE_PREFIX}{open_kfid}:{external_userid}", json.dumps(current, ensure_ascii=False), ex=86400 * 90)
        except Exception as e:
            _redis_down(e)


# ---- 通用键值存储（短流程状态机） ----
_inmem_kv: dict[str, tuple[str, float]] = {}  # key -> (value, 过期时间戳)


def kv_set(key: str, value: str, ttl: int = 600) -> None:
    """写入键值，带过期时间（秒）。内存模式同样生效 TTL（惰性过期）"""
    r = _get_redis()
    if r:
        try:
            r.set(key, value, ex=ttl)
            return
        except Exception as e:
            _redis_down(e)
    _inmem_kv[key] = (value, time.time() + ttl)


def kv_get(key: str) -> str:
    """读取键值，不存在或已过期返回空串"""
    r = _get_redis()
    if r:
        try:
            return r.get(key) or ""
        except Exception as e:
            _redis_down(e)
    item = _inmem_kv.get(key)
    if not item:
        return ""
    value, expire_at = item
    if expire_at <= time.time():
        _inmem_kv.pop(key, None)
        return ""
    return value


def kv_delete(key: str) -> None:
    """删除键"""
    r = _get_redis()
    if r:
        try:
            r.delete(key)
            return
        except Exception as e:
            _redis_down(e)
    _inmem_kv.pop(key, None)


# ---- 隐私授权状态 ----
_PRIVACY_PREFIX = "privacy:"


def privacy_notice_accepted(open_kfid: str, external_userid: str) -> bool:
    """检查用户是否已确认隐私声明"""
    r = _get_redis()
    if r:
        try:
            return r.get(f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}") == "1"
        except Exception as e:
            _redis_down(e)
    return False


def privacy_notice_accept(open_kfid: str, external_userid: str) -> None:
    """记录用户已确认隐私声明"""
    r = _get_redis()
    if r:
        try:
            r.set(f"{_PRIVACY_PREFIX}{open_kfid}:{external_userid}", "1", ex=86400 * 365)
        except Exception as e:
            _redis_down(e)
