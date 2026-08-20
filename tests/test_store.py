"""store 数据层测试（内存模式，conftest 强制 _get_redis 返回 None）"""

import threading
import time
import uuid

from src.ai import store


def _uid() -> str:
    return "test_" + uuid.uuid4().hex[:8]


class TestMsgidClaimRelease:
    def test_claim_first_time(self):
        mid = _uid()
        assert store.msgid_claim(mid) is True

    def test_claim_duplicate(self):
        mid = _uid()
        assert store.msgid_claim(mid) is True
        assert store.msgid_claim(mid) is False

    def test_release_allows_reclaim(self):
        mid = _uid()
        assert store.msgid_claim(mid) is True
        store.msgid_release(mid)
        assert store.msgid_claim(mid) is True

    def test_expired_msgid_can_reclaim(self):
        """过期 msgid 惰性清理后可重新认领"""
        mid = _uid()
        assert store.msgid_claim(mid) is True
        # 手动把过期时间拨到过去
        store._inmem_msgids[mid] = time.time() - 1
        assert store.msgid_claim(mid) is True

    def test_is_duplicate_compat(self):
        """兼容旧接口 msgid_is_duplicate 基于 claim 实现"""
        mid = _uid()
        assert store.msgid_is_duplicate(mid, "wk_any") is False
        assert store.msgid_is_duplicate(mid, "wk_any") is True


class TestKvTTL:
    def test_set_and_get(self):
        key = _uid()
        store.kv_set(key, "v1", ttl=100)
        assert store.kv_get(key) == "v1"

    def test_ttl_expiry(self):
        """内存模式 TTL 到期后 kv_get 返回空串"""
        key = _uid()
        store.kv_set(key, "v1", ttl=100)
        assert store.kv_get(key) == "v1"
        # 手动把过期时间拨到过去
        store._inmem_kv[key] = ("v1", time.time() - 1)
        assert store.kv_get(key) == ""
        # 过期项被惰性清除
        assert key not in store._inmem_kv

    def test_delete(self):
        key = _uid()
        store.kv_set(key, "v1", ttl=100)
        store.kv_delete(key)
        assert store.kv_get(key) == ""


class TestSessionAtomicAppend:
    def test_concurrent_append_no_loss(self):
        """内存模式多线程追加不丢消息（GIL 下 list.append 原子）"""
        uid = _uid()
        n_threads, n_msgs = 4, 50

        def worker(n: int):
            for i in range(n_msgs):
                store.session_add(uid, "user", f"t{n}-{i}", max_msgs=n_threads * n_msgs)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ctx = store.session_get(uid)
        assert len(ctx) == n_threads * n_msgs

    def test_append_order_and_trim(self):
        uid = _uid()
        for i in range(25):
            store.session_add(uid, "user", f"m{i}", max_msgs=20)
        ctx = store.session_get(uid)
        assert len(ctx) == 20
        # 保留最近 20 条，最早的消息被裁剪
        assert ctx[0]["content"] == "m5"
        assert ctx[-1]["content"] == "m24"
