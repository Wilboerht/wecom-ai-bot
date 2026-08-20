#!/bin/bash
# NIHPLOD AI 客服 — ECS 一键部署脚本
# 使用方法: chmod +x deploy.sh && ./deploy.sh

set -e

APP_DIR="/opt/wecom-ai-bot"
read -p "输入你的域名: " DOMAIN

echo "=== 安装系统依赖 ==="
apt-get update -qq && apt-get install -y python3-pip python3-dev python3-venv nginx certbot python3-certbot-nginx -qq

echo "=== 部署应用 ==="
mkdir -p $APP_DIR
cp -r src/ knowledge.txt knowledge_skincare.txt links.json kf_accounts.json requirements.txt .env $APP_DIR/
cp -r deploy/ $APP_DIR/

echo "=== 创建虚拟环境并安装依赖 ==="
python3 -m venv $APP_DIR/venv
$APP_DIR/venv/bin/pip install -r $APP_DIR/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

echo "=== 授权应用目录给 www-data ==="
chown -R www-data:www-data $APP_DIR
chmod 600 $APP_DIR/.env

echo "=== 配置 systemd 服务 ==="
cp $APP_DIR/deploy/wecom-ai.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable wecom-ai
systemctl restart wecom-ai

echo "=== 配置 Nginx (先装 80 端口引导配置, 供 certbot 验证域名) ==="
cat > /etc/nginx/sites-available/wecom-ai <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    location / {
        default_type text/plain;
        return 200 'ok';
    }
}
EOF
ln -sf /etc/nginx/sites-available/wecom-ai /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "=== 申请 SSL 证书 ==="
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@$DOMAIN"

echo "=== 安装完整 Nginx 配置 (替换域名占位符) ==="
sed "s/__DOMAIN__/$DOMAIN/g" $APP_DIR/deploy/nginx.conf > /etc/nginx/sites-available/wecom-ai
nginx -t && systemctl reload nginx

echo "=== 部署完成 ==="
echo "健康检查: curl https://$DOMAIN/health"
echo "企微回调URL: https://$DOMAIN/callback"
