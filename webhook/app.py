import logging

from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from contextlib import asynccontextmanager

from .config import settings, get_provider
from .database import engine
from .models import Base
from . import router

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
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # a origem do seu frontend Vite
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router.router)


async def _read_body(request):
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        return await request.json()

    return dict(await request.form())


def _process(text: str):
    return (
        f"Mensagem recebida: {text}\n"
        "Obrigado por entrar em contato!"
    )

def _process_and_reply(message):
    try:
        reply = _process(message.text)
        provider.send_message(message.from_number, reply)
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

    # _is_duplicate

    logger.info(
        f"Mensagem recebida | de={message.from_number} | tipo={message.message_type} | texto={message.text} | media_url={message.media_url}"
    )

    background.add_task(_process_and_reply, message)
    return PlainTextResponse("", status_code=200)