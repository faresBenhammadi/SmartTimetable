"""Bridge between UI data and the scheduling engine."""

from school import Assignment, Schedule, School, Teacher, TimeSlot, Subject, SchoolClass, Session
import data_store
import os
import threading
import uuid
from zoneinfo import ZoneInfo
from datetime import datetime
try:
    from cp_sat_solver import solve_with_cp_sat, validate_strict_subject_preferences
except ImportError:
    solve_with_cp_sat = None
    validate_strict_subject_preferences = None


# ─── In-memory async job store ────────────────────────────────────────────────
import threading
import uuid

_jobs = {}  # job_id -> {status, user_id, cancel_token, schedule_id, errors}
_jobs_lock = threading.Lock()


def _run_solver_in_background(job_id, user_id, generation_prefs=None):
    """Worker function that runs the solver in a background thread."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        cancel_token = job.get("cancel_token") if job else None

    try:
        success, result = run_solver(user_id, generation_prefs=generation_prefs, cancel_token=cancel_token)
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job and job["status"] == "canceling":
                _jobs[job_id] = {"status": "canceled", "user_id": user_id}
                return

            if success:
                from datetime import datetime
                time_str = datetime.now(ZoneInfo("Africa/Algiers")).strftime("%d/%m/%Y %H:%M")
                sched_name = f"Emploi du temps - {time_str}"
                schedule_id = data_store.save_schedule(user_id, sched_name, result)
                data_store.set_last_schedule(result, user_id)
                _jobs[job_id] = {
                    "status": "done",
                    "user_id": user_id,
                    "schedule_id": schedule_id,
                }
            else:
                _jobs[job_id] = {"status": "error", "user_id": user_id, "errors": result}
    except Exception as e:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job and job["status"] == "canceling":
                _jobs[job_id] = {"status": "canceled", "user_id": user_id}
            else:
                _jobs[job_id] = {"status": "error", "user_id": user_id, "errors": [str(e)]}


def start_solver_job(user_id, generation_prefs=None):
    """Starts a background solver job and returns the job_id immediately."""
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "user_id": user_id,
            "cancel_token": {"event": threading.Event(), "solver": None},
        }
    t = threading.Thread(target=_run_solver_in_background, args=(job_id, user_id, generation_prefs), daemon=True)
    t.start()
    return job_id


def get_job_status(job_id):
    """Returns the current status of a solver job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        return dict(job) if job else {"status": "not_found"}


