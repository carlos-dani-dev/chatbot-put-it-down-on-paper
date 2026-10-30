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
    "features": [
        {
            "title": "Registre por mensagem",
            "text": "Mande \"supino 80kg 8 reps\" e pronto. O Arnaldo entende linguagem natural e anota exercício, carga e repetições.",
        },
        {
            "title": "Planos de treino",
            "text": "Monte planos com exercícios e meta de séries. Durante o treino ele mostra o que falta fazer.",
        },
        {
            "title": "Controle de carga",
            "text": "Recordes pessoais, melhor carga, 1RM estimado e o desempenho da última vez em cada exercício.",
        },
        {
            "title": "Página de resultados",
            "text": "Calendário de treinos e a progressão de cada exercício em gráficos, num link exclusivo seu.",
        },
    ],
    "plans": [
        {
            "name": "Grátis",
            "price": "R$ 0",
            "period": "para sempre",
            "items": [
                "Registro ilimitado de treinos",
                "1 plano de treino com até 5 exercícios",
                "Página de resultados e recordes pessoais",
            ],
            "highlight": False,
        },
        {
            "name": "Pro",
            "price": "R$ 14,90",
            "period": "por mês",
            "items": ["Tudo do Grátis", "Planos de treino ilimitados", "Exercícios ilimitados por plano"],
            "highlight": True,
        },
    ],
    "socials": [
        {"name": "Instagram", "handle": "@arnaldo.gym.ai", "url": "https://instagram.com/arnaldo.gym.ai"},
        {"name": "TikTok", "handle": "@arnaldo.gym.ai", "url": "https://tiktok.com/@arnaldo.gym.ai"},
        {"name": "WhatsApp", "handle": "Falar com o Arnaldo", "url": "https://wa.me/5500000000000"},
    ],
}

app = FastAPI(title="Arnaldo.gym.ai", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"content": CONTENT})
