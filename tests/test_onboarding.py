"""onboarding — boas-vindas de quem chega pela primeira vez."""
from agent.agents import onboarding as agente
from agent.context.onboarding import build_context
from agent.terms import ACCEPT_BUTTONS, PRIVACY_URL, TERMS_URL, TERMS_VERSION
from agent.tools import onboarding as tools


def test_primeira_mensagem_recebe_boas_vindas_com_o_aceite_dos_termos(db, make_user):
    novo = make_user(state="onboarding")

    resposta, estado = agente.handle(db, novo, "oi")

    assert estado == "onboarding"
    assert "Eu sou o *Arnaldo*" in resposta.text
    assert TERMS_URL in resposta.text and PRIVACY_URL in resposta.text
    assert resposta.buttons == ACCEPT_BUTTONS  # o toque no botão é o aceite (ver test_termos.py)
    assert novo.state_data == {"step": "awaiting_user_onboarding_reply"}


def test_quem_ja_aceitou_recebe_as_boas_vindas_sem_os_termos(db, make_user):
    """Ex.: aceitou ao mandar /pro antes das boas-vindas."""
    novo = make_user(state="onboarding")
    novo.terms_version = TERMS_VERSION

    resposta, estado = agente.handle(db, novo, "oi")

    assert estado == "onboarding"
    assert resposta.text.endswith("Vamos começar?") and TERMS_URL not in resposta.text


def test_resposta_vazia_depois_das_boas_vindas(db, make_user):
    novo = make_user(state="onboarding")
    novo.state_data = {"step": "awaiting_user_onboarding_reply"}

    resposta, estado = agente.handle(db, novo, "")

    assert (resposta.text, estado) == ("Vamos começar? É só me dizer 🙂", "onboarding")


def aguardando_resposta(make_user):
    novo = make_user(state="onboarding")
    novo.state_data = {"step": "awaiting_user_onboarding_reply"}
    return novo


def test_aceitar_conclui_o_onboarding_e_abre_o_menu(db, make_user, gemini):
    novo = aguardando_resposta(make_user)
    gemini.script(gemini.call("confirm_start"))

    resposta, estado = agente.handle(db, novo, "bora!")

    assert estado == "idle"
    assert novo.onboarding_completed is True
    assert novo.state_data == {}
    assert resposta.text == "[menu gerado pela LLM]"


def test_recusar_tambem_conclui_mas_com_mensagem_propria(db, make_user, gemini):
    novo = aguardando_resposta(make_user)
    gemini.script(gemini.call("decline_start"))

    resposta, estado = agente.handle(db, novo, "agora não")

    assert estado == "idle"
    assert novo.onboarding_completed is True
    assert resposta.text == "🙂 Claro, me avise quando quiser continuar!"


def test_duvida_e_respondida_em_texto_e_continua_no_onboarding(db, make_user, gemini):
    novo = aguardando_resposta(make_user)
    fake = gemini.script(gemini.text("Eu anoto suas **séries**. Quer *Começar agora*?"))

    resposta, estado = agente.handle(db, novo, "como funciona?")

    assert estado == "onboarding"
    assert resposta.text == "Eu anoto suas *séries*. Quer *Começar agora*?"
    assert fake.offered_tools(0) == ["confirm_start", "decline_start"]
    assert novo.onboarding_completed is False


def test_ferramenta_inesperada_nao_conclui_nada(db, make_user, gemini):
    novo = aguardando_resposta(make_user)
    gemini.script(gemini.call("start_session"))  # existe em outro estado, não aqui

    resposta, estado = agente.handle(db, novo, "?")

    assert estado == "onboarding" and "Não consegui processar" in resposta.text
    assert novo.onboarding_completed is False


def test_ferramentas_diretamente(db, make_user):
    novo = make_user(state="onboarding")
    ctx = build_context(db, novo)

    assert tools.confirm_start(db, novo, ctx) == {"ok": True, "next_state": "idle"}
    assert tools.decline_start(db, novo, ctx)["next_state"] == "idle"
    assert novo.onboarding_completed is True
