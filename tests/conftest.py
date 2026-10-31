"""Peças compartilhadas pelos testes. O pytest carrega este arquivo sozinho, antes de qualquer teste.

Conceito central: FIXTURE. É uma função marcada com @pytest.fixture que prepara algo (um banco,
um usuário, um Gemini falso). Um teste "pede" uma fixture só colocando o nome dela como
parâmetro — `def test_x(db, user):` recebe o banco e o usuário prontos. O que vem depois do
`yield` numa fixture roda no fim do teste (limpeza). Cada teste recebe peças novas: nenhum
teste enxerga o que outro gravou.
"""
import os

# Precisa vir ANTES de importar qualquer coisa de `agent/`: o config.py lê as variáveis na
# importação, e o agent/shared.py já cria um cliente do Gemini — que quebra sem chave. No GitHub
# Actions não existe .env, então estes valores falsos são o que vale lá. Na sua máquina o .env
# sobrepõe (o config.py usa override=True), mas os testes não usam nada disso de verdade:
# o banco é um SQLite em memória e o Gemini é substituído por um falso (ver abaixo).
os.environ.setdefault("GEMINI_API_KEY", "chave-falsa-de-teste")
os.environ.setdefault("SQLALCHEMY_DB_URL", "sqlite://")
os.environ.setdefault("DASHBOARD_SIGNING_KEY", "chave-de-teste")

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from google.genai import types
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from agent.clients.mercadopago import MercadoPagoError, PixPayment
from agent.context.session import build_context
from agent.models import Base, PixCharge, User
from agent.repos import plan_repo, session_repo
from agent.tools import session as session_tools


# --------------------------------------------------------------------------
# Banco
# --------------------------------------------------------------------------
@pytest.fixture
def db():
    """Um banco SQLite novo, em memória, com todas as tabelas — some no fim do teste."""
    # StaticPool: todas as conexões compartilham o MESMO banco em memória (sem ele, cada
    # conexão nova abriria um banco vazio diferente)
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")  # igual ao app: o SQLite vem com FK desligada

    Base.metadata.create_all(engine)
    # autoflush=False igual ao SessionLocal do app (agent/database.py): o teste se comporta como produção
    with Session(engine, autoflush=False) as session:
        yield session
    engine.dispose()


# --------------------------------------------------------------------------
# Fábricas de dados — fixtures que devolvem uma FUNÇÃO, pra cada teste montar o cenário que quiser
# --------------------------------------------------------------------------
@pytest.fixture
def make_user(db):
    counter = iter(range(1, 1000))

    def _make(state: str = "idle") -> User:
        user = User(phone_number=f"55869999{next(counter):05d}", state=state)
        db.add(user)
        db.flush()
        return user

    return _make


@pytest.fixture
def user(make_user):
    """Atalho pro caso comum: um usuário só."""
    return make_user()


@pytest.fixture
def make_plan(db, user):
    """make_plan("Perna", [("Agachamento livre", 2), ("Stiff", 1)]) -> plano salvo com as metas de séries."""

    def _make(name: str = "Perna", items=(("Agachamento livre", 2), ("Stiff", 1)), owner: User | None = None):
        return plan_repo.save_plan(
            db, owner or user, {"name": name, "items": [{"name": n, "target_sets": t} for n, t in items]}
        )

    return _make


@pytest.fixture
def start_workout(db, user):
    """start_workout(plano) ou start_workout() pra treino livre -> sessão aberta."""

    def _start(plan=None, owner: User | None = None):
        return session_repo.start_session(db, owner or user, plan.id if plan is not None else None)

    return _start


@pytest.fixture
def log(db, user):
    """log(sessao, "Agachamento livre", (60, 10), (60, 8)) -> registra séries pelo nome do exercício.

    Usa a mesma ferramenta que o agente usa (log_sets). Exercício que não está na sessão entra
    como novo — igual ao agente fazendo exercise_id="new".
    """

    def _log(ws, exercise_name: str, *sets, owner: User | None = None):
        who = owner or user
        ctx = build_context(db, who, ws)
        known = next((e for e in ctx.exercises if e.name.lower() == exercise_name.lower()), None)
        return session_tools.log_sets(
            db, who, ws, ctx,
            exercise_id=f"ex:{known.id}" if known else "new",
            new_exercise_name=None if known else exercise_name,
            sets=[{"weight_kg": w, "reps": r} for w, r in sets],
        )

    return _log


def ex_id(ctx, name: str) -> str:
    """O ID que o agente veria no contexto pra um exercício ("ex:3")."""
    return next(f"ex:{e.id}" for e in ctx.exercises if e.name.lower() == name.lower())


@pytest.fixture
def exercise_id():
    return ex_id


