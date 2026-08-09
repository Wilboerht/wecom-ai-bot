"""消息解析与构造测试"""

import pytest
from src.wecom.handler import (
    parse_encrypted_xml,
    parse_message_xml,
    build_text_reply_xml,
    build_encrypted_reply_xml,
)
from src.wecom.types import TextMessage, ImageMessage, VoiceMessage, EventMessage


ENCRYPTED_CALLBACK_XML = """<xml>
    <ToUserName><![CDATA[ww1234567890test]]></ToUserName>
    <AgentID><![CDATA[1000001]]></AgentID>
    <Encrypt><![CDATA[encrypted_content_here]]></Encrypt>
</xml>"""

TEXT_MESSAGE_XML = """<xml>
    <ToUserName><![CDATA[ww1234567890test]]></ToUserName>
    <FromUserName><![CDATA[user123]]></FromUserName>
    <CreateTime>1408091189</CreateTime>
    <MsgType><![CDATA[text]]></MsgType>
    <Content><![CDATA[你好，请问有什么产品？]]></Content>
    <MsgId>1234567890123456</MsgId>
    <AgentID>1000001</AgentID>
</xml>"""

EVENT_MESSAGE_XML = """<xml>
    <ToUserName><![CDATA[ww27c38ff0313efbec]]></ToUserName>
    <FromUserName><![CDATA[user456]]></FromUserName>
    <CreateTime>1408091200</CreateTime>
    <MsgType><![CDATA[event]]></MsgType>
    <Event><![CDATA[enter_agent]]></Event>
    <EventKey><![CDATA[]]></EventKey>
    <AgentID>1000001</AgentID>
</xml>"""

IMAGE_MESSAGE_XML = """<xml>
    <ToUserName><![CDATA[ww27c38ff0313efbec]]></ToUserName>
    <FromUserName><![CDATA[user789]]></FromUserName>
    <CreateTime>1408091300</CreateTime>
    <MsgType><![CDATA[image]]></MsgType>
    <PicUrl><![CDATA[http://example.com/pic.jpg]]></PicUrl>
    <MediaId><![CDATA[media_id_123]]></MediaId>
    <MsgId>1234567890123457</MsgId>
    <AgentID>1000001</AgentID>
</xml>"""

VOICE_MESSAGE_XML = """<xml>
    <ToUserName><![CDATA[ww27c38ff0313efbec]]></ToUserName>
    <FromUserName><![CDATA[user101]]></FromUserName>
    <CreateTime>1408091400</CreateTime>
    <MsgType><![CDATA[voice]]></MsgType>
    <MediaId><![CDATA[voice_media_456]]></MediaId>
    <Format><![CDATA[amr]]></Format>
    <MsgId>1234567890123458</MsgId>
    <AgentID>1000001</AgentID>
</xml>"""


class TestParseEncryptedXML:
    def test_parse_valid(self):
        data = parse_encrypted_xml(ENCRYPTED_CALLBACK_XML)
        assert data["encrypt"] == "encrypted_content_here"
        assert data["to_user_name"] == "ww1234567890test"
        assert data["agent_id"] == "1000001"

    def test_parse_missing_encrypt(self):
        with pytest.raises(ValueError, match="Encrypt"):
            parse_encrypted_xml("<xml><ToUserName>test</ToUserName></xml>")

    def test_parse_empty_encrypt(self):
        with pytest.raises(ValueError, match="Encrypt"):
            parse_encrypted_xml(
                "<xml><Encrypt></Encrypt><ToUserName>test</ToUserName></xml>"
            )


class TestParseMessageXML:
    def test_text_message(self):
        msg = parse_message_xml(TEXT_MESSAGE_XML)
        assert isinstance(msg, TextMessage)
        assert msg.msg_type == "text"
        assert msg.from_user == "user123"
        assert msg.to_user == "ww1234567890test"
        assert msg.content == "你好，请问有什么产品？"
        assert msg.msg_id == "1234567890123456"

    def test_event_message(self):
        msg = parse_message_xml(EVENT_MESSAGE_XML)
        assert isinstance(msg, EventMessage)
        assert msg.msg_type == "event"
        assert msg.from_user == "user456"
        assert msg.event == "enter_agent"

    def test_image_message(self):
        msg = parse_message_xml(IMAGE_MESSAGE_XML)
        assert isinstance(msg, ImageMessage)
        assert msg.msg_type == "image"
        assert msg.pic_url == "http://example.com/pic.jpg"
        assert msg.media_id == "media_id_123"

    def test_voice_message(self):
        msg = parse_message_xml(VOICE_MESSAGE_XML)
        assert isinstance(msg, VoiceMessage)
        assert msg.msg_type == "voice"
        assert msg.media_id == "voice_media_456"
        assert msg.format == "amr"


class TestBuildReplyXML:
    def test_text_reply(self):
        xml = build_text_reply_xml("corp", "user123", "你好，欢迎咨询")
        assert "<ToUserName><![CDATA[user123]]></ToUserName>" in xml
        assert "<FromUserName><![CDATA[corp]]></FromUserName>" in xml
        assert "<MsgType><![CDATA[text]]></MsgType>" in xml
        assert "<Content><![CDATA[你好，欢迎咨询]]></Content>" in xml

    def test_text_reply_special_chars(self):
        xml = build_text_reply_xml("corp", "user", "价格: ¥99 | 特价 💡")
        assert "¥99" in xml
        assert "💡" in xml

    def test_encrypted_reply(self):
        xml = build_encrypted_reply_xml(
            encrypt="abc123",
            msgsignature="def456",
            timestamp="1234567890",
            nonce="ghi789",
        )
        assert "<Encrypt><![CDATA[abc123]]></Encrypt>" in xml
        assert "<MsgSignature><![CDATA[def456]]></MsgSignature>" in xml
        assert "<TimeStamp>1234567890</TimeStamp>" in xml
        assert "<Nonce><![CDATA[ghi789]]></Nonce>" in xml
