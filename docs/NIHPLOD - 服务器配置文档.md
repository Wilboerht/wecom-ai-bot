**服务器信息概览**

```
名称：nihplod
账号：root
密码：Moidas888!
公网 IP：47.116.125.52
公网带宽：5 Mbps
主私网 IP：172.22.245.232
配置：2 核（vCPU）4 GiB
操作系统：Ubuntu 24.04 64位
系统盘大小：ESSD Entry 云盘 80 GiB
服务器物理位置：华东2（上海）B
到期时间：2027年3月23日 23:59:59 （阿里云 99 计划，自动续费）
```

> ⚠️ 本文档包含生产环境敏感信息（密码、密钥等），请勿外传，建议后续迁移至密码管理工具。

---

## 一、服务器基础环境安装

### 1. 系统更新

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Node.js & PM2

```bash
# 安装 Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install nodejs -y
node -v && npm -v

# 全局安装 PM2（使用 npmmirror 加速）
npm install -g pm2 --registry=https://registry.npmmirror.com
```

### 3. PostgreSQL

```bash
sudo apt install postgresql postgresql-contrib -y
sudo -i -u postgres psql
```

在 `psql` 内执行：

```sql
CREATE USER nihplod_user WITH PASSWORD 'Moidas888!';
CREATE DATABASE nihplod_db OWNER nihplod_user;
CREATE DATABASE ba_nihplod_db OWNER nihplod_user;
CREATE DATABASE skin_advisor_db OWNER nihplod_user;
\q
```

### 4. Nginx

```bash
sudo apt install nginx -y
sudo rm /etc/nginx/sites-enabled/default
```

### 5.（可选但推荐）Redis

SSO 相关黑名单（access token 撤销、wechat_exchange_token 兑换）当前默认使用数据库持久化存储（配置 `TOKEN_BLACKLIST_STORAGE=database` 与 `RATE_LIMIT_STORAGE=database`）。单实例够用；若后续水平扩展，建议安装 Redis：

```bash
sudo apt install redis-server -y
sudo systemctl enable redis-server
```

并在官网 `.env` 中配置 `REDIS_URL=redis://localhost:6379`。

---

## 二、SSO 架构总览

NIHPLOD 官网作为 **OAuth 2.0 / OIDC 认证中心**，为接入的子项目提供统一登录。BA 系统独立运行，不接入 SSO。

```
┌─────────────────┐      OAuth 2.0 + PKCE S256      ┌──────────────────┐
│   子项目 SPA    │  ─────────authorize──────────> │                  │
│  / Next.js /    │  <────────code + state─────────│   NIHPLOD 官网    │
│  纯 JS 项目     │  ─────────token──────────────> │  (OAuth Provider)│
│                 │  <─access_token/refresh_token─│                  │
│                 │  ─────userinfo/introspect────> │                  │
└─────────────────┘                                └──────────────────┘
```

### 角色划分

| 项目 | 角色 | Client 类型 | 说明 |
|---|---|---|---|
| NIHPLOD 官网 | OAuth Provider | — | 统一签发 access_token / refresh_token / id_token |
| Skin-Advisor | Public Client | Public | 浏览器 SPA，不传 `client_secret`，走 PKCE |
| BA-NIHPLOD | 独立认证系统 | — | 自建账号体系，不接入 SSO |
| WeCom-AI-Bot | 独立服务 | — | Python FastAPI，企业微信 AI 客服，不接入 SSO |

### 认证流程（子项目）

1. 子站未登录用户跳转至 `https://nihplod.cn/login?return_to=<授权端点>`。
2. 用户在官网完成登录（账号密码、短信、微信等）。
3. 官网回调子站 `redirect_uri` 并携带 `code` 与 `state`。
4. 子站回调 handler 用 `code` 换取 `access_token` / `refresh_token` / `id_token`。
5. 子站受保护 API 通过 introspection 或本地 JWKS 验证 access_token。

### SDK 与验证包

子项目可直接从 npm 安装：

- `@nihplod/sso-sdk`：授权码 + PKCE 流程封装，含 `SsoClient`、`createCallbackRouteHandler`、React Provider 等
- `@nihplod/sso-verify`：服务端 token 验证，支持 Introspection 与 JWKS

官网源码位于 `packages/sso-sdk` 与 `packages/sso-verify`，修改后需要重新构建并发布到 npm，子项目再升级版本。

### 必配环境变量

**官网（Provider）**：`JWT_ADMIN_SECRET`、`JWT_ACCESS_SECRET`、`JWT_REFRESH_SECRET`、`JWT_ID_TOKEN_SECRET`、`JWT_LOGOUT_SECRET`、`JWT_WECHAT_BIND_SECRET`、`JWT_WECHAT_EXCHANGE_SECRET`、`TOTP_ENCRYPTION_KEY`、`INTERNAL_API_KEYS`、`RATE_LIMIT_STORAGE`、`TOKEN_BLACKLIST_STORAGE`、`OAUTH_CODE_CHALLENGE_REQUIRED`、`CORS_ALLOWED_ORIGINS`。

