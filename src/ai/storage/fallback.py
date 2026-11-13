"""Redis 优先、失败降级内存的存储组合。"""
import logging

from .memory import MemoryStorage
from .redis import RedisStorage

logger = logging.getLogger(__name__)


class FallbackStorage:
    def __init__(self, redis_getter, memory: MemoryStorage, on_down):
        self._redis_getter = redis_getter
        self._memory = memory
        self._on_down = on_down
        self.sessions = _FallbackSessions(self)
        self.profiles = _FallbackProfiles(self)
        self.kv = _FallbackKv(self)
        self.privacy = _FallbackPrivacy(self)
        self.agents = _FallbackAgents(self)
        self.dedup = _FallbackDedup(self)
        self.ratelimit = _FallbackRateLimit(self)

    def _redis(self):
        return self._redis_getter()

    @staticmethod
    def _resolve(target, dotted: str):
        for name in dotted.split("."):
            target = getattr(target, name)
        return target

    def _call(self, redis_attr: str, memory_attr: str, *args, **kwargs):
        r = self._redis()
        if r:
            try:
                return self._resolve(RedisStorage(r), redis_attr)(*args, **kwargs)
            except Exception as e:
                self._on_down(e)
        return self._resolve(self._memory, memory_attr)(*args, **kwargs)


class _FallbackSessions:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def cleanup(self) -> None:
        self._s._memory.sessions.cleanup()

    def get(self, user_id: str) -> list[dict]:
        return self._s._call("sessions.get", "sessions.get", user_id)

    def add(self, user_id: str, role: str, content: str, max_msgs: int, ttl: int) -> None:
        self._s._call("sessions.add", "sessions.add", user_id, role, content, max_msgs, ttl)


class _FallbackProfiles:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def get(self, key: str) -> dict:
        return self._s._call("profiles.get", "profiles.get", key)

    def set(self, key: str, data: dict, ttl: int) -> None:
        self._s._call("profiles.set", "profiles.set", key, data, ttl)

    def delete(self, key: str) -> None:
        self._s._call("profiles.delete", "profiles.delete", key)


class _FallbackKv:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def get(self, key: str) -> str:
        return self._s._call("kv.get", "kv.get", key)

    def set(self, key: str, value: str, ttl: int) -> None:
        self._s._call("kv.set", "kv.set", key, value, ttl)

    def set_nx(self, key: str, value: str, ttl: int) -> bool:
        return self._s._call("kv.set_nx", "kv.set_nx", key, value, ttl)

    def delete(self, key: str) -> None:
        self._s._call("kv.delete", "kv.delete", key)


class _FallbackPrivacy:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def accepted(self, key: str) -> bool:
        return self._s._call("privacy.accepted", "privacy.accepted", key)

    def accept(self, key: str, ttl: int) -> None:
        self._s._call("privacy.accept", "privacy.accept", key, ttl)


class _FallbackAgents:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def get(self, external_userid: str) -> str:
        return self._s._call("agents.get", "agents.get", external_userid)

    def set(self, external_userid: str, agent: str, ttl: int) -> None:
        self._s._call("agents.set", "agents.set", external_userid, agent, ttl)


class _FallbackDedup:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def claim(self, msgid: str, ttl: int) -> bool:
        return self._s._call("dedup.claim", "dedup.claim", msgid, ttl)

    def release(self, msgid: str) -> None:
        self._s._call("dedup.release", "dedup.release", msgid)


class _FallbackRateLimit:
    def __init__(self, storage: FallbackStorage):
        self._s = storage

    def check(self, user_id: str, send_time: int, max_per_window: int, window_secs: int) -> bool:
        return self._s._call(
            "ratelimit.check", "ratelimit.check",
            user_id, send_time, max_per_window, window_secs,
        )
