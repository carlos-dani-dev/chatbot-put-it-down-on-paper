"""Nível 4 — o agente da sessão (agent/agents/session.py) com um Gemini FALSO.

A fixture `gemini` (conftest.py) instala um cliente roteirizado: cada `gemini.call(...)` ou
`gemini.text(...)` é uma resposta da "LLM", entregue na ordem. O teste não avalia a LLM — avalia
o que o NOSSO código faz com o que ela responde: que ferramenta executa, o que grava, como monta
a mensagem final, pra que estado vai.
"""
from sqlalchemy import select

from agent.agents import session as agente
from agent.context.session import build_context
from agent.models import WorkoutSet
from agent.repos import session_repo


def numero_de_series(db):
    return len(db.scalars(select(WorkoutSet)).all())


def test_sem_treino_aberto_volta_pro_menu_sem_chamar_a_llm(db, user):
    resposta, estado = agente.handle(db, user, "supino 80kg 8 reps")

    assert estado == "idle"
    assert resposta.text == "[menu gerado pela LLM]"  # o stub do conftest pros menus


def test_mensagem_vazia_pede_as_series(db, user, start_workout):
    start_workout()
    resposta, estado = agente.handle(db, user, "")
    assert estado == "session" and "Me manda as séries" in resposta.text


def test_registro_monta_a_resposta_em_codigo_sem_segunda_chamada(db, user, start_workout, make_plan, gemini, exercise_id):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 2), ("Stiff", 1)]))
    ex = exercise_id(build_context(db, user, ws), "Agachamento livre")
    fake = gemini.script(gemini.call("log_sets", exercise_id=ex, sets=[{"weight_kg": 60, "reps": 10}]))

    resposta, estado = agente.handle(db, user, "agachamento 60kg 10 reps")

    assert estado == "session"
    assert resposta.text == (
        "✅ *Agachamento livre*: série 1 (60kg x 10), progresso 1/2, falta 1 série\n\n"
        "Boa! Manda a próxima. 💪"
    )
    assert numero_de_series(db) == 1
    assert len(fake.calls) == 1  # o comentário saiu do código: a LLM só foi chamada pra decidir o registro


def test_registro_junto_com_outra_ferramenta_ainda_volta_pra_llm(db, user, start_workout, make_plan, gemini, exercise_id):
    """Ex.: "supino 80x8, e quanto fiz semana passada?" — a outra parte precisa da LLM pra responder."""
    ws = start_workout(make_plan("Peito", [("Supino", 3)]))
    ex = exercise_id(build_context(db, user, ws), "Supino")
    fake = gemini.script(
        gemini.calls(("log_sets", {"exercise_id": ex, "sets": [{"weight_kg": 80, "reps": 8}]}),
                     ("check_performance", {"exercise_id": ex})),
        gemini.text("Semana passada foi parecido!"),
    )

    resposta, _ = agente.handle(db, user, "supino 80x8, e semana passada?")

    assert resposta.text.startswith("✅ *Supino*") and resposta.text.endswith("Semana passada foi parecido!")
    assert len(fake.calls) == 2
    # na 2ª chamada a LLM foi avisada de que a confirmação já aparece (pra não repetir números)
    assert "já foi (ou vai ser) exibida automaticamente" not in fake.system_prompt(0)
    assert "já foi (ou vai ser) exibida automaticamente" in fake.system_prompt(1)
    assert fake.tool_results(1)["log_sets"]["target_reached"] is False


def test_regras_de_meta_estao_no_prompt(db, user, start_workout, gemini):
    start_workout()
    fake = gemini.script(gemini.text("Oi!"))

    agente.handle(db, user, "oi")

    prompt = fake.system_prompt(0)
    assert "NUNCA compare números por conta própria" in prompt
    assert "target_reached=false" in prompt


def test_dois_exercicios_na_mesma_resposta_geram_duas_linhas_na_ordem(db, user, start_workout, make_plan, gemini, exercise_id):
    ws = start_workout(make_plan("Perna", [("Agachamento livre", 1), ("Stiff", 1)]))
    ctx = build_context(db, user, ws)
    gemini.script(
        gemini.calls(
            ("log_sets", {"exercise_id": exercise_id(ctx, "Agachamento livre"), "sets": [{"weight_kg": 60, "reps": 10}]}),
            ("log_sets", {"exercise_id": exercise_id(ctx, "Stiff"), "sets": [{"weight_kg": 40, "reps": 12}]}),
        ),
    )

    resposta, _ = agente.handle(db, user, "agachamento 60x10 e stiff 40x12")

    linhas = resposta.text.split("\n\n")
    assert linhas[0].startswith("✅ *Agachamento livre*")
    assert linhas[1].startswith("✅ *Stiff*") and linhas[1].endswith("🏁 Todos os exercícios do plano bateram a meta!")
    assert linhas[2] == "Quando quiser, é só mandar *Finalizar treino*. 🏁"


def test_registro_recusado_nao_gera_linha_de_confirmacao(db, user, start_workout, gemini):
    start_workout()
    fake = gemini.script(
        gemini.call("log_sets", exercise_id="ex:999", sets=[{"weight_kg": 60, "reps": 10}]),
        gemini.text("Não achei esse exercício, qual foi?"),
    )

    resposta, _ = agente.handle(db, user, "xyz 60kg")

    assert resposta.text == "Não achei esse exercício, qual foi?"
    assert fake.tool_results(1)["log_sets"]["ok"] is False  # o erro voltou pra LLM se corrigir
    assert numero_de_series(db) == 0


