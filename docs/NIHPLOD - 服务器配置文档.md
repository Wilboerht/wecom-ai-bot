**鏈嶅姟鍣ㄤ俊鎭瑙?*

```
鍚嶇О锛歯ihplod
璐﹀彿锛歳oot
瀵嗙爜锛歁oidas888!
鍏綉 IP锛?7.116.125.52
鍏綉甯﹀锛? Mbps
涓荤缃?IP锛?72.22.245.232
閰嶇疆锛? 鏍革紙vCPU锛? GiB
鎿嶄綔绯荤粺锛歎buntu 24.04 64浣?绯荤粺鐩樺ぇ灏忥細ESSD Entry 浜戠洏 80 GiB
鏈嶅姟鍣ㄧ墿鐞嗕綅缃細鍗庝笢2锛堜笂娴凤級B
鍒版湡鏃堕棿锛?027骞?鏈?3鏃?23:59:59 锛堥樋閲屼簯 99 璁″垝锛岃嚜鍔ㄧ画璐癸級
```

> 鈿狅笍 鏈枃妗ｅ寘鍚敓浜х幆澧冩晱鎰熶俊鎭紙瀵嗙爜銆佸瘑閽ョ瓑锛夛紝璇峰嬁澶栦紶锛屽缓璁悗缁縼绉昏嚦瀵嗙爜绠＄悊宸ュ叿銆?
---

## 涓€銆佹湇鍔″櫒鍩虹鐜瀹夎

### 1. 绯荤粺鏇存柊

```bash
sudo apt update && sudo apt upgrade -y
```

### 2. Node.js & PM2

```bash
# 瀹夎 Node.js 20
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install nodejs -y
node -v && npm -v

# 鍏ㄥ眬瀹夎 PM2锛堜娇鐢?npmmirror 鍔犻€燂級
npm install -g pm2 --registry=https://registry.npmmirror.com
```

### 3. PostgreSQL

```bash
sudo apt install postgresql postgresql-contrib -y
sudo -i -u postgres psql
```

鍦?`psql` 鍐呮墽琛岋細

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

### 5.锛堝彲閫変絾鎺ㄨ崘锛塕edis

SSO 鐩稿叧榛戝悕鍗曪紙access token 鎾ら攢銆亀echat_exchange_token 鍏戞崲锛夊綋鍓嶉粯璁や娇鐢ㄦ暟鎹簱鎸佷箙鍖栧瓨鍌紙閰嶇疆 `TOKEN_BLACKLIST_STORAGE=database` 涓?`RATE_LIMIT_STORAGE=database`锛夈€傚崟瀹炰緥澶熺敤锛涜嫢鍚庣画姘村钩鎵╁睍锛屽缓璁畨瑁?Redis锛?
```bash
sudo apt install redis-server -y
sudo systemctl enable redis-server
```

骞跺湪瀹樼綉 `.env` 涓厤缃?`REDIS_URL=redis://localhost:6379`銆?
---

## 浜屻€丼SO 鏋舵瀯鎬昏

NIHPLOD 瀹樼綉浣滀负 **OAuth 2.0 / OIDC 璁よ瘉涓績**锛屼负鎺ュ叆鐨勫瓙椤圭洰鎻愪緵缁熶竴鐧诲綍銆侭A 绯荤粺鐙珛杩愯锛屼笉鎺ュ叆 SSO銆?
```
鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?     OAuth 2.0 + PKCE S256      鈹屸攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?鈹?  瀛愰」鐩?SPA    鈹? 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€authorize鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€> 鈹?                 鈹?鈹? / Next.js /    鈹? <鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€code + state鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?  NIHPLOD 瀹樼綉    鈹?鈹? 绾?JS 椤圭洰     鈹? 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€token鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€> 鈹? (OAuth Provider)鈹?鈹?                鈹? <鈹€access_token/refresh_token鈹€鈹?                 鈹?鈹?                鈹? 鈹€鈹€鈹€鈹€鈹€userinfo/introspect鈹€鈹€鈹€鈹€> 鈹?                 鈹?鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?                               鈹斺攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹?```

### 瑙掕壊鍒掑垎

| 椤圭洰 | 瑙掕壊 | Client 绫诲瀷 | 璇存槑 |
|---|---|---|---|
| NIHPLOD 瀹樼綉 | OAuth Provider | 鈥?| 缁熶竴绛惧彂 access_token / refresh_token / id_token |
| Skin-Advisor | Public Client | Public | 娴忚鍣?SPA锛屼笉浼?`client_secret`锛岃蛋 PKCE |
| BA-NIHPLOD | 鐙珛璁よ瘉绯荤粺 | 鈥?| 鑷缓璐﹀彿浣撶郴锛屼笉鎺ュ叆 SSO |
| WeCom-AI-Bot | 鐙珛鏈嶅姟 | 鈥?| Python FastAPI锛屼紒涓氬井淇?AI 瀹㈡湇锛屼笉鎺ュ叆 SSO |

### 璁よ瘉娴佺▼锛堝瓙椤圭洰锛?
1. 瀛愮珯鏈櫥褰曠敤鎴疯烦杞嚦 `https://nihplod.cn/login?return_to=<鎺堟潈绔偣>`銆?2. 鐢ㄦ埛鍦ㄥ畼缃戝畬鎴愮櫥褰曪紙璐﹀彿瀵嗙爜銆佺煭淇°€佸井淇＄瓑锛夈€?3. 瀹樼綉鍥炶皟瀛愮珯 `redirect_uri` 骞舵惡甯?`code` 涓?`state`銆?4. 瀛愮珯鍥炶皟 handler 鐢?`code` 鎹㈠彇 `access_token` / `refresh_token` / `id_token`銆?5. 瀛愮珯鍙椾繚鎶?API 閫氳繃 introspection 鎴栨湰鍦?JWKS 楠岃瘉 access_token銆?
### SDK 涓庨獙璇佸寘

