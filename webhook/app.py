import logging
from datetime import datetime, timezone

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager

from .config import settings, get_provider
from .database import engine, SessionLocal
from .models import Base
from .handlers import HANDLERS, handle_universal_command, handle_resume_check
from .schemas.replies import TextReply, ButtonsReply, ListReply

from .repos.user_repo import get_or_create_user
from .repos import chat_message_repo

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("case-dix-digital")
provider = get_provider()

app = FastAPI(
    title="Let me check",
    description="Multi user whatsapp chatbot",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # a origem do seu frontend Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


##############################################################
##############################################################


async def _read_body(request):
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        return await request.json()

    return dict(await request.form())


def _process(from_number: str, text: str, button_id: str | None = None, message_id: str | None = None):

    with SessionLocal.begin() as db:
        user = get_or_create_user(db, from_number)
        last_seen = user.last_message_at
        if last_seen is not None and last_seen.tzinfo is None:
            # SQLite não preserva timezone no round-trip; sempre gravamos em UTC
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
            reply, next_state = HANDLERS[user.state](db, user, text, button_id)

        chat_message_repo.log_message(db, user, "assistant", reply.text)

        user.state = next_state

        return reply


def _process_and_reply(message):

    if message.message_id:
        try:
            provider.mark_as_read_with_typing(message.message_id)
        except Exception:
            logger.exception(f"Falha ao marcar como lida/mostrar 'digitando' para {message.from_number}")

    try:
        reply = _process(message.from_number, message.text, message.button_id, message.message_id)
        if isinstance(reply, ButtonsReply):
            provider.send_interactive_buttons(message.from_number, reply.text, reply.buttons)
        elif isinstance(reply, ListReply):
            provider.send_interactive_list(message.from_number, reply.text, reply.button_label, reply.rows)
        else:
            provider.send_message(message.from_number, reply.text)
        logger.info(f"Resposta enviada para {message.from_number}")
    except Exception:
        logger.exception(f"Falha ao processar/enviar a resposta para {message.from_number}")


@app.get('/healthy')
async def health_check():
    return {'status': 'Healthy'}

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
        logger.exception("Falha ao interpretar o payload recebido")
        return PlainTextResponse("", status_code=200)
    if message is None:
        logger.info("Evento ignorado (status de entrega ou callback administrativo)")
        return PlainTextResponse("", status_code=200)

    logger.info(
        f"Mensagem recebida | de={message.from_number} | tipo={message.message_type} | texto={message.text} | media_url={message.media_url}"
    )

    background.add_task(_process_and_reply, message)
    return PlainTextResponse("", status_code=200)