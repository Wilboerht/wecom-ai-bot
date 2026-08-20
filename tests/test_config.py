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

    def test_missing_knowledge_file_blocks_startup(self, tmp_path):
        """knowledge_file 路径不存在阻断启动：空知识库静默降级比启动失败更危险"""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            '{"wkTest123": {"welcome": "hi", "system_prompt": "p",'
            ' "knowledge_file": "not_exists_knowledge.txt"}}',
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode != 0
        assert "knowledge_file 不存在" in result.stdout

    def test_missing_prompt_both_rejected(self, tmp_path):
        """system_prompt 与 system_prompt_file 都缺失 → 阻断"""
        knowledge = tmp_path / "knowledge.txt"
        knowledge.write_text("知识库", encoding="utf-8")
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            json.dumps({"wkTest123": {
                "welcome": "hi",
                "knowledge_file": knowledge.as_posix(),
            }}),
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode != 0
        assert "system_prompt 或 system_prompt_file" in result.stdout

    def test_prompt_file_missing_only_warns(self, tmp_path):
        """system_prompt_file 不存在不阻断：运行期回退内联/默认提示词"""
        knowledge = tmp_path / "knowledge.txt"
        knowledge.write_text("知识库", encoding="utf-8")
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            json.dumps({"wkTest123": {
                "welcome": "hi",
                "system_prompt_file": "prompts/not_exists.txt",
                "knowledge_file": knowledge.as_posix(),
            }}),
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode == 0, result.stdout + result.stderr

    def test_bool_field_wrong_type_rejected(self, tmp_path):
        """order_flow/knowledge_split/advisor_report 非布尔 → 阻断"""
        knowledge = tmp_path / "knowledge.txt"
        knowledge.write_text("知识库", encoding="utf-8")
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            json.dumps({"wkTest123": {
                "welcome": "hi",
                "system_prompt": "p",
                "knowledge_file": knowledge.as_posix(),
                "order_flow": "yes",
            }}),
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode != 0
        assert "order_flow 必须是布尔值" in result.stdout

    def test_list_field_wrong_type_rejected(self, tmp_path):
        """service_userid/transfer_keywords 类型错误 → 阻断"""
        knowledge = tmp_path / "knowledge.txt"
        knowledge.write_text("知识库", encoding="utf-8")
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text(
            json.dumps({"wkTest123": {
                "welcome": "hi",
                "system_prompt": "p",
                "knowledge_file": knowledge.as_posix(),
                "transfer_keywords": {"k": 1},
            }}),
            encoding="utf-8",
        )
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode != 0
        assert "transfer_keywords 必须是字符串或字符串数组" in result.stdout

    def test_account_not_dict_rejected(self, tmp_path):
        """账号配置非 JSON 对象 → 阻断"""
        accounts_file = tmp_path / "accounts.json"
        accounts_file.write_text('{"wkTest123": "just a string"}', encoding="utf-8")
        result = _import_config({"KF_ACCOUNTS_FILE": str(accounts_file)})
        assert result.returncode != 0
        assert "必须是 JSON 对象" in result.stdout
