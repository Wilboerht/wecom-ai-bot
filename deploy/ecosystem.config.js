module.exports = {
    apps: [{
        name: 'wecom-ai',
        script: 'venv/bin/python3',
        args: '-m uvicorn src.main:app --host 127.0.0.1 --port 8080',
        cwd: '/var/www/wecom-ai-bot',
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
        max_memory_restart: '300M',
        kill_timeout: 5000,
        listen_timeout: 10000,
        env_file: './.env',
    }]
};