瀛愰」鐩彲鐩存帴浠?npm 瀹夎锛?
- `@nihplod/sso-sdk`锛氭巿鏉冪爜 + PKCE 娴佺▼灏佽锛屽惈 `SsoClient`銆乣createCallbackRouteHandler`銆丷eact Provider 绛?- `@nihplod/sso-verify`锛氭湇鍔＄ token 楠岃瘉锛屾敮鎸?Introspection 涓?JWKS

瀹樼綉婧愮爜浣嶄簬 `packages/sso-sdk` 涓?`packages/sso-verify`锛屼慨鏀瑰悗闇€瑕侀噸鏂版瀯寤哄苟鍙戝竷鍒?npm锛屽瓙椤圭洰鍐嶅崌绾х増鏈€?
### 蹇呴厤鐜鍙橀噺

**瀹樼綉锛圥rovider锛?*锛歚JWT_ADMIN_SECRET`銆乣JWT_ACCESS_SECRET`銆乣JWT_REFRESH_SECRET`銆乣JWT_ID_TOKEN_SECRET`銆乣JWT_LOGOUT_SECRET`銆乣JWT_WECHAT_BIND_SECRET`銆乣JWT_WECHAT_EXCHANGE_SECRET`銆乣TOTP_ENCRYPTION_KEY`銆乣INTERNAL_API_KEYS`銆乣RATE_LIMIT_STORAGE`銆乣TOKEN_BLACKLIST_STORAGE`銆乣OAUTH_CODE_CHALLENGE_REQUIRED`銆乣CORS_ALLOWED_ORIGINS`銆?
鑻ュ惎鐢?RS256 + JWKS锛岃繕闇€閰嶇疆锛歚JWT_ACCESS_PRIVATE_KEY`銆乣JWT_ACCESS_PUBLIC_KEY`銆乣JWT_ID_TOKEN_PRIVATE_KEY`銆乣JWT_ID_TOKEN_PUBLIC_KEY`銆?
**鎺ュ叆 SSO 鐨勫瓙绔欙紙Client锛?*锛歚NEXT_PUBLIC_SSO_CLIENT_ID`銆乣NEXT_PUBLIC_SSO_BASE_URL`銆乣SSO_CLIENT_SECRET`锛圕onfidential Client锛夈€乣NEXT_PUBLIC_SSO_REDIRECT_URI`銆乣SSO_SCOPES`銆?
**BA 绯荤粺**锛氱嫭绔嬭璇侊紝鎸?BA 鑷韩鏂囨。閰嶇疆 `AUTH_SECRET`銆乣DATABASE_URL` 绛夛紝涓嶉渶瑕佷换浣?`SSO_*` 鍙橀噺銆?
> **Skin-Advisor 鐢熶骇鐜浣跨敤 `.env.production`**锛堢敱 `ecosystem.config.js` 涓?`env_file: './.env.production'` 鎸囧畾锛夈€傛湰鍦板紑鍙戝鍒?`.env.example` 涓?`.env.local`銆傜敓浜ф湇鍔″櫒涓婁笉瑕佹贩鐢?`.env`銆乣.env.local` 鍜?`.env.production`锛岄伩鍏嶄紭鍏堢骇娣蜂贡銆?
> 鏃х増鍚勫瓙绔欏叡浜?`JWT_SECRET` 鐨勬柟妗堝凡搴熷純銆傚畼缃戜繚鐣?`JWT_SECRET` 浠呯敤浜庡吋瀹规瀬涓埆鏃ц剼鏈紝涓嶅弬涓?SSO token 鐨勭鍚嶄笌楠岃瘉銆傛帴鍏?SSO 鐨勫瓙绔欑櫥褰曟€佸繀椤昏蛋 OAuth 2.0 娴佺▼銆?
---

## 涓夈€侀」鐩儴缃?
### 閫氱敤閮ㄧ讲姝ラ

1. `git clone` / `git pull`
2. `npm ci --legacy-peer-deps`锛堢敓浜х幆澧冩帹鑽愮敤 `ci`锛岄伩鍏嶄緷璧栨诞鍔級
3. 閰嶇疆 `.env.production`锛堢敓浜э級鎴?`.env.local`锛堟湰鍦板紑鍙戯級
4. `npx prisma migrate deploy`锛堥」鐩凡鏈夎縼绉绘枃浠舵椂涓€寰嬬敤姝ゅ懡浠わ級
5. `npx prisma generate`
6. `npm run build`
7. 楠岃瘉 `.next/prerender-manifest.json` 瀛樺湪
8. `pm2 start/restart`

---

### A. 瀹樼綉锛圢IHPLOD-Homepage.CN锛夆€斺€?SSO 璁よ瘉涓績

浠撳簱锛歔https://github.com/Wilboerht/NIHPLOD-Homepage.CN](https://github.com/Wilboerht/NIHPLOD-Homepage.CN)

```bash
mkdir -p /var/www && cd /var/www
git clone https://github.com/Wilboerht/NIHPLOD-Homepage.CN.git
cd NIHPLOD-Homepage.CN
```

#### 1. 鐢熸垚鐙珛 JWT Secret

姣忕被 token 蹇呴』鐙珛銆傜敓鎴愬己闅忔満瀵嗛挜锛?
```bash
for name in JWT_ADMIN_SECRET JWT_ACCESS_SECRET JWT_REFRESH_SECRET JWT_WECHAT_BIND_SECRET JWT_WECHAT_EXCHANGE_SECRET JWT_ID_TOKEN_SECRET JWT_LOGOUT_SECRET TOTP_ENCRYPTION_KEY; do
  echo "$name=$(openssl rand -hex 32)"
