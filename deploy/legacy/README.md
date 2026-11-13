# Legacy 部署文件（DEPRECATED）

生产环境以服务器配置文档为准：**PM2 + `/var/www/wecom-ai-bot`**。

- 使用 `deploy/server-deploy.sh` + `deploy/ecosystem.config.js`。
- 本目录的 `deploy.sh` / `wecom-ai.service` 是早期 `/opt` + systemd 方案，仅保留用于历史参考，不再作为生产路径维护。