def cancel_job(job_id, user_id=None):
    """Request cancellation for a running solver job."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") not in ("running", "canceling"):
            return False
        if user_id is not None and job.get("user_id") != user_id:
            return False
        job["status"] = "canceling"
        cancel_token = job.get("cancel_token")

    if cancel_token:
        cancel_token["event"].set()
        solver = cancel_token.get("solver")
        if solver is not None:
            solver.StopSearch()

    return True



def slot_key(day, period):
    return f"{day}-{period}"


def parse_slot_key(key):
    day, period = key.rsplit("-", 1)
    return day, int(period)


def get_periods_by_day(config):
    day_periods = config.get("day_periods", {})
    default_periods = config.get("periods_per_day", 6)
    return {
        day: day_periods.get(day, default_periods)
        for day in config.get("days", [])
    }


def build_timeslots(config):
    periods_by_day = get_periods_by_day(config)
    timeslots = []
    for day in config["days"]:
        for period in range(1, periods_by_day.get(day, 1) + 1):
            timeslots.append(TimeSlot(day, period))
    return timeslots


def build_teacher(teacher_data, all_timeslots):
    preferred = []
    unavailable = []
    availability = teacher_data.get("availability", {})

    for ts in all_timeslots:
        key = slot_key(ts.day, ts.period)
        status = availability.get(key, "available")
        if status == "preferred":
            preferred.append(ts)
        elif status == "unavailable":
            unavailable.append(ts)

    required_hours = (
        teacher_data.get("required_hours_per_week")
        or teacher_data.get("required_hours")
        or teacher_data.get("max_hours_per_week")
        or 30
    )

    return Teacher(
        id=teacher_data["id"],
        name=teacher_data["name"],
        subjects=teacher_data.get("subjects", []),
        allowed_classes=teacher_data.get("allowed_classes", []),
        preferred_slots=preferred,
        unavailable_slots=unavailable,
        required_hours=required_hours,
    )


def derive_allowed_teachers(class_name, subject, teachers_data):
    """Build allowed teacher IDs from teachers who teach this subject and class."""
    return [
        t["id"]
        for t in teachers_data
        if subject in t.get("subjects", [])
        and class_name in t.get("allowed_classes", [])
    ]


def build_school_class(class_data, teachers_data):
    allowed_teachers = {}
    all_subjects = set(class_data.get("required_hours", {}).keys())
    tp_pairs = class_data.get("tp_pairs", []) or []
    for tp in tp_pairs:
        if tp.get("subj1"):
            all_subjects.add(tp["subj1"])
        if tp.get("subj2"):
            all_subjects.add(tp["subj2"])

    for subject in all_subjects:
        allowed_teachers[subject] = derive_allowed_teachers(
            class_data["name"], subject, teachers_data
        )

    return SchoolClass(
        name=class_data["name"],
        required_hours=class_data.get("required_hours", {}),
        max_teachers=class_data.get("max_teachers", {}),
        allowed_teachers=allowed_teachers,
        tp_pairs=tp_pairs,
    )


SUBJECT_PREF_ALIASES = {
    "francais": ["francais", "French", "french"],
    "arabe": ["arabe", "Arabic", "arabic"],
}


def build_merged_slot_preferences(data, all_timeslots):
    """Merge alias keys (French/francais, Arabic/arabe) into one slot list per subject."""
    raw = data.get("slot_preferences", {})
    ts_by_key = {slot_key(ts.day, ts.period): ts for ts in all_timeslots}
    merged = {}

    for subject_item in data.get("subjects", []):
        subject = subject_item["name"] if isinstance(subject_item, dict) else subject_item
        alias_keys = SUBJECT_PREF_ALIASES.get(subject, [subject])
        slots = []
        seen = set()
        for alias in alias_keys:
            for key in raw.get(alias, []):
                ts = ts_by_key.get(key)
                if ts and ts not in seen:
                    seen.add(ts)
                    slots.append(ts)
        if slots:
            merged[subject] = slots

    return merged


def build_school_from_store(user_id):
    data = data_store.load_data(user_id)
    config = data["timeslots_config"]
    all_timeslots = build_timeslots(config)

    slot_preferences = build_merged_slot_preferences(data, all_timeslots)

    school = School(slot_preferences=slot_preferences)
    school.subjects = [Subject(s) for s in data.get("subjects", [])]
    school.timeslots = all_timeslots
    school.teachers = [
        build_teacher(t, all_timeslots) for t in data.get("teachers", [])
    ]
    teachers_data = data.get("teachers", [])
    school.classes = [
        build_school_class(c, teachers_data) for c in data.get("classes", [])
    ]

    return school


def validate_before_generate(user_id):
    errors = []
    data = data_store.load_data(user_id)

    if not data.get("subjects"):
        errors.append("Ajoutez au moins une matière avant de générer.")

    if not data.get("teachers"):
        errors.append("Ajoutez au moins un enseignant avant de générer.")

    if not data.get("classes"):
        errors.append("Ajoutez au moins une classe avant de générer.")

    config = data.get("timeslots_config", {})
    if not config.get("days"):
        errors.append("Sélectionnez au moins un jour d'école sur la page Créneaux Horaires.")
    if not config.get("periods_per_day", 0):
        errors.append("Définissez le nombre de périodes par jour sur la page Créneaux Horaires.")

    for cls in data.get("classes", []):
        all_subjects = set(cls.get("required_hours", {}).keys())
        tp_pairs = cls.get("tp_pairs", []) or []
        for tp in tp_pairs:
            if tp.get("subj1"): all_subjects.add(tp["subj1"])
            if tp.get("subj2"): all_subjects.add(tp["subj2"])

        for subject in all_subjects:
            normal_h = cls.get("required_hours", {}).get(subject, 0)
            tp_h = sum(tp.get("count", 0) * 2 for tp in tp_pairs if tp.get("subj1") == subject or tp.get("subj2") == subject)
            if normal_h + tp_h <= 0:
                errors.append(
                    f"Classe {cls['name']} : {subject} a besoin d'au moins 1 heure par semaine."
                )
            else:
                allowed = derive_allowed_teachers(
                    cls["name"], subject, data.get("teachers", [])
                )
                if not allowed:
                    errors.append(
                        f"Classe {cls['name']} : aucun enseignant n'est assigné pour enseigner "
                        f"{subject}. Vérifiez les paramètres de l'enseignant."
                    )

    total_periods = sum(get_periods_by_day(config).values())
    for cls in data.get("classes", []):
        normal_h = sum(cls.get("required_hours", {}).values())
        tp_pairs = cls.get("tp_pairs", []) or []
        tp_periods = sum(tp.get("count", 0) * 2 for tp in tp_pairs)
        required = normal_h + tp_periods
        if required > total_periods:
            errors.append(
                f"La classe {cls['name']} a besoin de {required} heures (incluant les TP) mais seulement "
                f"{total_periods} créneaux horaires sont disponibles."
            )

    if not errors:
        school = build_school_from_store(user_id)
        strict_errors = validate_strict_subject_preferences(school)
        if strict_errors:
            errors.append(
                "Certaines leçons n'ont pas de placement valide dans les créneaux de matières préférées "
                "(le français, l'arabe, etc. sont strictement limités aux créneaux préférés uniquement)."
            )
            errors.extend(strict_errors[:6])
            if len(strict_errors) > 6:
                errors.append(f"... et {len(strict_errors) - 6} de plus.")

    return errors


def _build_solver_failure_details(school, domains):
    total_lessons = len(school.sessions)
    total_slots = len(school.timeslots)
    teacher_capacity = sum(
        teacher.required_hours for teacher in school.teachers
    )

    details = [
        "Le solveur CP-SAT n'a pas pu créer un emploi du temps complet avec les paramètres actuels.",
        (
            f"Il y a {total_lessons} séances de cours à placer dans "
            f"{total_slots} créneaux horaires disponibles."
        ),
        (
            f"La capacité totale des enseignants est de {teacher_capacity} affectations de cours."
        ),
    ]

    if teacher_capacity != total_lessons:
        details.append(
            (
                "Cela suggère une inadéquation des capacités : la charge combinée "
                "des enseignants ne correspond pas au nombre de cours requis."
            )
        )

    empty_domains = [
        f"- {session} : aucune combinaison enseignant/créneau valide"
        for session in school.sessions
        if not domains.get(session)
    ]
    if empty_domains:
        details.append("Cours sans aucun placement possible :")
        details.extend(empty_domains)
    else:
        details.append(
            "Chaque cours a au moins un placement possible, donc l'échec est probablement dû à des contraintes conflictuelles."
        )

    constrained = sorted(
        (
            (len(domains.get(session, [])), session)
            for session in school.sessions
        ),
        key=lambda item: (item[0], str(item[1]))
    )[:6]
    if constrained:
        details.append("Cours les plus contraints :")
        for count, session in constrained:
            details.append(f"- {session} : {count} placements possibles")

    return details


def run_solver(user_id, time_limit_seconds=None, generation_prefs=None, cancel_token=None):
    import os
    if time_limit_seconds is None:
        time_limit_seconds = int(os.environ.get("SOLVER_TIME_LIMIT", 300))

    errors = validate_before_generate(user_id)
    if errors:
        return False, errors

    school = build_school_from_store(user_id)
    schedule, message = solve_with_cp_sat(
        school,
        time_limit_seconds,
        generation_prefs=generation_prefs or {},
        cancel_token=cancel_token,
    )

    if schedule is None:
        if cancel_token and cancel_token.get("event") and cancel_token["event"].is_set():
            return False, ["La génération a été annulée."]

        school.generate_sessions()
        domains = school.generate_domains()
        details = _build_solver_failure_details(school, domains)
        if message:
            details.insert(0, message)
        return False, details

    return True, serialize_schedule(school, user_id)


def _get_timeslot_by_key(timeslots, key):
    if not key:
        return None

    if " P" in key:
        try:
            day, period_str = key.rsplit(" P", 1)
            period = int(period_str)
        except Exception:
            return None
    else:
        try:
            day, period = parse_slot_key(key)
        except Exception:
            return None

    return next(
        (ts for ts in timeslots if ts.day == day and ts.period == period),
        None,
    )


def _find_teacher_by_id(teachers, teacher_id):
    return next((t for t in teachers if t.id == teacher_id), None)


def _find_teacher_by_name(teachers, name):
    return next((t for t in teachers if t.name == name), None)


def _find_class_by_name(classes, name):
    return next((c for c in classes if c.name == name), None)


def _build_schedule_from_serialized(school, serialized_schedule):
    entries = serialized_schedule.get("entries") or []
    if not entries:
        return None, "L'emploi du temps ne contient pas les métadonnées nécessaires. Veuillez en générer un nouveau."

    schedule = Schedule()
    assigned_sessions = set()

    for entry in entries:
        cls = _find_class_by_name(school.classes, entry.get("class"))
        teacher = _find_teacher_by_id(school.teachers, entry.get("teacher_id"))
        if teacher is None:
            teacher = _find_teacher_by_name(school.teachers, entry.get("teacher"))
        timeslot = _get_timeslot_by_key(school.timeslots, entry.get("time"))

        if cls is None or teacher is None or timeslot is None:
            return None, "Les métadonnées de l'emploi du temps sont invalides ou désynchronisées avec la configuration actuelle."

        candidates = [
            s for s in school.sessions
            if s.school_class == cls and s.subject == entry.get("subject")
        ]
        session = next((s for s in candidates if s not in assigned_sessions), None)
        if session is None:
            next_num = len(candidates) + 1
            session = Session(cls, entry.get("subject"), next_num)
            school.sessions.append(session)

        schedule.assign(session, Assignment(teacher, timeslot))
        assigned_sessions.add(session)

    return schedule, None


def try_swap_class_cells(user_id, serialized_schedule, class_name, time_a, time_b):
    if time_a == time_b:
        return False, "Veuillez sélectionner deux créneaux différents pour déplacer ou échanger."

    entries = serialized_schedule.get("entries") or []
    entry_a = next((e for e in entries if e.get("class") == class_name and e.get("time") == time_a), None)
    entry_b = next((e for e in entries if e.get("class") == class_name and e.get("time") == time_b), None)

    school = build_school_from_store(user_id)
    school.generate_sessions()
    schedule_obj, err = _build_schedule_from_serialized(school, serialized_schedule)
    if err:
        return False, err

    timeslot_a = _get_timeslot_by_key(school.timeslots, time_a)
    timeslot_b = _get_timeslot_by_key(school.timeslots, time_b)
    if timeslot_a is None or timeslot_b is None:
        return False, "Les créneaux sélectionnés sont invalides."

    session_a = next(
        (s for s, a in schedule_obj.assignments.items()
         if s.school_class.name == class_name and a.timeslot == timeslot_a),
        None,
    )
    session_b = next(
        (s for s, a in schedule_obj.assignments.items()
         if s.school_class.name == class_name and a.timeslot == timeslot_b),
        None,
    )

    if entry_a is None and entry_b is None:
        return False, "Au moins un créneau sélectionné doit contenir un cours planifié."

    assignment_a = schedule_obj.assignments.get(session_a) if session_a else None
    assignment_b = schedule_obj.assignments.get(session_b) if session_b else None

    if entry_a is not None and entry_b is not None:
        if session_a is None or session_b is None:
            return False, "Impossible d'identifier les séances sélectionnées dans l'emploi du temps."
        swap_session = session_a
        swap_session_b = session_b
    elif entry_a is not None and entry_b is None:
        if session_a is None:
            return False, "Impossible d'identifier le cours sélectionné dans l'emploi du temps."
        swap_session = session_a
        target_timeslot = timeslot_b
    else:
        if session_b is None:
            return False, "Impossible d'identifier le cours sélectionné dans l'emploi du temps."
        swap_session = session_b
        target_timeslot = timeslot_a

    new_schedule = Schedule()
    for session, assignment in schedule_obj.assignments.items():
        if entry_a is not None and entry_b is not None:
            if session == swap_session:
                new_schedule.assign(session, Assignment(assignment.teacher, assignment_b.timeslot))
            elif session == swap_session_b:
                new_schedule.assign(session, Assignment(assignment.teacher, assignment_a.timeslot))
            else:
                new_schedule.assign(session, assignment)
        elif entry_a is not None and entry_b is None:
            if session == swap_session:
                new_schedule.assign(session, Assignment(assignment.teacher, target_timeslot))
            else:
                new_schedule.assign(session, assignment)
        elif entry_a is None and entry_b is not None:
            if session == swap_session:
                new_schedule.assign(session, Assignment(assignment.teacher, target_timeslot))
            else:
                new_schedule.assign(session, assignment)

    for session, assignment in new_schedule.assignments.items():
        if not school.is_consistent_with(
            session,
            assignment.teacher,
            assignment.timeslot,
            new_schedule,
            ignore_subject_slot_allowed=True,
        ):
            return False, "Ce déplacement enfreindrait une contrainte de planification obligatoire."

    min_violations = school.validate_min_per_day_schedule(new_schedule)
    if min_violations:
        return False, min_violations[0]

    school.schedule = new_schedule
    return True, serialize_schedule(school, user_id)


def try_swap_teacher_cells(user_id, serialized_schedule, teacher_name, time_a, time_b):
    if time_a == time_b:
        return False, "Veuillez sélectionner deux créneaux différents pour échanger."

    entries = serialized_schedule.get("entries") or []
    entry_a = next((e for e in entries if e.get("teacher") == teacher_name and e.get("time") == time_a), None)
    entry_b = next((e for e in entries if e.get("teacher") == teacher_name and e.get("time") == time_b), None)

    if entry_a is None or entry_b is None:
        return False, "Les deux créneaux sélectionnés doivent contenir des cours de l'enseignant."

    school = build_school_from_store(user_id)
    school.generate_sessions()
    schedule_obj, err = _build_schedule_from_serialized(school, serialized_schedule)
    if err:
        return False, err

    timeslot_a = _get_timeslot_by_key(school.timeslots, time_a)
    timeslot_b = _get_timeslot_by_key(school.timeslots, time_b)
    if timeslot_a is None or timeslot_b is None:
        return False, "Les créneaux sélectionnés sont invalides."

    session_a = next(
        (s for s, a in schedule_obj.assignments.items()
         if a.teacher.name == teacher_name and a.timeslot == timeslot_a),
        None,
    )
    session_b = next(
        (s for s, a in schedule_obj.assignments.items()
         if a.teacher.name == teacher_name and a.timeslot == timeslot_b),
        None,
    )

    if session_a is None or session_b is None:
        return False, "Impossible d'identifier les séances de l'enseignant sélectionné."

    new_schedule = Schedule()
    for session, assignment in schedule_obj.assignments.items():
        if session == session_a:
            new_schedule.assign(session, Assignment(assignment.teacher, timeslot_b))
        elif session == session_b:
            new_schedule.assign(session, Assignment(assignment.teacher, timeslot_a))
        else:
            new_schedule.assign(session, assignment)

    for session, assignment in new_schedule.assignments.items():
        if not school.is_consistent_with(
            session,
            assignment.teacher,
            assignment.timeslot,
            new_schedule,
            ignore_subject_slot_allowed=True,
        ):
            return False, "Cet échange enfreindrait une contrainte de planification obligatoire."

    min_violations = school.validate_min_per_day_schedule(new_schedule)
    if min_violations:
        return False, min_violations[0]

    school.schedule = new_schedule
    return True, serialize_schedule(school, user_id)


def try_add_lesson(user_id, serialized_schedule, class_name, time_key, subject_name, teacher_name):
    school = build_school_from_store(user_id)
    school.generate_sessions()
    schedule_obj, err = _build_schedule_from_serialized(school, serialized_schedule)
    if err:
        return False, err

    timeslot = _get_timeslot_by_key(school.timeslots, time_key)
    if timeslot is None:
        return False, "Créneau horaire invalide."

    teacher = _find_teacher_by_name(school.teachers, teacher_name)
    if teacher is None:
        return False, f"Enseignant '{teacher_name}' introuvable."

    cls = _find_class_by_name(school.classes, class_name)
    if cls is None:
        return False, f"Classe '{class_name}' introuvable."

    class_busy = any(
        other_session.school_class == cls and assignment.timeslot == timeslot
        for other_session, assignment in schedule_obj.assignments.items()
    )
    if class_busy:
        return False, "La classe a déjà un cours sur ce créneau."

    teacher_busy = any(
        assignment.teacher == teacher and assignment.timeslot == timeslot
        for other_session, assignment in schedule_obj.assignments.items()
    )
    if teacher_busy:
        return False, f"L'enseignant {teacher_name} a déjà un cours sur ce créneau."

    if timeslot in teacher.unavailable_slots:
        return False, f"L'enseignant {teacher_name} est indisponible sur ce créneau."

    subject_count = sum(
        1 for other_session, assignment in schedule_obj.assignments.items()
        if other_session.school_class == cls
        and other_session.subject == subject_name
        and assignment.timeslot.day == timeslot.day
    )
    max_per_day = school.get_subject_max_per_day(subject_name)
    if subject_count >= max_per_day:
        return False, f"La limite quotidienne pour la matière {subject_name} ({max_per_day} cours/jour) est dépassée."

    candidates = [
        s for s in school.sessions
        if s.school_class == cls and s.subject == subject_name
    ]
    next_num = len(candidates) + 1
    new_session = Session(cls, subject_name, next_num)
    school.sessions.append(new_session)

    schedule_obj.assign(new_session, Assignment(teacher, timeslot))

    if not school.is_consistent_with(
        new_session,
        teacher,
        timeslot,
        schedule_obj,
        ignore_subject_slot_allowed=True,
    ):
        return False, "L'ajout enfreint une contrainte de planification."

    min_violations = school.validate_min_per_day_schedule(schedule_obj)
    if min_violations:
        return False, min_violations[0]

    school.schedule = schedule_obj
    return True, serialize_schedule(school, user_id)


def try_remove_lesson(user_id, serialized_schedule, class_name, time_key):
    school = build_school_from_store(user_id)

    entries = serialized_schedule.get("entries") or []
    new_entries = [
        e for e in entries
        if not (e.get("class") == class_name and e.get("time") == time_key)
    ]

    temp_serialized = serialized_schedule.copy()
    temp_serialized["entries"] = new_entries

    school.generate_sessions()
    schedule_obj, err = _build_schedule_from_serialized(school, temp_serialized)
    if err:
        return False, err

    min_violations = school.validate_min_per_day_schedule(schedule_obj)
    if min_violations:
        return False, min_violations[0]

    school.schedule = schedule_obj
    return True, serialize_schedule(school, user_id)


def serialize_schedule(school, user_id):
    """Convert schedule to JSON-serializable format for the UI."""
    from collections import defaultdict
    class_view_raw = defaultdict(lambda: defaultdict(list))
    teacher_view = {}
    entries = []

    for session, assignment in school.schedule.assignments.items():
        ts = assignment.timeslot
        time_label = f"{ts.day} P{ts.period}"
        cls_name = session.school_class.name
        teacher_name = assignment.teacher.name
        subject = session.subject

        class_view_raw[cls_name][time_label].append({
            "subject": subject,
            "is_tp": getattr(session, "is_tp", False),
        })

        display_subj = f"TP {subject}" if getattr(session, "is_tp", False) else subject
        teacher_view.setdefault(teacher_name, []).append({
            "time": time_label,
            "class": cls_name,
            "subject": display_subj,
        })

        entries.append({
            "class": cls_name,
            "subject": subject,
            "time": time_label,
            "teacher": teacher_name,
            "teacher_id": assignment.teacher.id,
            "is_tp": getattr(session, "is_tp", False),
        })

    class_view = {}
    for cls_name, timeslots_map in class_view_raw.items():
        class_view[cls_name] = {}
        for time_label, items in timeslots_map.items():
            if len(items) == 1:
                class_view[cls_name][time_label] = items[0]["subject"]
            else:
                subjs = sorted(set(it["subject"] for it in items))
                class_view[cls_name][time_label] = f"TP {' / '.join(subjs)}"

    for teacher_name in teacher_view:
        teacher_view[teacher_name].sort(key=lambda x: x["time"])

    timeslots = []
    config = data_store.get_timeslots_config(user_id)
    periods_by_day = get_periods_by_day(config)
    for day in config["days"]:
        for period in range(1, periods_by_day.get(day, 1) + 1):
            timeslots.append(f"{day} P{period}")

    return {
        "timeslots": timeslots,
        "class_view": class_view,
        "teacher_view": teacher_view,
        "entries": entries,
    }
