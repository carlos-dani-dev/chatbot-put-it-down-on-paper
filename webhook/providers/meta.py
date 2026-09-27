import time
import requests

from typing import Dict, Any

from ..schemas.meta_schema import IncomingMessage

DEFAULT_ATTEMPTS = 3
GRAPH_API_VERSION = "v20.0"


def _transitorio(erro: Exception) -> bool:
    if isinstance(erro, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    if isinstance(erro, requests.exceptions.HTTPError) and erro.response is not None:
        status = erro.response.status_code
        return status >= 500 or status == 429
    return False


class MetaProvider:

    def __init__(
            self,
            access_token: str,
            phone_number_id: str,
            verify_token: str,
            attempts: int = DEFAULT_ATTEMPTS
    ):
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.verify_token = verify_token
        self.attempts = attempts

        self.base_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"

    async def parse_incoming(self, raw_data: Dict[str, Any]):
        try:
            value = raw_data["entry"][0]["changes"][0]["value"]
        except (KeyError, IndexError, TypeError):
            return None

        if "statuses" in value:
            return None

        messages = value.get("messages")
        if not messages:
            return None

        msg = messages[0]
        from_number = msg.get("from")
        msg_type = msg.get("type")

        text = None
        media_id = None
        media_mime_type = None

        if msg_type == "text":
            text = msg["text"]["body"]
        elif msg_type == "audio":
            media_id = msg["audio"]["id"]
            media_mime_type = msg["audio"].get("mime_type")
        elif msg_type == "image":
            media_id = msg["image"]["id"]
            media_mime_type = msg["image"].get("mime_type")

        message_type = msg_type if msg_type in ("text", "audio", "image") else "unknown"

        return IncomingMessage(
            from_number=from_number,
            message_type=message_type,
            text=text,
            media_url=media_id,
            media_mime_type=media_mime_type,
            message_id=msg.get("id"),
        )

    def send_message(self, to: str, text: str) -> None:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        for tentativa in range(self.attempts):
            try:
                response = requests.post(self.base_url, headers=headers, json=payload, timeout=30)
                response.raise_for_status()
                return
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.HTTPError) as erro:
                ultima = tentativa == self.attempts - 1
                if ultima or not _transitorio(erro):
                    raise
                time.sleep(2**tentativa)

    def get_media_url(self, media_id: str) -> str:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(
            f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}",
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["url"]

    def download_media(self, media_url: str) -> bytes:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(media_url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.content

    def baixar_midia(self, referencia: str) -> bytes:
        url = self.get_media_url(referencia)
        return self.download_media(url)