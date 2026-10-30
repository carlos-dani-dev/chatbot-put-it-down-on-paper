"""Site de divulgação do Arnaldo — página única servida pelo FastAPI, com Vue e
Tailwind via CDN (sem build). Todo o texto editável fica em CONTENT.

Rodar com (da raiz do projeto):
    poetry run uvicorn app:app --app-dir site --port 8002

`site` é também um módulo da biblioteca padrão do Python, então `site.app:app` não
importa — por isso o `--app-dir site`.
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent

CONTENT = {
    "whatsapp_url": "https://wa.me/+558688713548",
    "stats": [
        {"strong": "Grátis", "text": "para começar"},
        {"strong": "Sem", "text": "instalar nada"},
        {"strong": "30s", "text": "para o 1º registro"},
    ],
    "features": [
        {
            "title": "Registro por mensagem",
            "text": "Mande \"supino 80kg 8 reps\" e pronto. O Arnaldo entende e anota.",
        },
        {
            "title": "Controle de carga",
            "text": "Acompanhe sua evolução de carga treino a treino, sem fazer conta.",
        },
        {
            "title": "Histórico na hora",
            "text": "Pergunte como foi da última vez e veja seu desempenho anterior.",
        },
    ],
    "steps": [
        {"title": "Chame no WhatsApp", "text": "Sem baixar app nem criar conta complicada."},
        {"title": "Conte seu treino", "text": "Escreva como falaria com um parceiro."},
        {"title": "Bata seus recordes", "text": "Veja a carga subir a cada semana."},
    ],
    # itens dos planos refletem os limites reais de agent/billing.py
    "plans": [
        {
            "name": "Grátis",
            "price": "R$ 0",
            "period": "",
            "items": [
                "Registro ilimitado de treinos",
                "1 plano com até 5 exercícios",
                "Página de resultados e recordes",
            ],
            "highlight": False,
        },
        {
            "name": "Pro",
            "price": "R$ 14,90",
            "period": "/mês",
            "items": ["Tudo do plano Grátis", "Planos de treino ilimitados", "Exercícios ilimitados por plano"],
            "highlight": True,
        },
    ],
    "socials": [
        {"name": "Instagram", "url": "https://instagram.com/arnaldo.gym.ai"},
        {"name": "TikTok", "url": "https://tiktok.com/@arnaldo.gym.ai"},
        {"name": "YouTube", "url": "https://youtube.com/@arnaldo.gym.ai"},
    ],
}

app = FastAPI(title="Arnaldo.gym.ai", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"content": CONTENT})
