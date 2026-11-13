"""KF 消息可靠队列。

背景：FastAPI BackgroundTasks 只存在于进程内，进程重启会丢任务。这里用
Redis list + processing list 做最小可靠队列：
- enqueue: LPUSH 待处理列表；
- worker: BRPOPLPUSH 到 processing 列表后处理；
- 成功: LREM processing；
- 失败: 放回待处理列表（attempts+1），超过上限进 dead letter；
- 启动时把 processing 中残留任务放回待处理列表（至少一次语义）。

未配置/连不上 Redis 时退化为进程内 queue.Queue；此时重启仍会丢任务，
但不会阻塞消息接收。真正的多 worker 扩容需要把 processing list 的回收
做成多消费者可见性超时（当前 PM2 instances=1）。
"""
import json
import logging
import queue
import threading
from typing import Callable

from . import metrics

logger = logging.getLogger(__name__)

QUEUE_KEY = "kf:jobs"
PROCESSING_KEY = "kf:jobs:processing"
DEAD_KEY = "kf:jobs:dead"
MAX_ATTEMPTS = 3


class ReliableQueue:
    def __init__(
        self,
        redis_getter: Callable,
        processor: Callable[[dict], None],
        *,
        queue_key: str = QUEUE_KEY,
        processing_key: str = PROCESSING_KEY,
        dead_key: str = DEAD_KEY,
        max_attempts: int = MAX_ATTEMPTS,
        poll_timeout: int = 2,
    ):
        self._redis_getter = redis_getter
        self._processor = processor
        self._queue_key = queue_key
        self._processing_key = processing_key
        self._dead_key = dead_key
        self._max_attempts = max_attempts
        self._poll_timeout = poll_timeout
        self._memory: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _redis(self):
        return self._redis_getter()

    def enqueue(self, job: dict) -> None:
        payload = json.dumps(job, ensure_ascii=False)
        r = self._redis()
        if r:
            try:
                r.lpush(self._queue_key, payload)
                metrics.inc(metrics.KF_QUEUE_ENQUEUED)
                return
            except Exception as e:
                logger.error("KF 队列写入 Redis 失败，降级内存队列: %s", type(e).__name__)
        self._memory.put(job)
        metrics.inc(metrics.KF_QUEUE_ENQUEUED)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.recover()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="kf-queue", daemon=True)
        self._thread.start()
        logger.info("KF 可靠队列已启动")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def recover(self) -> None:
        """启动时把 processing 中未确认的任务放回待处理列表。"""
        r = self._redis()
        if not r:
            return
        try:
            pending = r.lrange(self._processing_key, 0, -1)
            for payload in pending:
                if r.lrem(self._processing_key, 1, payload):
                    r.lpush(self._queue_key, payload)
            if pending:
                logger.warning("KF 队列恢复 %d 条未确认任务", len(pending))
        except Exception as e:
            logger.error("KF 队列恢复失败: %s", type(e).__name__)

    def _dequeue(self) -> tuple[str | None, dict | None]:
        r = self._redis()
        if r:
            try:
                payload = r.brpoplpush(self._queue_key, self._processing_key, timeout=self._poll_timeout)
                if payload:
                    if isinstance(payload, bytes):
                        payload = payload.decode("utf-8")
                    return payload, json.loads(payload)
            except json.JSONDecodeError as e:
                logger.error("KF 队列任务 JSON 损坏，已丢弃: %s", e)
                self._ack(payload)
                return None, None
            except Exception as e:
                logger.error("KF 队列 Redis 读取失败，降级内存队列: %s", type(e).__name__)
        try:
            return None, self._memory.get(timeout=self._poll_timeout)
        except queue.Empty:
            return None, None

    def _ack(self, payload: str | None) -> None:
        if payload is None:
            return
        r = self._redis()
        if r:
            try:
                r.lrem(self._processing_key, 1, payload)
            except Exception as e:
                logger.error("KF 队列 ACK 失败: %s", type(e).__name__)

    def _run(self) -> None:
        while not self._stop.is_set():
            payload, job = self._dequeue()
            if job is None:
                continue
            try:
                self._processor(job)
            except Exception as e:
                attempts = int(job.get("attempts", 0)) + 1
                job["attempts"] = attempts
                logger.error(
                    "KF 队列任务处理失败: open_kfid=%s attempts=%d err=%s",
                    job.get("open_kfid", ""), attempts, type(e).__name__,
                )
                self._ack(payload)
                if attempts < self._max_attempts:
                    metrics.inc(metrics.KF_QUEUE_RETRIED)
                    self.enqueue(job)
                else:
                    metrics.inc(metrics.KF_QUEUE_DEAD)
                    self._dead_letter(job)
            else:
                metrics.inc(metrics.KF_QUEUE_PROCESSED)
                self._ack(payload)

    def _dead_letter(self, job: dict) -> None:
        # dead letter 只保留诊断字段，不保留短期 sync token
        dead = {k: v for k, v in job.items() if k != "token"}
        payload = json.dumps(dead, ensure_ascii=False)
        r = self._redis()
        if r:
            try:
                r.lpush(self._dead_key, payload)
                return
            except Exception as e:
                logger.error("KF 死信写入 Redis 失败: %s", type(e).__name__)
        logger.error("KF 任务进入死信（内存队列无持久化）: %s", payload[:200])

    def run_once(self, timeout: float = 1.0) -> bool:
        """测试/运维用：同步消费一条任务；返回是否处理了任务。"""
        payload, job = self._dequeue()
        if job is None:
            return False
        try:
            self._processor(job)
        except Exception:
            self._ack(payload)
            raise
        self._ack(payload)
        return True
