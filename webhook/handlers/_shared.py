import re
from typing import Any

from ..models import User
from ..schemas.replies import TextReply, ButtonsReply, ListReply
from ..repos import plan_repo, chat_message_repo
from ..config import get_llm_client

llm = get_llm_client()


def _recent_history(db: Any, user: User, limit: int = 2) -> str:
    last_messages = chat_message_repo.get_last_messages(db, user, limit=limit)
    return "\n".join(f"{m.role}: {m.content}" for m in reversed(last_messages)) or "(nenhuma mensagem anterior)"


def _fix_whatsapp_bold(text: str) -> str:
    text = text.replace("**", "*")
    return re.sub(r"\*([^*\n]+)\*", lambda m: f"*{m.group(1).strip()}*", text)


def _generate(prompt: str) -> str:
    return _fix_whatsapp_bold(llm.generate(prompt))


_ENTER_PLANNING_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário quer criar um "
    "plano de treino novo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Planos de treino que ele já tem salvos (pode estar vazio): {plans}\n\n"
    "{note}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Se ele já tiver planos salvos, cite os nomes deles.\n"
    "- É de suma importância pedir SEMPRE o nome do plano novo, os exercícios e a"
    "quantidade de séries para cada um deles — sem isso o plano não pode ser criado.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_LIST_PLANS_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário quer ver quais "
    "planos de treino ele já tem salvos.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Planos de treino salvos: {plans}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental listar os nomes de TODOS os planos salvos acima, numa única linha, "
    "separados por vírgula.\n"
    "- Pergunte se ele quer criar um plano novo, excluir um dos listados, ou começar um treino.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_NO_PLANS_SAVED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário quer ver quais "
    "planos de treino ele já tem salvos, mas ele não tem nenhum plano salvo ainda.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Avise que não há nenhum plano salvo ainda.\n"
    "- É fundamental sugerir que ele pode *criar um plano novo* me mandando o nome e os exercícios.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_LIST_EXERCISES_MENU_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário quer ver os "
    "exercícios de um dos planos de treino salvos.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Planos de treino salvos, que ele pode escolher: {plans}\n\n"
    "{intro}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental listar os nomes de TODOS os planos salvos acima, numa única linha, "
    "separados por vírgula.\n"
    "- Peça pra ele dizer o nome exato de um dos planos listados.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PLAN_EXERCISE_LIST_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário pediu pra ver "
    "os exercícios de um plano de treino salvo (fora de uma sessão de treino).\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Nome do plano: {name}\n"
    "Exercícios do plano (nome e quantidade de séries planejadas): {items}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental listar, numa única linha, todos os exercícios acima com a "
    "quantidade de séries de cada um.\n"
    "- Pergunte se ele quer fazer mais alguma coisa com os planos (criar um novo, ver "
    "outro plano, ou excluir um).\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_DELETE_PLAN_MENU_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário quer excluir "
    "um plano de treino salvo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Planos de treino salvos, que ele pode escolher excluir: {plans}\n\n"
    "{intro}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental listar os nomes de TODOS os planos salvos acima, numa única linha, "
    "separados por vírgula.\n"
    "- Peça pra ele dizer o nome exato de um dos planos listados, pra confirmar a exclusão.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_NO_PLANS_TO_DELETE_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário pediu pra "
    "excluir um plano de treino, mas ele não tem nenhum plano salvo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Avise que não há nenhum plano salvo pra excluir.\n"
    "- É fundamental sugerir que ele pode *criar um plano novo* me mandando o nome e os exercícios.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_NO_EXERCISES_PARSED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário tentou descrever "
    "um plano de treino novo, mas não foi possível identificar nenhum exercício na mensagem.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Avise que não identificou nenhum exercício.\n"
    "- É fundamental pedir de novo o nome do plano e os exercícios, com a quantidade de "
    "séries de cada um, e dar um exemplo curto (ex.: *Peito: Supino 3 séries*).\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_DRAFT_MENU_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está montando um "
    "plano de treino novo, ainda não salvo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Nome do plano: {name}\n"
    "Exercícios já incluídos (nome e número de séries): {items}\n\n"
    "{note}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental listar, numa única linha, todos os exercícios já incluídos acima "
    "com a quantidade de séries de cada um.\n"
    "- Pergunte se ele quer adicionar mais exercícios, salvar ou cancelar o plano.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PLAN_SAVED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário acabou de salvar "
    "um plano de treino novo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Nome do plano salvo: {name}\n"
    "Exercícios do plano (nome e número de séries): {items}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Confirme que o plano foi salvo, citando o nome dele em negrito.\n"
    "- É fundamental informar que ele pode *Começar um treino* ou *Planejar outro treino* "
    "quando quiser — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PLAN_CANCELED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário acabou de "
    "cancelar/descartar o plano de treino que estava montando.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Confirme que o plano foi cancelado/descartado.\n"
    "- É fundamental perguntar se ele quer *Planejar um treino* ou *Começar uma sessão "
    "de treino* — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PLAN_DELETED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário acabou de excluir "
    "um plano de treino salvo.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Nome do plano excluído: {name}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Confirme que o plano foi excluído, citando o nome dele em negrito.\n"
    "- É fundamental perguntar se ele quer *Planejar um treino* ou *Começar uma sessão "
    "de treino* — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PLAN_EXERCISES_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está numa sessão "
    "de treino ativa e quer ver quais exercícios fazem parte do plano de hoje.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Nome do plano: {name}\n"
    "Exercícios do plano, com o progresso de séries já feitas (feito/meta): {items}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental listar, numa única linha, todos os exercícios acima com o progresso "
    "de cada um.\n"
    "- Lembre que ele pode continuar registrando as séries.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_FREE_SESSION_NO_PLAN_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está numa sessão "
    "de treino livre (sem plano salvo) e perguntou quais exercícios fazem parte do treino "
    "de hoje — mas um treino livre não tem uma lista fixa de exercícios.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Explique que é um treino livre, sem lista fixa de exercícios.\n"
    "- Incentive ele a continuar registrando os exercícios que for fazendo.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PLANNING_BLOCKED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está numa sessão "
    "de treino ativa, mas pediu algo relacionado a planos de treino (ver, excluir ou editar "
    "um plano) — isso só pode ser feito fora de uma sessão de treino.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Explique que pra mexer nos planos ele precisa encerrar a sessão de treino atual primeiro.\n"
    "- É fundamental dizer que ele pode encerrar dizendo que quer *finalizar* o treino.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_SESSION_FINISHED_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário acabou de "
    "finalizar a sessão de treino de hoje.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Séries registradas na sessão: {n_sets}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental parabenizar o usuário e citar quantas séries ele registrou.\n"
    "- É fundamental perguntar se ele quer *Planejar um treino* ou *Começar uma sessão "
    "de treino* — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_NO_SET_TO_UNDO_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário pediu pra "
    "corrigir ou desfazer a última série registrada, mas ainda não existe nenhuma "
    "série registrada nessa sessão de treino.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental avisar que não há nenhuma série registrada ainda pra remover.\n"
    "- Incentive ele a mandar a primeira série do treino.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_SET_UNDONE_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário pediu pra "
    "corrigir a última série registrada, e ela acabou de ser removida.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Exercício da série removida: {exercise_name}\n"
    "Detalhe da série removida: {set_detail}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental confirmar que a série removida foi a do exercício e dos valores acima.\n"
    "- Incentive ele a mandar de novo, agora com os valores certos.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_ASK_FOR_SETS_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está numa sessão "
    "de treino ativa e precisa mandar as séries que fez (exercício, peso, repetições).\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "{note}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental pedir pra ele mandar o exercício, o peso e as repetições, com um "
    "exemplo curto (ex.: *supino 80kg 8 reps*).\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_SESSION_MESSAGE_ERROR_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. Houve um problema técnico "
    "ao tentar entender a última mensagem do usuário, numa sessão de treino ativa.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- Peça desculpas pelo problema, sem entrar em detalhes técnicos.\n"
    "- É fundamental pedir pra ele mandar a mensagem de novo.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_WHICH_EXERCISE_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário relatou uma "
    "série numa sessão de treino, mas não deu pra saber qual é o exercício.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental perguntar qual é o exercício, pedindo pra mandar o nome junto com "
    "o peso e as repetições, com um exemplo curto (ex.: *supino 80kg 8 reps*).\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_MISSING_WEIGHT_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário relatou uma "
    "série, mas não disse a carga usada.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Exercício: {exercise_name}\n"
    "Repetições relatadas: {reps}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental perguntar qual foi a carga usada nesse exercício, pedindo pra mandar "
    "de novo com o peso, citando o nome do exercício e as repetições acima como exemplo.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_INVALID_SET_VALUES_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário relatou uma "
    "série com valores fora do aceitável (carga ou repetições absurdas), e nada foi "
    "registrado.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental avisar que os valores parecem errados (carga de 0 a 500 kg, de 1 a "
    "100 repetições) e que nada foi registrado.\n"
    "- Peça pra ele mandar de novo com os valores corretos.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_TOO_MANY_SETS_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário mandou muitas "
    "séries de uma vez, ou nada pôde ser registrado, numa única mensagem.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental avisar que não conseguiu registrar e pedir pra mandar poucas séries "
    "por vez, com um exemplo curto (ex.: *supino 80kg 8 reps*).\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

