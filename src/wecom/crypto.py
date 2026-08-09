"""
企业微信消息加解密 (WXBizMsgCrypt)

基于企业微信官方文档实现:
https://developer.work.weixin.qq.com/document/path/90968

加密流程: random(16B) + msg_len(4B, 网络字节序) + msg + corp_id → PKCS7 → AES-256-CBC
解密流程: Base64解码 → AES解密 → 去PKCS7 → 解析 random + len + msg + corp_id
签名算法: SHA1(sort([token, timestamp, nonce, encrypt_msg]))
"""

import base64
import hashlib
import random
import string
import struct
import time
from Crypto.Cipher import AES


def pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def pkcs7_unpad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = data[-1]
    if pad_len < 1 or pad_len > block_size:
        raise ValueError(f"Invalid PKCS7 padding byte: {pad_len}")
    if data[-pad_len:] != bytes([pad_len] * pad_len):
        raise ValueError("PKCS7 padding validation failed")
    return data[:-pad_len]


class SignatureError(Exception):
    pass


class DecryptError(Exception):
    pass


class WXBizMsgCrypt:
    """
    企业微信消息加解密工具

    Args:
        token: 回调配置中的 Token (3-32位)
        encoding_aes_key: 回调配置中的 EncodingAESKey (43位)
        corp_id: 企业微信 CorpID
    """

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id

        if len(encoding_aes_key) != 43:
            raise ValueError(
                f"EncodingAESKey 长度必须为 43 位，当前: {len(encoding_aes_key)}"
            )

        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        if len(self.aes_key) != 32:
            raise ValueError(
                f"EncodingAESKey 解码后应为 32 字节，实际 {len(self.aes_key)} 字节"
            )

    def _compute_signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        params = sorted([self.token, timestamp, nonce, encrypt])
        raw = "".join(params)
        return hashlib.sha1(raw.encode()).hexdigest()

    def _generate_nonce(self) -> str:
        return "".join(
            random.choices(string.ascii_letters + string.digits, k=16)
        )

    def _generate_timestamp(self) -> str:
        return str(int(time.time()))

    def verify_url(
        self, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> bytes:
        """
        验证回调 URL（企业微信配置回调时发送 GET 请求）

        1. 验证签名
        2. 解密 echostr
        3. 验证 CorpID
        4. 返回明文

        Raises:
            SignatureError: 签名验证失败
            DecryptError: 解密失败
        """
        computed = self._compute_signature(timestamp, nonce, echostr)
        if computed != msg_signature:
            raise SignatureError(
                f"签名验证失败: computed={computed}, expected={msg_signature}"
            )

        try:
            encrypted = base64.b64decode(echostr)
            iv = self.aes_key[:16]
            cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted)
            decrypted = pkcs7_unpad(decrypted)
        except Exception as e:
            raise DecryptError(f"解密失败: {e}") from e

        content = decrypted[16:]
        msg_len = struct.unpack("!I", content[:4])[0]
        msg = content[4 : 4 + msg_len]
        corp_id = content[4 + msg_len :].decode("utf-8")

        if corp_id != self.corp_id:
            raise DecryptError(f"CorpID 不匹配: {corp_id} != {self.corp_id}")

        return msg

    def decrypt_message(
        self, msg_signature: str, timestamp: str, nonce: str, encrypt: str
    ) -> str:
        """
        解密企业微信回调消息

        1. 验证签名
        2. Base64 解码
        3. AES-256-CBC 解密
        4. 去除 PKCS7 填充
        5. 解析: random(16B) + msg_len(4B) + msg + corp_id
        6. 验证 CorpID，返回明文 XML

        Raises:
            SignatureError: 签名验证失败
            DecryptError: 解密失败或 CorpID 不匹配
        """
        computed = self._compute_signature(timestamp, nonce, encrypt)
        if computed != msg_signature:
            raise SignatureError(
                f"签名验证失败: computed={computed}, expected={msg_signature}"
            )

        try:
            encrypted = base64.b64decode(encrypt)
            iv = self.aes_key[:16]
            cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
            decrypted = cipher.decrypt(encrypted)
            decrypted = pkcs7_unpad(decrypted)
        except Exception as e:
            raise DecryptError(f"解密失败: {e}") from e

        content = decrypted[16:]
        msg_len = struct.unpack("!I", content[:4])[0]
        msg = content[4 : 4 + msg_len].decode("utf-8")
        corp_id = content[4 + msg_len :].decode("utf-8")

        if corp_id != self.corp_id:
            raise DecryptError(
                f"CorpID 不匹配: expected={self.corp_id}, got={corp_id}"
            )

        return msg

    def encrypt_message(
        self,
        msg: str,
        nonce: str | None = None,
        timestamp: str | None = None,
    ) -> dict:
        """
        加密回复消息

        Args:
            msg: 明文 XML 消息
            nonce: 随机字符串（可选，自动生成）
            timestamp: 时间戳（可选，自动生成）

        Returns:
            dict with keys: encrypt, msgsignature, timestamp, nonce
        """
        nonce = nonce or self._generate_nonce()
        timestamp = timestamp or self._generate_timestamp()

        random_bytes = bytes(random.randint(0, 255) for _ in range(16))
        msg_bytes = msg.encode("utf-8")
        msg_len_bytes = struct.pack("!I", len(msg_bytes))
        corp_id_bytes = self.corp_id.encode("utf-8")

        raw = random_bytes + msg_len_bytes + msg_bytes + corp_id_bytes
        padded = pkcs7_pad(raw)

        iv = self.aes_key[:16]
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(padded)
        encrypt_b64 = base64.b64encode(encrypted).decode()

        signature = self._compute_signature(timestamp, nonce, encrypt_b64)

        return {
            "encrypt": encrypt_b64,
            "msgsignature": signature,
            "timestamp": timestamp,
            "nonce": nonce,
        }
