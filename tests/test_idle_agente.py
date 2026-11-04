"""O agente do menu principal (idle) com o Gemini falso."""
from agent.agents import idle as agente
from agent.schemas.replies import ButtonsReply, CtaUrlReply, ListReply, MultiTextReply, TextReply


def test_mensagem_vazia_mostra_o_menu(db, user):
    resposta, estado = agente.handle(db, user, "")
    assert (resposta.text, estado) == ("[menu gerado pela LLM]", "idle")


def test_pergunta_generica_responde_em_texto(db, user, gemini):
    gemini.script(gemini.text("Eu registro seus **treinos**!"))
    resposta, estado = agente.handle(db, user, "o que você faz?")
    assert (resposta.text, estado) == ("Eu registro seus *treinos*!", "idle")


def test_prompt_informa_o_plano_de_assinatura_e_os_planos(db, user, make_plan, gemini):
    make_plan("Perna")
    fake = gemini.script(gemini.text("ok"))

    agente.handle(db, user, "oi")

    prompt = fake.system_prompt(0)
    assert "Plano de assinatura do usuário: Grátis" in prompt
    assert "Planos de treino salvos do usuário, com os IDs entre colchetes: [plan:1] Perna" in prompt


def test_comecar_treino_vai_pra_escolha_do_treino(db, user, make_plan, gemini):
    make_plan("Perna")
    gemini.script(gemini.call("start_session"))

    resposta, estado = agente.handle(db, user, "bora treinar")

    assert estado == "session_setup"
    assert isinstance(resposta, ListReply)  # lista com os planos + "Treino livre"
    assert user.state_data == {"step": "choosing_plan"}


def test_comecar_treino_sem_planos_oferece_treino_livre(db, user, gemini):
    gemini.script(gemini.call("start_session"))
    resposta, estado = agente.handle(db, user, "bora treinar")
    assert estado == "session_setup" and isinstance(resposta, ButtonsReply)


def test_criar_plano_vai_pro_planejamento(db, user, gemini):
    gemini.script(gemini.call("start_planning"))

    _resposta, estado = agente.handle(db, user, "quero montar um plano")

    assert estado == "planning"
    assert user.state_data == {"step": "awaiting_plan"}


def test_gratis_com_plano_nao_entra_no_planejamento(db, user, make_plan, gemini):
    make_plan("Perna")
    gemini.script(gemini.call("start_planning"))

    resposta, estado = agente.handle(db, user, "quero outro plano")

    assert estado == "idle"
    assert resposta.text == (
        "📋 No plano *Grátis* dá pra ter 1 plano ativo, e você já tem: *Perna*.\n\n"
        "Pra criar outro, você pode *Editar ou excluir um plano* ou *Assinar o Pro* "
        "pra ter planos ilimitados. 💪"
    )


def test_pro_com_plano_pode_criar_outro(db, user, make_plan, make_pro, gemini):
    make_plan("Perna")
    make_pro(user)
    gemini.script(gemini.call("start_planning"))

    _resposta, estado = agente.handle(db, user, "quero outro plano")

    assert estado == "planning"


def test_gerenciar_plano_delega_pro_planejamento_na_mesma_mensagem(db, user, make_plan, gemini):
    """'exclua o plano Perna' no menu: o idle passa a MESMA mensagem pro agente de planejamento,
    que já propõe a exclusão — sem o usuário ter que repetir o pedido."""
    plano = make_plan("Perna")
    fake = gemini.script(
        gemini.call("manage_plans"),                                         # idle
        gemini.call("request_delete_plan", plan_id=f"plan:{plano.id}"),      # planning
        gemini.text("Quer mesmo excluir o plano *Perna*?"),                  # planning
    )

    resposta, estado = agente.handle(db, user, "exclua o plano perna")

    assert estado == "planning"
    assert resposta.text == "Quer mesmo excluir o plano *Perna*?"
    assert user.state_data["pending_action"]["type"] == "delete_plan"
    assert fake.calls[1]["contents"][0].parts[0].text == "exclua o plano perna"  # mesma mensagem


def test_resultados_viram_botao_com_link(db, user, gemini):
    gemini.script(gemini.call("mostrar_resultados"))

    resposta, estado = agente.handle(db, user, "meus resultados")

    assert estado == "idle"
    assert isinstance(resposta, CtaUrlReply)
    assert resposta.button_text == "Ver resultados"
    assert "/webhook/dashboard?user_id=" in resposta.url


def test_resultados_sem_chave_respondem_erro_e_nao_link(db, user, gemini, monkeypatch):
    monkeypatch.setattr("agent.signing.settings.dashboard_signing_key", "")
    gemini.script(gemini.call("mostrar_resultados"))

    resposta, _ = agente.handle(db, user, "meus resultados")

    assert isinstance(resposta, TextReply) and "Não consegui gerar o link" in resposta.text


def test_assinar_manda_o_copia_e_cola_sozinho_na_segunda_mensagem(db, user, gemini, mercadopago):
    gemini.script(gemini.call("assinar_pro"))

    resposta, estado = agente.handle(db, user, "quero o pro")

    assert estado == "idle"
    assert isinstance(resposta, MultiTextReply)
    explicacao, codigo = resposta.texts
    assert "*Plano Pro*: R$ 19,90 por 30 dias" in explicacao
    assert codigo == "00020126PIXCOPIAECOLA1"  # sozinho, pra o usuário copiar com um toque


def test_assinar_sendo_pro(db, user, make_pro, gemini, mercadopago):
    make_pro(user)
    gemini.script(gemini.call("assinar_pro"))

    resposta, _ = agente.handle(db, user, "quero o pro")

    assert resposta.text.startswith("✅ Você já é *Pro*!")


def test_assinar_com_mercado_pago_fora_do_ar(db, user, gemini, mercadopago):
    mercadopago.offline = True
    gemini.script(gemini.call("assinar_pro"))

    resposta, _ = agente.handle(db, user, "quero o pro")

    assert resposta.text == "😕 Não consegui gerar o Pix agora. Tenta de novo daqui a pouco?"


def test_listar_planos_volta_o_resultado_pra_llm_responder(db, user, make_plan, gemini):
    make_plan("Perna")
    fake = gemini.script(gemini.call("list_plans"), gemini.text("Você tem o plano *Perna*."))

    resposta, estado = agente.handle(db, user, "quais planos tenho?")

    assert fake.tool_results(1)["list_plans"] == {"ok": True, "plans": [{"plan_id": "plan:1", "name": "Perna"}]}
    assert (resposta.text, estado) == ("Você tem o plano *Perna*.", "idle")


def test_llm_em_loop_para_no_limite(db, user, gemini):
    fake = gemini.script(*[gemini.call("list_plans") for _ in range(agente._MAX_TOOL_ROUNDS)])

    resposta, estado = agente.handle(db, user, "?")

    assert len(fake.calls) == agente._MAX_TOOL_ROUNDS
    assert "Tive dificuldade" in resposta.text and estado == "idle"
