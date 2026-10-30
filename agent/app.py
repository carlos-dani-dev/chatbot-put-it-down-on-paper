"""Webhook da Meta despachando pelo roteador agêntico (`agent/router.py`) —
cada estado do chatbot é tratado por um agente de tool-calling (ver `agent/agents/`).

Rodar com:
    poetry run uvicorn agent.app:app --port 8001
"""
import logging
from datetime import datetime, timezone

from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from landing.app import app as landing_app

from agent.config import get_provider, settings
from agent.database import engine, SessionLocal
from .resume_check import handle_resume_check
from .commands import handle_universal_command
from agent.models import Base, User
from agent.repos import chat_message_repo
from agent.repos.user_repo import get_or_create_user
from agent.schemas.replies import ButtonsReply, CtaUrlReply, ListReply, MultiTextReply

from . import billing
from .clients.mercadopago import verify_webhook_signature
from .dashboard import build_dashboard_context
from .llm_usage import end_turn, start_turn
from .router import handle_message
from .signing import verify_result_link

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("agent-app")
provider = get_provider()

app = FastAPI(
    title="Chatbot (agent, tool-calling)",
    description="Chatbot de treino no WhatsApp — cada estado tratado por um agente de tool-calling",
    version="0.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
# site de divulgação servido pelo mesmo processo, pra caber no único domínio fixo do ngrok
app.mount("/site", landing_app, name="site")

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
    start_turn(from_number)
    try:
        return _process_turn(from_number, text, button_id, message_id)
    finally:
        end_turn()


def _process_turn(from_number: str, text: str | None, button_id: str | None, message_id: str | None):
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
        elif isinstance(reply, CtaUrlReply):
            provider.send_cta_url_button(message.from_number, reply.text, reply.button_text, reply.url)
        elif isinstance(reply, MultiTextReply):
            for text in reply.texts:
                provider.send_message(message.from_number, text)
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


def _notify_pro_activated(activated: billing.ProActivated) -> None:
    text = (
        "✅ Pagamento confirmado! Seu *Pro* está ativo até "
        f"{billing.format_local_time(activated.pro_until)}.\n\n"
        "Bora treinar? É só pedir pra *Começar um treino* ou *Criar um plano*. 💪"
    )
    try:
        provider.send_message(activated.phone_number, text)
    except Exception:
        # fora da janela de 24h do WhatsApp a mensagem livre é recusada — o Pro já está ativo mesmo assim
        logger.exception(f"[agent] Pro ativado, mas falhou o aviso no WhatsApp para {activated.phone_number}")
        return
    with SessionLocal.begin() as db:
        user = db.get(User, activated.user_id)
        if user is not None:
            chat_message_repo.log_message(db, user, "assistant", text)


@app.post("/webhook/pix")
async def pix_webhook(request: Request, background: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        body = {}
    params = request.query_params

    kind = body.get("type") or params.get("type") or params.get("topic")
    payment_id = str((body.get("data") or {}).get("id") or params.get("data.id") or params.get("id") or "")
    if kind != "payment" or not payment_id:
        return PlainTextResponse("", status_code=200)

    if settings.mercadopago_webhook_secret:
        valid = verify_webhook_signature(
            settings.mercadopago_webhook_secret,
            request.headers.get("x-signature", ""),
            request.headers.get("x-request-id", ""),
            params.get("data.id") or payment_id,
        )
        if not valid:
            logger.warning(f"[agent] webhook Pix com assinatura inválida payment_id={payment_id}")
            return PlainTextResponse("assinatura inválida", status_code=401)

    try:
        with SessionLocal.begin() as db:
            activated = billing.apply_payment_update(db, payment_id)
    except Exception:
        logger.exception(f"[agent] falha ao processar webhook Pix payment_id={payment_id}")
        return PlainTextResponse("", status_code=500)  # o Mercado Pago reenvia o aviso

    if activated is not None:
        background.add_task(_notify_pro_activated, activated)
    return PlainTextResponse("", status_code=200)


@app.get("/webhook/dashboard")
async def show_results_page(request: Request):
    user_id = request.query_params.get("user_id")
    exp = request.query_params.get("exp")
    sig = request.query_params.get("sig")

    if not verify_result_link(user_id, exp, sig):
        return HTMLResponse("<h1>Link inválido ou expirado</h1>", status_code=403)

    with SessionLocal() as db:
        user = db.get(User, int(user_id))
        if user is None:
            return HTMLResponse("<h1>Usuário não encontrado</h1>", status_code=404)

        context = build_dashboard_context(db, user)

    return templates.TemplateResponse(request, "dashboard.html", context)