若启用 RS256 + JWKS，还需配置：`JWT_ACCESS_PRIVATE_KEY`、`JWT_ACCESS_PUBLIC_KEY`、`JWT_ID_TOKEN_PRIVATE_KEY`、`JWT_ID_TOKEN_PUBLIC_KEY`。

**接入 SSO 的子站（Client）**：`NEXT_PUBLIC_SSO_CLIENT_ID`、`NEXT_PUBLIC_SSO_BASE_URL`、`SSO_CLIENT_SECRET`（Confidential Client）、`NEXT_PUBLIC_SSO_REDIRECT_URI`、`SSO_SCOPES`。

**BA 系统**：独立认证，按 BA 自身文档配置 `AUTH_SECRET`、`DATABASE_URL` 等，不需要任何 `SSO_*` 变量。

> **Skin-Advisor 生产环境使用 `.env.production`**（由 `ecosystem.config.js` 中 `env_file: './.env.production'` 指定）。本地开发复制 `.env.example` 为 `.env.local`。生产服务器上不要混用 `.env`、`.env.local` 和 `.env.production`，避免优先级混乱。

> 旧版各子站共享 `JWT_SECRET` 的方案已废弃。官网保留 `JWT_SECRET` 仅用于兼容极个别旧脚本，不参与 SSO token 的签名与验证。接入 SSO 的子站登录态必须走 OAuth 2.0 流程。

---

## 三、项目部署

### 通用部署步骤

1. `git clone` / `git pull`
2. `npm ci --legacy-peer-deps`（生产环境推荐用 `ci`，避免依赖浮动）
3. 配置 `.env.production`（生产）或 `.env.local`（本地开发）
4. `npx prisma migrate deploy`（项目已有迁移文件时一律用此命令）
5. `npx prisma generate`
6. `npm run build`
7. 验证 `.next/prerender-manifest.json` 存在
8. `pm2 start/restart`

---

### A. 官网（NIHPLOD-Homepage.CN）—— SSO 认证中心

