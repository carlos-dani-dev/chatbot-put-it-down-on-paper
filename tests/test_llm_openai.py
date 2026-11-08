"""GPT Luna como modelo principal, com o Gemini 3.5 de reserva (agent/llm_openai.py + agent/llm_call.py).

A fixture `openai` liga esse cenário com a API da OpenAI falsa; o Gemini é o falso de sempre. Nenhum
teste chama a OpenAI nem o Gemini de verdade.
"""
import base64

import httpx
import pytest
from google.genai import types
from sqlalchemy import select

from agent import llm_call, llm_openai, llm_usage
from agent.agents import idle as idle_agent
from agent.agents import session as session_agent
from agent.context.session import build_context
from agent.models import LlmCall

LUNA, GEMINI = "gpt-6-luna", "gemini-flash-lite-latest"


def chamar(contents=("oi",), config=None, label="session"):
    return llm_call.generate(llm_call.make_client(), contents=list(contents), config=config, label=label)


def ferramenta(nome, **props):
    return types.Tool(function_declarations=[types.FunctionDeclaration(
        name=nome, description=f"faz {nome}",
        parameters=types.Schema(type="OBJECT", properties=props, required=list(props)),
    )])


# ---------------------------------------------------------------- tradução

def test_luna_responde_em_texto_e_o_gemini_nem_e_chamado(openai, gemini):
    fake_gemini = gemini.script()
    openai.script(openai.text("Bora treinar!"))

    resposta = chamar()

    assert resposta.text == "Bora treinar!"
    assert fake_gemini.models_used() == []
    assert openai.requests[0]["model"] == LUNA


def test_pedido_leva_prompt_de_sistema_e_ferramentas_em_json_schema(openai, gemini):
    gemini.script()
    openai.script(openai.text("ok"))
    config = types.GenerateContentConfig(
        system_instruction="Você é o Arnaldo.", temperature=0.1,
        tools=[ferramenta("log_sets",
                          exercise_id=types.Schema(type="STRING", enum=["ex:1", "new"]),
                          sets=types.Schema(type="ARRAY", items=types.Schema(type="OBJECT", properties={
                              "weight_kg": types.Schema(type="NUMBER", minimum=0, maximum=500),
                              "rest_seconds": types.Schema(type="INTEGER", nullable=True),
                          })))],
    )

    chamar(["supino 80kg 8"], config)

    corpo = openai.requests[0]
    assert corpo["messages"] == [{"role": "system", "content": "Você é o Arnaldo."},
                                 {"role": "user", "content": "supino 80kg 8"}]
    params = corpo["tools"][0]["function"]["parameters"]
    assert params["type"] == "object" and params["required"] == ["exercise_id", "sets"]
    assert params["properties"]["exercise_id"] == {"type": "string", "enum": ["ex:1", "new"]}
    serie = params["properties"]["sets"]["items"]["properties"]
    assert serie["weight_kg"] == {"type": "number", "minimum": 0, "maximum": 500}
    assert serie["rest_seconds"] == {"type": ["integer", "null"]}
    # GPT de raciocínio recusa temperatura diferente da padrão: não vai no pedido
    assert "temperature" not in corpo


def test_chamada_de_ferramenta_volta_no_formato_do_gemini(openai, gemini):
    gemini.script()
    openai.script(openai.call("log_sets", exercise_id="ex:1", sets=[{"weight_kg": 80, "reps": 8}]))

    resposta = chamar()

    [parte] = resposta.candidates[0].content.parts
    assert parte.function_call.name == "log_sets"
    assert parte.function_call.args == {"exercise_id": "ex:1", "sets": [{"weight_kg": 80, "reps": 8}]}


def test_historico_com_ferramenta_vira_tool_calls_e_resultados_casados_pelo_id(openai, gemini):
    gemini.script()
    openai.script(openai.text("ok"))
    chamada = types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
        id="call_abc", name="list_plans", args={}))])
    resultado = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        name="list_plans", response={"ok": True, "plans": ["Peito"]}))])

    chamar(["quais planos eu tenho?", chamada, resultado])

    _, assistente, ferramenta_msg = openai.requests[0]["messages"]
    assert assistente["tool_calls"][0]["id"] == "call_abc"
    assert ferramenta_msg == {"role": "tool", "tool_call_id": "call_abc",
                              "content": '{"ok": true, "plans": ["Peito"]}'}


def test_chamada_paralela_sem_resultado_ganha_um_resultado_de_nao_executada(openai, gemini):
    """Os agentes de idle/planning só executam a 1ª ferramenta; a OpenAI exige resposta pra todas."""
    gemini.script()
    openai.script(openai.text("ok"))
    chamadas = types.Content(role="model", parts=[
        types.Part(function_call=types.FunctionCall(name="add_to_draft", args={})),
        types.Part(function_call=types.FunctionCall(name="save_draft", args={})),
    ])
    so_a_primeira = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        name="add_to_draft", response={"ok": True}))])

    chamar(["adiciona supino e salva", chamadas, so_a_primeira])

    resultados = [m for m in openai.requests[0]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in resultados] == ["call_1_0", "call_1_1"]
    assert "não foi executada" in resultados[1]["content"]


# ---------------------------------------------------------------- reserva

