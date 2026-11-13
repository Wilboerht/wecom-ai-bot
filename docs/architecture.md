# 架构说明与演进状态

## 当前分层（Phase B/C 已落地部分）

```text
FastAPI (src/main.py)
  ├─ 回调/内部 API/短链/健康检查/中间件/lifespan
  ├─ KF 可靠队列 (src/queue_service.py)
  └─ 消息编排 (src/core.py)
       ├─ KF 拉取、去重、限流、转人工、订单流程
       ├─ 领域规则 (src/domain/)
       │    ├─ render.py       Markdown 清理、UTF-8 拆条、发送
       │    ├─ intent.py       转人工/政策咨询
       │    ├─ order_flow.py   订单号/渠道解析
       │    ├─ profile.py      档案字段、阶段状态机、复评注入
       │    ├─ schedule.py     服务时段
       │    └─ prompt.py       prompt/知识库路由/注入防护
       ├─ AI: src/ai/deepseek.py、knowledge.py
       ├─ 存储端口: src/ai/storage/
       │    ├─ base.py         Protocol
       │    ├─ memory.py       内存适配器
       │    ├─ redis.py        Redis 适配器
       │    └─ fallback.py     Redis 优先、失败降级内存
       └─ 企业微信: src/wecom/*
```

### 已落地

- `core.py` 已把渲染、意图、订单解析、档案/阶段、服务时段、prompt 组装拆到 `src/domain/`；
  `core.py` 保留编排并 re-export 兼容旧调用。
- `store.py` 通过 `SessionStore / ProfileStore / KvStore / PrivacyStore / AgentStore /
  DedupStore / RateLimiter` 端口访问存储，`store.py` 只保留兼容 API。
- KF 处理从 `BackgroundTasks` 改为 `ReliableQueue`：Redis list + processing list，
  启动恢复未确认任务；`process_kf_messages` 拉取失败会向队列抛出，由队列重试，
  超过上限进入死信；无 Redis 时降级内存队列。
- 档案字段做 key/长度/日期校验；阶段由代码状态机裁决：
  - 允许 1→1/2、2→1/2/3、3→1/2/3；
  - 禁止 1→3 跳级，非法跳变保留旧阶段并计数。
- 服务时段、禁语、知识库内容有校验测试；PII/日志/访问日志已脱敏。

### 当前部署约束

**PM2 instances=1 + `/var/www/wecom-ai-bot`**。以下仍是进程内状态，扩容前需要外置：

- 用户级串行锁、短链缓存、metrics；
- 队列 consumer（多 worker 需要分布式 consumer/可见性超时）；
- 内存降级模式下的会话/档案/隐私授权/接待人绑定（重启丢失）。

## 后续建议

### 阶段 C（规模化）

- Redis Stream / RQ / ARQ 的完整 visibility timeout、死信监控和积压告警。
- 出站消息 outbox，解决拆条部分失败重复发送。
- AI 客户端异步化、熔断、每日/每用户 token 预算；按 token 裁剪会话。
- Prometheus/结构化日志/请求 ID/队列积压指标。
- 多 worker 时使用 Redis 分布式用户锁、集中短链缓存和 metrics。
- 内容后台：产品目录/链接/禁语/服务时间在线维护，减少知识库与 `links.json` 漂移。

### 测试边界

- 单元/行为测试覆盖队列、存储端口、阶段状态机、PII、内容一致性。
- 黄金用例需真实 LLM，默认跳过；内容/提示词改动后建议单独跑一次。
- 多 worker、Redis 故障、WeCom 限频等场景仍建议在上线前做一次集成压测。
