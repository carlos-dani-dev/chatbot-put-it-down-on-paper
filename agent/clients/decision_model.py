import asyncio
import time

import logging

import requests
import pydantic

import ai
from ai.ops.experimental import (
    ChoiceAnswer,
    ChoiceQuestion,
    EvaluationInput,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    evaluate,
)

logger = logging.getLogger("chatbot-pidop")

DEFAULT_MODEL = "typesafe-ai/jev"
ATTEMPTS = 3

_KEY = "decision"


class JevClient:

    def __init__(self, model_id: str = DEFAULT_MODEL, attempts: int = ATTEMPTS):
        self._model = ai.get_model(model_id)
        self._attempts = attempts

    async def choose(
        self, state: EvaluationInput, instructions: str, criteria: dict[str, EvaluationInput | None]
    ) -> ChoiceAnswer:
        question = ChoiceQuestion(instructions=instructions, criteria=criteria)
        return await self._ask(state, question)

    async def estimate(
        self, state: EvaluationInput, instructions: str, criteria: NoulCriteria | None = None
    ) -> NoulAnswer:
        question = NoulQuestion(instructions=instructions, criteria=criteria)
        return await self._ask(state, question)

    async def score(
        self, state: EvaluationInput, instructions: str, criteria: list[EvaluationInput | None]
    ) -> ScoreAnswer:
        question = ScoreQuestion(instructions=instructions, criteria=criteria)
        return await self._ask(state, question)

    async def _ask(self, state: EvaluationInput, question) -> ChoiceAnswer | ScoreAnswer | NoulAnswer:
        for attempt in range(self._attempts):
            try:
                result = await evaluate(self._model, state, {_KEY: question})
                return result.value[_KEY]
            except ai.ProviderError:
                if attempt == self._attempts - 1:
                    raise
                await asyncio.sleep(2**attempt)


def _transient(erro: Exception) -> bool:
    if isinstance(erro, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    if isinstance(erro, requests.exceptions.HTTPError) and erro.response is not None:
        status = erro.response.status_code
        return status >= 500 or status == 429
    return False


_ANSWER_TYPES: dict[str, type[pydantic.BaseModel]] = {
    "choice": ChoiceAnswer,
    "score": ScoreAnswer,
    "noul": NoulAnswer,
}


class MercuryDecideClient:
    """Cliente pro Mercury Decide (Inception Labs) via OpenRouter.

    Fala o protocolo de Decisions da OpenRouter (/api/alpha/decisions),
    o mesmo formato "System One" do Jev — só que via chamada HTTP direta,
    já que o pacote `ai` não tem um provider pro OpenRouter.
    """

    BASE_URL = "https://openrouter.ai/api/alpha/decisions"
    DEFAULT_MODEL = "inception/mercury-decide"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, attempts: int = ATTEMPTS):
        self._api_key = api_key
        self._model = model
        self._attempts = attempts

    def choose(
        self, state: EvaluationInput, instructions: str, criteria: dict[str, EvaluationInput | None]
    ) -> ChoiceAnswer:
        question = ChoiceQuestion(instructions=instructions, criteria=criteria)
        return self._ask(state, question)

    def estimate(
        self, state: EvaluationInput, instructions: str, criteria: NoulCriteria | None = None
    ) -> NoulAnswer:
        question = NoulQuestion(instructions=instructions, criteria=criteria)
        return self._ask(state, question)

    def score(
        self, state: EvaluationInput, instructions: str, criteria: list[EvaluationInput | None]
    ) -> ScoreAnswer:
        question = ScoreQuestion(instructions=instructions, criteria=criteria)
        return self._ask(state, question)

    def _ask(
        self, state: EvaluationInput, question: ChoiceQuestion | ScoreQuestion | NoulQuestion
    ) -> ChoiceAnswer | ScoreAnswer | NoulAnswer:
        payload = {
            "model": self._model,
            "state": state,
            "questions": {_KEY: question.model_dump(mode="json")},
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}

        for attempt in range(self._attempts):
            try:
                response = requests.post(self.BASE_URL, headers=headers, json=payload, timeout=30)
                response.raise_for_status()
                data = response.json()["answers"][_KEY]
                return _ANSWER_TYPES[data["type"]].model_validate(data)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.HTTPError) as erro:
                ultima = attempt == self._attempts - 1
                if ultima or not _transient(erro):
                    raise
                time.sleep(2**attempt)


class ElephantDecideClient(MercuryDecideClient):
    """Cliente pro Elephant 1 (Baseten) via OpenRouter — mesma API de Decisions do
    Mercury Decide (/api/alpha/decisions), só muda o modelo padrão. Gratuito
    (usage.cost = 0), confirmado direto na resposta da API.
    """

    DEFAULT_MODEL = "baseten/elephant-1:free"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL, attempts: int = ATTEMPTS):
        super().__init__(api_key=api_key, model=model, attempts=attempts)
