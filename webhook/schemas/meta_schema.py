from dataclasses import dataclass
from typing import Optional


@dataclass
class IncomingMessage:
    from_number: str
    message_type: str
    text: Optional[str] = None
    media_url: Optional[str] = None
    media_mime_type: Optional[str] = None
    message_id: Optional[str] = None
