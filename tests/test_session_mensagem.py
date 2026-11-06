"""Nível 1 — função pura: a linha de confirmação que o usuário recebe ao registrar séries.

`format_log_sets_fact` recebe o resultado da ferramenta log_sets (um dict) e devolve o texto.
Sem banco, sem LLM: o teste é só "esta entrada -> este texto".
"""
import pytest

from agent.tools.session import format_log_sets_fact


def resultado(done, target=None, sets=((60, 10),), name="Agachamento livre", **extra):
    """Monta um resultado de log_sets como a ferramenta devolveria (ajudante, não é teste)."""
    first = done - len(sets) + 1
    r = {
        "exercise_name": name,
        "sets_logged": [{"weight_kg": w, "reps": reps} for w, reps in sets],
        "set_range": [first, done],
        "total_done": done,
        "target_sets": target,
    }
    if target is not None:
        r["remaining_sets"] = max(0, target - done)
        r["target_reached"] = done >= target
    r.update(extra)
    return r


# Arrange / Act / Assert — o padrão de todo teste
def test_o_caso_do_bug_falta_uma_serie_e_nao_diz_meta_batida():
    linha = format_log_sets_fact(resultado(done=1, target=2))  # 1ª série de um exercício com meta 2

    assert linha == "✅ *Agachamento livre*: série 1 (60kg x 10), progresso 1/2, falta 1 série"
    assert "meta batida" not in linha


# parametrize: o MESMO teste roda uma vez pra cada linha da tabela, e cada uma aparece
# separada no relatório (ex.: test_situacao_da_meta[2-4-faltam 2 séries])
@pytest.mark.parametrize(
    ("done", "target", "esperado"),
    [
        (1, 2, "falta 1 série"),
        (2, 4, "faltam 2 séries"),
        (2, 2, "meta batida 🎯"),
        (3, 2, "acima da meta 💪"),
    ],
)
def test_situacao_da_meta(done, target, esperado):
    assert format_log_sets_fact(resultado(done, target)).endswith(f"progresso {done}/{target}, {esperado}")


def test_meta_batida_pulando_a_meta_numa_mensagem_so_conta_como_batida_e_nao_acima():
    # tinha 1 de 2 e registrou 2 de uma vez -> 3/2: a meta foi alcançada AGORA, então "meta batida"
    linha = format_log_sets_fact(resultado(done=3, target=2, sets=((60, 10), (60, 8))))
    assert linha.endswith("meta batida 🎯")


@pytest.mark.parametrize(
    ("sets", "done", "faixa"),
    [
        (((60, 10),), 1, "série 1 (60kg x 10)"),
        (((60, 10), (62.5, 8), (65, 6)), 3, "séries 1 a 3 (60kg x 10, 62.5kg x 8, 65kg x 6)"),
        (((80, 5),), 4, "série 4 (80kg x 5)"),  # 4ª série: numeração continua de onde parou
    ],
)
def test_faixa_e_cargas_das_series(sets, done, faixa):
    assert f": {faixa}" in format_log_sets_fact(resultado(done, sets=sets))


def test_carga_inteira_sai_sem_casa_decimal():
    assert "(80kg x 8)" in format_log_sets_fact(resultado(1, sets=((80.0, 8),)))


def test_treino_livre_nao_fala_em_progresso_nem_meta():
    linha = format_log_sets_fact(resultado(done=2, target=None, sets=((20, 12), (20, 10))))

    assert linha == "✅ *Agachamento livre*: séries 1 a 2 (20kg x 12, 20kg x 10)"


def test_plano_completo_avisa_na_serie_que_completou():
    linha = format_log_sets_fact(resultado(done=1, target=1, all_targets_reached=True))

    assert linha.splitlines()[-1] == "🏁 Todos os exercícios do plano bateram a meta!"


def test_serie_extra_depois_do_plano_completo_nao_repete_o_aviso():
    # o plano já estava completo; esta é uma série a mais
    linha = format_log_sets_fact(resultado(done=2, target=1, all_targets_reached=True))

    assert "🏁" not in linha
    assert linha.endswith("acima da meta 💪")
