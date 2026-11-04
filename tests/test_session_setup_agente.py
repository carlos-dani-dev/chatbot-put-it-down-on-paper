"""O agente do session_setup com o Gemini falso: da escolha do treino até a mensagem de início."""
from agent.agents import session_setup as agente
from agent.repos import session_repo


def test_mensagem_vazia_lista_os_planos_sem_chamar_a_llm(db, user, make_plan):
    make_plan("Perna")
    make_plan("Peito")

    resposta, estado = agente.handle(db, user, "")

    assert estado == "session_setup"
    assert "Perna" in resposta.text and "Peito" in resposta.text and "treino livre" in resposta.text


def test_mensagem_vazia_sem_planos(db, user):
    resposta, _ = agente.handle(db, user, None)
    assert "nenhum plano salvo" in resposta.text


def test_treino_livre_inicia_com_a_mensagem_fixa(db, user, gemini):
    fake = gemini.script(gemini.call("start_free_session"))

    resposta, estado = agente.handle(db, user, "treino livre")

    assert estado == "session"
    assert resposta.text.startswith("💪 Treino livre iniciado! Bora!")
    assert "*Registrar séries*" in resposta.text and "*Finalizar treino*" in resposta.text
    assert "Ver desempenho anterior" not in resposta.text  # só faz sentido com plano
    assert session_repo.get_open_session(db, user).plan_id is None
    assert len(fake.calls) == 1  # a mensagem de início é montada em código: nenhuma 2ª chamada à LLM


def test_treino_com_plano_inicia_citando_o_plano(db, user, make_plan, gemini):
    plano = make_plan("Perna")
    gemini.script(gemini.call("start_session_with_plan", plan_id=f"plan:{plano.id}"))

    resposta, estado = agente.handle(db, user, "vou de perna")

    assert estado == "session"
    assert resposta.text.startswith("💪 Treino *Perna* iniciado! Bora!")
    assert "*Ver desempenho anterior*" in resposta.text
    assert session_repo.get_open_session(db, user).plan_id == plano.id


def test_plano_inexistente_volta_o_erro_pra_llm_e_nao_inicia(db, user, make_plan, gemini):
    make_plan("Perna")
    fake = gemini.script(
        gemini.call("start_session_with_plan", plan_id="plan:999"),
        gemini.text("Não achei esse plano. Quer *Perna* ou *Treino livre*?"),
    )

    resposta, estado = agente.handle(db, user, "plano costas")

    assert estado == "session_setup"
    assert resposta.text == "Não achei esse plano. Quer *Perna* ou *Treino livre*?"
    assert fake.tool_results(1)["start_session_with_plan"]["ok"] is False
    assert session_repo.get_open_session(db, user) is None


def test_os_planos_salvos_aparecem_no_prompt(db, user, make_plan, gemini):
    make_plan("Perna")
    fake = gemini.script(gemini.text("Qual treino?"))

    agente.handle(db, user, "oi")

    assert "Planos de treino salvos, com os IDs entre colchetes: [plan:1] Perna" in fake.system_prompt(0)
    assert "start_session_with_plan" in fake.offered_tools(0)


def test_voltar_vai_pro_menu_e_limpa_o_estado(db, user, gemini):
    user.state_data = {"step": "choosing_plan"}
    gemini.script(gemini.call("go_back"))

    resposta, estado = agente.handle(db, user, "deixa pra depois")

    assert estado == "idle"
    assert user.state_data == {}
    assert resposta.text == "[menu gerado pela LLM]"


def test_ir_pro_planejamento(db, user, gemini):
    gemini.script(gemini.call("go_to_planning"))

    _resposta, estado = agente.handle(db, user, "quero criar um plano")

    assert estado == "planning"
    assert user.state_data == {"step": "awaiting_plan"}


def test_resposta_so_de_texto_continua_na_escolha(db, user, gemini):
    gemini.script(gemini.text("Quer **Treino livre**?"))

    resposta, estado = agente.handle(db, user, "hm")

    assert (resposta.text, estado) == ("Quer *Treino livre*?", "session_setup")


def test_llm_errando_sem_parar_para_no_limite(db, user, gemini):
    fake = gemini.script(*[gemini.call("start_session_with_plan", plan_id="plan:999")
                           for _ in range(agente._MAX_TOOL_ROUNDS)])

    resposta, estado = agente.handle(db, user, "?")

    assert len(fake.calls) == agente._MAX_TOOL_ROUNDS
    assert "Tive dificuldade" in resposta.text and estado == "session_setup"
