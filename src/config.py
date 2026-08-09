import os
import json
from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    pass


class Config:
    def __init__(self):
        self.WECOM_CORP_ID = self._require("WECOM_CORP_ID")
        self.WECOM_AGENT_ID = self._require("WECOM_AGENT_ID")
        self.WECOM_SECRET = self._require("WECOM_SECRET")
        self.WECOM_TOKEN = self._require("WECOM_TOKEN")
        self.WECOM_ENCODING_AES_KEY = self._require("WECOM_ENCODING_AES_KEY")

        self._validate_token(self.WECOM_TOKEN)
        self._validate_aes_key(self.WECOM_ENCODING_AES_KEY)

        self.WX_APP_ID = os.getenv("WX_APP_ID", "")
        self.WX_TOKEN = os.getenv("WX_TOKEN", "")
        self.WX_ENCODING_AES_KEY = os.getenv("WX_ENCODING_AES_KEY", "")

        if self.WX_APP_ID:
            self._validate_token(self.WX_TOKEN)
            self._validate_aes_key(self.WX_ENCODING_AES_KEY)

        self.WECOM_SERVICE_USERID = os.getenv("WECOM_SERVICE_USERID", "")

        self.DEEPSEEK_API_KEY = self._require("DEEPSEEK_API_KEY")
        self.DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        self.SYSTEM_PROMPT = os.getenv(
            "SYSTEM_PROMPT",
            "你是一个专业的智能客服助手，请用简洁、友好的语言回答用户的问题。",
        )

        self.SESSION_TTL = int(os.getenv("SESSION_TTL", "1800"))
        self.LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

        self._accounts = {}
        self._load_accounts()

    def _load_accounts(self) -> None:
        path = os.getenv("KF_ACCOUNTS_FILE", "kf_accounts.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._accounts = json.load(f)

    def get_account(self, open_kfid: str) -> dict:
        """获取客服账号配置，未匹配时返回 _default"""
        return self._accounts.get(
            open_kfid,
            self._accounts.get("_default", {}),
        )

    def _require(self, key: str) -> str:
        value = os.getenv(key)
        if not value or value.startswith("请替换"):
            raise ConfigError(
                f"环境变量 {key} 未设置或仍为占位值，请在 .env 文件中配置"
            )
        return value

    def _validate_token(self, token: str) -> None:
        if not (3 <= len(token) <= 32):
            raise ConfigError(
                f"WECOM_TOKEN 长度必须为 3-32 位，当前长度: {len(token)}"
            )

    def _validate_aes_key(self, key: str) -> None:
        if len(key) != 43:
            raise ConfigError(
                f"WECOM_ENCODING_AES_KEY 长度必须为 43 位，当前长度: {len(key)}"
            )


try:
    config = Config()
except ConfigError as e:
    print(f"[启动失败] {e}")
    raise SystemExit(1) from e
