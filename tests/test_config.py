"""config.py 启动时校验测试

config.py 在 import 时即校验并创建全局 config，校验失败打印 [启动失败] 后
SystemExit(1)。为避免污染当前进程的全局 config，一律用子进程 import 验证。
环境变量显式传入会覆盖 .env（load_dotenv 不覆盖已存在的变量）。
"""

import json
import os
import subprocess
import sys


def _import_config(extra_env: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **extra_env}
    return subprocess.run(
        [sys.executable, "-c", "import src.config"],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )


class TestServiceHoursValidation:
    """core.py 的时间窗判断为 start <= hour < end，不支持跨午夜，
    故 config 校验 0-23 范围且 START < END"""

    def test_start_hour_out_of_range_rejected(self):
        result = _import_config({"SERVICE_START_HOUR": "25"})
        assert result.returncode != 0
        assert "SERVICE_START_HOUR 必须在 0-23 之间" in result.stdout

    def test_end_hour_negative_rejected(self):
        result = _import_config({"SERVICE_END_HOUR": "-1"})
        assert result.returncode != 0
        assert "SERVICE_END_HOUR 必须在 0-23 之间" in result.stdout

    def test_cross_midnight_rejected(self):
        """22 点到次日 6 点这类跨午夜窗口不被 core.py 支持，启动时拒绝"""
        result = _import_config({"SERVICE_START_HOUR": "22", "SERVICE_END_HOUR": "6"})
        assert result.returncode != 0
        assert "必须小于 SERVICE_END_HOUR" in result.stdout

    def test_valid_hours_accepted(self):
        result = _import_config({"SERVICE_START_HOUR": "9", "SERVICE_END_HOUR": "21"})
        assert result.returncode == 0, result.stdout + result.stderr


class TestKfAccountsLoading:
    def test_invalid_json_friendly_error(self, tmp_path):
        bad_file = tmp_path / "bad_accounts.json"
        bad_file.write_text("{ not valid json", encoding="utf-8")
        result = _import_config({"KF_ACCOUNTS_FILE": str(bad_file)})
        assert result.returncode != 0
        assert "不是合法 JSON" in result.stdout
        # 是包装后的 ConfigError 提示，而非裸 JSONDecodeError  traceback
        assert "JSONDecodeError" not in result.stderr

    def test_missing_required_field_rejected(self, tmp_path):
        bad_file = tmp_path / "missing_field.json"
        bad_file.write_text(
            '{"wkTest123": {"welcome": "hi", "system_prompt": "prompt"}}',
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(bad_file)})
        assert result.returncode != 0
        assert "缺少必填字段: knowledge_file" in result.stdout

    def test_valid_accounts_accepted(self, tmp_path):
        knowledge = tmp_path / "knowledge.txt"
        knowledge.write_text("知识库", encoding="utf-8")
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            json.dumps({
                "_default": {"welcome": "hi"},
                "wkTest123": {
                    "welcome": "hi",
                    "system_prompt": "p",
                    "knowledge_file": knowledge.as_posix(),
                },
            }),
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_missing_knowledge_file_only_warns(self, tmp_path):
        """knowledge_file 路径不存在只打 warning，不阻断启动"""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            '{"wkTest123": {"welcome": "hi", "system_prompt": "p",'
            ' "knowledge_file": "not_exists_knowledge.txt"}}',
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode == 0, result.stdout + result.stderr
