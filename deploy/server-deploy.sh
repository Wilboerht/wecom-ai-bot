#!/bin/bash
# NIHPLOD AI 客服 — 服务器端部署脚本（PM2 canonical）
# 使用: 先将项目文件上传/ git pull 到 $APP_DIR，然后在服务器上执行此脚本。
# 生产路径以服务器配置文档为准：/var/www/wecom-ai-bot + PM2。
set -e

APP_DIR="/var/www/wecom-ai-bot"
cd "$APP_DIR"

echo "=== 检查运行环境 ==="
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("需要 Python >= 3.10，当前: %s" % sys.version.split()[0])
PY

if [ ! -f ".env" ]; then
  echo "错误: 缺少 $APP_DIR/.env" >&2
  exit 1
fi

if head -c 3 .env | od -An -tx1 | tr -d ' \n' | grep -qi '^efbbbf$'; then
  echo "错误: .env 带 UTF-8 BOM，请先保存为 UTF-8 无 BOM 再部署" >&2
  exit 1
fi

for f in kf_accounts.json knowledge.txt knowledge_skincare.txt links.json prompts/brand_cs.txt prompts/skincare_advisor.txt; do
  if [ ! -e "$f" ]; then
    echo "错误: 缺少部署文件 $APP_DIR/$f" >&2
    exit 1
  fi
done

echo "=== Redis 检查（可选） ==="
if command -v redis-cli >/dev/null 2>&1 && redis-cli ping >/dev/null 2>&1; then
  echo "Redis 可用"
else
  echo "警告: Redis 不可用，将使用内存模式；进程重启会丢失会话/档案/隐私授权。" >&2
fi

echo "=== 创建虚拟环境并安装依赖 ==="
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple -q
deactivate

echo "=== 创建日志目录 ==="
mkdir -p logs

echo "=== 安装/启动 PM2 ==="
if ! command -v pm2 >/dev/null 2>&1; then
  npm i -g pm2
fi

pm2 startOrReload deploy/ecosystem.config.js
pm2 save

echo "=== 验证 ==="
sleep 2
curl -fsS http://127.0.0.1:8080/health
echo ""
echo "部署完成!"
