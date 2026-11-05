import logging

from google.genai import types

from pydantic import BaseModel

from .. import llm_call

logger = logging.getLogger("chatbot-pidop")

TRANSCRIPTION_PROMPT=(
    "Transcreva o áudio a seguir literalmente, em português. Números e "
    "e-mails soletrados devem virar a forma escrita normal (ex: \"oito seis "
    "nove\" -> \"869\", \"arroba\" -> \"@\"). Devolva só a transcrição, sem "
    "comentário, sem aspas."
)

class GeminiClient:
    """Chamadas avulsas (menus gerados e transcrição de áudio). Tentativas, tempo máximo e modelo
    reserva vêm do agent/llm_call.py, igual aos agentes."""

    def __init__(self, api_key: str):
        self._client = llm_call.make_client()

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> str:
        config = None
        if schema is not None:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            )
        return llm_call.generate(self._client, contents=prompt, config=config, label="shared.generate").text

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        content = [TRANSCRIPTION_PROMPT, types.Part.from_bytes(data=audio, mime_type=mime_type)]
        return llm_call.generate(self._client, contents=content, config=None, label="shared.transcribe").text.strip()