def test_resposta_so_de_texto_passa_pelo_ajuste_de_negrito(db, user, start_workout, gemini):
    start_workout()
    gemini.script(gemini.text("Quer **Finalizar treino**?"))

    resposta, estado = agente.handle(db, user, "e agora?")

    assert resposta.text == "Quer *Finalizar treino*?"  # ** do Markdown vira * do WhatsApp
    assert estado == "session"


def test_finalizar_fecha_o_treino_e_vai_pro_menu(db, user, start_workout, log, gemini):
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10))
    fake = gemini.script(gemini.call("finish_session"), gemini.text("Treino fechado, mandou bem! 💪"))

    resposta, estado = agente.handle(db, user, "acabei")

    assert estado == "idle"
    assert resposta.text == "Treino fechado, mandou bem! 💪"
    assert ws.ended_reason == "finished"
    assert session_repo.get_open_session(db, user) is None
    assert fake.offered_tools(1) == []  # a mensagem de encerramento não oferece mais ferramentas


def test_finalizar_sem_texto_da_llm_usa_a_mensagem_padrao(db, user, start_workout, gemini):
    start_workout()
    gemini.script(gemini.call("finish_session"), gemini.text(""))

    resposta, estado = agente.handle(db, user, "fim")

    assert (resposta.text, estado) == ("🎉 Treino finalizado!", "idle")


def test_ferramenta_desconhecida_vira_erro_pra_llm(db, user, start_workout, gemini):
    start_workout()
    fake = gemini.script(gemini.call("apagar_tudo"), gemini.text("Não posso fazer isso."))

    resposta, _ = agente.handle(db, user, "apaga tudo")

    assert "Ferramenta desconhecida" in fake.tool_results(1)["apagar_tudo"]["error"]
    assert resposta.text == "Não posso fazer isso."


def test_argumento_faltando_vira_erro_pra_llm_em_vez_de_derrubar_o_bot(db, user, start_workout, gemini):
    start_workout()
    fake = gemini.script(gemini.call("log_sets", exercise_id="new"), gemini.text("Faltou a carga."))  # sem `sets`

    resposta, _ = agente.handle(db, user, "supino")

    assert "Argumentos inválidos" in fake.tool_results(1)["log_sets"]["error"]
    assert resposta.text == "Faltou a carga."


def test_llm_em_loop_de_ferramentas_para_no_limite(db, user, start_workout, gemini):
    start_workout()
    fake = gemini.script(*[gemini.call("list_exercises") for _ in range(agente._MAX_TOOL_ROUNDS)])

    resposta, estado = agente.handle(db, user, "?")

    assert len(fake.calls) == agente._MAX_TOOL_ROUNDS
    assert "Tive dificuldade" in resposta.text and estado == "session"


def test_exclusao_so_acontece_em_mensagem_separada_da_confirmacao(db, user, start_workout, log, gemini):
    """1ª mensagem: a LLM propõe apagar (fica pendente). 2ª mensagem: o usuário confirma."""
    ws = start_workout()
    log(ws, "Rosca direta", (12, 10))
    serie = build_context(db, user, ws).sets[0]

    gemini.script(gemini.call("request_delete_sets", set_ids=[f"set:{serie.id}"]), gemini.text("Apago a série 1?"))
    agente.handle(db, user, "apaga a última")
    assert numero_de_series(db) == 1  # só propôs

    fake = gemini.script(gemini.call("confirm_pending_action"), gemini.text("Apagada."))
    resposta, _ = agente.handle(db, user, "sim")

    assert "confirm_pending_action" in fake.offered_tools(0)  # só aparece porque há algo pendente
    assert numero_de_series(db) == 0
    assert resposta.text == "Apagada."


# ---------------------------------------------------------------- custo do prompt

def test_comeco_do_prompt_e_igual_em_toda_mensagem(db, user, start_workout, make_plan, log):
    """O cache do Gemini só aproveita o começo repetido do prompt: tudo o que muda fica depois do marcador."""
    ws = start_workout(make_plan("Peito", [("Supino", 3)]))
    antes = agente._system_prompt(build_context(db, user, ws))
    log(ws, "Supino", (80, 8))
    user.state_data = {"pending_action": {"type": "delete_sets", "set_ids": [1]}}
    depois = agente._system_prompt(build_context(db, user, ws), facts_confirmed=True)

    marcador = "=== Estado do treino agora ==="
    assert antes.split(marcador)[0] == depois.split(marcador)[0] == agente._SESSION_RULES.split(marcador)[0]
    assert "Regras importantes" in agente._SESSION_RULES  # as regras (a parte grande) estão no começo fixo
    assert "Supino" not in agente._SESSION_RULES


def test_prompt_lista_so_as_series_mais_recentes(db, user, start_workout, log):
    from agent.context.session import RECENT_SETS_SHOWN
    from agent.tools import session as tools

    ws = start_workout()
    log(ws, "Supino", *[(60 + i, 10) for i in range(RECENT_SETS_SHOWN + 2)])
    ctx = build_context(db, user, ws)

    prompt = agente._system_prompt(ctx)
    apagaveis = {d.name: d for d in tools.build_declarations(ctx)}["request_delete_sets"]

    assert f"Últimas {RECENT_SETS_SHOWN} de {RECENT_SETS_SHOWN + 2} séries" in prompt
    assert "60kg" not in prompt and f"{60 + RECENT_SETS_SHOWN + 1}kg" in prompt  # a mais antiga saiu, a última está
    assert len(apagaveis.parameters.properties["set_ids"].items.enum) == RECENT_SETS_SHOWN
