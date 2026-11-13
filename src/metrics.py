"""轻量业务指标计数器（进程内存，无外部依赖）

覆盖接待量、AI 成败、转人工、限流命中、orderflow 转化等核心业务面，
出问题时不必翻日志：
- GET /metrics（x-internal-key 鉴权）随时查看
- 后台线程按 METRICS_LOG_INTERVAL 秒定期输出一行汇总日志

计数自进程启动累计；多 worker 部署下每个进程独立计数（与内存模式同理）。
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_counters: dict[str, int] = {}
_start_time = time.time()

# 业务埋点用的计数器名（集中登记，避免散落拼写错误）
KF_MSG_RECEIVED = "kf_msg_received"          # 客服侧接待文本消息数
WX_MSG_RECEIVED = "wx_msg_received"          # 公众号被动回复消息数
RATELIMIT_HIT = "ratelimit_hit"              # 限流命中次数
AI_REPLY_OK = "ai_reply_ok"                  # AI 成功回复数
AI_FAILURE = "ai_failure"                    # AI 调用失败数（重试耗尽）
TRANSFER_REQUESTED = "transfer_requested"    # 转人工诉求命中数
TRANSFER_SUCCESS = "transfer_success"        # 转接成功数
TRANSFER_FAILED = "transfer_failed"          # 转接失败数
ORDERFLOW_STARTED = "orderflow_started"      # 订单查询流程进入数
ORDERFLOW_COMPLETED = "orderflow_completed"  # 订单流程收集齐并成功转人工数
BANNED_SANITIZED = "banned_reply_sanitized"  # 输出侧禁语命中替换次数
WX_BUDGET_TIMEOUT = "wx_budget_timeout"      # 公众号路径 AI 超出 5s 响应预算返回兜底次数
STAGE_FIELD_INVALID = "stage_field_invalid"  # 阶段字段（日期格式）校验失败剔除次数
AI_TRUNCATED = "ai_reply_truncated"          # AI 回复被 max_tokens 截断次数（finish_reason=length）
AI_EMPTY_RESPONSE = "ai_empty_response"      # AI 返回空内容/被内容过滤次数
KF_QUEUE_ENQUEUED = "kf_queue_enqueued"      # KF 任务入队数
KF_QUEUE_PROCESSED = "kf_queue_processed"    # KF 任务成功处理数
KF_QUEUE_RETRIED = "kf_queue_retried"        # KF 任务失败后重新入队数
KF_QUEUE_DEAD = "kf_queue_dead"              # KF 任务进入死信数
REVIEW_TRIGGERED = "review_triggered"        # 阶段复评指令触达用户数（每随访周期一次）
# 阶段状态机流转计数（复评裁决结果的可观测性，防止「模型当裁判」黑盒）
STAGE_STARTED = "stage_started"              # 首次进入阶段（新方案）
STAGE_PROMOTED = "stage_promoted"            # 晋级（阶段 N 变大）
STAGE_HELD = "stage_held"                    # 留级/阶段内调整（阶段 N 不变）
STAGE_REGRESSED = "stage_regressed"          # 回退（阶段 N 变小，如恶化回打底期）
STAGE_TRANSITION_INVALID = "stage_transition_invalid"  # 非法阶段跳变被代码拒绝（如 1→3）


def inc(name: str, n: int = 1) -> None:
    """计数器 +n（线程安全）"""
    with _lock:
        _counters[name] = _counters.get(name, 0) + n


def snapshot(reset: bool = False) -> dict[str, int]:
    """返回计数器副本；reset=True 时清零（定期日志用增量视角时可选）"""
    with _lock:
        data = dict(_counters)
        if reset:
            _counters.clear()
    return data


def summary(reset: bool = False) -> dict:
    """计数器 + 派生比率。分母为 0 时比率为 None（前端/日志按缺省处理）"""
    c = snapshot(reset)

    def ratio(a: str, b: str):
        num, den = c.get(a, 0), c.get(b, 0)
        return round(num / den, 4) if den else None

    ai_total = c.get(AI_REPLY_OK, 0) + c.get(AI_FAILURE, 0)
    return {
        "uptime_seconds": round(time.time() - _start_time),
        "counters": c,
        "rates": {
            "ai_failure_rate": round(c.get(AI_FAILURE, 0) / ai_total, 4) if ai_total else None,
            # 转人工率/限流率以客服侧接待量为分母（公众号路径无这些分支）
            "transfer_rate": ratio(TRANSFER_SUCCESS, KF_MSG_RECEIVED),
            "ratelimit_rate": ratio(RATELIMIT_HIT, KF_MSG_RECEIVED),
            "orderflow_conversion": ratio(ORDERFLOW_COMPLETED, ORDERFLOW_STARTED),
        },
    }


def format_summary(reset: bool = False) -> str:
    """一行日志格式：指标名=值 … | 比率名=值 …"""
    s = summary(reset)
    counters = " ".join(f"{k}={v}" for k, v in sorted(s["counters"].items())) or "无计数"
    rates = " ".join(f"{k}={'-' if v is None else v}" for k, v in s["rates"].items())
    return f"uptime={s['uptime_seconds']}s {counters} | {rates}"


# ---- 定期日志线程（main.py startup 时启动）----
_started = False


def start_periodic_logger(interval_seconds: int) -> None:
    """后台 daemon 线程每 interval_seconds 秒打一行汇总；<=0 表示关闭。重复调用幂等"""
    global _started
    if _started or interval_seconds <= 0:
        return
    _started = True

    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                logger.info("业务指标: %s", format_summary())
            except Exception:  # 指标日志不允许影响主流程
                logger.exception("指标汇总日志失败")

    t = threading.Thread(target=_loop, name="metrics-logger", daemon=True)
    t.start()
    logger.info("指标定期日志已启动: 间隔 %ds", interval_seconds)
