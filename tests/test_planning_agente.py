"""O agente de planejamento com o Gemini falso: montar, salvar, limites, excluir, ir treinar."""
from agent.agents import planning as agente
from agent.repos import plan_repo
from agent.schemas.replies import ButtonsReply, MultiTextReply


def planos_ativos(db, user):
    return plan_repo.list_active_plan_names(db, user)


def test_mensagem_vazia_pede_o_plano(db, user):
    resposta, estado = agente.handle(db, user, "")
    assert estado == "planning" and "Me manda o nome e os exercícios" in resposta.text


def test_montar_rascunho_e_continuar_no_planejamento(db, user, gemini):
    fake = gemini.script(
        gemini.call("add_to_draft", name="Peito", exercises=[{"name": "Supino reto com barra", "target_sets": 3}]),
        gemini.text("Montei o *Peito* com Supino reto com barra (3x). Quer *Salvar o plano*?"),
    )

    resposta, estado = agente.handle(db, user, "plano peito: supino reto com barra 3 séries")

    assert estado == "planning"
    assert user.state_data["plan_draft"]["items"] == [{"name": "Supino reto com barra", "target_sets": 3}]
    assert planos_ativos(db, user) == []
    assert "Rascunho em andamento: 'Peito'" in fake.system_prompt(1)  # a 2ª rodada já enxerga o rascunho
    assert "Salvar o plano" in resposta.text


def test_regra_de_copiar_o_nome_literal_esta_no_prompt(db, user, gemini):
    fake = gemini.script(gemini.text("ok"))
    agente.handle(db, user, "oi")
    assert "EXATAMENTE o que o usuário escreveu" in fake.system_prompt(0)


def test_salvar_grava_o_plano_e_fecha_com_uma_chamada_sem_ferramentas(db, user, gemini):
    user.state_data = {"plan_draft": {"name": "Peito", "items": [{"name": "Supino", "target_sets": 3}]}}
    fake = gemini.script(gemini.call("save_draft"), gemini.text("Plano *Peito* salvo! Bora *Começar um treino*?"))

    resposta, estado = agente.handle(db, user, "salva")

    assert estado == "idle"
    assert planos_ativos(db, user) == ["Peito"]
    assert user.state_data == {}  # enter_idle limpa o estado
    assert resposta.text == "Plano *Peito* salvo! Bora *Começar um treino*?"
    assert fake.offered_tools(1) == []  # a chamada de encerramento não oferece ferramentas


def test_salvar_sem_texto_da_llm_usa_o_padrao(db, user, gemini):
    user.state_data = {"plan_draft": {"name": "Peito", "items": [{"name": "Supino", "target_sets": 3}]}}
    gemini.script(gemini.call("save_draft"), gemini.text(""))

    resposta, estado = agente.handle(db, user, "salva")

    assert (resposta.text, estado) == ("✅ Pronto!", "idle")


def test_limite_de_planos_volta_pra_llm_explicar_sem_criar_nada(db, user, make_plan, gemini):
    make_plan("Perna")
    fake = gemini.script(
        gemini.call("add_to_draft", name="Peito", exercises=[{"name": "Supino", "target_sets": 3}]),
        gemini.text("No *Grátis* é 1 plano. Pode *Excluir um plano* ou *Assinar o Pro*."),
    )

    resposta, estado = agente.handle(db, user, "plano peito: supino 3")

    assert estado == "planning"
    assert fake.tool_results(1)["add_to_draft"]["limit_reached"] == "plans"
    assert "plan_draft" not in (user.state_data or {})
    assert "Plano de assinatura do usuário: Grátis" in fake.system_prompt(0)
    assert "Assinar o Pro" in resposta.text


def test_assinar_dentro_do_planejamento(db, user, gemini, mercadopago):
    gemini.script(gemini.call("assinar_pro"))

    resposta, estado = agente.handle(db, user, "quero o pro")

    assert estado == "planning"  # continua onde estava, com o rascunho intacto
    assert isinstance(resposta, MultiTextReply) and resposta.texts[1] == "00020126PIXCOPIAECOLA1"


def test_excluir_em_duas_mensagens(db, user, make_plan, gemini):
    plano = make_plan("Perna")

    gemini.script(gemini.call("request_delete_plan", plan_id=f"plan:{plano.id}"), gemini.text("Excluo o *Perna*?"))
    _resposta, estado = agente.handle(db, user, "apaga o perna")
    assert estado == "planning" and planos_ativos(db, user) == ["Perna"]  # só propôs

    fake = gemini.script(gemini.call("confirm_pending_action"), gemini.text("Plano *Perna* excluído."))
    resposta, estado = agente.handle(db, user, "sim")

    assert "confirm_pending_action" in fake.offered_tools(0)
    assert estado == "idle" and planos_ativos(db, user) == []
    assert resposta.text == "Plano *Perna* excluído."


def test_ir_treinar_sem_rascunho_abre_a_escolha_do_treino(db, user, gemini):
    gemini.script(gemini.call("go_to_session"))

    resposta, estado = agente.handle(db, user, "deixa, quero treinar")

    assert estado == "session_setup" and isinstance(resposta, ButtonsReply)


def test_ir_treinar_com_rascunho_aberto_e_barrado(db, user, gemini):
    user.state_data = {"plan_draft": {"name": "Peito", "items": [{"name": "Supino", "target_sets": 3}]}}
    fake = gemini.script(gemini.call("go_to_session"), gemini.text("Salva ou descarta o *Peito* antes?"))

    resposta, estado = agente.handle(db, user, "quero treinar")

    assert estado == "planning"
    assert fake.tool_results(1)["go_to_session"]["ok"] is False
    assert user.state_data["plan_draft"]["name"] == "Peito"


def test_ferramenta_desconhecida_e_argumento_errado_viram_erro_pra_llm(db, user, gemini):
    fake = gemini.script(
        gemini.call("apagar_tudo"),
        gemini.call("add_to_draft", name="Peito"),  # sem `exercises`
        gemini.text("Me diz os exercícios?"),
    )

    resposta, _ = agente.handle(db, user, "?")

    assert "Ferramenta desconhecida" in fake.tool_results(1)["apagar_tudo"]["error"]
    assert "Argumentos inválidos" in fake.tool_results(2)["add_to_draft"]["error"]
    assert resposta.text == "Me diz os exercícios?"


def test_llm_em_loop_para_no_limite(db, user, gemini):
    fake = gemini.script(*[gemini.call("list_plans") for _ in range(agente._MAX_TOOL_ROUNDS)])

    resposta, estado = agente.handle(db, user, "?")

    assert len(fake.calls) == agente._MAX_TOOL_ROUNDS
    assert "Tive dificuldade" in resposta.text and estado == "planning"
