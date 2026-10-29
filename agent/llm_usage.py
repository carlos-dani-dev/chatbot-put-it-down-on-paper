"""Contagem de tokens das chamadas a LLM: loga cada chamada e, por mensagem do
usuário, o total somado (o acumulador vive num ContextVar aberto em `start_turn`).
Os números vêm do que a própria API reporta, não de estimativa local.
"""
import logging
from contextvars import ContextVar

logger = logging.getLogger("agent.llm_usage")

_turn: ContextVar[dict | None] = ContextVar("llm_turn_usage", default=None)


def start_turn(phone: str) -> None:
    _turn.set({"phone": phone, "calls": 0, "prompt": 0, "output": 0, "total": 0})


def end_turn() -> None:
    turn = _turn.get()
    if turn is None:
        return
    if turn["calls"]:
        logger.info(
            "llm_usage TURN user=%s calls=%d prompt=%d output=%d total=%d",
            turn["phone"], turn["calls"], turn["prompt"], turn["output"], turn["total"],
        )
    _turn.set(None)


def _add(label: str, model: str | None, prompt: int, output: int, total: int, extra: str = "") -> None:
    turn = _turn.get()
    phone = turn["phone"] if turn else "-"
    logger.info(
        "llm_usage call=%s user=%s model=%s prompt=%d output=%d total=%d%s",
        label, phone, model or "-", prompt, output, total, extra,
    )
    if turn is not None:
        turn["calls"] += 1
        turn["prompt"] += prompt
        turn["output"] += output
        turn["total"] += total


def record_gemini(label: str, response) -> None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        logger.info("llm_usage call=%s tokens=indisponível", label)
        return
    prompt = usage.prompt_token_count or 0
    output = usage.candidates_token_count or 0
    thoughts = usage.thoughts_token_count or 0
    total = usage.total_token_count or (prompt + output + thoughts)
    extra = f" thoughts={thoughts}" if thoughts else ""
    _add(label, getattr(response, "model_version", None), prompt, output + thoughts, total, extra)


def record_openrouter(label: str, model: str, usage: dict | None) -> None:
    if not usage:
        logger.info("llm_usage call=%s model=%s tokens=indisponível", label, model)
        return
    prompt = usage.get("prompt_tokens") or 0
    output = usage.get("completion_tokens") or 0
    total = usage.get("total_tokens") or (prompt + output)
    cost = usage.get("cost")
    extra = f" cost={cost}" if cost is not None else ""
    _add(label, model, prompt, output, total, extra)
