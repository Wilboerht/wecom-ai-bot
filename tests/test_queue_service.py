"""KF 可靠队列测试（内存路径 + fakeredis 恢复路径）。"""

import threading
import time

import fakeredis

from src.queue_service import ReliableQueue


class TestMemoryQueue:
    def test_run_once_processes_job(self):
        processed = []
        q = ReliableQueue(lambda: None, processed.append, poll_timeout=1)
        q.enqueue({"token": "t", "open_kfid": "kf1"})
        assert q.run_once() is True
        assert processed == [{"token": "t", "open_kfid": "kf1"}]

    def test_worker_thread_processes_job(self):
        done = threading.Event()
        processed = []

        def processor(job):
            processed.append(job)
            done.set()

        q = ReliableQueue(lambda: None, processor, poll_timeout=1)
        q.start()
        try:
            q.enqueue({"token": "t", "open_kfid": "kf1"})
            assert done.wait(3)
        finally:
            q.stop()
        assert processed[0]["open_kfid"] == "kf1"


class TestRecover:
    def test_processing_jobs_are_requeued(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        q = ReliableQueue(lambda: client, lambda job: None)
        client.lpush(q._processing_key, '{"token":"t","open_kfid":"kf1"}')
        q.recover()
        assert client.llen(q._processing_key) == 0
        assert client.llen(q._queue_key) == 1

class TestRedisQueue:
    def test_enqueue_and_run_once(self):
        client = fakeredis.FakeRedis(decode_responses=True)
        processed = []
        q = ReliableQueue(lambda: client, processed.append, poll_timeout=1)
        q.enqueue({"token": "t", "open_kfid": "kf1"})
        assert client.llen(q._queue_key) == 1
        assert q.run_once() is True
        assert processed == [{"token": "t", "open_kfid": "kf1"}]
        assert client.llen(q._queue_key) == 0
        assert client.llen(q._processing_key) == 0

class TestRetryAndDeadLetter:
    def test_failure_retries_then_dead_letter_without_token(self):
        client = fakeredis.FakeRedis(decode_responses=True)

        def fail(_job):
            raise RuntimeError("boom")

        q = ReliableQueue(lambda: client, fail, max_attempts=2, poll_timeout=1)
        q.start()
        try:
            q.enqueue({"token": "secret-sync-token", "open_kfid": "kf1"})
            deadline = time.time() + 5
            while time.time() < deadline and client.llen(q._dead_key) == 0:
                time.sleep(0.05)
        finally:
            q.stop()
        dead = client.lrange(q._dead_key, 0, -1)
        assert dead
        assert "token" not in dead[0]
        assert "kf1" in dead[0]
