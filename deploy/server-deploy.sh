#!/bin/bash
# NIHPLOD AI 客服 — 服务器端部署脚本
# 使用: 先 scp 上传项目文件, 然后在服务器上执行此脚本
set -e

APP_DIR="/var/www/wecom-ai-bot"
echo "=== 安装系统依赖 ==="
apt-get update -qq && apt-get install -y python3-full python3-pip python3-venv -qq

echo "=== 创建虚拟环境 ==="
python3 -m venv $APP_DIR/venv
source $APP_DIR/venv/bin/activate

echo "=== 安装 Python 依赖 ==="
pip install -r $APP_DIR/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo "=== 创建日志目录 ==="
mkdir -p $APP_DIR/logs
deactivate

echo "=== PM2 启动 ==="
pm2 start $APP_DIR/deploy/ecosystem.config.js
pm2 save

echo "=== 验证 ==="
sleep 2
curl -s http://127.0.0.1:8080/health
echo ""
echo "部署完成!"
