import pytest
from src.wecom.crypto import WXBizMsgCrypt


CORP_ID = "ww1234567890test"
TOKEN = "TestToken123"
AES_KEY = "abcDefGhijKlmnOpqRstUvwXyz12345678901234567"


@pytest.fixture
def crypto():
    return WXBizMsgCrypt(token=TOKEN, encoding_aes_key=AES_KEY, corp_id=CORP_ID)
