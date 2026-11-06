"""Nenhuma mensagem do bot leva travessão: o texto da LLM passa por agent.shared._fix_whatsapp_bold,
que troca travessões por vírgula e itens de lista com '-' por '•'. Hífen dentro de palavra continua."""
import pytest

from agent.shared import _fix_whatsapp_bold


@pytest.mark.parametrize("entrada, saida", [
    ("Plano salvo — bora treinar!", "Plano salvo, bora treinar!"),
    ("Treino de peito - feito!", "Treino de peito, feito!"),
    ("Seu plano:\n- Supino (3 séries)\n- Crucifixo (3 séries)", "Seu plano:\n• Supino (3 séries)\n• Crucifixo (3 séries)"),
    ("Faça 10–12 repetições", "Faça 10 a 12 repetições"),
    ("**Supino** — 80kg (meta batida — boa)", "*Supino*, 80kg (meta batida, boa)"),
    ("Fechado —\nagora é salvar", "Fechado\nagora é salvar"),
    ("Boas-vindas! Use o cupom PARCEIRO-JOAO", "Boas-vindas! Use o cupom PARCEIRO-JOAO"),
])
def test_travessao_some_do_texto_da_llm(entrada, saida):
    assert _fix_whatsapp_bold(entrada) == saida
