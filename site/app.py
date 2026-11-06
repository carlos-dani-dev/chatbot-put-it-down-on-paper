"""Site de divulgação do Arnaldo — página única servida pelo FastAPI, com Vue e
Tailwind via CDN (sem build). Todo o texto editável fica em CONTENT.

Rodar com (da raiz do projeto):
    poetry run uvicorn app:app --app-dir site --port 8002

`site` é também um módulo da biblioteca padrão do Python, então `site.app:app` não
importa — por isso o `--app-dir site`.
"""
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from markupsafe import Markup, escape
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

WHATSAPP_NUMBER = "558688286852"
# o ?text= deixa a mensagem já escrita no WhatsApp, é só a pessoa enviar. /pro é o comando
# que o bot reconhece em qualquer momento da conversa (ver agent/commands.py)
PRO_MESSAGE = "/pro"

# preço e limites de uso justo, os mesmos de agent/billing.py (um teste confere)
PRO_PRICE = "R$ 19,90"
FREE_DAILY_MESSAGES = 25
PRO_DAILY_MESSAGES = 250

CONTENT = {
    "whatsapp_url": f"https://wa.me/{WHATSAPP_NUMBER}",
    # o caminho de UMA série num app de treino típico — o argumento central do site
    "app_taps": [
        "Desbloquear o celular e abrir o app",
        "Entrar no treino de hoje",
        "Achar o supino na lista",
        "Abrir o exercício",
        "Tocar em \"adicionar série\"",
        "Tocar no campo de carga",
        "Apagar o valor da última vez",
        "Digitar 80",
        "Tocar no campo de repetições",
        "Digitar 8",
        "Confirmar a série",
        "Fechar o cronômetro de descanso",
    ],
    "steps": [
        {"title": "Chame no WhatsApp", "text": "O app que você já usa. Sem baixar nada, sem criar conta."},
        {"title": "Conte o treino como falaria com um parceiro",
         "text": "\"supino 80kg 8, 8 e 7\". Várias séries de uma vez, do seu jeito."},
        {"title": "Veja a carga subir", "text": "Peça seus resultados e abra o calendário, os gráficos e os recordes."},
    ],
    # nomes fixos do manual da marca; `example` mostra o recurso na voz do Arnaldo
    "features": [
        {"title": "Treino guiado por metas", "text": "A cada série, o Arnaldo diz quanto falta e qual é o próximo exercício.",
         "example": "progresso 3/4, falta 1 série"},
        {"title": "Desempenho anterior", "text": "Pergunte quanto fez da última vez e saiba de onde partir hoje.",
         "example": "Na última vez: 32kg x 10. Bora tentar 34kg?"},
        {"title": "Planos de treino", "text": "Monte, edite e exclua planos pelo chat, com a meta de séries de cada exercício.",
         "example": "Push: supino 4 séries, desenvolvimento 3"},
        {"title": "Página de resultados", "text": "Calendário, progressão por exercício, recordes e ficha. Dá pra mandar em PDF pro parceiro de treino.",
         "example": "Supino reto: 60kg → 80kg"},
    ],
    # itens dos planos refletem os limites reais de agent/billing.py
    "plans": [
        {
            "name": "Grátis",
            "price": "R$ 0",
            "period": "",
            "items": [
                "Registro de treinos por mensagem",
                f"Até {FREE_DAILY_MESSAGES} mensagens por dia",
                "1 plano com até 5 exercícios",
                "Página de resultados e recordes",
            ],
            "highlight": False,
            "cta_url": f"https://wa.me/{WHATSAPP_NUMBER}",
        },
        {
            "name": "Pro",
            "price": PRO_PRICE,
            "period": "/ 30 dias",
            "items": [
                "Tudo do plano Grátis",
                f"Até {PRO_DAILY_MESSAGES} mensagens por dia",
                "Planos de treino ilimitados",
                "Exercícios ilimitados por plano",
            ],
            "highlight": True,
            "cta_url": f"https://wa.me/{WHATSAPP_NUMBER}?text={quote(PRO_MESSAGE)}",
        },
    ],
    "plans_note": "Mensagens por dia são um limite de uso justo e zeram à meia-noite (horário de Brasília).",
    "socials": [
        {"name": "Instagram", "url": "https://instagram.com/arnaldo.gym.ai"},
        {"name": "TikTok", "url": "https://tiktok.com/@arnaldo.gym.ai"},
        {"name": "YouTube", "url": "https://youtube.com/@arnaldo.gym.ai"},
    ],
}

# Termos de Uso e Política de Privacidade (/termos e /privacidade). A versão é a mesma de
# agent/terms.py: mudou o texto, suba as duas e o bot pede o aceite de novo (um teste confere)
TERMS_VERSION = "2026-11-04"
TERMS_UPDATED = "4 de novembro de 2026"
# quem responde pelo serviço — vazio aparece destacado nas páginas como "[preencher: ...]"
LEGAL = {"owner": "Arnaldo Gym AI", "document": "69.915.314/0001-10", "contact_email": "arnaldo.gym.ai@gmail.com"}
LEGAL_CONTEXT = {
    "terms_version": TERMS_VERSION,
    "terms_updated": TERMS_UPDATED,
    "legal": LEGAL,
    # mesmos limites de agent/billing.py
    "free": {"max_plans": 1, "max_exercises": 5, "daily_messages": FREE_DAILY_MESSAGES},
    "pro": {"price": PRO_PRICE, "days": 30, "daily_messages": PRO_DAILY_MESSAGES},
}

app = FastAPI(title="Arnaldo.gym.ai", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def asset(path: str) -> str:
    """URL de arquivo estático com a versão do arquivo. A Cloudflare manda o navegador guardar
    imagens por horas: com a versão no endereço, um arquivo novo nunca fica preso no cache."""
    try:
        version = int((BASE_DIR / "static" / path).stat().st_mtime)
    except FileNotFoundError:
        return f"static/{path}"
    return f"static/{path}?v={version}"


templates.env.globals["asset"] = asset


def todo(value: str, label: str) -> str:
    """Campo das páginas legais ainda não preenchido em LEGAL: aparece destacado, impossível de passar batido."""
    return value or Markup(f'<span class="todo">[preencher: {escape(label)}]</span>')


templates.env.filters["todo"] = todo


# a página inicial do site fica em "/". HEAD além de GET: monitores como o UptimeRobot checam com HEAD
@app.api_route("/", methods=["GET", "HEAD"])
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"content": CONTENT, "year": date.today().year})


@app.api_route("/termos", methods=["GET", "HEAD"])
async def terms(request: Request):
    return templates.TemplateResponse(request, "termos.html", LEGAL_CONTEXT)


@app.api_route("/privacidade", methods=["GET", "HEAD"])
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacidade.html", LEGAL_CONTEXT)
