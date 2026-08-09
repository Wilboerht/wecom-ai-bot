from dataclasses import dataclass


@dataclass
class Message:
    msg_type: str = ""
    from_user: str = ""
    to_user: str = ""
    create_time: str = ""
    msg_id: str = ""


@dataclass
class TextMessage(Message):
    msg_type: str = "text"
    content: str = ""


@dataclass
class ImageMessage(Message):
    msg_type: str = "image"
    pic_url: str = ""
    media_id: str = ""


@dataclass
class VoiceMessage(Message):
    msg_type: str = "voice"
    media_id: str = ""
    format: str = ""


@dataclass
class EventMessage(Message):
    msg_type: str = "event"
    event: str = ""
    event_key: str = ""


EVENT_ENTER_AGENT = "enter_agent"
EVENT_SUBSCRIBE = "subscribe"
EVENT_CLICK = "click"
