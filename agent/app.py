"""Mesmo webhook da Meta que `webhook/app.py`, mas despachando pelo roteador
agêntico (`agent/router.py`) em vez de `webhook.handlers.HANDLERS` puro. Mesmo
banco de dados, mesma infra de mensageria — só a lógica de `session` muda.

Rodar em porta separada pra não colidir com o `webhook/` original:
    poetry run uvicorn agent.app:app --port 8001
"""
import logging
from datetime import datetime, timezone

from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from webhook.config import get_provider, settings
from webhook.database import engine, SessionLocal
from webhook.handlers import handle_resume_check, handle_universal_command
from webhook.models import Base
from webhook.repos import chat_message_repo
from webhook.repos.user_repo import get_or_create_user
from webhook.schemas.replies import ButtonsReply, ListReply

from .router import handle_message


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("agent-app")
provider = get_provider()

app = FastAPI(
    title="Chatbot (agent, tool-calling)",
    description="Reprodução agêntica do chatbot — session via tool-calling, resto via webhook/handlers",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _read_body(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return await request.json()
    return dict(await request.form())


def _process(from_number: str, text: str | None, button_id: str | None = None, message_id: str | None = None):
    with SessionLocal.begin() as db:
        user = get_or_create_user(db, from_number)
        last_seen = user.last_message_at
        if last_seen is not None and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        user.last_message_at = datetime.now(timezone.utc)
        gap = (user.last_message_at - last_seen) if last_seen else None

        if text:
            chat_message_repo.log_message(db, user, "user", text, wa_message_id=message_id)

        intercept_result = None
        if button_id is None:
            intercept_result = handle_universal_command(user, text)
            if intercept_result is None:
                intercept_result = handle_resume_check(db, user, text, gap)

        if intercept_result is not None:
            reply, next_state = intercept_result
        else:
            reply, next_state = handle_message(db, user, text, button_id)

        chat_message_repo.log_message(db, user, "assistant", reply.text)
        user.state = next_state
        return reply


def _process_and_reply(message):
    if message.message_id:
        try:
            provider.mark_as_read_with_typing(message.message_id)
        except Exception:
            logger.exception(f"[agent] Falha ao marcar como lida/'digitando' para {message.from_number}")

    try:
        reply = _process(message.from_number, message.text, message.button_id, message.message_id)
        if isinstance(reply, ButtonsReply):
            provider.send_interactive_buttons(message.from_number, reply.text, reply.buttons)
        elif isinstance(reply, ListReply):
            provider.send_interactive_list(message.from_number, reply.text, reply.button_label, reply.rows)
        else:
            provider.send_message(message.from_number, reply.text)
        logger.info(f"[agent] Resposta enviada para {message.from_number}")
    except Exception:
        logger.exception(f"[agent] Falha ao processar/enviar a resposta para {message.from_number}")


@app.get("/healthy")
async def health_check():
    return {"status": "Healthy"}


@app.get("/webhook/whatsapp")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == settings.meta_verify_token:
        return PlainTextResponse(challenge, status_code=200)

    return PlainTextResponse("Forbidden", status_code=403)


@app.post("/webhook/whatsapp")
async def receive_wap_message(request: Request, background: BackgroundTasks):
    try:
        raw_data = await _read_body(request)
        message = await provider.parse_incoming(raw_data)
    except Exception:
        logger.exception("[agent] Falha ao interpretar o payload recebido")
        return PlainTextResponse("", status_code=200)

    if message is None:
        logger.info("[agent] Evento ignorado (status de entrega ou callback administrativo)")
        return PlainTextResponse("", status_code=200)

    logger.info(
        f"[agent] Mensagem recebida | de={message.from_number} | tipo={message.message_type} | texto={message.text}"
    )

    background.add_task(_process_and_reply, message)
    return PlainTextResponse("", status_code=200)