done
```

鑻ュ惎鐢?RS256 + JWKS锛堟帹鑽愮敓浜х幆澧冿級锛岃繍琛岋細

```bash
npx tsx scripts/generate-oauth-rs256-keys.ts
```

#### 2. 鐢熸垚鍐呴儴 API 瀵嗛挜

瀛愮珯璋冪敤瀹樼綉 `/api/v1/internal/*` 浠ュ強寰俊妯℃澘娑堟伅绛夊唴閮ㄦ帴鍙ｆ椂浣跨敤锛?
```bash
npx tsx scripts/generate-internal-api-keys.ts
```

#### 3. 閰嶇疆鐜鍙橀噺

```bash
vim .env.local
```

```env
NEXT_PUBLIC_APP_URL="https://nihplod.cn"
NEXT_PUBLIC_ADVISOR_URL="https://advisor.nihplod.cn"
TRUST_PROXY=true

DATABASE_URL="postgresql://nihplod_user:Moidas888!@localhost:5432/nihplod_db?schema=public"

# 姣忎釜瀵嗛挜蹇呴』鐙珛銆佽嚦灏?32 瀛楃
JWT_ADMIN_SECRET=...
JWT_ACCESS_SECRET=...
JWT_REFRESH_SECRET=...
JWT_WECHAT_BIND_SECRET=...
JWT_WECHAT_EXCHANGE_SECRET=...
JWT_ID_TOKEN_SECRET=...
JWT_LOGOUT_SECRET=...
TOTP_ENCRYPTION_KEY=...

# 鐢熶骇鎺ㄨ崘鍚敤 RS256 + JWKS
JWT_ACCESS_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
JWT_ACCESS_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"
JWT_ID_TOKEN_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
JWT_ID_TOKEN_PUBLIC_KEY="-----BEGIN PUBLIC KEY-----\n...\n-----END PUBLIC KEY-----"

# 鍒嗗竷寮忛儴缃插繀濉細浣跨敤鏁版嵁搴撴寔涔呭寲榛戝悕鍗曚笌闄愭祦
RATE_LIMIT_STORAGE=database
TOKEN_BLACKLIST_STORAGE=database

# 鎺堟潈绔己鍒?PKCE锛堟帹鑽?true锛?OAUTH_CODE_CHALLENGE_REQUIRED=true

# 鍏佽鐨勮法鍩熸潵婧愶紝澶氫釜鐢ㄩ€楀彿鍒嗛殧
CORS_ALLOWED_ORIGINS=https://advisor.nihplod.cn,https://ba.nihplod.cn

# 鍐呴儴 API 瀵嗛挜
INTERNAL_API_KEYS='[{"project":"advisor","key":"...","secret":"..."}]'

# 鍏朵粬鍘熸湁閰嶇疆锛堝井淇°€佹敮浠樸€丱SS銆丼MTP 绛夛級鎸夐渶琛ュ厖
```

#### 4. 鏁版嵁搴撹縼绉讳笌绉嶅瓙

```bash
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma migrate deploy
npx prisma generate
npm run db:seed
```

> **杩佺Щ鍘熷垯**锛氶」鐩凡鏈?`prisma/migrations/` 鍚庯紝鐢熶骇鐜涓€寰嬩娇鐢?`npx prisma migrate deploy`銆俙npx prisma db push` 浠呯敤浜庢湰鍦板紑鍙戝垵鏈熴€佸皻鏈垱寤鸿縼绉绘枃浠舵椂銆?*鍒囧嬁鍦ㄥ悓涓€鏁版嵁搴撲笂娣风敤 `db push` 涓?`migrate deploy`**锛屽惁鍒欏悗缁縼绉讳細鍑虹幇 `already exists` 鍐茬獊銆?>
> 濡傛灉姝ゅ墠璇敤浜?`db push`锛屽啿绐佹姤閿欎负 `column/type/relation ... already exists`锛岃В鍐虫柟寮忔槸鐢?`npx prisma migrate resolve --applied <杩佺Щ鍚?` 鎶婂啿绐佽縼绉绘爣璁颁负宸插簲鐢紝鍐嶉噸鏂?`migrate deploy`銆?
#### 5. 鏋勫缓涓庡惎鍔?
```bash
npm run build

# 纭鏋勫缓浜х墿瀹屾暣
ls -lh .next/prerender-manifest.json

pm2 start npm --name "nihplod-app" -- start
pm2 save
pm2 startup
```

#### 6. Nginx 閰嶇疆

鐢变簬绾夸笂缁忚繃 Cloudflare 浠ｇ悊锛孨ginx 鏀跺埌鐨?`remote_addr` 鍙兘鏄?Cloudflare IP銆傚繀椤婚厤缃?`set_real_ip_from` 涓?`real_ip_header`锛屽苟鎶婄湡瀹炲崗璁?鍩熷悕閫忎紶缁?Next.js銆?
`/etc/nginx/sites-available/nihplod`锛?
```nginx
server {
    listen 80;
    server_name nihplod.cn www.nihplod.cn;

    # Cloudflare 鐪熷疄 IP锛圛Pv4 绀轰緥锛孖Pv6 涓庡畬鏁?CIDR 璇锋寜 Cloudflare 瀹樻柟鍒楄〃琛ュ厖锛?    set_real_ip_from 173.245.48.0/20;
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

鍚敤锛?
```bash
sudo ln -s /etc/nginx/sites-available/nihplod /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

### B. BA 绯荤粺锛圔A-NIHPLOD锛夆€斺€?鐙珛璁よ瘉

浠撳簱锛歔https://github.com/Wilboerht/BA-NIHPLOD](https://github.com/Wilboerht/BA-NIHPLOD)

> BA 绯荤粺浣跨敤鑷缓璐﹀彿浣撶郴锛屼笉鎺ュ叆 NIHPLOD SSO锛屼篃涓嶉渶瑕?`@nihplod/sso-sdk`銆傛寜 BA 鑷韩璁よ瘉閫昏緫閮ㄧ讲鍗冲彲銆?
```bash
cd /var/www
git clone https://ghfast.top/https://github.com/Wilboerht/BA-NIHPLOD.git
cd BA-NIHPLOD
```

#### 1. 閰嶇疆鐜鍙橀噺

```bash
cat > .env.local << 'EOF'
DATABASE_URL="postgresql://nihplod_user:Moidas888!@localhost:5432/ba_nihplod_db?schema=public"
AUTH_SECRET="浣犵殑寮洪殢鏈哄瘑閽ヨ嚦灏?2浣?
NEXT_PUBLIC_BASE_URL="https://ba.nihplod.cn"

# BA 鍏朵粬鑷湁閰嶇疆鎸夐渶琛ュ厖
EOF
```

> BA 绯荤粺**涓嶉渶瑕?*浠讳綍 `SSO_*` 鐜鍙橀噺锛屼篃涓嶉渶瑕佸湪瀹樼綉鍚庡彴娉ㄥ唽 OAuth Client銆?
#### 2. 瀹夎渚濊禆涓庢暟鎹簱

```bash
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma db push
npx prisma generate

# BA 椤圭洰鍖呭惈 Prisma 涓嶇鐞嗙殑 RLS/瑙﹀彂鍣?鍑芥暟/绱㈠紩锛屾瘡娆?schema.sql 鏈夊彉鏇存椂閮借鎵ц
sudo -u postgres psql -d ba_nihplod_db -f db/schema.sql
```

> BA 鏁版嵁搴撳繀椤?*鍚屾椂鎵ц** `npx prisma db push` 涓?`sudo -u postgres psql -d ba_nihplod_db -f db/schema.sql`锛屽惁鍒欎細鍑虹幇鍏宠仈璁板綍涓㈠け鎴栨悳绱㈢储寮曠己澶便€?
#### 3. 鏋勫缓涓庡惎鍔?
```bash
npm run build
pm2 start npm --name "ba-nihplod-app" -- start -- -p 3001
pm2 save
pm2 startup
```

#### 4. Nginx 閰嶇疆

`/etc/nginx/sites-available/ba-nihplod`锛?
```nginx
server {
    listen 80;
    server_name ba.nihplod.cn www.ba.nihplod.cn;

    set_real_ip_from 173.245.48.0/20;
    # ... 鍏朵粬 Cloudflare CIDR ...
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

鍚敤锛?
```bash
sudo ln -s /etc/nginx/sites-available/ba-nihplod /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

### C. Skin-Advisor锛堟祻瑙堝櫒绔?Public Client锛?
浠撳簱锛歔https://github.com/Wilboerht/Skin-Advisor](https://github.com/Wilboerht/Skin-Advisor)

```bash
cd /var/www
git clone https://github.com/Wilboerht/Skin-Advisor.git
cd Skin-Advisor
```

#### 1. 鍦ㄥ畼缃戝悗鍙版敞鍐?OAuth Client

- `client_id`锛氫緥濡?`skin-advisor`
- `name`锛歋kin Advisor
- `redirect_uris`锛歚https://advisor.nihplod.cn/api/auth/callback`
- `scopes`锛歚openid profile phone`
- Client Type锛歅ublic
- 淇濆瓨鍚?*娌℃湁 client_secret**

#### 2. 閰嶇疆鐜鍙橀噺

```bash
cat > .env.production << 'EOF'
# ===== 鏁版嵁搴?=====
DATABASE_URL="postgresql://nihplod_user:Moidas888!@localhost:5432/skin_advisor_db?schema=public"

# ===== AI 鏈嶅姟 =====
AI_PROVIDER="qwen"
AI_VISION_PROVIDER="qwen"

# ===== SSO 閰嶇疆锛圥ublic Client锛?====
NEXT_PUBLIC_SITE_URL="https://advisor.nihplod.cn"
NEXT_PUBLIC_BASE_URL="https://advisor.nihplod.cn"
NEXT_PUBLIC_SSO_CLIENT_ID="skin-advisor"
NEXT_PUBLIC_SSO_BASE_URL="https://nihplod.cn"
NEXT_PUBLIC_SSO_REDIRECT_URI="https://advisor.nihplod.cn/api/auth/callback"
NEXT_PUBLIC_SSO_SCOPES="openid profile phone"

# ===== API Keys锛堜繚鐣欎笌瀹樼綉鍐呴儴鎺ュ彛閫氫俊锛?====
QWEN_API_KEY="..."
WANXIANG_API_KEY="..."
VOLC_ACCESSKEY=""
VOLC_SECRETKEY=""

# ===== 绠＄悊鍛樺瘑閽?=====
ADMIN_SECRET="浣犵殑寮洪殢鏈哄瘑閽?
IP_HASH_SALT="浣犵殑寮洪殢鏈哄瘑閽?
CRON_SECRET="浣犵殑寮洪殢鏈哄瘑閽?

# ===== 浼佷笟寰俊 AI 瀹㈡湇鑱斿姩锛堜簩鏈燂級=====
# INTERNAL_API_KEY 闇€涓?wecom-ai-bot 鐨?.env 淇濇寔涓€鑷达紙openssl rand -hex 24 鐢熸垚锛?INTERNAL_API_KEY="<涓庡鏈嶆湇鍔′竴鑷寸殑寮洪殢鏈哄瘑閽?"
WECOM_BOT_BASE_URL="https://kf.nihplod.cn"
EOF
```

#### 3. 浠ｇ爜鏀归€?
瀹夎 SDK锛?
```bash
npm install @nihplod/sso-sdk
```

Next.js 鍥炶皟 handler锛?
```typescript
import { createCallbackRouteHandler } from "@nihplod/sso-sdk/next";

export const GET = createCallbackRouteHandler({
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  ssoBaseUrl: process.env.NEXT_PUBLIC_SSO_BASE_URL!,
  redirectUri: process.env.NEXT_PUBLIC_SSO_REDIRECT_URI!,
});
```

React 鐧诲綍鎸夐挳锛?
```tsx
import { SsoClient } from "@nihplod/sso-sdk";

const sso = new SsoClient({
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  redirectUri: process.env.NEXT_PUBLIC_SSO_REDIRECT_URI!,
  ssoBaseUrl: process.env.NEXT_PUBLIC_SSO_BASE_URL!,
  scopes: process.env.NEXT_PUBLIC_SSO_SCOPES!,
});

<button onClick={() => sso.login()}>浣跨敤 NIHPLOD 璐﹀彿鐧诲綍</button>
```

鏈嶅姟绔?API 楠岃瘉 token锛?
```typescript
import { createTokenVerifier } from "@nihplod/sso-verify";

const verifier = createTokenVerifier({
  introspectionEndpoint: "https://nihplod.cn/api/oauth/introspect",
  clientId: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  audience: process.env.NEXT_PUBLIC_SSO_CLIENT_ID!,
  issuer: "https://nihplod.cn",
});
```

#### 4. 瀹夎銆佹瀯寤恒€侀儴缃?
```bash
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com

# 鍑嗗鐢熶骇鐜锛氬姞杞?.env.production + 楠岃瘉鍙橀噺 + prisma generate + migrate deploy
node scripts/prepare-production.js

# 鏋勫缓锛坧ostbuild 浼氳嚜鍔ㄥ鍒?public/ 鍜?.next/static/ 鍒?standalone锛?# 骞惰嚜鍔ㄥ垱寤?.next/standalone/public/uploads 鈫?public/uploads 绗﹀彿閾炬帴锛?npm run build
```

> **璇存槑**锛?> - `npx prisma db push` 宸叉浛鎹负 `node scripts/prepare-production.js` 涓殑 `prisma migrate deploy`銆?> - `rsync` 澶嶅埗 `public/` 鍒?standalone 鐨勬楠ゅ凡鍒犻櫎锛屽洜涓?`postbuild` 鑷姩瀹屾垚銆?> - **涓婁紶鐩綍缁撴瀯**锛氫复鏃舵枃浠讹紙娓稿涓婁紶/鐢ㄦ埛澶村儚锛夊瓨浜?`public/uploads/` 鏍圭洰褰曪紝鍙?30 澶╂竻鐞嗙瓥鐣ョ害鏉燂紱灞曞搧鍥剧瓑姘镐箙璧勪骇瀛樹簬 `public/uploads/products/` 瀛愮洰褰曪紝璞佸厤娓呯悊銆?> - `.next/standalone/public/uploads` 绗﹀彿閾炬帴宸茬敱 `postbuild` 鑷姩鍒涘缓锛屾寚鍚戦」鐩牴 `public/uploads/`锛岀‘淇?standalone 杩愯鏃舵柊涓婁紶鏂囦欢鍐欏叆鍚屼竴鎸佷箙鍖栦綅缃€?> - Nginx `/uploads/` alias 鎸囧悜 `/var/www/Skin-Advisor/public/uploads/`锛岀敱 Nginx 鐩存帴鎻愪緵闈欐€佹枃浠讹紝涓嶇粡杩?Node.js銆?
PM2 閰嶇疆锛?
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

#### 5. Crontab 閰嶇疆

```bash
sudo crontab -e
*/30 * * * * cd /var/www/Skin-Advisor && /usr/bin/node scripts/run-cron.js /api/cron/data-cleanup >> /var/log/myskin-cron.log 2>&1
sudo touch /var/log/myskin-cron.log
sudo chmod 644 /var/log/myskin-cron.log
```

#### 6. Nginx 閰嶇疆

`/etc/nginx/sites-available/skin-advisor`锛?
```nginx
server {
    listen 80;
    server_name advisor.nihplod.cn;

    set_real_ip_from 173.245.48.0/20;
    # ... 鍏朵粬 Cloudflare CIDR ...
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

鍚敤锛?
```bash
sudo ln -s /etc/nginx/sites-available/skin-advisor /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

---

### D. WeCom-AI-Bot锛堜紒涓氬井淇?AI 瀹㈡湇锛夆€斺€?鐙珛鏈嶅姟

浠撳簱锛歔https://github.com/Wilboerht/wecom-ai-bot](https://github.com/Wilboerht/wecom-ai-bot)

> Python FastAPI 椤圭洰锛屼笉鎺ュ叆 SSO銆傞€氳繃浼佷笟寰俊鍥炶皟 + DeepSeek API 瀹炵幇 C 绔井淇″鏈?AI 鑷姩鍥炲锛屾敮鎸佽浆浜哄伐銆?
```bash
cd /var/www
git clone https://github.com/Wilboerht/wecom-ai-bot.git
cd wecom-ai-bot
```

#### 1. 閰嶇疆鐜鍙橀噺

```bash
cat > .env << 'EOF'
WECOM_CORP_ID=ww27c38ff0313efbec
WECOM_AGENT_ID=1000004
WECOM_SECRET=daF2mDuIhzDKz0n9f6XRSGoa0EofhCLVpzPl62JrSxA
WECOM_TOKEN=IpsCwAtiShzlOLc
WECOM_ENCODING_AES_KEY=aJ8XGEkHfmIvAvCHtcJxz3ldzLwGOU7WhqMDBg0OrVW
DEEPSEEK_API_KEY=sk-51ad4ce5b4874ac38e635dfd83afe2b5
DEEPSEEK_MODEL=deepseek-chat
SYSTEM_PROMPT=浣犳槸NIHPLOD鏃庢煆鐨勫鏈嶉【闂€備繚鎸佷笓涓氥€佸皧閲嶃€佺畝娲佺殑璇皵锛岀敤璇嶄綋闈€?SESSION_TTL=1800
LOG_LEVEL=INFO
WECOM_SERVICE_USERID=WangHongKun

# ===== 娴嬭偆鎶ュ憡鑱斿姩锛堜簩鏈燂細缁撴灉椤?鈫?瀹㈡湇閾炬帴 scene_param 鈫?鑷姩鎷夊彇鎶ュ憡鍐欐。妗堬級=====
# INTERNAL_API_KEY 闇€涓?Skin-Advisor 瀛愮珯 .env.production 淇濇寔涓€鑷达紙openssl rand -hex 24 鐢熸垚锛?INTERNAL_API_KEY=<涓庡瓙绔欎竴鑷寸殑寮洪殢鏈哄瘑閽?
ADVISOR_REPORT_API_URL=https://advisor.nihplod.cn/api/internal/report-summary
KF_ADVISOR_OPEN_KFID=wkBpRSVwAAeouflfh1MDy58qaCx7oKTA
KF_SCENE=advisor
KF_SEND_WELCOME=1
EOF
```

> 鈿狅笍 `.env` 宸茶 `.gitignore` 鎺掗櫎锛屼笉浼氭彁浜ゅ埌浠撳簱銆傞娆￠儴缃叉垨 `.env` 涓㈠け鏃堕渶鎵嬪姩閲嶅缓銆?
#### 2. 瀹夎渚濊禆锛堣櫄鎷熺幆澧冿級

```bash
# 瀹夎 venv锛堟柊鏈嶅姟鍣ㄩ娆★級
apt-get install -y python3-full python3-venv

# 鍒涘缓铏氭嫙鐜
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
mkdir -p logs
deactivate
```

#### 3. 鍚姩锛圥M2锛?
```bash
pm2 start deploy/ecosystem.config.js
pm2 save
```

#### 4. Nginx 閰嶇疆

`/etc/nginx/sites-available/wecom-ai`锛堥」鐩唴 `deploy/nginx-kf.conf`锛夛細

```nginx
server {
    listen 80;
    server_name kf.nihplod.cn;

    set_real_ip_from 173.245.48.0/20;
    # ... 鍏朵粬 Cloudflare CIDR ...
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

    # 鍐呴儴 API锛坅dvisor 瀛愮珯鏈嶅姟绔皟鐢ㄧ敓鎴愬鏈嶉摼鎺ワ紝甯?x-internal-key 鏍￠獙锛?    location /api/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 15s;
    }

    location / {
        return 404;
    }
}
```

鍚敤锛?
```bash
sudo cp /var/www/wecom-ai-bot/deploy/nginx-kf.conf /etc/nginx/sites-available/wecom-ai
sudo ln -s /etc/nginx/sites-available/wecom-ai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

