"""Web UI for the school timetable scheduler with multi-tenancy and database backend."""

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
import os
from functools import wraps
from datetime import datetime

from database.database import SessionLocal
from database.models import User, SchoolConfig, Schedule
from werkzeug.security import generate_password_hash, check_password_hash

import data_store
import solver_adapter

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required")


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("is_admin"):
            flash("Accès refusé. Vous devez être administrateur.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def require_login():
    if request.endpoint in ("login", "logout", "register", "static") or request.path.startswith("/static"):
        return
    if not session.get("authenticated"):
        return redirect(url_for("login"))
    
    # Verify user approval status on every request
    user_id = session.get("user_id")
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if not user or not user.is_approved:
            session.clear()
            flash("Votre compte n'est pas encore approuvé ou a été désactivé.", "error")
            return redirect(url_for("login"))
        session["is_admin"] = user.is_admin
        session["username"] = user.username
    finally:
        db.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password")
        
        if not username or not password:
            flash("Veuillez remplir tous les champs.", "error")
            return render_template("login.html")

        db = SessionLocal()
        try:
            user = db.query(User).filter_by(username=username).first()
            if user and check_password_hash(user.password, password):
                if not user.is_approved:
                    flash("Votre compte est en attente d'approbation par l'administrateur.", "info")
                else:
                    session["authenticated"] = True
                    session["user_id"] = user.id
                    session["username"] = user.username
                    session["is_admin"] = user.is_admin
                    return redirect(url_for("dashboard"))
            else:
                flash("Identifiants incorrects.", "error")
        finally:
            db.close()

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password")
        
        if not username or not password:
            flash("Veuillez remplir tous les champs.", "error")
            return render_template("register.html")

        db = SessionLocal()
        try:
            existing = db.query(User).filter_by(username=username).first()
            if existing:
                flash("Ce nom d'utilisateur est déjà pris.", "error")
            else:
                new_user = User(
                    username=username,
                    password=generate_password_hash(password),
                    is_approved=False,
                    is_admin=False
                )
                db.add(new_user)
                db.commit()
                flash("Compte créé avec succès ! En attente d'approbation par l'administrateur.", "success")
                return redirect(url_for("login"))
        except Exception as e:
            db.rollback()
            flash("Une erreur s'est produite lors de l'enregistrement.", "error")
        finally:
            db.close()

    return render_template("register.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


DAY_OPTIONS = [
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
]

AVAILABILITY_CYCLE = ["available", "preferred", "unavailable"]


@app.context_processor
def inject_globals():
    day_translation = {
        "Sunday": "Dimanche",
        "Monday": "Lundi",
        "Tuesday": "Mardi",
        "Wednesday": "Mercredi",
        "Thursday": "Jeudi",
        "Friday": "Vendredi",
        "Saturday": "Samedi"
    }
    day_translation_short = {
        "Sunday": "Dim",
        "Monday": "Lun",
        "Tuesday": "Mar",
        "Wednesday": "Mer",
        "Thursday": "Jeu",
        "Friday": "Ven",
        "Saturday": "Sam"
    }
    
    nav_items = [
        ("dashboard", "Accueil", "home"),
        ("teachers", "Enseignants", "users"),
        ("classes", "Classes", "book-open"),
        ("subjects", "Matières", "layers"),
        ("timeslots", "Créneaux", "clock"),
        ("preferences", "Préférences", "star"),
        ("generate", "Générer", "calendar"),
        ("history", "Historique", "calendar"),
    ]
    
    if session.get("is_admin"):
        nav_items.append(("admin_dashboard", "Administration", "users"))
        
    return {
        "nav_items": nav_items,
        "translate_day": lambda d: day_translation.get(d, d),
        "translate_day_short": lambda d: day_translation_short.get(d, d[:3])
    }


@app.route("/")
def dashboard():
    user_id = session.get("user_id")
    data = data_store.load_data(user_id)
    config = data_store.get_timeslots_config(user_id)
    stats = {
        "teachers": len(data.get("teachers", [])),
        "classes": len(data.get("classes", [])),
        "subjects": len(data.get("subjects", [])),
        "timeslots": sum(solver_adapter.get_periods_by_day(config).values()),
    }
    has_schedule = data_store.get_last_schedule(user_id) is not None
    return render_template("dashboard.html", stats=stats, has_schedule=has_schedule)


# ─── Subjects ───────────────────────────────────────────────────────────────

@app.route("/subjects", methods=["GET", "POST"])
def subjects():
    user_id = session.get("user_id")
    if request.method == "POST":
        action = request.form.get("action")
        subjects_list = data_store.get_subjects(user_id)

        if action in ("add", "edit"):
            name = request.form.get("name", "").strip()
            original_name = request.form.get("original_name", "").strip()
            max_per_day = request.form.get("max_per_day", "7")
            min_per_day = request.form.get("min_per_day", "0")
            try:
                max_per_day = int(max_per_day)
            except (TypeError, ValueError):
                max_per_day = 7
            max_per_day = max(1, min(max_per_day, 7))
            try:
                min_per_day = int(min_per_day)
            except (TypeError, ValueError):
                min_per_day = 0
            min_per_day = max(0, min(min_per_day, max_per_day))

            if not name:
                flash("Veuillez entrer un nom de matière.", "error")
            else:
                duplicate = any(
                    (isinstance(s, str) and s == name) or
                    (isinstance(s, dict) and s.get("name") == name)
                    for s in subjects_list
                )
                if action == "add" and duplicate:
                    flash(f'"{name}" existe déjà.', "error")
                elif action == "edit" and duplicate and name != original_name:
                    flash(f'"{name}" existe déjà.', "error")
                else:
                    updated_subjects = []
                    subject_payload = {
                        "name": name,
                        "max_per_day": max_per_day,
                        "min_per_day": min_per_day,
                    }
                    if action == "edit":
                        for subject in subjects_list:
                            subject_name = subject if isinstance(subject, str) else subject.get("name")
                            if subject_name == original_name:
                                updated_subjects.append(subject_payload)
                            else:
                                if isinstance(subject, str):
                                    updated_subjects.append({
                                        "name": subject,
                                        "max_per_day": 7,
                                        "min_per_day": 0,
                                    })
                                else:
                                    updated_subjects.append(subject)
                        subjects_list = updated_subjects
                        flash(f'Matière "{original_name}" mise à jour en "{name}".', "success")
                    else:
                        subjects_list.append(subject_payload)
                        flash(f'Matière "{name}" ajoutée.', "success")

                    data_store.set_subjects(subjects_list, user_id)

        elif action == "delete":
            name = request.form.get("name", "").strip()
            subjects_list = [
                s for s in subjects_list
                if not ((isinstance(s, str) and s == name) or
                        (isinstance(s, dict) and s.get("name") == name))
            ]
            data_store.set_subjects(subjects_list, user_id)
            flash(f'Matière "{name}" supprimée.', "success")

        return redirect(url_for("subjects"))

    return render_template(
        "subjects.html",
        subjects=data_store.get_subjects(user_id),
    )


# ─── Teachers ─────────────────────────────────────────────────────────────────

@app.route("/teachers")
def teachers():
    user_id = session.get("user_id")
    config = data_store.get_timeslots_config(user_id)
    return render_template(
        "teachers.html",
        teachers=data_store.get_teachers(user_id),
        subjects=data_store.get_subject_names(user_id),
        classes=[c["name"] for c in data_store.get_classes(user_id)],
        days=config["days"],
        periods_by_day=solver_adapter.get_periods_by_day(config),
    )


@app.route("/teachers/save", methods=["POST"])
def save_teacher():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    teacher_id = payload.get("id")
    teachers_list = data_store.get_teachers(user_id)

    required_hours = int(
        payload.get("required_hours_per_week")
        or payload.get("required_hours")
        or payload.get("max_hours_per_week")
        or 30
    )
    teacher_data = {
        "id": teacher_id or data_store.next_teacher_id(user_id),
        "name": (payload.get("name") or "").strip(),
        "subjects": payload.get("subjects", []),
        "allowed_classes": payload.get("allowed_classes", []),
        "required_hours_per_week": required_hours,
        "required_hours": required_hours,
        "availability": payload.get("availability", {}),
    }

    if not teacher_data["name"]:
        return jsonify({"ok": False, "error": "Le nom de l'enseignant est requis."}), 400

    if teacher_id:
        teachers_list = [t if t["id"] != teacher_id else teacher_data for t in teachers_list]
    else:
        teachers_list.append(teacher_data)

    data_store.set_teachers(teachers_list, user_id)
    return jsonify({"ok": True, "teacher": teacher_data})


@app.route("/teachers/<int:teacher_id>/delete", methods=["POST"])
def delete_teacher(teacher_id):
    user_id = session.get("user_id")
    teachers_list = [t for t in data_store.get_teachers(user_id) if t["id"] != teacher_id]
    data_store.set_teachers(teachers_list, user_id)
    flash("Enseignant supprimé.", "success")
    return redirect(url_for("teachers"))


# ─── Classes ──────────────────────────────────────────────────────────────────

@app.route("/classes")
def classes():
    user_id = session.get("user_id")
    return render_template(
        "classes.html",
        classes=data_store.get_classes(user_id),
        subjects=data_store.get_subject_names(user_id),
    )


@app.route("/classes/save", methods=["POST"])
def save_class():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    original_name = payload.get("original_name")
    class_data = {
        "name": (payload.get("name") or "").strip(),
        "required_hours": payload.get("required_hours", {}),
        "max_teachers": payload.get("max_teachers", {}),
    }

    if not class_data["name"]:
        return jsonify({"ok": False, "error": "Le nom de la classe est requis."}), 400

    classes_list = data_store.get_classes(user_id)
    if original_name:
        classes_list = [
            class_data if c["name"] == original_name else c for c in classes_list
        ]
    else:
        if any(c["name"] == class_data["name"] for c in classes_list):
            return jsonify({"ok": False, "error": "Une classe avec ce nom existe déjà."}), 400
        classes_list.append(class_data)

    data_store.set_classes(classes_list, user_id)
    return jsonify({"ok": True, "class": class_data})


@app.route("/classes/<name>/delete", methods=["POST"])
def delete_class(name):
    user_id = session.get("user_id")
    classes_list = [c for c in data_store.get_classes(user_id) if c["name"] != name]
    data_store.set_classes(classes_list, user_id)
    flash(f'Classe "{name}" supprimée.', "success")
    return redirect(url_for("classes"))


# ─── Time Slots ───────────────────────────────────────────────────────────────

@app.route("/timeslots", methods=["GET", "POST"])
def timeslots():
    user_id = session.get("user_id")
    if request.method == "POST":
        days = request.form.getlist("days")
        if not days:
            flash("Sélectionnez au moins un jour de classe.", "error")
            return redirect(url_for("timeslots"))

        day_periods = {}
        for day in days:
            raw_value = request.form.get(f"day_periods[{day}]")
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                value = None
            if value is None or value < 1 or value > 12:
                flash(
                    f"Chaque jour sélectionné doit avoir entre 1 et 12 périodes. ({day})",
                    "error",
                )
                return redirect(url_for("timeslots"))
            day_periods[day] = value

        data_store.set_timeslots_config(
            {
                "days": days,
                "periods_per_day": max(day_periods.values()),
                "day_periods": day_periods,
            },
            user_id
        )
        flash("Créneaux horaires mis à jour.", "success")
        return redirect(url_for("timeslots"))

    config = data_store.get_timeslots_config(user_id)
    periods_by_day = solver_adapter.get_periods_by_day(config)
    return render_template(
        "timeslots.html",
        config=config,
        day_options=DAY_OPTIONS,
        total_slots=sum(periods_by_day.values()),
        max_periods=max(periods_by_day.values(), default=0),
    )


# ─── Subject Preferences ──────────────────────────────────────────────────────

@app.route("/preferences")
def preferences():
    user_id = session.get("user_id")
    config = data_store.get_timeslots_config(user_id)
    return render_template(
        "preferences.html",
        subjects=data_store.get_subject_names(user_id),
        preferences=data_store.get_slot_preferences(user_id),
        days=config["days"],
        periods_by_day=solver_adapter.get_periods_by_day(config),
    )


@app.route("/preferences/save", methods=["POST"])
def save_preferences():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    data_store.set_slot_preferences(payload.get("preferences", {}), user_id)
    return jsonify({"ok": True})


# ─── Generate ─────────────────────────────────────────────────────────────────

@app.route("/generate")
def generate():
    user_id = session.get("user_id")
    errors = solver_adapter.validate_before_generate(user_id)
    has_schedule = data_store.get_last_schedule(user_id) is not None
    ts_config = data_store.get_timeslots_config(user_id)
    periods_per_day = ts_config.get("periods_per_day", 6)
    return render_template("generate.html", errors=errors, has_schedule=has_schedule,
                           periods_per_day=periods_per_day)



@app.route("/generate/run", methods=["POST"])
def generate_run():
    user_id = session.get("user_id")
    # Validate before starting the background job
    errors = solver_adapter.validate_before_generate(user_id)
    if errors:
        return jsonify({"ok": False, "errors": errors}), 400

    # Extract generation preferences from the request
    body = request.get_json(silent=True) or {}
    generation_prefs = {
        "max_entry_period": body.get("max_entry_period"),   # int or None
        "min_exit_period":  body.get("min_exit_period"),    # int or None
        "allow_student_gaps": body.get("allow_student_gaps", True),  # bool
    }
    # Strip None values so solver defaults apply cleanly
    generation_prefs = {k: v for k, v in generation_prefs.items() if v is not None}

    # Start solver in background thread — returns immediately
    job_id = solver_adapter.start_solver_job(user_id, generation_prefs=generation_prefs)
    return jsonify({"ok": True, "job_id": job_id})



@app.route("/generate/cancel/<job_id>", methods=["POST"])
def generate_cancel(job_id):
    user_id = session.get("user_id")
    if solver_adapter.cancel_job(job_id, user_id=user_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "Impossible d'annuler ce travail."}), 400


@app.route("/generate/status/<job_id>")
def generate_status(job_id):
    """Polling endpoint — frontend calls this every 3s to check if solver is done."""
    status = solver_adapter.get_job_status(job_id)
    if status["status"] == "done":
        return jsonify({"ok": True, "status": "done", "redirect": url_for("results", id=status["schedule_id"])})
    elif status["status"] == "error":
        return jsonify({"ok": False, "status": "error", "errors": status.get("errors", [])})
    elif status["status"] == "canceling":
        return jsonify({"ok": True, "status": "canceling"})
    elif status["status"] == "canceled":
        return jsonify({"ok": False, "status": "canceled", "errors": ["La génération a été annulée."]})
    elif status["status"] == "running":
        return jsonify({"ok": True, "status": "running"})
    else:
        return jsonify({"ok": False, "status": "not_found"}), 404



# ─── Results & Editor ─────────────────────────────────────────────────────────

@app.route("/results")
def results():
    user_id = session.get("user_id")
    schedule_id = request.args.get("id")
    
    schedule = None
    schedule_name = "Dernier emploi du temps généré"
    
    if schedule_id:
        try:
            schedule_id = int(schedule_id)
            saved_schedule = data_store.get_schedule_by_id(schedule_id, user_id)
            if saved_schedule:
                schedule = saved_schedule["schedule_data"]
                schedule_name = saved_schedule["name"]
        except (TypeError, ValueError):
            pass
            
    if not schedule:
        schedule = data_store.get_last_schedule(user_id)
        schedule_id = None
        
    if not schedule:
        flash("Aucun emploi du temps n'a encore été généré.", "error")
        return redirect(url_for("generate"))

    config = data_store.get_timeslots_config(user_id)
    periods_by_day = solver_adapter.get_periods_by_day(config)
    max_periods = max(periods_by_day.values(), default=0)

    teacher_slot_map = {}
    for teacher_name, entries in schedule.get("teacher_view", {}).items():
        teacher_slot_map[teacher_name] = {
            entry["time"]: entry for entry in entries
        }

    subject_palette = [
        "#fde68a", "#a5b4fc", "#6ee7b7", "#fca5a5", "#93c5fd",
        "#fb7185", "#f59e0b", "#34d399", "#c084fc", "#fbbf24",
        "#60a5fa", "#f472b6", "#f97316", "#22c55e", "#38bdf8",
    ]
    subject_colors = {}
    for idx, subject in enumerate(data_store.get_subject_names(user_id)):
        subject_colors[subject] = subject_palette[idx % len(subject_palette)]

    return render_template(
        "results.html",
        schedule=schedule,
        schedule_id=schedule_id,
        schedule_name=schedule_name,
        config=config,
        periods_by_day=periods_by_day,
        max_periods=max_periods,
        teacher_slot_map=teacher_slot_map,
        subject_colors=subject_colors,
        teachers=data_store.get_teachers(user_id),
        subjects=data_store.get_subject_names(user_id),
    )


@app.route("/results/swap", methods=["POST"])
def results_swap():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    class_name = payload.get("class")
    time_a = payload.get("time_a")
    time_b = payload.get("time_b")
    schedule_id = payload.get("schedule_id")

    schedule = None
    if schedule_id:
        try:
            saved = data_store.get_schedule_by_id(int(schedule_id), user_id)
            if saved:
                schedule = saved["schedule_data"]
        except (TypeError, ValueError):
            pass
            
    if not schedule:
        schedule = data_store.get_last_schedule(user_id)

    if not schedule:
        return jsonify({"ok": False, "error": "Aucun emploi du temps disponible à modifier."}), 400
    if not class_name or not time_a or not time_b:
        return jsonify({"ok": False, "error": "Paramètres de demande d'échange manquants."}), 400

    success, result = solver_adapter.try_swap_class_cells(user_id, schedule, class_name, time_a, time_b)
    if not success:
        return jsonify({"ok": False, "error": result}), 400

    if schedule_id:
        data_store.update_schedule_data(int(schedule_id), user_id, result)
    else:
        data_store.set_last_schedule(result, user_id)

    return jsonify({"ok": True, "schedule": result})


@app.route("/results/swap-teacher", methods=["POST"])
def results_swap_teacher():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    teacher_name = payload.get("teacher")
    time_a = payload.get("time_a")
    time_b = payload.get("time_b")
    schedule_id = payload.get("schedule_id")

    schedule = None
    if schedule_id:
        try:
            saved = data_store.get_schedule_by_id(int(schedule_id), user_id)
            if saved:
                schedule = saved["schedule_data"]
        except (TypeError, ValueError):
            pass

    if not schedule:
        schedule = data_store.get_last_schedule(user_id)

    if not schedule:
        return jsonify({"ok": False, "error": "Aucun emploi du temps disponible à modifier."}), 400
    if not teacher_name or not time_a or not time_b:
        return jsonify({"ok": False, "error": "Paramètres de demande d'échange manquants."}), 400

    success, result = solver_adapter.try_swap_teacher_cells(user_id, schedule, teacher_name, time_a, time_b)
    if not success:
        return jsonify({"ok": False, "error": result}), 400

    if schedule_id:
        data_store.update_schedule_data(int(schedule_id), user_id, result)
    else:
        data_store.set_last_schedule(result, user_id)

    return jsonify({"ok": True, "schedule": result})


@app.route("/results/add-lesson", methods=["POST"])
def results_add_lesson():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    class_name = payload.get("class")
    time_key = payload.get("time")
    subject = payload.get("subject")
    teacher_name = payload.get("teacher")
    schedule_id = payload.get("schedule_id")

    schedule = None
    if schedule_id:
        try:
            saved = data_store.get_schedule_by_id(int(schedule_id), user_id)
            if saved:
                schedule = saved["schedule_data"]
        except (TypeError, ValueError):
            pass

    if not schedule:
        schedule = data_store.get_last_schedule(user_id)

    if not schedule:
        return jsonify({"ok": False, "error": "Aucun emploi du temps disponible."}), 400
    if not class_name or not time_key or not subject or not teacher_name:
        return jsonify({"ok": False, "error": "Paramètres de demande manquants."}), 400

    success, result = solver_adapter.try_add_lesson(user_id, schedule, class_name, time_key, subject, teacher_name)
    if not success:
        return jsonify({"ok": False, "error": result}), 400

    if schedule_id:
        data_store.update_schedule_data(int(schedule_id), user_id, result)
    else:
        data_store.set_last_schedule(result, user_id)

    return jsonify({"ok": True, "schedule": result})


@app.route("/results/remove-lesson", methods=["POST"])
def results_remove_lesson():
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    class_name = payload.get("class")
    time_key = payload.get("time")
    schedule_id = payload.get("schedule_id")

    schedule = None
    if schedule_id:
        try:
            saved = data_store.get_schedule_by_id(int(schedule_id), user_id)
            if saved:
                schedule = saved["schedule_data"]
        except (TypeError, ValueError):
            pass

    if not schedule:
        schedule = data_store.get_last_schedule(user_id)

    if not schedule:
        return jsonify({"ok": False, "error": "Aucun emploi du temps disponible."}), 400
    if not class_name or not time_key:
        return jsonify({"ok": False, "error": "Paramètres de demande manquants."}), 400

    success, result = solver_adapter.try_remove_lesson(user_id, schedule, class_name, time_key)
    if not success:
        return jsonify({"ok": False, "error": result}), 400

    if schedule_id:
        data_store.update_schedule_data(int(schedule_id), user_id, result)
    else:
        data_store.set_last_schedule(result, user_id)

    return jsonify({"ok": True, "schedule": result})


# ─── History & History Actions ───────────────────────────────────────────────

@app.route("/history")
def history():
    user_id = session.get("user_id")
    schedules = data_store.get_schedules(user_id)
    return render_template("history.html", schedules=schedules)


@app.route("/history/rename/<int:schedule_id>", methods=["POST"])
def rename_schedule(schedule_id):
    user_id = session.get("user_id")
    new_name = request.form.get("name", "").strip()
    if not new_name:
        flash("Le nom ne peut pas être vide.", "error")
    else:
        success = data_store.rename_schedule(schedule_id, user_id, new_name)
        if success:
            flash("Emploi du temps renommé.", "success")
        else:
            flash("Erreur lors du renommage.", "error")
    return redirect(url_for("history"))


@app.route("/history/delete/<int:schedule_id>", methods=["POST"])
def delete_schedule(schedule_id):
    user_id = session.get("user_id")
    success = data_store.delete_schedule(schedule_id, user_id)
    if success:
        flash("Emploi du temps supprimé de l'historique.", "success")
    else:
        flash("Erreur lors de la suppression.", "error")
    return redirect(url_for("history"))


# ─── Admin Dashboard ──────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.username != "admin").all()
        return render_template("admin.html", users=users)
    finally:
        db.close()


@app.route("/admin/approve/<int:user_id>", methods=["POST"])
@admin_required
def approve_user(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if user:
            user.is_approved = True
            db.commit()
            flash(f"L'utilisateur '{user.username}' a été approuvé.", "success")
        else:
            flash("Utilisateur introuvable.", "error")
    except Exception as e:
        db.rollback()
        flash("Une erreur s'est produite.", "error")
    finally:
        db.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/deactivate/<int:user_id>", methods=["POST"])
@admin_required
def deactivate_user(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if user:
            if user.username == "admin":
                flash("Impossible de désactiver l'administrateur principal.", "error")
            else:
                user.is_approved = False
                db.commit()
                flash(f"L'utilisateur '{user.username}' a été désactivé.", "success")
        else:
            flash("Utilisateur introuvable.", "error")
    except Exception as e:
        db.rollback()
        flash("Une erreur s'est produite.", "error")
    finally:
        db.close()
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
@admin_required
def delete_user(user_id):
    db = SessionLocal()
    try:
        user = db.query(User).filter_by(id=user_id).first()
        if user:
            if user.username == "admin":
                flash("Impossible de supprimer l'administrateur principal.", "error")
            else:
                # Delete user-related configs and saved schedules
                config = db.query(SchoolConfig).filter_by(user_id=user_id).first()
                if config:
                    db.delete(config)
                schedules = db.query(Schedule).filter_by(user_id=user_id).all()
                for s in schedules:
                    db.delete(s)
                db.delete(user)
                db.commit()
                flash(f"L'utilisateur '{user.username}' et toutes ses données ont été supprimés.", "success")
        else:
            flash("Utilisateur introuvable.", "error")
    except Exception as e:
        db.rollback()
        flash("Une erreur s'est produite lors de la suppression.", "error")
    finally:
        db.close()
    return redirect(url_for("admin_dashboard"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