仓库：[https://github.com/Wilboerht/NIHPLOD-Homepage.CN](https://github.com/Wilboerht/NIHPLOD-Homepage.CN)

```bash
mkdir -p /var/www && cd /var/www
git clone https://github.com/Wilboerht/NIHPLOD-Homepage.CN.git
cd NIHPLOD-Homepage.CN
```

#### 1. 生成独立 JWT Secret

每类 token 必须独立。生成强随机密钥：

```bash
for name in JWT_ADMIN_SECRET JWT_ACCESS_SECRET JWT_REFRESH_SECRET JWT_WECHAT_BIND_SECRET JWT_WECHAT_EXCHANGE_SECRET JWT_ID_TOKEN_SECRET JWT_LOGOUT_SECRET TOTP_ENCRYPTION_KEY; do
  echo "$name=$(openssl rand -hex 32)"
done
```

若启用 RS256 + JWKS（推荐生产环境），运行：

```bash
npx tsx scripts/generate-oauth-rs256-keys.ts
```

#### 2. 生成内部 API 密钥

子站调用官网 `/api/v1/internal/*` 以及微信模板消息等内部接口时使用：

```bash
npx tsx scripts/generate-internal-api-keys.ts
```

#### 3. 配置环境变量

```bash
vim .env.local
```

```env
NEXT_PUBLIC_APP_URL="https://nihplod.cn"
NEXT_PUBLIC_ADVISOR_URL="https://advisor.nihplod.cn"
TRUST_PROXY=true

DATABASE_URL="postgresql://nihplod_user:Moidas888!@localhost:5432/nihplod_db?schema=public"

# 每个密钥必须独立、至少 32 字符
JWT_ADMIN_SECRET=...
JWT_ACCESS_SECRET=...
JWT_REFRESH_SECRET=...
JWT_WECHAT_BIND_SECRET=...
JWT_WECHAT_EXCHANGE_SECRET=...
JWT_ID_TOKEN_SECRET=...
JWT_LOGOUT_SECRET=...
TOTP_ENCRYPTION_KEY=...

# 生产推荐启用 RS256 + JWKS
JWT_ACCESS_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
JWT_ACCESS_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
JWT_ID_TOKEN_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
JWT_ID_TOKEN_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"

# 分布式部署必填：使用数据库持久化黑名单与限流
RATE_LIMIT_STORAGE=database
TOKEN_BLACKLIST_STORAGE=database

# 授权端强制 PKCE（推荐 true）
OAUTH_CODE_CHALLENGE_REQUIRED=true

# 允许的跨域来源，多个用逗号分隔
CORS_ALLOWED_ORIGINS=https://advisor.nihplod.cn,https://ba.nihplod.cn

# 内部 API 密钥
INTERNAL_API_KEYS='[{"project":"advisor","key":"...","secret":"..."}]'

# 其他原有配置（微信、支付、OSS、SMTP 等）按需补充
```

#### 4. 数据库迁移与种子

```bash
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma migrate deploy
npx prisma generate
npm run db:seed
```

> **迁移原则**：项目已有 `prisma/migrations/` 后，生产环境一律使用 `npx prisma migrate deploy`。`npx prisma db push` 仅用于本地开发初期、尚未创建迁移文件时。**切勿在同一数据库上混用 `db push` 与 `migrate deploy`**，否则后续迁移会出现 `already exists` 冲突。
>
> 如果此前误用了 `db push`，冲突报错为 `column/type/relation ... already exists`，解决方式是用 `npx prisma migrate resolve --applied <迁移名>` 把冲突迁移标记为已应用，再重新 `migrate deploy`。

#### 5. 构建与启动

```bash
npm run build

# 确认构建产物完整
ls -lh .next/prerender-manifest.json

pm2 start npm --name "nihplod-app" -- start
pm2 save
pm2 startup
```

#### 6. Nginx 配置

由于线上经过 Cloudflare 代理，Nginx 收到的 `remote_addr` 可能是 Cloudflare IP。必须配置 `set_real_ip_from` 与 `real_ip_header`，并把真实协议/域名透传给 Next.js。

`/etc/nginx/sites-available/nihplod`：

```nginx
server {
    listen 80;
    server_name nihplod.cn www.nihplod.cn;

    # Cloudflare 真实 IP（IPv4 示例，IPv6 与完整 CIDR 请按 Cloudflare 官方列表补充）
    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 104.16.0.0/13;
    set_real_ip_from 104.24.0.0/14;
    set_real_ip_from 172.64.0.0/13;
    set_real_ip_from 131.0.72.0/22;
    real_ip_header CF-Connecting-IP;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;

        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        client_max_body_size 50M;
    }

    location /_next/static/ {
        alias /var/www/NIHPLOD-Homepage.CN/.next/static/;
        expires 365d;
        access_log off;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/nihplod /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

### B. BA 系统（BA-NIHPLOD）—— 独立认证

仓库：[https://github.com/Wilboerht/BA-NIHPLOD](https://github.com/Wilboerht/BA-NIHPLOD)

> BA 系统使用自建账号体系，不接入 NIHPLOD SSO，也不需要 `@nihplod/sso-sdk`。按 BA 自身认证逻辑部署即可。

```bash
cd /var/www
git clone https://ghfast.top/https://github.com/Wilboerht/BA-NIHPLOD.git
cd BA-NIHPLOD
```

#### 1. 配置环境变量

```bash
cat > .env.local << 'EOF'
DATABASE_URL="postgresql://nihplod_user:Moidas888!@localhost:5432/ba_nihplod_db?schema=public"
AUTH_SECRET="你的强随机密钥至少32位"
NEXT_PUBLIC_BASE_URL="https://ba.nihplod.cn"

# BA 其他自有配置按需补充
EOF
```

> BA 系统**不需要**任何 `SSO_*` 环境变量，也不需要在官网后台注册 OAuth Client。

#### 2. 安装依赖与数据库

```bash
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma db push
npx prisma generate

# BA 项目包含 Prisma 不管理的 RLS/触发器/函数/索引，每次 schema.sql 有变更时都要执行
sudo -u postgres psql -d ba_nihplod_db -f db/schema.sql
```

> BA 数据库必须**同时执行** `npx prisma db push` 与 `sudo -u postgres psql -d ba_nihplod_db -f db/schema.sql`，否则会出现关联记录丢失或搜索索引缺失。

#### 3. 构建与启动

```bash
npm run build
pm2 start npm --name "ba-nihplod-app" -- start -- -p 3001
pm2 save
pm2 startup
```

#### 4. Nginx 配置

`/etc/nginx/sites-available/ba-nihplod`：

```nginx
server {
    listen 80;
    server_name ba.nihplod.cn www.ba.nihplod.cn;

    set_real_ip_from 173.245.48.0/20;
    # ... 其他 Cloudflare CIDR ...
    real_ip_header CF-Connecting-IP;

    location /uploads/ {
        alias /var/www/BA-NIHPLOD/public/uploads/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
        access_log off;
    }

    location / {
        proxy_pass http://localhost:3001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        client_max_body_size 50M;
    }

    location /_next/static/ {
        alias /var/www/BA-NIHPLOD/.next/static/;
        expires 365d;
        access_log off;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/ba-nihplod /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

### C. Skin-Advisor（浏览器端 Public Client）

仓库：[https://github.com/Wilboerht/Skin-Advisor](https://github.com/Wilboerht/Skin-Advisor)

```bash
cd /var/www
git clone https://github.com/Wilboerht/Skin-Advisor.git
cd Skin-Advisor
```

#### 1. 在官网后台注册 OAuth Client

- `client_id`：例如 `skin-advisor`
- `name`：Skin Advisor
- `redirect_uris`：`https://advisor.nihplod.cn/api/auth/callback`
- `scopes`：`openid profile phone`
- Client Type：Public
- 保存后**没有 client_secret**

#### 2. 配置环境变量

```bash
cat > .env.production << 'EOF'
# ===== 数据库 =====
DATABASE_URL="postgresql://nihplod_user:Moidas888!@localhost:5432/skin_advisor_db?schema=public"

# ===== AI 服务 =====
AI_PROVIDER="qwen"
AI_VISION_PROVIDER="qwen"

# ===== SSO 配置（Public Client）=====
NEXT_PUBLIC_SITE_URL="https://advisor.nihplod.cn"
NEXT_PUBLIC_BASE_URL="https://advisor.nihplod.cn"
NEXT_PUBLIC_SSO_CLIENT_ID="skin-advisor"
NEXT_PUBLIC_SSO_BASE_URL="https://nihplod.cn"
NEXT_PUBLIC_SSO_REDIRECT_URI="https://advisor.nihplod.cn/api/auth/callback"
NEXT_PUBLIC_SSO_SCOPES="openid profile phone"

# ===== API Keys（保留与官网内部接口通信）=====
QWEN_API_KEY="..."
WANXIANG_API_KEY="..."
VOLC_ACCESSKEY=""
VOLC_SECRETKEY=""

# ===== 管理员密钥 =====
ADMIN_SECRET="你的强随机密钥"
IP_HASH_SALT="你的强随机密钥"
CRON_SECRET="你的强随机密钥"
EOF
```

#### 3. 代码改造

安装 SDK：

```bash
npm install @nihplod/sso-sdk
```

Next.js 回调 handler：

```typescript
import { createCallbackRouteHandler } from "@nihplod/sso-sdk/next";

export const GET = createCallbackRouteHandler({
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  ssoBaseUrl: process.env.NEXT_PUBLIC_SSO_BASE_URL!,
  redirectUri: process.env.NEXT_PUBLIC_SSO_REDIRECT_URI!,
});
```

React 登录按钮：

```tsx
import { SsoClient } from "@nihplod/sso-sdk";

const sso = new SsoClient({
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  redirectUri: process.env.NEXT_PUBLIC_SSO_REDIRECT_URI!,
  ssoBaseUrl: process.env.NEXT_PUBLIC_SSO_BASE_URL!,
  scopes: process.env.NEXT_PUBLIC_SSO_SCOPES!,
});

<button onClick={() => sso.login()}>使用 NIHPLOD 账号登录</button>
```

服务端 API 验证 token：

```typescript
import { createTokenVerifier } from "@nihplod/sso-verify";

const verifier = createTokenVerifier({
  introspectionEndpoint: "https://nihplod.cn/api/oauth/introspect",
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  audience: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  issuer: "https://nihplod.cn",
});
```

#### 4. 安装、构建、部署

```bash
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com

# 准备生产环境：加载 .env.production + 验证变量 + prisma generate + migrate deploy
node scripts/prepare-production.js

# 构建（postbuild 会自动复制 public/ 和 .next/static/ 到 standalone，
# 并自动创建 .next/standalone/public/uploads → public/uploads 符号链接）
npm run build
```

> **说明**：
> - `npx prisma db push` 已替换为 `node scripts/prepare-production.js` 中的 `prisma migrate deploy`。
> - `rsync` 复制 `public/` 到 standalone 的步骤已删除，因为 `postbuild` 自动完成。
> - **上传目录结构**：临时文件（游客上传/用户头像）存于 `public/uploads/` 根目录，受 30 天清理策略约束；展品图等永久资产存于 `public/uploads/products/` 子目录，豁免清理。
> - `.next/standalone/public/uploads` 符号链接已由 `postbuild` 自动创建，指向项目根 `public/uploads/`，确保 standalone 运行时新上传文件写入同一持久化位置。
> - Nginx `/uploads/` alias 指向 `/var/www/Skin-Advisor/public/uploads/`，由 Nginx 直接提供静态文件，不经过 Node.js。

PM2 配置：

```bash
cat > ecosystem.config.js << 'EOF'
module.exports = {
    apps: [{
        name: 'skin-advisor',
        script: 'node .next/standalone/server.js',
        instances: 1,
        exec_mode: 'fork',
        env: {
            NODE_ENV: 'production',
            PORT: 3002,
        },
        log_file: './logs/combined.log',
        out_file: './logs/out.log',
        error_file: './logs/error.log',
        log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
        merge_logs: true,
        autorestart: true,
        max_restarts: 10,
        min_uptime: '10s',
        max_memory_restart: '1G',
        kill_timeout: 5000,
        listen_timeout: 10000,
        env_file: './.env.production',
    }]
};
EOF

pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

#### 5. Crontab 配置

```bash
sudo crontab -e
*/30 * * * * cd /var/www/Skin-Advisor && /usr/bin/node scripts/run-cron.js /api/cron/data-cleanup >> /var/log/myskin-cron.log 2>&1
sudo touch /var/log/myskin-cron.log
sudo chmod 644 /var/log/myskin-cron.log
```

#### 6. Nginx 配置

`/etc/nginx/sites-available/skin-advisor`：

```nginx
server {
    listen 80;
    server_name advisor.nihplod.cn;

    set_real_ip_from 173.245.48.0/20;
    # ... 其他 Cloudflare CIDR ...
    real_ip_header CF-Connecting-IP;

    location /uploads/ {
        alias /var/www/Skin-Advisor/public/uploads/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000";
        access_log off;
    }

    location / {
        proxy_pass http://localhost:3002;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        client_max_body_size 50M;
        proxy_read_timeout 120s;
        proxy_connect_timeout 120s;
        proxy_send_timeout 120s;
    }

    location /_next/static/ {
        alias /var/www/Skin-Advisor/.next/static/;
        expires 365d;
        access_log off;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/skin-advisor /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

---

### D. WeCom-AI-Bot（企业微信 AI 客服）—— 独立服务

仓库：[https://github.com/Wilboerht/wecom-ai-bot](https://github.com/Wilboerht/wecom-ai-bot)

> Python FastAPI 项目，不接入 SSO。通过企业微信回调 + DeepSeek API 实现 C 端微信客服 AI 自动回复，支持转人工。

```bash
cd /var/www
git clone https://github.com/Wilboerht/wecom-ai-bot.git
cd wecom-ai-bot
```

#### 1. 配置环境变量

```bash
cat > .env << 'EOF'
WECOM_CORP_ID=ww27c38ff0313efbec
WECOM_AGENT_ID=1000004
WECOM_SECRET=daF2mDuIhzDKz0n9f6XRSGoa0EofhCLVpzPl62JrSxA
WECOM_TOKEN=IpsCwAtiShzlOLc
WECOM_ENCODING_AES_KEY=aJ8XGEkHfmIvAvCHtcJxz3ldzLwGOU7WhqMDBg0OrVW
DEEPSEEK_API_KEY=sk-51ad4ce5b4874ac38e635dfd83afe2b5
DEEPSEEK_MODEL=deepseek-chat
SYSTEM_PROMPT=你是NIHPLOD旎柏的客服顾问。保持专业、尊重、简洁的语气，用词体面。
SESSION_TTL=1800
LOG_LEVEL=INFO
WECOM_SERVICE_USERID=WangHongKun
EOF
```

> ⚠️ `.env` 已被 `.gitignore` 排除，不会提交到仓库。首次部署或 `.env` 丢失时需手动重建。

#### 2. 安装依赖（虚拟环境）

```bash
# 安装 venv（新服务器首次）
apt-get install -y python3-full python3-venv

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
mkdir -p logs
deactivate
```

#### 3. 启动（PM2）

```bash
pm2 start deploy/ecosystem.config.js
pm2 save
```

#### 4. Nginx 配置

`/etc/nginx/sites-available/wecom-ai`（项目内 `deploy/nginx-kf.conf`）：

```nginx
server {
    listen 80;
    server_name kf.nihplod.cn;

    set_real_ip_from 173.245.48.0/20;
    # ... 其他 Cloudflare CIDR ...
    real_ip_header CF-Connecting-IP;

    location /callback {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 30s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }

    location / {
        return 404;
    }
}
```

启用：

```bash
sudo cp /var/www/wecom-ai-bot/deploy/nginx-kf.conf /etc/nginx/sites-available/wecom-ai
sudo ln -s /etc/nginx/sites-available/wecom-ai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

#### 5. Cloudflare DNS

| 类型 | 名称 | 内容 | 代理 |
|------|------|------|------|
| A | `kf` | `47.116.125.52` | ✅ 橙色云朵 |

> 需要关闭 Cloudflare → Security → Browser Integrity Check，否则企微回调会被 1010 拦截。

#### 6. 企业微信后台配置

| 设置项 | 值 |
|--------|-----|
| 回调 URL | `https://kf.nihplod.cn/callback` |
| 可信 IP | `47.116.125.52` |

---

## 四、OAuth Client 注册与管理

### 方式一：官网管理后台（推荐）

1. 登录 `https://nihplod.cn/admin`
2. 左侧菜单 → **SSO 客户端管理**（`/admin/oauth-clients`）
3. 点击「新建 Client」
4. 填写：
   - `client_id`：唯一英文标识，如 `skin-advisor`
   - `name`：显示名称
   - `redirect_uris`：回调地址，必须精确匹配（包括协议、域名、路径）
   - `scopes`：该 client 允许申请的 scope，如 `openid profile phone`
   - `isActive`：是否启用
   - `backchannelLogoutUri`：（可选）接收 backchannel logout 的地址
5. 选择 Client Type：
   - **Public**：浏览器 SPA、移动端、桌面端（无 client_secret）
   - **Confidential**：Next.js BFF、后端服务（有 client_secret）
6. 保存后，Confidential Client 会显示**明文 client_secret**（仅此一次），立即复制到子站 `.env.production`。

### 方式二：命令行直接插入（应急）

```bash
cd /var/www/NIHPLOD-Homepage.CN
node -e "
const { PrismaClient } = require('./src/generated/prisma/client.js');
const prisma = new PrismaClient({ datasourceUrl: process.env.DATABASE_URL });
const bcrypt = require('bcryptjs');
(async () => {
  const secret = 'plain-secret-you-must-copy';
  const client = await prisma.oAuthClient.create({
    data: {
      clientId: 'skin-advisor',
      name: 'Skin Advisor',
      clientSecret: await bcrypt.hash(secret, 12),
      redirectUris: ['https://advisor.nihplod.cn/api/auth/callback'],
      scopes: ['openid', 'profile', 'phone'],
      isActive: true,
    },
  });
  console.log('client_id:', client.clientId);
  console.log('plain_secret:', secret);
  await prisma.$disconnect();
})();
"
```

> BA 系统不接入 SSO，不需要注册 OAuth Client。

---

## 五、项目更新流程

### 官网

```bash
cd /var/www/NIHPLOD-Homepage.CN
git pull origin master
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma migrate deploy     # schema 有变更时应用迁移
npx prisma generate
npm run build
pm2 restart nihplod-app
```

### BA

```bash
cd /var/www/BA-NIHPLOD
git pull origin master
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma migrate deploy
npx prisma generate

# 仅当 db/schema.sql 或 db/migrate-*.sql 有变更时执行
sudo -u postgres psql -d ba_nihplod_db -f /var/www/BA-NIHPLOD/db/schema.sql

npm run build
pm2 restart ba-nihplod-app
```

> BA 更新必须**同时**执行 `npx prisma migrate deploy` 与 `sudo -u postgres psql -d ba_nihplod_db -f db/schema.sql`，不可只执行其中一项。

### Skin-Advisor

```bash
cd /var/www/Skin-Advisor

# 拉取最新代码（不要 reset package-lock.json，生产用 npm ci）
git pull origin main
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
node scripts/prepare-production.js
npm run build

# ⚠️ 重要：cron 定时任务每 30 分钟清理 public/uploads/ 根目录中 mtime > 30 天的文件
# 展品图必须存放在 products/ 子目录下（豁免清理），否则会被误删
# 如果此前有展品图遗留在根目录，执行迁移：
#   npx -y tsx scripts/migrate-product-uploads.ts

# 重启 PM2 并重新加载环境变量
pm2 restart skin-advisor --update-env

# 健康检查
sleep 5 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3002
```

> **说明**：
> - `postbuild` 自动复制 `public/`（排除 `uploads`）到 `standalone`，并创建 `uploads` 符号链接。
> - 生产环境 uploads 由 Nginx `location /uploads/` 直接 alias 到 `/var/www/Skin-Advisor/public/uploads/`。
> - 如果更新涉及迁移脚本（如 `scripts/migrate-product-uploads.ts`），必须在 `npm run build` 之前先执行。

---

### WeCom-AI-Bot

```bash
cd /var/www/wecom-ai-bot
git pull origin main
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
deactivate
pm2 restart wecom-ai
```

### SSO SDK 更新与发布

当 `packages/sso-sdk` 或 `packages/sso-verify` 源码有变更时，需要重新构建并发布到 npm，子项目再升级依赖。

#### 1. 发布前准备

- npm 账号已加入 `@nihplod` 组织
- 已创建 Granular Access Token（@nihplod scope + Publish + bypass 2FA）
- 本地 npm registry 切到官方源：

```bash
npm config set registry https://registry.npmjs.org/
npm config set //registry.npmjs.org/:_authToken npm_你的token
```

#### 2. 发布 sso-verify

```bash
cd /var/www/NIHPLOD-Homepage.CN/packages/sso-verify
npm run build
npm version patch   # 或 minor/major
npm publish --access public
```

#### 3. 发布 sso-sdk

```bash
cd /var/www/NIHPLOD-Homepage.CN/packages/sso-sdk
npm version patch
npm publish --access public
```

#### 4. 切回淘宝镜像

```bash
npm config set registry https://registry.npmmirror.com/
```

#### 5. 子项目升级 SDK

```bash
cd /var/www/Skin-Advisor
npm install @nihplod/sso-sdk@latest @nihplod/sso-verify@latest
npm run build
pm2 restart skin-advisor --update-env
```

> **安全提示**：Granular Access Token 只显示一次，泄露后应立即到 npm 网站删除并重新生成。不要把 token 写入项目代码或提交到 git。

---

## 六、通用运维命令

### PM2

```bash
pm2 list
pm2 logs <app-name>
pm2 logs <app-name> --lines 30
pm2 logs <app-name> --err
pm2 info <app-name>
pm2 monit
pm2 restart <app-name>
pm2 stop <app-name>
pm2 start <app-name>
pm2 save
pm2 startup
```

应用名：`nihplod-app`、`ba-nihplod-app`、`skin-advisor`、`wecom-ai`。

### Nginx

```bash
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl reload nginx
sudo systemctl status nginx
sudo tail -f /var/log/nginx/error.log
sudo tail -f /var/log/nginx/access.log
```

### PostgreSQL

```bash
# 进入 psql
sudo -i -u postgres psql

# 测试指定数据库
sudo -u postgres psql -d <db-name> -c "SELECT 1;"

# 查看连接数
sudo -u postgres psql -d <db-name> -c "SELECT count(*) FROM pg_stat_activity WHERE datname = '<db-name>';"
```

---

## 七、SSO 健康检查与端到端验证

### 1. Discovery 端点

```bash
curl -s https://nihplod.cn/api/oauth/.well-known/openid-configuration | jq .
```

期望返回包含 `issuer`、`authorization_endpoint`、`token_endpoint`、`userinfo_endpoint`、`introspection_endpoint`、`jwks_uri`。

> 若 `issuer` 返回 `http://localhost:3000`，说明 Nginx 未正确传递 `Host` 或 `X-Forwarded-Proto`，需检查 `proxy_set_header` 与 `TRUST_PROXY=true`。

### 2. 授权端点 302

`state` 参数必须足够长（建议 43 字符以上）。

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://nihplod.cn/api/oauth/authorize?response_type=code&client_id=skin-advisor&redirect_uri=https://advisor.nihplod.cn/api/auth/callback&scope=openid&state=abcdefghijklmnopqrstuvwxyz1234567890abcdefgh&code_challenge=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&code_challenge_method=S256"
```

期望：`302`（跳转登录页或 consent 页）。

### 3. JWKS 端点（如启用 RS256）

```bash
curl -s https://nihplod.cn/api/oauth/jwks | jq .
```

### 4. 子站登录链路

1. 浏览器访问子站登录入口
2. 应跳转 `https://nihplod.cn/login?return_to=...`
3. 登录成功后跳转回子站 `redirect_uri?code=...&state=...`
4. 子站回调 handler 处理并设置 Cookie
5. 检查子站 Cookie：`__Host-user_token`（Next.js 回调）或子站自定义 token Cookie

---

## 八、故障排查

### 1. `issuer` 返回 `http://localhost:3000`

原因：Nginx 未透传真实域名/协议，或 Next.js 未信任代理。

检查：

```bash
grep -E "proxy_set_header Host|X-Forwarded-Proto|TRUST_PROXY" \
  /etc/nginx/sites-available/nihplod /var/www/NIHPLOD-Homepage.CN/.env.local
```

确保：

- Nginx 配置中有 `proxy_set_header Host $host;` 与 `proxy_set_header X-Forwarded-Proto $scheme;`
- 官网 `.env.local` 中有 `TRUST_PROXY=true`

### 2. SSO 登录失败 / 子站提示 "授权失败"

1. 检查官网 OAuth Client 是否存在、是否启用、`redirect_uri` 是否精确匹配。
2. 检查子站环境变量 `NEXT_PUBLIC_SSO_CLIENT_ID`、`NEXT_PUBLIC_SSO_REDIRECT_URI` 是否与官网一致。
3. 检查 Nginx 是否把 `Host` 头正确传递给官网：
   ```bash
   grep -E "proxy_set_header Host|X-Forwarded-Proto" /etc/nginx/sites-available/nihplod
   ```
4. 查看官网日志：
   ```bash
   pm2 logs nihplod-app --lines 50
   ```
5. 查看子站回调日志：
   ```bash
   pm2 logs skin-advisor --lines 50
   ```

### 3. Prisma 迁移冲突（`already exists`）

如果之前在同一数据库上执行过 `npx prisma db push`，后续 `npx prisma migrate deploy` 会报 `column/type/relation ... already exists`。

解决：把冲突的迁移标记为已应用，然后重新 deploy。

```bash
cd /var/www/NIHPLOD-Homepage.CN
npx prisma migrate resolve --applied <冲突的迁移文件名>
npx prisma migrate deploy
npx prisma generate
```

### 4. `.next/prerender-manifest.json` 缺失

PM2 启动后报 `ENOENT: .next/prerender-manifest.json`，说明构建产物不完整或 `.next` 目录被清理过。

```bash
cd /var/www/NIHPLOD-Homepage.CN
npm run build
ls -lh .next/prerender-manifest.json
pm2 restart nihplod-app --update-env
```

### 5. 授权端点返回 400 / `state 参数无效或长度不足`

OAuth 2.0 / PKCE 要求 `state` 参数长度足够（建议 43 字符以上）。检查 curl 或 SDK 生成的 `state` 是否被截断。

### 6. `OAuthClient.xxx does not exist in the current database`

说明 `schema.prisma` 里有字段但数据库没有。通常因为迁移文件与 schema 不同步。

解决：

1. 确认 `prisma/migrations/` 下是否有对应迁移。
2. 如果没有，手动创建迁移文件并应用：

```bash
mkdir -p /var/www/NIHPLOD-Homepage.CN/prisma/migrations/20260729000000_xxx
cat > /var/www/NIHPLOD-Homepage.CN/prisma/migrations/20260729000000_xxx/migration.sql << 'EOF'
ALTER TABLE "OAuthClient" ADD COLUMN IF NOT EXISTS "xxx" TEXT[] DEFAULT '{}';
EOF
sudo -u postgres psql -d nihplod_db -f /var/www/NIHPLOD-Homepage.CN/prisma/migrations/20260729000000_xxx/migration.sql
cd /var/www/NIHPLOD-Homepage.CN && npx prisma migrate resolve --applied 20260729000000_xxx
```

### 7. 内存不足导致 PM2 重启

```bash
pm2 info skin-advisor | grep -A2 "restart"
```

### 8. Skin-Advisor 图片 404

```bash
# 1. 确认文件是否真实存在
grep "alias" /etc/nginx/sites-available/skin-advisor | grep uploads
find /var/www/Skin-Advisor/public/uploads -type f | head -10

# 2. 确认 Nginx alias 指向正确（应为 public/uploads/，非 .next/standalone/...）
# 3. 确认符号链接存在
ls -la /var/www/Skin-Advisor/.next/standalone/public/uploads

# 4. 测试
curl -I -H "Host: advisor.nihplod.cn" http://127.0.0.1/uploads/products/<文件名>

# 5. 如果文件缺失但数据库有引用，可能是被 cron 30 天清理误删
#    展品图应托管于 public/uploads/products/ 子目录
```

### 9. BA 搜索功能异常

确认 BA 数据库同时执行了：

```bash
npx prisma migrate deploy
sudo -u postgres psql -d ba_nihplod_db -f /var/www/BA-NIHPLOD/db/schema.sql
```

---

## 九、生产环境建议

1. **启用 RS256 + JWKS**：在官网运行 `npx tsx scripts/generate-oauth-rs256-keys.ts`，配置 `JWT_ACCESS_PRIVATE_KEY` / `JWT_ACCESS_PUBLIC_KEY` 与 `JWT_ID_TOKEN_PRIVATE_KEY` / `JWT_ID_TOKEN_PUBLIC_KEY`，子站可本地验证 access_token，减少 introspection 网络往返。
2. **分布式部署必填数据库黑名单**：生产环境务必设置 `RATE_LIMIT_STORAGE=database` 与 `TOKEN_BLACKLIST_STORAGE=database`，确保多实例间共享 token 黑名单与限流状态。
3. **Redis 黑名单（可选）**：高并发或多实例场景下，可把 access token 撤销和 wechat_exchange 兑换黑名单迁到 Redis。
4. **HTTPS 全站**：`__Host-` Cookie 和 OAuth 回调都要求 HTTPS。
5. **Cloudflare 真实 IP**：线上经过 Cloudflare 时，Nginx 必须配置 `set_real_ip_from` 与 `real_ip_header CF-Connecting-IP`。
6. **密钥轮转**：`SSO_CLIENT_SECRET` 可在官网后台「轮换 Secret」后更新子站 `.env.production` 并重启；JWT 密钥轮转需所有实例同时更新，建议低峰期执行。
7. **日志监控**：关注官网 `/api/oauth/*` 端点的 `invalid_grant`、`client_id_mismatch`、`invalid_scope` 等错误。
8. **Scope 最小化**：子站只申请需要的 scope，如商城用 `openid profile phone`，论坛用 `openid profile`。
9. **Cookie 前缀**：子站 SDK 默认使用 `__Host-` 前缀 Cookie，Nginx 必须传递 `Host` 和 `X-Forwarded-Proto` 头。