#### 5. Cloudflare DNS

| 绫诲瀷 | 鍚嶇О | 鍐呭 | 浠ｇ悊 |
|------|------|------|------|
| A | `kf` | `47.116.125.52` | 鉁?姗欒壊浜戞湹 |

> 闇€瑕佸叧闂?Cloudflare 鈫?Security 鈫?Browser Integrity Check锛屽惁鍒欎紒寰洖璋冧細琚?1010 鎷︽埅銆?
#### 6. 浼佷笟寰俊鍚庡彴閰嶇疆

| 璁剧疆椤?| 鍊?|
|--------|-----|
| 鍥炶皟 URL | `https://kf.nihplod.cn/callback` |
| 鍙俊 IP | `47.116.125.52` |

---

## 鍥涖€丱Auth Client 娉ㄥ唽涓庣鐞?
### 鏂瑰紡涓€锛氬畼缃戠鐞嗗悗鍙帮紙鎺ㄨ崘锛?
1. 鐧诲綍 `https://nihplod.cn/admin`
2. 宸︿晶鑿滃崟 鈫?**SSO 瀹㈡埛绔鐞?*锛坄/admin/oauth-clients`锛?3. 鐐瑰嚮銆屾柊寤?Client銆?4. 濉啓锛?   - `client_id`锛氬敮涓€鑻辨枃鏍囪瘑锛屽 `skin-advisor`
   - `name`锛氭樉绀哄悕绉?   - `redirect_uris`锛氬洖璋冨湴鍧€锛屽繀椤荤簿纭尮閰嶏紙鍖呮嫭鍗忚銆佸煙鍚嶃€佽矾寰勶級
   - `scopes`锛氳 client 鍏佽鐢宠鐨?scope锛屽 `openid profile phone`
   - `isActive`锛氭槸鍚﹀惎鐢?   - `backchannelLogoutUri`锛氾紙鍙€夛級鎺ユ敹 backchannel logout 鐨勫湴鍧€
5. 閫夋嫨 Client Type锛?   - **Public**锛氭祻瑙堝櫒 SPA銆佺Щ鍔ㄧ銆佹闈㈢锛堟棤 client_secret锛?   - **Confidential**锛歂ext.js BFF銆佸悗绔湇鍔★紙鏈?client_secret锛?6. 淇濆瓨鍚庯紝Confidential Client 浼氭樉绀?*鏄庢枃 client_secret**锛堜粎姝や竴娆★級锛岀珛鍗冲鍒跺埌瀛愮珯 `.env.production`銆?
### 鏂瑰紡浜岋細鍛戒护琛岀洿鎺ユ彃鍏ワ紙搴旀€ワ級

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

> BA 绯荤粺涓嶆帴鍏?SSO锛屼笉闇€瑕佹敞鍐?OAuth Client銆?
---

## 浜斻€侀」鐩洿鏂版祦绋?
### 瀹樼綉

```bash
cd /var/www/NIHPLOD-Homepage.CN
git pull origin master
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
npx prisma migrate deploy     # schema 鏈夊彉鏇存椂搴旂敤杩佺Щ
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