@pytest.mark.parametrize("erro", [503, 500, 429])
def test_luna_fora_do_ar_tenta_de_novo_e_cai_pro_gemini(openai, gemini, erro):
    fake_gemini = gemini.script(gemini.text("do Gemini"))
    openai.script(openai.error(erro), openai.error(erro))

    assert chamar().text == "do Gemini"
    assert len(openai.requests) == 2 and fake_gemini.models_used() == [GEMINI]


def test_timeout_da_luna_tambem_cai_pro_gemini(openai, gemini):
    fake_gemini = gemini.script(gemini.text("do Gemini"))
    openai.script(httpx.ReadTimeout("lento"), httpx.ReadTimeout("lento"))

    assert chamar().text == "do Gemini"
    assert fake_gemini.models_used() == [GEMINI]


@pytest.mark.parametrize("erro", [400, 401, 403, 404])
def test_luna_recusando_o_pedido_vai_direto_pro_gemini_sem_insistir(openai, gemini, erro):
    """Chave errada, modelo com nome errado ou pedido recusado: é configuração da OpenAI, e o
    Gemini segue respondendo enquanto isso é corrigido."""
    fake_gemini = gemini.script(gemini.text("do Gemini"))
    openai.script(openai.error(erro))

    assert chamar().text == "do Gemini"
    assert len(openai.requests) == 1 and fake_gemini.models_used() == [GEMINI]


def test_sem_chave_da_openai_usa_o_gemini_direto(openai, gemini, monkeypatch):
    monkeypatch.setattr("agent.llm_openai.settings.openai_api_key", "")
    fake_gemini = gemini.script(gemini.text("do Gemini"))

    assert chamar().text == "do Gemini"
    assert openai.requests == [] and fake_gemini.models_used() == [GEMINI]


def test_audio_continua_no_gemini(openai, gemini):
    fake_gemini = gemini.script(gemini.text("supino 80kg 8"))
    audio = types.Part.from_bytes(data=b"ogg", mime_type="audio/ogg")

    assert chamar(["Transcreva", audio], label="shared.transcribe").text == "supino 80kg 8"
    assert openai.requests == [] and fake_gemini.models_used() == [GEMINI]


def test_luna_e_gemini_fora_do_ar(openai, gemini):
    gemini.script(gemini.error(503), gemini.error(503))
    openai.script(openai.error(503), openai.error(503))

    with pytest.raises(llm_call.LLMUnavailable):
        chamar()


def test_gemini_aceita_historico_com_ferramenta_chamada_pela_luna(openai, gemini, user, db):
    """A Luna chamou uma ferramenta e caiu antes de escrever a resposta: o Gemini recebe o histórico
    com a chamada dela, marcada com a assinatura que o Google aceita pra histórico de outro modelo."""
    fake_gemini = gemini.script(gemini.text("Você ainda não tem planos salvos."))
    openai.script(openai.call("list_plans"), openai.error(503), openai.error(503))

    resposta, estado = idle_agent.handle(db, user, "quais planos eu tenho?")

    assert resposta.text == "Você ainda não tem planos salvos." and estado == "idle"
    historico = fake_gemini.calls[0]["contents"]
    [chamada_da_luna] = [p for c in historico for p in c.parts or [] if p.function_call]
    assert chamada_da_luna.function_call.name == "list_plans"
    assert chamada_da_luna.thought_signature == base64.urlsafe_b64decode("skip_thought_signature_validator")


# ---------------------------------------------------------------- consumo e agentes

def test_consumo_da_luna_e_gravado_com_o_modelo_e_o_cache(openai, gemini, llm_calls_db, user):
    gemini.script()
    openai.script(openai.text("ok", usage={"prompt_tokens": 1000, "completion_tokens": 40, "total_tokens": 1040,
                                           "prompt_tokens_details": {"cached_tokens": 800}}))

    llm_usage.start_turn(user.phone_number)
    chamar(label="idle")
    llm_usage.end_turn()

    [linha] = llm_calls_db.scalars(select(LlmCall)).all()
    assert (linha.model, linha.prompt_tokens, linha.cached_tokens, linha.output_tokens) == \
        ("gpt-6-luna-2026-09-23", 1000, 800, 40)


def test_registro_de_serie_com_a_luna(db, user, start_workout, make_plan, gemini, openai, exercise_id):
    ws = start_workout(make_plan("Peito", [("Supino", 3)]))
    ex = exercise_id(build_context(db, user, ws), "Supino")
    gemini.script()
    openai.script(openai.call("log_sets", exercise_id=ex, sets=[{"weight_kg": 80, "reps": 8}]))

    resposta, estado = session_agent.handle(db, user, "supino 80kg 8")

    assert resposta.text.startswith("✅ *Supino*: série 1 (80kg x 8)") and estado == "session"
    oferecidas = [t["function"]["name"] for t in openai.requests[0]["tools"]]
    assert "log_sets" in oferecidas and "finish_session" in oferecidas


def test_luna_e_reconhecida_pelo_nome():
    assert llm_openai.is_openai_model("gpt-6-luna") and llm_openai.is_openai_model("openai/gpt-6-luna")
    assert not llm_openai.is_openai_model("gemini-flash-lite-latest")
