import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    pass


class Config:
    def __init__(self):
        self.WECOM_CORP_ID = self._require("WECOM_CORP_ID")
        self.WECOM_AGENT_ID = os.getenv("WECOM_AGENT_ID", "")
        self.WECOM_SECRET = self._require("WECOM_SECRET")
        self.WECOM_TOKEN = self._require("WECOM_TOKEN")
        self.WECOM_ENCODING_AES_KEY = self._require("WECOM_ENCODING_AES_KEY")

        self._validate_token(self.WECOM_TOKEN)
        self._validate_aes_key(self.WECOM_ENCODING_AES_KEY)

        self.WX_APP_ID = os.getenv("WX_APP_ID", "")
        self.WX_TOKEN = os.getenv("WX_TOKEN", "")
        self.WX_ENCODING_AES_KEY = os.getenv("WX_ENCODING_AES_KEY", "")

        if self.WX_APP_ID:
            self._validate_token(self.WX_TOKEN, "WX_TOKEN")
            self._validate_aes_key(self.WX_ENCODING_AES_KEY, "WX_ENCODING_AES_KEY")

        self.WECOM_SERVICE_USERID = os.getenv("WECOM_SERVICE_USERID", "")

        self.DEEPSEEK_API_KEY = self._require("DEEPSEEK_API_KEY")
        self.DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        # 单次回复的最大 token 数，非法值回退默认 1000（不阻断启动）
        self.DEEPSEEK_MAX_TOKENS = self._optional_int("DEEPSEEK_MAX_TOKENS", 1000)

        self.SYSTEM_PROMPT = os.getenv(
            "SYSTEM_PROMPT",
            "你是一个专业的智能客服助手，请用简洁、友好的语言回答用户的问题。",
        )

        self.SESSION_TTL = self._require_int("SESSION_TTL", 1800)
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
        self.REDIS_URL = os.getenv("REDIS_URL", "")
        self.SERVICE_START_HOUR = self._require_int("SERVICE_START_HOUR", 10)
        self.SERVICE_END_HOUR = self._require_int("SERVICE_END_HOUR", 20)
        self._validate_service_hours()

        # 测肤报告联动（enter_session → scene_param → 拉取报告写档案）
        self.INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
        self.ADVISOR_REPORT_API_URL = os.getenv(
            "ADVISOR_REPORT_API_URL",
            "https://advisor-api.example.com/api/internal/report-summary",
        )
        self.KF_ADVISOR_OPEN_KFID = os.getenv("KF_ADVISOR_OPEN_KFID", "")
        self.KF_SCENE = os.getenv("KF_SCENE", "advisor")
        self.KF_SEND_WELCOME = os.getenv("KF_SEND_WELCOME", "1") == "1"

        self._accounts = {}
        self._load_accounts()

    def _require_int(self, key: str, default: int) -> int:
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            raise ConfigError(f"环境变量 {key} 值无效，需为数字: {os.getenv(key)}") from None

    def _optional_int(self, key: str, default: int) -> int:
        """非关键整数配置：非法值回退默认值，不阻断启动"""
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    def _validate_service_hours(self) -> None:
        # core.py is_within_service_hours() 用 start <= hour < end 判断，
        # 不支持跨午夜窗口（如 22 点到次日 6 点），故要求 0 <= START < END <= 23
        for name, hour in (
            ("SERVICE_START_HOUR", self.SERVICE_START_HOUR),
            ("SERVICE_END_HOUR", self.SERVICE_END_HOUR),
        ):
            if not (0 <= hour <= 23):
                raise ConfigError(f"{name} 必须在 0-23 之间，当前: {hour}")
        if self.SERVICE_START_HOUR >= self.SERVICE_END_HOUR:
            raise ConfigError(
                "SERVICE_START_HOUR 必须小于 SERVICE_END_HOUR（不支持跨午夜），"
                f"当前: {self.SERVICE_START_HOUR} >= {self.SERVICE_END_HOUR}"
            )

    def _load_accounts(self) -> None:
        path = os.getenv("KF_ACCOUNTS_FILE", "kf_accounts.json")
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                accounts = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"客服账号配置文件 {path} 不是合法 JSON: {e}") from None
        if not isinstance(accounts, dict):
            raise ConfigError(f"客服账号配置文件 {path} 顶层必须是 JSON 对象")
        self._accounts = accounts
        self._validate_accounts(path)

    def _validate_accounts(self, path: str) -> None:
        for open_kfid, account in self._accounts.items():
            if not isinstance(account, dict):
                raise ConfigError(
                    f"客服账号配置文件 {path} 中账号 {open_kfid} 的配置必须是 JSON 对象"
                )
            # _default 是未匹配时的兜底配置（可能只有 welcome），跳过必填字段校验
            if open_kfid == "_default":
                continue
            for field in ("welcome", "knowledge_file"):
                if not account.get(field):
                    raise ConfigError(
                        f"客服账号配置文件 {path} 中账号 {open_kfid} 缺少必填字段: {field}"
                    )
            # 提示词二选一：外部文件（推荐，多账号共用+热加载）或内联 system_prompt
            if not (account.get("system_prompt") or account.get("system_prompt_file")):
                raise ConfigError(
                    f"客服账号配置文件 {path} 中账号 {open_kfid} 缺少必填字段: "
                    "system_prompt 或 system_prompt_file"
                )
            prompt_file = account.get("system_prompt_file")
            if prompt_file and not os.path.exists(prompt_file):
                # 不阻断：运行期会回退内联/默认提示词并记警告
                logger.warning(
                    "%s 中账号 %s 的 system_prompt_file 不存在: %s",
                    path, open_kfid, prompt_file,
                )
            knowledge_file = account["knowledge_file"]
            if not os.path.exists(knowledge_file):
                # 不阻断：多账号配置里可能有暂未启用的账号
                logger.warning(
                    "%s 中账号 %s 的 knowledge_file 不存在: %s",
                    path, open_kfid, knowledge_file,
                )

    def get_account(self, open_kfid: str) -> dict:
        """获取客服账号配置，未匹配时返回 _default"""
        return self._accounts.get(
            open_kfid,
            self._accounts.get("_default", {}),
        )

    def _require(self, key: str) -> str:
        value = os.getenv(key)
        # 拦截占位值：中文「请替换」前缀及 .env.example 的 your_ 前缀
        if not value or value.startswith("请替换") or value.startswith("your_"):
            raise ConfigError(
                f"环境变量 {key} 未设置或仍为占位值，请在 .env 文件中配置"
            )
        return value

    def _validate_token(self, token: str, name: str = "TOKEN") -> None:
        if not (3 <= len(token) <= 32):
            raise ConfigError(f"{name} 长度必须为 3-32 位，当前: {len(token)}")

    def _validate_aes_key(self, key: str, name: str = "WECOM_ENCODING_AES_KEY") -> None:
        if len(key) != 43:
            raise ConfigError(
                f"{name} 长度必须为 43 位，当前长度: {len(key)}"
            )


try:
    config = Config()
except ConfigError as e:
    print(f"[启动失败] {e}")
    raise SystemExit(1) from e
