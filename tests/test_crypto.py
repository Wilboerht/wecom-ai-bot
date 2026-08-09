"""消息加解密模块测试"""

import pytest
from src.wecom.crypto import (
    WXBizMsgCrypt,
    SignatureError,
    DecryptError,
    pkcs7_pad,
    pkcs7_unpad,
)


CORP_ID = "ww1234567890test"
TOKEN = "TestToken123"
AES_KEY = "abcDefGhijKlmnOpqRstUvwXyz12345678901234567"


class TestPKCS7:
    def test_pad_and_unpad_roundtrip(self):
        data = b"hello world"
        padded = pkcs7_pad(data)
        assert len(padded) % 32 == 0
        assert pkcs7_unpad(padded) == data

    def test_pad_empty(self):
        padded = pkcs7_pad(b"")
        assert len(padded) == 32
        assert pkcs7_unpad(padded) == b""

    def test_pad_exact_block(self):
        data = b"A" * 32
        padded = pkcs7_pad(data)
        assert len(padded) == 64
        assert pkcs7_unpad(padded) == data

    def test_pad_chinese(self):
        data = "你好世界".encode("utf-8")
        padded = pkcs7_pad(data)
        assert len(padded) % 32 == 0
        assert pkcs7_unpad(padded) == data

    def test_unpad_invalid(self):
        data = b"\x00" * 32
        with pytest.raises(ValueError):
            pkcs7_unpad(data)


class TestWXBizMsgCrypt:
    def test_init_invalid_aes_key_length(self):
        with pytest.raises(ValueError, match="长度必须为 43 位"):
            WXBizMsgCrypt(TOKEN, "short", CORP_ID)

    def test_init_success(self):
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        assert c.token == TOKEN
        assert c.corp_id == CORP_ID
        assert len(c.aes_key) == 32

    def test_encrypt_decrypt_roundtrip(self):
        """加密后解密能还原明文"""
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        msg = "<xml><Content>你好，企业微信</Content></xml>"

        result = c.encrypt_message(msg)
        assert "encrypt" in result
        assert "msgsignature" in result
        assert "timestamp" in result
        assert "nonce" in result

        decrypted = c.decrypt_message(
            msg_signature=result["msgsignature"],
            timestamp=result["timestamp"],
            nonce=result["nonce"],
            encrypt=result["encrypt"],
        )
        assert decrypted == msg

    def test_encrypt_decrypt_emoji(self):
        """加密解密支持 emoji"""
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        msg = "产品名称: Pro Max 🚀💡✨ 售价: ¥999"

        result = c.encrypt_message(msg)
        decrypted = c.decrypt_message(
            msg_signature=result["msgsignature"],
            timestamp=result["timestamp"],
            nonce=result["nonce"],
            encrypt=result["encrypt"],
        )
        assert decrypted == msg

    def test_encrypt_decrypt_long_text(self):
        """加密解密长文本"""
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        msg = "长" * 2000

        result = c.encrypt_message(msg)
        decrypted = c.decrypt_message(
            msg_signature=result["msgsignature"],
            timestamp=result["timestamp"],
            nonce=result["nonce"],
            encrypt=result["encrypt"],
        )
        assert decrypted == msg

    def test_signature_verification_failure(self):
        """签名错误应抛出 SignatureError"""
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        result = c.encrypt_message("test")

        with pytest.raises(SignatureError):
            c.decrypt_message(
                msg_signature="invalid_signature",
                timestamp=result["timestamp"],
                nonce=result["nonce"],
                encrypt=result["encrypt"],
            )

    def test_corp_id_mismatch(self):
        """CorpID 不匹配应报错"""
        correct = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        wrong = WXBizMsgCrypt(TOKEN, AES_KEY, "wrong_corp_id")
        msg = "test"

        result = correct.encrypt_message(msg)
        with pytest.raises(Exception, match="CorpID"):
            wrong.decrypt_message(
                msg_signature=result["msgsignature"],
                timestamp=result["timestamp"],
                nonce=result["nonce"],
                encrypt=result["encrypt"],
            )

    def test_verify_url_roundtrip(self):
        """URL 验证流程：加密 echostr → 解密还原"""
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        echostr_plain = b"19890874671278"

        # 模拟企业微信发送的加密 echostr
        result = c.encrypt_message(echostr_plain.decode())
        echostr_encrypted = result["encrypt"]

        # 解密
        decrypted = c.verify_url(
            msg_signature=result["msgsignature"],
            timestamp=result["timestamp"],
            nonce=result["nonce"],
            echostr=echostr_encrypted,
        )
        assert decrypted == echostr_plain

    def test_verify_url_bad_signature(self):
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        result = c.encrypt_message("test")

        with pytest.raises(SignatureError):
            c.verify_url(
                msg_signature="wrong",
                timestamp=result["timestamp"],
                nonce=result["nonce"],
                echostr=result["encrypt"],
            )

    def test_encrypt_message_custom_nonce_timestamp(self):
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        result = c.encrypt_message("test", nonce="mynonce", timestamp="1234567890")
        assert result["nonce"] == "mynonce"
        assert result["timestamp"] == "1234567890"

    def test_compute_signature_deterministic(self):
        c = WXBizMsgCrypt(TOKEN, AES_KEY, CORP_ID)
        sig1 = c._compute_signature("123", "abc", "encrypt_data")
        sig2 = c._compute_signature("123", "abc", "encrypt_data")
        assert sig1 == sig2