# 浠呭綋 db/schema.sql 鎴?db/migrate-*.sql 鏈夊彉鏇存椂鎵ц
sudo -u postgres psql -d ba_nihplod_db -f /var/www/BA-NIHPLOD/db/schema.sql

npm run build
pm2 restart ba-nihplod-app
```

> BA 鏇存柊蹇呴』**鍚屾椂**鎵ц `npx prisma migrate deploy` 涓?`sudo -u postgres psql -d ba_nihplod_db -f db/schema.sql`锛屼笉鍙彧鎵ц鍏朵腑涓€椤广€?
### Skin-Advisor

```bash
cd /var/www/Skin-Advisor

# 鎷夊彇鏈€鏂颁唬鐮侊紙涓嶈 reset package-lock.json锛岀敓浜х敤 npm ci锛?git pull origin main
npm ci --legacy-peer-deps --registry=https://registry.npmmirror.com
node scripts/prepare-production.js
npm run build

# 鈿狅笍 閲嶈锛歝ron 瀹氭椂浠诲姟姣?30 鍒嗛挓娓呯悊 public/uploads/ 鏍圭洰褰曚腑 mtime > 30 澶╃殑鏂囦欢
# 灞曞搧鍥惧繀椤诲瓨鏀惧湪 products/ 瀛愮洰褰曚笅锛堣眮鍏嶆竻鐞嗭級锛屽惁鍒欎細琚鍒?# 濡傛灉姝ゅ墠鏈夊睍鍝佸浘閬楃暀鍦ㄦ牴鐩綍锛屾墽琛岃縼绉伙細
#   npx -y tsx scripts/migrate-product-uploads.ts

