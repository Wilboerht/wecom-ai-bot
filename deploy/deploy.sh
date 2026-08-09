#!/bin/bash
# NIHPLOD AI 客服 — ECS 一键部署脚本
# 使用方法: chmod +x deploy.sh && ./deploy.sh

set -e

APP_DIR="/opt/wecom-ai-bot"
echo "=== 安装系统依赖 ==="
apt-get update -qq && apt-get install -y python3-pip python3-dev nginx certbot python3-certbot-nginx -qq

echo "=== 部署应用 ==="
mkdir -p $APP_DIR
cp -r src/ knowledge.txt requirements.txt .env $APP_DIR/
cd $APP_DIR
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q

echo "=== 配置 systemd 服务 ==="
cp deploy/wecom-ai.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable wecom-ai
systemctl restart wecom-ai

echo "=== 配置 Nginx ==="
cp deploy/nginx.conf /etc/nginx/sites-available/wecom-ai
ln -sf /etc/nginx/sites-available/wecom-ai /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "=== 申请 SSL 证书 ==="
read -p "输入你的域名: " DOMAIN
certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@$DOMAIN

echo "=== 部署完成 ==="
echo "健康检查: curl https://$DOMAIN/health"
echo "企微回调URL: https://$DOMAIN/callback"