_PRODUCT_FAQ = (
    "Você é o assistente do chatbot de treino no WhatsApp. O usuário fez uma "
    "pergunta sobre o produto. Responda breve e amigável, usando só estas "
    "informações:\n\n"
    "- Registra treinos, séries e cargas mandando mensagem de texto normal.\n"
    "- Um modelo de IA interpreta o que foi digitado (exercício, peso, reps).\n"
    "- Dá pra criar plano de treino com antecedência ou treinar livre.\n"
    "- O progresso fica salvo automaticamente, comparado com a meta do plano.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "Pergunta do usuário: "
)


def product_faq_prompt(db: Any, user: User, text: str) -> str:
    return _PRODUCT_FAQ.format(history=_recent_history(db, user)) + text

_IDLE_MENU_PROMPT = (
    "Você é o assistente de um chatbot de treino no WhatsApp. O usuário está de volta "
    "ao menu principal.\n\n"
    "Últimas mensagens trocadas com ele, para contexto (pode estar vazio):\n{history}\n\n"
    "{intro}"
    "Escreva a próxima mensagem do bot, respeitando:\n"
    "- Seja sucinto e direto ao ponto, sem enrolação.\n"
    "- É fundamental perguntar se ele quer *Planejar um treino* ou *Começar uma sessão "
    "de treino* — escreva essas duas opções exatamente assim, em negrito.\n"
    "- O WhatsApp usa um asterisco só para negrito (*assim*), nunca dois (**assim** não funciona). "
    "O asterisco tem que colar direto na primeira e na última letra do trecho, sem espaço "
    "nenhum entre o asterisco e a palavra (nunca * assim*, *assim * ou * assim *)."
)

