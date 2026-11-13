# NIHPLOD 企业微信 AI 客服

FastAPI 服务，承接企业微信客服（KF）消息，调用 DeepSeek 生成回复，并支持：
护肤顾问测肤报告联动、用户档案/阶段管理、转人工、订单查询引导、短链、PII 导出/删除、
业务指标、Redis/内存降级。

架构上已拆分领域规则（`src/domain/`）、存储端口（`src/ai/storage/`）和 KF 可靠队列
（`src/queue_service.py`）；详见 `docs/architecture.md`。

## 运行环境

- Python >= 3.10（生产当前为 3.11）
- Redis（可选，推荐生产配置；未配置时自动使用内存模式）
- PM2（生产托管 uvicorn）
- Nginx（回调、内部 API、短链）

## 配置

复制 `.env.example` 为 `.env` 并填写真实值。`.env` 必须是 **UTF-8 无 BOM**。

关键变量：

- `WECOM_CORP_ID` / `WECOM_SECRET` / `WECOM_TOKEN` / `WECOM_ENCODING_AES_KEY`
- `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL`
- `INTERNAL_API_KEY`（与 Skin-Advisor 测肤报告联动共用）
- `REDIS_URL`（留空使用内存模式）
- `KF_ADVISOR_OPEN_KFID` / `KF_SCENE` / `KF_SEND_WELCOME`
- `SERVICE_START_HOUR` / `SERVICE_END_HOUR`
- `NON_KF_AUTO_REPLY_ENABLED`：默认 `0`。公众号/企微应用普通消息当前**不启用**客户文本 AI；
  该路径没有隐私声明、限流和微信被动回复长度保护，启用前请先补齐。

数据文件：

- `kf_accounts.json`：客服账号、欢迎语、知识库、提示词、转接人、流程开关
- `prompts/`：品牌客服/护肤顾问提示词
- `knowledge.txt` / `knowledge_skincare.txt`：知识库
- `links.json`：短链 code → 长链接

> 绿魔法护理油的三条链接目前是占位（指向 `https://nihplod.cn`），拿到真实
> 抖音/小红书/天猫国际链接后替换 `links.json` 中的 `oil-dy` / `oil-xhs` / `oil-tm`。

## 本地运行

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux
pip install -r requirements.txt
python -m uvicorn src.main:app --host 127.0.0.1 --port 8080
curl http://127.0.0.1:8080/health
```

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest -q -p no:cacheprovider
```

黄金用例会调用真实 LLM，默认跳过；需要时：

```bash
RUN_GOLDEN=1 python -m pytest tests/test_golden.py -q
```

## 生产部署（PM2 canonical）

服务器路径以运维配置为准：`/var/www/wecom-ai-bot`。

当前设计为 **PM2 instances=1**：去重/限流/会话/档案/隐私授权走存储端口，Redis 可用时持久化，
无 Redis 时降级内存；用户级锁、短链缓存和 metrics 仍在进程内。
KF 消息进入 `src/queue_service.py` 的可靠队列：Redis 可用时是 list + processing list，
启动时恢复未确认任务；无 Redis 时退化为进程内队列（重启丢任务）。
扩容到多 worker 前需要把用户锁、短链缓存、metrics 和队列 consumer 协调改为 Redis/外部存储，
否则会出现重复回复和状态不一致。

```bash
cd /var/www/wecom-ai-bot
git pull --ff-only origin main
bash deploy/server-deploy.sh
```

脚本会检查 Python 版本、`.env` BOM、`prompts/`、知识库和账号文件；Redis 不可用时只告警，
继续使用内存模式并提示重启会丢失数据。生产 Nginx 请把 `deploy/nginx-kf.conf` 中的
`log_format wecom_safe` / `access_log ... wecom_safe` 片段套到实际 `server` 块（80 或 443 回源均可），
不记录 query/UA，避免 `external_userid` / `session_id` 进入访问日志。

早期 `/opt` + systemd 方案已归档到 `deploy/legacy/`，不再作为生产路径。

## PII 与日志

- 应用日志中的 external_userid/session_id 只保留前 8 位 + `***`，不记录档案值。
- uvicorn access log 已关闭；Nginx 使用脱敏日志格式。
- PII 接口推荐用请求头传参：
  - `x-open-kfid`
  - `x-external-userid`
  - `x-session-id`（`/api/kf-link`）
- 旧 query 参数仍兼容，但 Cloudflare 可能记录 URL，建议外部调用方同步改为请求头。
- Skin-Advisor 的 `/api/advisor/kf-link` 已改为使用 `x-session-id` 请求头调用本服务。
- 详见 `docs/pii-management.md`。

## 内容维护

- 改文案/提示词/知识库后运行 `python -m pytest tests/test_content_consistency.py -q`。
- 定期检查短链：

```bash
python scripts/check_links.py
python scripts/check_links.py --network --strict
```

## 目录

```text
src/
  main.py          FastAPI 路由/中间件/lifespan
  core.py          消息编排与兼容 re-export
  queue_service.py KF 可靠队列（Redis + 内存降级）
  config.py        环境变量与账号配置
  copy.py          用户可见固定文案
  compliance.py    广告法禁语输出侧兜底
  links.py         短链
  metrics.py       业务指标
  advisor_report.py 测肤报告联动
  domain/          render/intent/order_flow/profile/schedule/prompt
  ai/              DeepSeek、知识库、存储门面
  ai/storage/      存储端口 + Redis/内存/Fallback 适配器
  wecom/           企业微信加解密/API
tests/             单元/行为/黄金用例
deploy/            PM2/Nginx/部署脚本
docs/              PII、文案规范、架构说明
scripts/           运维检查脚本
```
