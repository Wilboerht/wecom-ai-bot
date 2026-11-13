"""Redis 存储适配器。异常由 FallbackStorage 捕获并降级内存。"""
import json
import logging
import time
from uuid import uuid4

logger = logging.getLogger("src.ai.store.redis")


class RedisStorage:
    def __init__(self, client):
        self._r = client
        self.sessions = _RedisSessions(client)
        self.profiles = _RedisProfiles(client)
        self.kv = _RedisKv(client)
        self.privacy = _RedisPrivacy(client)
        self.agents = _RedisAgents(client)
        self.dedup = _RedisDedup(client)
        self.ratelimit = _RedisRateLimit(client)


class _RedisSessions:
    def __init__(self, client):
        self._r = client

    def cleanup(self) -> None:
        return

    def get(self, user_id: str) -> list[dict]:
        items = self._r.lrange(f"sess:{user_id}", 0, -1)
        history: list[dict] = []
        for item in items:
            try:
                parsed = json.loads(item)
            except (TypeError, json.JSONDecodeError) as e:
                logger.warning("会话记录 JSON 损坏已跳过: err=%s", e)
                continue
            if isinstance(parsed, dict):
                history.append(parsed)
        return history

    def add(self, user_id: str, role: str, content: str, max_msgs: int, ttl: int) -> None:
        key = f"sess:{user_id}"
        self._r.rpush(key, json.dumps({"role": role, "content": content}, ensure_ascii=False))
        self._r.ltrim(key, -max_msgs, -1)
        self._r.expire(key, ttl)


class _RedisProfiles:
    def __init__(self, client):
        self._r = client

    def get(self, key: str) -> dict:
        data = self._r.get(key)
        if not data:
            return {}
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            logger.error("档案 JSON 损坏已清除: err=%s", e)
            self._r.delete(key)
            return {}
        if not isinstance(parsed, dict):
            logger.error("档案类型异常已清除: type=%s", type(parsed).__name__)
            self._r.delete(key)
            return {}
        return parsed

    def set(self, key: str, data: dict, ttl: int) -> None:
        self._r.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)

    def delete(self, key: str) -> None:
        self._r.delete(key)


class _RedisKv:
    def __init__(self, client):
        self._r = client

    def get(self, key: str) -> str:
        return self._r.get(key) or ""

    def set(self, key: str, value: str, ttl: int) -> None:
        self._r.set(key, value, ex=ttl)

    def set_nx(self, key: str, value: str, ttl: int) -> bool:
        return bool(self._r.set(key, value, nx=True, ex=ttl))

    def delete(self, key: str) -> None:
        self._r.delete(key)


class _RedisPrivacy:
    def __init__(self, client):
        self._r = client

    def accepted(self, key: str) -> bool:
        return self._r.get(key) == "1"

    def accept(self, key: str, ttl: int) -> None:
        self._r.set(key, "1", ex=ttl)


class _RedisAgents:
    def __init__(self, client):
        self._r = client

    def get(self, external_userid: str) -> str:
        return self._r.get(f"agent:{external_userid}") or ""

    def set(self, external_userid: str, agent: str, ttl: int) -> None:
        self._r.set(f"agent:{external_userid}", agent, ex=ttl)


class _RedisDedup:
    def __init__(self, client):
        self._r = client

    def claim(self, msgid: str, ttl: int) -> bool:
        return bool(self._r.set(f"kf:msgid:{msgid}", 1, nx=True, ex=ttl))

    def release(self, msgid: str) -> None:
        self._r.delete(f"kf:msgid:{msgid}")


class _RedisRateLimit:
    def __init__(self, client):
        self._r = client

    def check(self, user_id: str, send_time: int, max_per_window: int, window_secs: int) -> bool:
        now = time.time()
        if send_time and now - send_time > window_secs:
            return False
        key = f"rl:{user_id}"
        cutoff = now - window_secs
        self._r.zremrangebyscore(key, "-inf", cutoff)
        count = self._r.zcard(key)
        if count >= max_per_window:
            return True
        ts = send_time or now
        self._r.zadd(key, {f"{ts}:{uuid4().hex[:8]}": ts})
        self._r.expire(key, window_secs * 2)
        return False