def enter_idle(db: Any, user: User, reply=None, clear_data: bool = True) -> tuple[TextReply | ButtonsReply, str]:

    if clear_data:
        user.state_data = {}

    return (reply or idle_menu(db, user)), "idle"


def enter_planning(db, user: User, note: str = "") -> tuple[TextReply | ButtonsReply, str]:
    names = plan_repo.list_active_plan_names(db, user)
    user.state_data = {"step": "awaiting_plan"}

    prompt = _ENTER_PLANNING_PROMPT.format(
        history=_recent_history(db, user),
        plans=", ".join(names) if names else "(nenhum)",
        note=f"{note}\n\n" if note else "",
    )
    return TextReply(_generate(prompt)), "planning"


def plan_picker(plans, note: str="") -> ListReply | ButtonsReply:
    if not plans:
            return ButtonsReply(
                f"{note}📋 Você ainda não tem treinos salvos.\n"
                "Que tal começar um *treino livre*? É só escolher aqui embaixo!"
                "\n\nSe quiser *voltar* para o menu anterior é só me avisar!",
                [("free", "Treino livre")],
            )

    rows = [(f"plan:{p.id}", p.name[:24]) for p in plans]
    rows += [("free", "Treino livre")]
    return ListReply(f"{note}🏋️ Boa, então conta pra mim, qual treino vamos fazer hoje?"
                     "\n\nSe quiser *voltar* para o menu anterior é só me avisar!", "Escolher", rows)


def enter_session_setup(db: Any, user: User) -> tuple[TextReply | ButtonsReply, str]:

    user.state_data = {"step": "choosing_plan"}
    
    return plan_picker(plan_repo.list_active_plans(db, user, limit=8)), "session_setup"


def idle_menu(db: Any, user: User, intro: str = "") -> TextReply:

    prompt = _IDLE_MENU_PROMPT.format(
        history=_recent_history(db, user),
        intro=f"{intro}\n\n" if intro else "",
    )
    return TextReply(_generate(prompt))


def list_plans_menu(db: Any, user: User, plans) -> TextReply:

    prompt = _LIST_PLANS_PROMPT.format(
        history=_recent_history(db, user),
        plans=", ".join(p.name for p in plans),
    )
    return TextReply(_generate(prompt))