# --------------------------------------------------------------------------
# Gemini falso
# --------------------------------------------------------------------------
class FakeGemini:
    """Faz o papel do genai.Client: devolve respostas roteirizadas, na ordem, e guarda o que recebeu.

    Assim o teste decide o que a "LLM" responde (ex.: "chame log_sets com estes argumentos") e
    confere o que o código FAZ com isso — que é o que importa testar. A LLM em si não se testa aqui.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.models = self  # o código chama client.models.generate_content(...)

    def generate_content(self, model, contents, config):
        self.calls.append({"contents": list(contents), "config": config})
        if not self._responses:
            raise AssertionError("o agente chamou o Gemini mais vezes do que o roteiro do teste previa")
        return self._responses.pop(0)

    # -- ajudantes pras conferências --
    def system_prompt(self, call: int) -> str:
        return self.calls[call]["config"].system_instruction

    def offered_tools(self, call: int) -> list[str]:
        tools = self.calls[call]["config"].tools or []
        return [d.name for t in tools for d in (t.function_declarations or [])]

    def tool_results(self, call: int) -> dict:
        """Resultados de ferramenta que o código devolveu pra LLM antes da chamada `call`."""
        results = {}
        for content in self.calls[call]["contents"]:
            for part in content.parts or []:
                if part.function_response is not None:
                    results[part.function_response.name] = part.function_response.response
        return results


class GeminiScript:
    """Monta as respostas do roteiro e instala o Gemini falso."""

    def __init__(self, monkeypatch):
        self._monkeypatch = monkeypatch
        self.fake: FakeGemini | None = None

    @staticmethod
    def call(tool: str, /, **args) -> types.GenerateContentResponse:
        """A LLM 'decide' chamar uma ferramenta. O `/` deixa `tool` só posicional: assim um argumento
        da ferramenta chamado `name` (ex.: add_to_draft) não colide com ele."""
        return GeminiScript.calls((tool, args))

    @staticmethod
    def calls(*name_and_args) -> types.GenerateContentResponse:
        """A LLM chama várias ferramentas na mesma resposta (chamadas paralelas)."""
        parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in name_and_args]
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])

    @staticmethod
    def text(content: str) -> types.GenerateContentResponse:
        """A LLM responde só com texto."""
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=content)]))]
        )

    def script(self, *responses) -> FakeGemini:
        self.fake = FakeGemini(responses)
        # monkeypatch troca algo só durante o teste e desfaz sozinho no final
        self._monkeypatch.setattr("google.genai.Client", lambda **_kwargs: self.fake)
        return self.fake


@pytest.fixture
def gemini(monkeypatch):
    return GeminiScript(monkeypatch)


# --------------------------------------------------------------------------
# Mercado Pago falso e plano Pro
# --------------------------------------------------------------------------
class FakeMercadoPago:
    """Faz o papel do MercadoPagoClient: cria "Pix" de mentira e guarda os pedidos recebidos."""

    def __init__(self):
        self.created: list[dict] = []
        self.offline = False  # True simula o Mercado Pago recusando/fora do ar

    def create_pix_payment(self, **kwargs) -> PixPayment:
        if self.offline:
            raise MercadoPagoError("HTTP 503 em POST /v1/payments: fora do ar (simulado)")
        self.created.append(kwargs)
        n = len(self.created)
        return PixPayment(id=f"pix-teste-{n}", status="pending", qr_code=f"00020126PIXCOPIAECOLA{n}", ticket_url=None)


@pytest.fixture
def mercadopago(monkeypatch):
    fake = FakeMercadoPago()
    monkeypatch.setattr("agent.billing.get_payment_client", lambda: fake)
    monkeypatch.setattr("agent.billing.settings.mercadopago_access_token", "token-de-teste")
    return fake


@pytest.fixture
def make_pro(db):
    """make_pro(user) -> usuário com um pagamento aprovado agora (Pro por 30 dias)."""

    def _make(who: User) -> User:
        db.add(PixCharge(
            user_id=who.id, provider="mercadopago", provider_payment_id=f"pago-{who.id}",
            idempotency_key=f"chave-{who.id}", amount=Decimal("14.90"), status="approved",
            qr_code="x", expires_at=datetime.now(timezone.utc), paid_at=datetime.now(timezone.utc),
        ))
        db.flush()
        return who

    return _make


# --------------------------------------------------------------------------
# Travas de segurança — autouse=True: valem pra TODOS os testes, sem precisar pedir
# --------------------------------------------------------------------------
class RealServiceBlocked(BaseException):
    """Herda de BaseException, e não de Exception, de propósito: o app tem `except Exception` em
    volta das chamadas externas (ex.: o billing responde "Pix indisponível" se o Mercado Pago falhar),
    e um erro comum seria engolido ali — o teste passaria em silêncio. Este atravessa e falha o teste."""


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Nenhum teste pode chamar o Gemini de verdade (custa dinheiro, é lento e não é determinístico).

    - genai.Client: quem precisar da LLM usa a fixture `gemini`, que instala o falso por cima.
    - agent.shared._generate: usado pelos menus (idle_menu, enter_planning) — vira um texto fixo.
    """
    def _blocked(**_kwargs):
        raise RealServiceBlocked("o teste tentou criar um cliente do Gemini de verdade — use a fixture `gemini`")

    monkeypatch.setattr("google.genai.Client", _blocked)
    monkeypatch.setattr("agent.shared._generate", lambda prompt: "[menu gerado pela LLM]")


@pytest.fixture(autouse=True)
def _no_real_payments(monkeypatch):
    """Nenhum teste pode falar com o Mercado Pago de verdade: na sua máquina o .env tem o token de
    PRODUÇÃO, e um teste chegando no assinar_pro criaria cobranças Pix reais. Quem precisar usa a
    fixture `mercadopago`, que instala o falso por cima."""
    def _blocked():
        raise RealServiceBlocked("o teste tentou usar o Mercado Pago de verdade — use a fixture `mercadopago`")

    monkeypatch.setattr("agent.billing.get_payment_client", _blocked)
