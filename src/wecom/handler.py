import time
import xml.etree.ElementTree as ET
from .types import Message, TextMessage, ImageMessage, VoiceMessage, EventMessage


def parse_encrypted_xml(xml_data: str) -> dict:
    """解析企业微信加密回调 XML 外层"""
    root = ET.fromstring(xml_data)
    encrypt = root.find("Encrypt")
    if encrypt is None or not encrypt.text:
        raise ValueError("XML 中未找到 Encrypt 节点或内容为空")

    return {
        "encrypt": encrypt.text,
        "to_user_name": root.findtext("ToUserName", ""),
        "agent_id": root.findtext("AgentID", ""),
    }


def parse_message_xml(xml_data: str) -> Message:
    """解析解密后的消息 XML

    异常契约：XML 畸形时原样抛出 xml.etree.ElementTree.ParseError（ET.ParseError），
    本函数不做局部捕获；调用方需自行 try/except 并按处理失败降级。
    """
    root = ET.fromstring(xml_data)

    msg_type = root.findtext("MsgType", "")
    from_user = root.findtext("FromUserName", "")
    to_user = root.findtext("ToUserName", "")
    create_time = root.findtext("CreateTime", "")
    msg_id = root.findtext("MsgId", "")

    if msg_type == "text":
        return TextMessage(
            from_user=from_user,
            to_user=to_user,
            create_time=create_time,
            msg_id=msg_id,
            content=root.findtext("Content", ""),
        )

    if msg_type == "image":
        return ImageMessage(
            from_user=from_user,
            to_user=to_user,
            create_time=create_time,
            msg_id=msg_id,
            pic_url=root.findtext("PicUrl", ""),
            media_id=root.findtext("MediaId", ""),
        )

    if msg_type == "voice":
        return VoiceMessage(
            from_user=from_user,
            to_user=to_user,
            create_time=create_time,
            msg_id=msg_id,
            media_id=root.findtext("MediaId", ""),
            format=root.findtext("Format", ""),
        )

    if msg_type == "event":
        return EventMessage(
            from_user=from_user,
            to_user=to_user,
            create_time=create_time,
            msg_id=msg_id,
            event=root.findtext("Event", ""),
            event_key=root.findtext("EventKey", ""),
        )

    return Message(
        msg_type=msg_type,
        from_user=from_user,
        to_user=to_user,
        create_time=create_time,
        msg_id=msg_id,
    )


def build_text_reply_xml(from_user: str, to_user: str, content: str) -> str:
    """构造文本回复消息 XML（明文，用于加密前）"""
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{_cdata_safe(to_user)}]]></ToUserName>"
        f"<FromUserName><![CDATA[{_cdata_safe(from_user)}]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{_cdata_safe(content)}]]></Content>"
        "</xml>"
    )


def build_encrypted_reply_xml(
    encrypt: str,
    msgsignature: str,
    timestamp: str,
    nonce: str,
) -> str:
    """构造加密回复消息 XML（企微接收格式）"""
    return (
        "<xml>"
        f"<Encrypt><![CDATA[{_cdata_safe(encrypt)}]]></Encrypt>"
        f"<MsgSignature><![CDATA[{_cdata_safe(msgsignature)}]]></MsgSignature>"
        f"<TimeStamp>{timestamp}</TimeStamp>"
        f"<Nonce><![CDATA[{_cdata_safe(nonce)}]]></Nonce>"
        "</xml>"
    )


def _cdata_safe(text: str) -> str:
    """确保文本可安全放入 CDATA: 如果含 ]]> 则拆分为两个 CDATA"""
    if "]]>" not in text:
        return text
    return text.replace("]]>", "]]]]><![CDATA[>")