def no_plans_saved(db: Any, user: User) -> TextReply:

    prompt = _NO_PLANS_SAVED_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def list_exercises_menu(db: Any, user: User, plans, note: str = "") -> TextReply:

    prompt = _LIST_EXERCISES_MENU_PROMPT.format(
        history=_recent_history(db, user),
        plans=", ".join(p.name for p in plans),
        intro=f"{note}\n\n" if note else "",
    )
    return TextReply(_generate(prompt))


def plan_exercise_list(db: Any, user: User, name: str, items: str) -> TextReply:

    prompt = _PLAN_EXERCISE_LIST_PROMPT.format(
        history=_recent_history(db, user),
        name=name,
        items=items,
    )
    return TextReply(_generate(prompt))


def delete_plan_menu(db: Any, user: User, plans, note: str = "") -> TextReply:

    prompt = _DELETE_PLAN_MENU_PROMPT.format(
        history=_recent_history(db, user),
        plans=", ".join(p.name for p in plans),
        intro=f"{note}\n\n" if note else "",
    )
    return TextReply(_generate(prompt))


def no_plans_to_delete(db: Any, user: User) -> TextReply:

    prompt = _NO_PLANS_TO_DELETE_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def no_exercises_identified(db: Any, user: User) -> TextReply:

    prompt = _NO_EXERCISES_PARSED_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def draft_menu(db: Any, user: User, draft: dict, note: str = "") -> TextReply:

    items = ", ".join(f"{item['name']} ({item['target_sets']}x)" for item in draft["items"])
    prompt = _DRAFT_MENU_PROMPT.format(
        history=_recent_history(db, user),
        name=draft["name"],
        items=items,
        note=f"{note}\n\n" if note else "",
    )
    return TextReply(_generate(prompt))


def plan_saved(db: Any, user: User, draft: dict) -> TextReply:

    items = ", ".join(f"{item['name']} ({item['target_sets']}x)" for item in draft["items"])
    prompt = _PLAN_SAVED_PROMPT.format(
        history=_recent_history(db, user),
        name=draft["name"],
        items=items,
    )
    return TextReply(_generate(prompt))


def plan_canceled(db: Any, user: User) -> TextReply:

    prompt = _PLAN_CANCELED_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def plan_deleted(db: Any, user: User, plan_name: str) -> TextReply:

    prompt = _PLAN_DELETED_PROMPT.format(history=_recent_history(db, user), name=plan_name)
    return TextReply(_generate(prompt))


def plan_exercises_menu(db: Any, user: User, name: str, items: str) -> TextReply:

    prompt = _PLAN_EXERCISES_PROMPT.format(
        history=_recent_history(db, user),
        name=name,
        items=items,
    )
    return TextReply(_generate(prompt))


def free_session_no_plan(db: Any, user: User) -> TextReply:

    prompt = _FREE_SESSION_NO_PLAN_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def planning_blocked_in_session(db: Any, user: User) -> TextReply:

    prompt = _PLANNING_BLOCKED_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def session_finished(db: Any, user: User, n_sets: int) -> TextReply:

    prompt = _SESSION_FINISHED_PROMPT.format(history=_recent_history(db, user), n_sets=n_sets)
    return TextReply(_generate(prompt))


def no_set_to_undo(db: Any, user: User) -> TextReply:

    prompt = _NO_SET_TO_UNDO_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def set_undone(db: Any, user: User, exercise_name: str, set_detail: str) -> TextReply:

    prompt = _SET_UNDONE_PROMPT.format(
        history=_recent_history(db, user),
        exercise_name=exercise_name,
        set_detail=set_detail,
    )
    return TextReply(_generate(prompt))


def ask_for_sets(db: Any, user: User, note: str = "") -> TextReply:

    prompt = _ASK_FOR_SETS_PROMPT.format(
        history=_recent_history(db, user),
        note=f"{note}\n\n" if note else "",
    )
    return TextReply(_generate(prompt))


def session_message_error(db: Any, user: User) -> TextReply:

    prompt = _SESSION_MESSAGE_ERROR_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def which_exercise(db: Any, user: User) -> TextReply:

    prompt = _WHICH_EXERCISE_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def missing_weight(db: Any, user: User, exercise_name: str, reps: int) -> TextReply:

    prompt = _MISSING_WEIGHT_PROMPT.format(
        history=_recent_history(db, user),
        exercise_name=exercise_name,
        reps=reps,
    )
    return TextReply(_generate(prompt))


def invalid_set_values(db: Any, user: User) -> TextReply:

    prompt = _INVALID_SET_VALUES_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))


def too_many_sets(db: Any, user: User) -> TextReply:

    prompt = _TOO_MANY_SETS_PROMPT.format(history=_recent_history(db, user))
    return TextReply(_generate(prompt))