# 閲嶅惎 PM2 骞堕噸鏂板姞杞界幆澧冨彉閲?pm2 restart skin-advisor --update-env

# 鍋ュ悍妫€鏌?sleep 5 && curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3002
```

> **璇存槑**锛?> - `postbuild` 鑷姩澶嶅埗 `public/`锛堟帓闄?`uploads`锛夊埌 `standalone`锛屽苟鍒涘缓 `uploads` 绗﹀彿閾炬帴銆?> - 鐢熶骇鐜 uploads 鐢?Nginx `location /uploads/` 鐩存帴 alias 鍒?`/var/www/Skin-Advisor/public/uploads/`銆?> - 濡傛灉鏇存柊娑夊強杩佺Щ鑴氭湰锛堝 `scripts/migrate-product-uploads.ts`锛夛紝蹇呴』鍦?`npm run build` 涔嬪墠鍏堟墽琛屻€?
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

### SSO SDK 鏇存柊涓庡彂甯?
褰?`packages/sso-sdk` 鎴?`packages/sso-verify` 婧愮爜鏈夊彉鏇存椂锛岄渶瑕侀噸鏂版瀯寤哄苟鍙戝竷鍒?npm锛屽瓙椤圭洰鍐嶅崌绾т緷璧栥€?
#### 1. 鍙戝竷鍓嶅噯澶?
- npm 璐﹀彿宸插姞鍏?`@nihplod` 缁勭粐
- 宸插垱寤?Granular Access Token锛園nihplod scope + Publish + bypass 2FA锛?- 鏈湴 npm registry 鍒囧埌瀹樻柟婧愶細

```bash
npm config set registry https://registry.npmjs.org/
npm config set //registry.npmjs.org/:_authToken npm_浣犵殑token
```

#### 2. 鍙戝竷 sso-verify

```bash
cd /var/www/NIHPLOD-Homepage.CN/packages/sso-verify
npm run build
npm version patch   # 鎴?minor/major
npm publish --access public
```

#### 3. 鍙戝竷 sso-sdk

```bash
cd /var/www/NIHPLOD-Homepage.CN/packages/sso-sdk
npm version patch
npm publish --access public
```

#### 4. 鍒囧洖娣樺疂闀滃儚

```bash
npm config set registry https://registry.npmmirror.com/
```

#### 5. 瀛愰」鐩崌绾?SDK

```bash
cd /var/www/Skin-Advisor
npm install @nihplod/sso-sdk@latest @nihplod/sso-verify@latest
npm run build
pm2 restart skin-advisor --update-env
```

> **瀹夊叏鎻愮ず**锛欸ranular Access Token 鍙樉绀轰竴娆★紝娉勯湶鍚庡簲绔嬪嵆鍒?npm 缃戠珯鍒犻櫎骞堕噸鏂扮敓鎴愩€備笉瑕佹妸 token 鍐欏叆椤圭洰浠ｇ爜鎴栨彁浜ゅ埌 git銆?
---

## 鍏€侀€氱敤杩愮淮鍛戒护

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

搴旂敤鍚嶏細`nihplod-app`銆乣ba-nihplod-app`銆乣skin-advisor`銆乣wecom-ai`銆?
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
# 杩涘叆 psql
sudo -i -u postgres psql

