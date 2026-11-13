module.exports = {
    apps: [{
        name: 'wecom-ai',
        script: 'venv/bin/python3',
        // 访问日志由 Nginx 统一记录（已配置脱敏格式）；关闭 uvicorn access log，
        // 避免 /api/ 查询参数中的 external_userid/session_id 进入应用 stdout。
        args: '-m uvicorn src.main:app --host 127.0.0.1 --port 8080 --no-access-log',
        cwd: '/var/www/wecom-ai-bot',
        // 当前队列 consumer、用户锁、短链缓存和指标都是进程内状态；多实例需先改分布式实现
        instances: 1,
        exec_mode: 'fork',
        log_file: './logs/combined.log',
        out_file: './logs/out.log',
        error_file: './logs/error.log',
        log_date_format: 'YYYY-MM-DD HH:mm:ss Z',
        merge_logs: true,
        autorestart: true,
        max_restarts: 10,
        min_uptime: '10s',
        max_memory_restart: '512M',
        kill_timeout: 5000,
        listen_timeout: 10000,
        // 环境变量由应用内 python-dotenv 从 ./.env 加载 (PM2 不支持 env_file 键, 勿加)
        // 日志轮转: 使用 pm2-logrotate (pm2 install pm2-logrotate)
    }]
};
