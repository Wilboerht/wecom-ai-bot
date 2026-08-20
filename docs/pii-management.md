# PII 管理（个人信息导出/删除）

依据《个人信息保护法》的查阅权/删除权，提供按用户（`open_kfid` + `external_userid`）导出与删除全部个人数据的通道。两个内部 API 仅供服务端间调用，鉴权方式与 `/api/kf-link` 相同（`x-internal-key` 请求头，值为 `INTERNAL_API_KEY`，`hmac.compare_digest` 比对；未配置 `INTERNAL_API_KEY` 时一律 401 拒绝）。

## 数据清单（按用户键控）

| 数据 | Redis key 形态 | 类型 | TTL |
| --- | --- | --- | --- |
| 会话历史 | `sess:{open_kfid}:{external_userid}` | LIST | 30 分钟（`SESSION_TTL`） |
| 用户护肤档案（含过敏史/孕期状态等敏感字段） | `profile:{open_kfid}:{external_userid}` | STRING(JSON) | 90 天 |
| 隐私告知确认标记 | `privacy:{open_kfid}:{external_userid}` | STRING | 365 天 |
| 订单查询流程状态 | `orderflow:{open_kfid}:{external_userid}` | STRING(JSON) | 600 秒 |
| 转人工跨批次标记 | `kf:transferred:{open_kfid}:{external_userid}` | STRING | 300 秒 |
| 欢迎语补发去重（按报告 scene_param） | `welcome-fallback:{open_kfid}:{external_userid}:*` | STRING | 7 天 |
| 用户→上次接待人绑定 | `agent:{external_userid}` | STRING | 30 天 |
| 限流时间戳 | `rl:{external_userid}` | ZSET | 约 120 秒 |

明确不覆盖：

- **msgid 去重键** `kf:msgid:{msgid}` 按消息而非按用户存储，无法按用户枚举，1800 秒（30 分钟）后自然过期，不做删除。
- **应用日志**已做脱敏：不含消息内容、档案值等数据内容，仅记录动作与截断后的标识（euid 前 8 位）。
- **nginx / PM2 日志**的留存与清除需在基础设施层另行处理，不在本通道范围内。
- 未配置 Redis 时数据落进程内存：profile / privacy / agent 本就只写 Redis；会话、kv、限流在内存中，删除通道同样生效（也可重启进程兜底）。

## API 用法

导出（GET，返回结构化 JSON，无数据的部分为 `null`）：

```bash
curl -s -H "x-internal-key: $INTERNAL_API_KEY" \
  "http://127.0.0.1:8080/api/pii/export?open_kfid=wkXXX&external_userid=wmXXX"
```

删除（POST，破坏性操作，不提供 GET；返回实际删除的 key 清单）：

```bash
curl -s -X POST -H "x-internal-key: $INTERNAL_API_KEY" \
  "http://127.0.0.1:8080/api/pii/delete?open_kfid=wkXXX&external_userid=wmXXX"
```

错误码：401 未授权（key 缺失/错误/未配置），400 参数非法（白名单正则 `^[0-9A-Za-z_-]{1,128}$`），500 服务端失败。删除/导出均会写 info 级审计日志（动作 + open_kfid + euid 前 8 位 + 删除类别），不记录数据内容。

## 手动兜底流程（API 不可用时）

Redis 模式（生产）：按 key pattern 用 `redis-cli` 删除，逐类执行：

```bash
KFID=wkXXX; EUID=wmXXX
redis-cli DEL "sess:$KFID:$EUID" "profile:$KFID:$EUID" "privacy:$KFID:$EUID" \
  "orderflow:$KFID:$EUID" "kf:transferred:$KFID:$EUID" "agent:$EUID" "rl:$EUID"
# welcome-fallback 带 scene_param 后缀，按模式扫描删除（勿用 KEYS 于大库，可换 SCAN）
redis-cli --scan --pattern "welcome-fallback:$KFID:$EUID:*" | xargs -r redis-cli DEL
```

内存模式（未配置 Redis 的单机部署）：数据在进程内存中，重启服务进程即全部清除：

```bash
pm2 restart wecom-ai   # 或 systemctl restart wecom-ai（按实际部署方式）
```