# 娴嬭瘯鎸囧畾鏁版嵁搴?sudo -u postgres psql -d <db-name> -c "SELECT 1;"

# 鏌ョ湅杩炴帴鏁?sudo -u postgres psql -d <db-name> -c "SELECT count(*) FROM pg_stat_activity WHERE datname = '<db-name>';"
```

---

## 涓冦€丼SO 鍋ュ悍妫€鏌ヤ笌绔埌绔獙璇?
### 1. Discovery 绔偣

```bash
curl -s https://nihplod.cn/api/oauth/.well-known/openid-configuration | jq .
```

鏈熸湜杩斿洖鍖呭惈 `issuer`銆乣authorization_endpoint`銆乣token_endpoint`銆乣userinfo_endpoint`銆乣introspection_endpoint`銆乣jwks_uri`銆?
> 鑻?`issuer` 杩斿洖 `http://localhost:3000`锛岃鏄?Nginx 鏈纭紶閫?`Host` 鎴?`X-Forwarded-Proto`锛岄渶妫€鏌?`proxy_set_header` 涓?`TRUST_PROXY=true`銆?
### 2. 鎺堟潈绔偣 302

`state` 鍙傛暟蹇呴』瓒冲闀匡紙寤鸿 43 瀛楃浠ヤ笂锛夈€?
```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  "https://nihplod.cn/api/oauth/authorize?response_type=code&client_id=skin-advisor&redirect_uri=https://advisor.nihplod.cn/api/auth/callback&scope=openid&state=abcdefghijklmnopqrstuvwxyz1234567890abcdefgh&code_challenge=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&code_challenge_method=S256"
```

鏈熸湜锛歚302`锛堣烦杞櫥褰曢〉鎴?consent 椤碉級銆?
### 3. JWKS 绔偣锛堝鍚敤 RS256锛?
```bash
curl -s https://nihplod.cn/api/oauth/jwks | jq .
```

### 4. 瀛愮珯鐧诲綍閾捐矾

1. 娴忚鍣ㄨ闂瓙绔欑櫥褰曞叆鍙?2. 搴旇烦杞?`https://nihplod.cn/login?return_to=...`
3. 鐧诲綍鎴愬姛鍚庤烦杞洖瀛愮珯 `redirect_uri?code=...&state=...`
4. 瀛愮珯鍥炶皟 handler 澶勭悊骞惰缃?Cookie
5. 妫€鏌ュ瓙绔?Cookie锛歚__Host-user_token`锛圢ext.js 鍥炶皟锛夋垨瀛愮珯鑷畾涔?token Cookie

---

## 鍏€佹晠闅滄帓鏌?
### 1. `issuer` 杩斿洖 `http://localhost:3000`

鍘熷洜锛歂ginx 鏈€忎紶鐪熷疄鍩熷悕/鍗忚锛屾垨 Next.js 鏈俊浠讳唬鐞嗐€?
妫€鏌ワ細

```bash
grep -E "proxy_set_header Host|X-Forwarded-Proto|TRUST_PROXY" \
  /etc/nginx/sites-available/nihplod /var/www/NIHPLOD-Homepage.CN/.env.local
```

纭繚锛?
- Nginx 閰嶇疆涓湁 `proxy_set_header Host $host;` 涓?`proxy_set_header X-Forwarded-Proto $scheme;`
- 瀹樼綉 `.env.local` 涓湁 `TRUST_PROXY=true`

### 2. SSO 鐧诲綍澶辫触 / 瀛愮珯鎻愮ず "鎺堟潈澶辫触"

1. 妫€鏌ュ畼缃?OAuth Client 鏄惁瀛樺湪銆佹槸鍚﹀惎鐢ㄣ€乣redirect_uri` 鏄惁绮剧‘鍖归厤銆?2. 妫€鏌ュ瓙绔欑幆澧冨彉閲?`NEXT_PUBLIC_SSO_CLIENT_ID`銆乣NEXT_PUBLIC_SSO_REDIRECT_URI` 鏄惁涓庡畼缃戜竴鑷淬€?3. 妫€鏌?Nginx 鏄惁鎶?`Host` 澶存纭紶閫掔粰瀹樼綉锛?   ```bash
   grep -E "proxy_set_header Host|X-Forwarded-Proto" /etc/nginx/sites-available/nihplod
   ```
4. 鏌ョ湅瀹樼綉鏃ュ織锛?   ```bash
   pm2 logs nihplod-app --lines 50
   ```
5. 鏌ョ湅瀛愮珯鍥炶皟鏃ュ織锛?   ```bash
   pm2 logs skin-advisor --lines 50
   ```

### 3. Prisma 杩佺Щ鍐茬獊锛坄already exists`锛?
濡傛灉涔嬪墠鍦ㄥ悓涓€鏁版嵁搴撲笂鎵ц杩?`npx prisma db push`锛屽悗缁?`npx prisma migrate deploy` 浼氭姤 `column/type/relation ... already exists`銆?
瑙ｅ喅锛氭妸鍐茬獊鐨勮縼绉绘爣璁颁负宸插簲鐢紝鐒跺悗閲嶆柊 deploy銆?
```bash
cd /var/www/NIHPLOD-Homepage.CN
npx prisma migrate resolve --applied <鍐茬獊鐨勮縼绉绘枃浠跺悕>
npx prisma migrate deploy
npx prisma generate
```

### 4. `.next/prerender-manifest.json` 缂哄け

PM2 鍚姩鍚庢姤 `ENOENT: .next/prerender-manifest.json`锛岃鏄庢瀯寤轰骇鐗╀笉瀹屾暣鎴?`.next` 鐩綍琚竻鐞嗚繃銆?
```bash
cd /var/www/NIHPLOD-Homepage.CN
npm run build
ls -lh .next/prerender-manifest.json
pm2 restart nihplod-app --update-env
```

