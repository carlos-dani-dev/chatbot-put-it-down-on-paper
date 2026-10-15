import unicodedata
from typing import Any
from dataclasses import dataclass

from ..models import PlanExercise, Exercise, WorkoutSession
from ..schemas.replies import TextReply, ButtonsReply
from ..schemas.llm_response import ParsedSessionMessage

from ._shared import enter_idle, idle_menu
from ..config import get_llm_client
from ..repos import exercise_repo, plan_repo, session_repo

llm = get_llm_client()

MAX_SETS_PER_MESSAGE = 20

_SESSION_PROMPT = (
    "Você interpreta mensagens de um usuário registrando séries numa academia.\n"
    "Exercícios do treino de hoje: {plan_exercises}.\n"
    "Exercício atual: {current}.\n\n"
    "Regras:\n"
    "- Se a mensagem citar um exercício da lista de hoje, devolva exatamente o nome da lista.\n"
    "- Se citar outro exercício, devolva o nome como foi escrito.\n"
    "- Se não citar nenhum exercício, deixe exercise vazio.\n"
    "- Converta libras para kg. Peso corporal vale 0.\n"
    "- Nunca invente valores: se a carga não foi dita, deixe weight_kg vazio.\n"
    "- Se a mensagem não registra séries, use intent unknown.\n\n"
    "Mensagem: {text}"
)

@dataclass
class _Target:
    exercise: Exercise | None          # None = exercício novo, criado só na gravação
    name: str
    pe: PlanExercise | None            # item do plano, ou None (livre / extra)
    is_new: bool = False


def _fmt_set(weight: float, reps: int) -> str:
    w = "peso corporal" if weight == 0 else f"{weight:g}kg"
    return f"{w} × {reps}"


def handle_session(db, user, text=None, button_id=None):
    ws = session_repo.get_open_session(db, user)
    if ws is None:
        return enter_idle(user, idle_menu("🤔 Não achei um treino em andamento."))

    if button_id == "finish":
        n = session_repo.close_session(db, ws)
        return enter_idle(user, TextReply(f"🎉 Treino finalizado!\n{n} séries registradas.\n\nMandou muito bem, te vejo no próximo treino!"))

    if button_id == "edit":
        removed = session_repo.undo_last_set(db, ws)
        if removed is None:
            return TextReply("🤔 Não tem nenhuma série registrada pra remover ainda."), "session"

        ex = db.get(Exercise, removed.exercise_id)
        return TextReply(
            f"↩️ Removi a última série de *{ex.name}* ({_fmt_set(float(removed.weight_kg), removed.reps)}).\n\n"
            "Manda de novo com os valores certos."
        ), "session"

    if button_id or not text: # botão antigo ou mídia
        return TextReply("📝 Me manda as séries, ex.: *supino 80kg 8 reps*"), "session"

    return log_from_message(db, ws, text), "session"


