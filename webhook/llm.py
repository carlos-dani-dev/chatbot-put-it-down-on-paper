import time
import logging

from google import genai
from google.genai import errors, types

from pydantic import BaseModel

logger = logging.getLogger("chatbot-pidop")

DEFAULT_MODEL = "gemini-flash-lite-latest"
ATTEMPTS = 3

TRANSCRIPTION_PROMPT=(
    "Transcreva o áudio a seguir literalmente, em português. Números e "
    "e-mails soletrados devem virar a forma escrita normal (ex: \"oito seis "
    "nove\" -> \"869\", \"arroba\" -> \"@\"). Devolva só a transcrição, sem "
    "comentário, sem aspas."
)

class GeminiClient:

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, attempts: int = ATTEMPTS):
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._attempts = attempts

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> str:
        config = None
        if schema is not None:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            )
        return self._call(prompt, config=config)

    def transcribe(self, audio: bytes, mime_type: str) -> str:
        content = [TRANSCRIPTION_PROMPT, types.Part.from_bytes(data=audio, mime_type=mime_type)]
        return self._call(content).strip()

    def _call(self, contents, config=None) -> str:
        for attempt in range(self._attempts):
            try:
                response = self._client.models.generate_content(
                    model=self._model,
                    contents=contents,
                    config=config,
                )
                return response.text
            except errors.ServerError:
                if attempt == self._attempts - 1:
                    raise
                time.sleep(2**attempt)