### 5. 鎺堟潈绔偣杩斿洖 400 / `state 鍙傛暟鏃犳晥鎴栭暱搴︿笉瓒砢

OAuth 2.0 / PKCE 瑕佹眰 `state` 鍙傛暟闀垮害瓒冲锛堝缓璁?43 瀛楃浠ヤ笂锛夈€傛鏌?curl 鎴?SDK 鐢熸垚鐨?`state` 鏄惁琚埅鏂€?
### 6. `OAuthClient.xxx does not exist in the current database`

璇存槑 `schema.prisma` 閲屾湁瀛楁浣嗘暟鎹簱娌℃湁銆傞€氬父鍥犱负杩佺Щ鏂囦欢涓?schema 涓嶅悓姝ャ€?
瑙ｅ喅锛?
1. 纭 `prisma/migrations/` 涓嬫槸鍚︽湁瀵瑰簲杩佺Щ銆?2. 濡傛灉娌℃湁锛屾墜鍔ㄥ垱寤鸿縼绉绘枃浠跺苟搴旂敤锛?
```bash
mkdir -p /var/www/NIHPLOD-Homepage.CN/prisma/migrations/20260729000000_xxx
cat > /var/www/NIHPLOD-Homepage.CN/prisma/migrations/20260729000000_xxx/migration.sql << 'EOF'
ALTER TABLE "OAuthClient" ADD COLUMN IF NOT EXISTS "xxx" TEXT[] DEFAULT '{}';
EOF
sudo -u postgres psql -d nihplod_db -f /var/www/NIHPLOD-Homepage.CN/prisma/migrations/20260729000000_xxx/migration.sql
cd /var/www/NIHPLOD-Homepage.CN && npx prisma migrate resolve --applied 20260729000000_xxx
```

### 7. 鍐呭瓨涓嶈冻瀵艰嚧 PM2 閲嶅惎

```bash
pm2 info skin-advisor | grep -A2 "restart"
```

### 8. Skin-Advisor 鍥剧墖 404

```bash
# 1. 纭鏂囦欢鏄惁鐪熷疄瀛樺湪
grep "alias" /etc/nginx/sites-available/skin-advisor | grep uploads
find /var/www/Skin-Advisor/public/uploads -type f | head -10

# 2. 纭 Nginx alias 鎸囧悜姝ｇ‘锛堝簲涓?public/uploads/锛岄潪 .next/standalone/...锛?# 3. 纭绗﹀彿閾炬帴瀛樺湪
ls -la /var/www/Skin-Advisor/.next/standalone/public/uploads

# 4. 娴嬭瘯
curl -I -H "Host: advisor.nihplod.cn" http://127.0.0.1/uploads/products/<鏂囦欢鍚?

# 5. 濡傛灉鏂囦欢缂哄け浣嗘暟鎹簱鏈夊紩鐢紝鍙兘鏄 cron 30 澶╂竻鐞嗚鍒?#    灞曞搧鍥惧簲鎵樼浜?public/uploads/products/ 瀛愮洰褰?```

### 9. BA 鎼滅储鍔熻兘寮傚父

纭 BA 鏁版嵁搴撳悓鏃舵墽琛屼簡锛?
```bash
npx prisma migrate deploy
sudo -u postgres psql -d ba_nihplod_db -f /var/www/BA-NIHPLOD/db/schema.sql
```

---

## 涔濄€佺敓浜х幆澧冨缓璁?
1. **鍚敤 RS256 + JWKS**锛氬湪瀹樼綉杩愯 `npx tsx scripts/generate-oauth-rs256-keys.ts`锛岄厤缃?`JWT_ACCESS_PRIVATE_KEY` / `JWT_ACCESS_PUBLIC_KEY` 涓?`JWT_ID_TOKEN_PRIVATE_KEY` / `JWT_ID_TOKEN_PUBLIC_KEY`锛屽瓙绔欏彲鏈湴楠岃瘉 access_token锛屽噺灏?introspection 缃戠粶寰€杩斻€?2. **鍒嗗竷寮忛儴缃插繀濉暟鎹簱榛戝悕鍗?*锛氱敓浜х幆澧冨姟蹇呰缃?`RATE_LIMIT_STORAGE=database` 涓?`TOKEN_BLACKLIST_STORAGE=database`锛岀‘淇濆瀹炰緥闂村叡浜?token 榛戝悕鍗曚笌闄愭祦鐘舵€併€?3. **Redis 榛戝悕鍗曪紙鍙€夛級**锛氶珮骞跺彂鎴栧瀹炰緥鍦烘櫙涓嬶紝鍙妸 access token 鎾ら攢鍜?wechat_exchange 鍏戞崲榛戝悕鍗曡縼鍒?Redis銆?4. **HTTPS 鍏ㄧ珯**锛歚__Host-` Cookie 鍜?OAuth 鍥炶皟閮借姹?HTTPS銆?5. **Cloudflare 鐪熷疄 IP**锛氱嚎涓婄粡杩?Cloudflare 鏃讹紝Nginx 蹇呴』閰嶇疆 `set_real_ip_from` 涓?`real_ip_header CF-Connecting-IP`銆?6. **瀵嗛挜杞浆**锛歚SSO_CLIENT_SECRET` 鍙湪瀹樼綉鍚庡彴銆岃疆鎹?Secret銆嶅悗鏇存柊瀛愮珯 `.env.production` 骞堕噸鍚紱JWT 瀵嗛挜杞浆闇€鎵€鏈夊疄渚嬪悓鏃舵洿鏂帮紝寤鸿浣庡嘲鏈熸墽琛屻€?7. **鏃ュ織鐩戞帶**锛氬叧娉ㄥ畼缃?`/api/oauth/*` 绔偣鐨?`invalid_grant`銆乣client_id_mismatch`銆乣invalid_scope` 绛夐敊璇€?8. **Scope 鏈€灏忓寲**锛氬瓙绔欏彧鐢宠闇€瑕佺殑 scope锛屽鍟嗗煄鐢?`openid profile phone`锛岃鍧涚敤 `openid profile`銆?9. **Cookie 鍓嶇紑**锛氬瓙绔?SDK 榛樿浣跨敤 `__Host-` 鍓嶇紑 Cookie锛孨ginx 蹇呴』浼犻€?`Host` 鍜?`X-Forwarded-Proto` 澶淬€?