def log_from_message(db: Any, ws: WorkoutSession, text: str) -> TextReply | ButtonsReply:

    def _norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", s)
        s = "".join(c for c in s if not unicodedata.combining(c))
        return " ".join(s.lower().split())
    
 

    def _plan_index() -> dict[str, tuple[PlanExercise, Exercise]]:
        """{nome normalizado: (item do plano, exercício)} do treino de hoje, na ordem do plano."""
        if ws.plan_id is None:
            return {}
        return {_norm(ex.name): (pe, ex) for pe, ex in plan_repo.get_plan_exercises(db, ws.plan_id)}

    def _resolve(plan_idx: dict, raw_name: str | None) -> _Target | None:
        def _pe_for(exercise_id: int) -> PlanExercise | None:
            return next((pe for pe, ex in plan_idx.values() if ex.id == exercise_id), None)

        name = (raw_name or "").strip()[:60]

        if not name:                                         # sem nome: exercício atual
            if ws.current_exercise_id is None:
                return None
            ex = db.get(Exercise, ws.current_exercise_id)
            return _Target(ex, ex.name, _pe_for(ex.id))

        hit = plan_idx.get(_norm(name))                      # 1) item do treino de hoje
        if hit:
            pe, ex = hit
            return _Target(ex, ex.name, pe)

        ex = exercise_repo.find_by_name(db, name)
        if ex:                                               # 2) já existe no catálogo
            return _Target(ex, ex.name, _pe_for(ex.id))

        return _Target(None, name[:1].upper() + name[1:], None, is_new=True)   # 3) novo

    def parse_session_message(text: str, plan_names: list[str], current: str | None) -> ParsedSessionMessage:
        prompt = _SESSION_PROMPT.format(
            plan_exercises=", ".join(plan_names) or "nenhum (treino livre)",
            current=current or "nenhum",
            text=text,
        )
        result = llm.generate(prompt, schema=ParsedSessionMessage)
        return ParsedSessionMessage.model_validate_json(result)

    plan_idx = _plan_index()
    current = db.get(Exercise, ws.current_exercise_id) if ws.current_exercise_id else None

    try:
        parsed = parse_session_message(
            text, [ex.name for _, ex in plan_idx.values()], current.name if current else None
        )
    except Exception:                                    # timeout, erro de API, JSON inválido
        return TextReply("🤔 Tive um problema pra entender a mensagem. Pode mandar de novo?")

    if parsed.intent != "log_sets" or not parsed.entries:
        return TextReply("📝 Me manda as séries, ex.: *supino 80kg 8 reps*")
 
    # passo 1: resolver e validar TUDO, sem gravar nada
    prepared: list[tuple[_Target, list[tuple[float, int, int | None]]]] = []
    total = 0
    for entry in parsed.entries:
        target = _resolve(plan_idx, entry.exercise)
        if target is None:
            return TextReply("🤔 Qual exercício? Me manda o nome junto, ex.: *supino 80kg 8 reps*")

        weight = session_repo.get_last_weight(db, ws.id, target.exercise.id) if target.exercise else None
        sets = []
        for s in entry.sets:
            if s.weight_kg is not None:
                weight = s.weight_kg
            if weight is None:
                return TextReply(
                    f"🤔 Qual a carga no *{target.name}*? Manda de novo com o peso, "
                    f"ex.: *{target.name} 80kg {s.reps} reps*"
                )
            if not (0 <= weight <= 500 and 1 <= s.reps <= 100):
                return TextReply(
                    "Esses valores parecem errados 🤔 (carga de 0 a 500 kg, de 1 a 100 repetições). "
                    "Não registrei nada, manda de novo?"
                )
            rest = s.rest_seconds if s.rest_seconds is not None and 0 <= s.rest_seconds <= 3600 else None
            sets.append((weight, s.reps, rest))
 
        total += len(sets)
        if sets:
            prepared.append((target, sets))
 
    if not prepared or total > MAX_SETS_PER_MESSAGE:
        return TextReply("🤔 Não consegui registrar. Manda poucas séries por vez, ex.: *supino 80kg 8 reps*")
 
    # passo 2: gravar
    lines = []
    for target, sets in prepared:
        ex = target.exercise
        if ex is None:
            ex = exercise_repo.create(db, target.name)

        first, n = session_repo.log_sets(
            db, ws, ex.id, target.pe.id if target.pe else None, sets, text
        )

        done = session_repo.count_sets(db, ws.id, ex.id)
        if target.pe:
            tail = f"Progresso: {done}/{target.pe.target_sets}"
        elif ws.plan_id is not None:
            tail = "Fora do plano" + (" (exercício novo)" if target.is_new else "")
        else:
            tail = "Exercício novo" if target.is_new else f"Total na sessão: {done}"
 
        label = f"série {first}" if first == n else f"séries {first} a {n}"
        detail = ", ".join(_fmt_set(w, r) for w, r, _ in sets)
        lines.append(f"✅ *{ex.name}*: {label} ({detail})\n{tail}")
 
    return ButtonsReply("\n\n".join(lines), [("finish", "Finalizar treino"), ("edit", "Editar série")])