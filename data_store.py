"""JSON-in-SQLite persistence for multi-tenant school scheduling configurations."""

import json
from pathlib import Path
from contextlib import contextmanager
from database.database import SessionLocal
from database.models import SchoolConfig, Schedule

DATA_DIR = Path(__file__).parent / "data"
DATA_FILE = DATA_DIR / "school_data.json"

DEFAULT_DATA = {
    "subjects": [],
    "teachers": [],
    "classes": [],
    "timeslots_config": {
        "days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
        "periods_per_day": 6,
    },
    "slot_preferences": {},
    "last_schedule": None,
}


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_or_create_config(db, user_id):
    config = db.query(SchoolConfig).filter_by(user_id=user_id).first()
    if not config:
        config = SchoolConfig(user_id=user_id)
        
        # Check if we should migrate from the legacy JSON file
        migrated = False
        if user_id == 1:  # Typically the first user / admin
            try:
                if DATA_FILE.exists():
                    with open(DATA_FILE, encoding="utf-8") as f:
                        legacy_data = json.load(f)
                    config.subjects = legacy_data.get("subjects", [])
                    config.teachers = legacy_data.get("teachers", [])
                    config.classes = legacy_data.get("classes", [])
                    config.timeslots_config = legacy_data.get("timeslots_config", {
                        "days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
                        "periods_per_day": 6,
                    })
                    config.slot_preferences = legacy_data.get("slot_preferences", {})
                    config.last_schedule = legacy_data.get("last_schedule")
                    migrated = True
            except Exception:
                pass
                
        if not migrated:
            config.subjects = []
            config.teachers = []
            config.classes = []
            config.timeslots_config = {
                "days": ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"],
                "periods_per_day": 6,
            }
            config.slot_preferences = {}
            config.last_schedule = None

        db.add(config)
        db.commit()
        db.refresh(config)
    return config


# ─── Per-request cache (Flask g) ──────────────────────────────────────────────

def _get_cached_config(user_id):
    """
    Load the full school config once per Flask request and cache it in g.
    Falls back to a direct DB call when outside a Flask request context
    (e.g. background solver thread).
    """
    cache_key = f"_cfg_{user_id}"
    try:
        from flask import g
        if not hasattr(g, cache_key):
            with get_db() as db:
                config = get_or_create_config(db, user_id)
                setattr(g, cache_key, {
                    "subjects":         list(config.subjects or []),
                    "teachers":         list(config.teachers or []),
                    "classes":          list(config.classes or []),
                    "timeslots_config": dict(config.timeslots_config or {}),
                    "slot_preferences": dict(config.slot_preferences or {}),
                    "last_schedule":    config.last_schedule,
                })
        return getattr(g, cache_key)
    except RuntimeError:
        # Outside Flask app context (background thread, CLI, tests)
        with get_db() as db:
            config = get_or_create_config(db, user_id)
            return {
                "subjects":         list(config.subjects or []),
                "teachers":         list(config.teachers or []),
                "classes":          list(config.classes or []),
                "timeslots_config": dict(config.timeslots_config or {}),
                "slot_preferences": dict(config.slot_preferences or {}),
                "last_schedule":    config.last_schedule,
            }


def _invalidate_cache(user_id):
    """Drop the cached config from g after a write so the next read is fresh."""
    try:
        from flask import g
        cache_key = f"_cfg_{user_id}"
        if hasattr(g, cache_key):
            delattr(g, cache_key)
    except RuntimeError:
        pass


def load_data(user_id):
    return _get_cached_config(user_id)



def save_data(data, user_id):
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.subjects = data.get("subjects", [])
        config.teachers = data.get("teachers", [])
        config.classes = data.get("classes", [])
        config.timeslots_config = data.get("timeslots_config", {})
        config.slot_preferences = data.get("slot_preferences", {})
        config.last_schedule = data.get("last_schedule")
        db.commit()


def normalize_subject_item(item):
    """Return a canonical subject dict: name, max_per_day, min_per_day."""
    if isinstance(item, str):
        return {"name": item.strip(), "max_per_day": 7, "min_per_day": 0}

    if not isinstance(item, dict):
        return None

    name = (item.get("name") or "").strip()
    try:
        max_per_day = int(item.get("max_per_day", 7))
    except (TypeError, ValueError):
        max_per_day = 7
    max_per_day = max(1, min(max_per_day, 7))

    try:
        min_per_day = int(item.get("min_per_day", 0))
    except (TypeError, ValueError):
        min_per_day = 0
    min_per_day = max(0, min(min_per_day, max_per_day))

    return {"name": name, "max_per_day": max_per_day, "min_per_day": min_per_day}


def get_subjects(user_id):
    raw_subjects = _get_cached_config(user_id)["subjects"]
    normalized = [normalize_subject_item(item) for item in raw_subjects]
    return [s for s in normalized if s is not None]


def get_subject_names(user_id):
    return [subject["name"] for subject in get_subjects(user_id)]


def set_subjects(subjects, user_id):
    normalized = [s for item in subjects if (s := normalize_subject_item(item)) and s["name"]]
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.subjects = normalized
        db.commit()
    _invalidate_cache(user_id)


def get_teachers(user_id):
    return _get_cached_config(user_id)["teachers"]


def set_teachers(teachers, user_id):
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.teachers = teachers
        db.commit()
    _invalidate_cache(user_id)


def get_classes(user_id):
    return _get_cached_config(user_id)["classes"]


def set_classes(classes, user_id):
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.classes = classes
        db.commit()
    _invalidate_cache(user_id)


def get_timeslots_config(user_id):
    return _get_cached_config(user_id)["timeslots_config"]


def set_timeslots_config(timeslots, user_id):
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.timeslots_config = timeslots
        db.commit()
    _invalidate_cache(user_id)


def get_slot_preferences(user_id):
    return _get_cached_config(user_id)["slot_preferences"]


def set_slot_preferences(preferences, user_id):
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.slot_preferences = preferences
        db.commit()
    _invalidate_cache(user_id)


def get_last_schedule(user_id):
    return _get_cached_config(user_id)["last_schedule"]


def set_last_schedule(schedule, user_id):
    with get_db() as db:
        config = get_or_create_config(db, user_id)
        config.last_schedule = schedule
        db.commit()
    _invalidate_cache(user_id)


def next_teacher_id(user_id):
    teachers = get_teachers(user_id)
    if not teachers:
        return 1
    return max(t["id"] for t in teachers) + 1


# ─── Saved Schedules Management ───────────────────────────────────────────────

def get_schedules(user_id):
    with get_db() as db:
        schedules = db.query(Schedule).filter_by(user_id=user_id).order_by(Schedule.created_at.desc()).all()
        return [
            {
                "id": s.id,
                "name": s.name,
                "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
                "schedule_data": s.schedule_data,
            }
            for s in schedules
        ]


def get_schedule_by_id(schedule_id, user_id):
    with get_db() as db:
        s = db.query(Schedule).filter_by(id=schedule_id, user_id=user_id).first()
        if s:
            return {
                "id": s.id,
                "name": s.name,
                "created_at": s.created_at.strftime("%Y-%m-%d %H:%M:%S") if s.created_at else "",
                "schedule_data": s.schedule_data,
            }
        return None


def save_schedule(user_id, name, schedule_data):
    with get_db() as db:
        new_sched = Schedule(
            user_id=user_id,
            name=name,
            schedule_data=schedule_data
        )
        db.add(new_sched)
        db.commit()
        db.refresh(new_sched)
        return new_sched.id


def update_schedule_data(schedule_id, user_id, schedule_data):
    with get_db() as db:
        s = db.query(Schedule).filter_by(id=schedule_id, user_id=user_id).first()
        if s:
            s.schedule_data = schedule_data
            db.commit()
            return True
        return False


def rename_schedule(schedule_id, user_id, new_name):
    with get_db() as db:
        s = db.query(Schedule).filter_by(id=schedule_id, user_id=user_id).first()
        if s:
            s.name = new_name
            db.commit()
            return True
        return False


def delete_schedule(schedule_id, user_id):
    with get_db() as db:
        s = db.query(Schedule).filter_by(id=schedule_id, user_id=user_id).first()
        if s:
            db.delete(s)
            db.commit()
            return True
        return False


# ─── Cascading Renames ───────────────────────────────────────────────────────

def _update_schedule_class_name(schedule_data, original_name, new_name):
    if not schedule_data or not isinstance(schedule_data, dict):
        return schedule_data

    # 1. Update assignments
    assignments = schedule_data.get("assignments") or []
    for assign in assignments:
        if isinstance(assign, dict):
            if assign.get("class_name") == original_name:
                assign["class_name"] = new_name
            if isinstance(assign.get("school_class"), dict) and assign["school_class"].get("name") == original_name:
                assign["school_class"]["name"] = new_name

    # 2. Update class_view
    class_view = schedule_data.get("class_view")
    if isinstance(class_view, dict) and original_name in class_view:
        class_view[new_name] = class_view.pop(original_name)

    # 3. Update teacher_view entries
    teacher_view = schedule_data.get("teacher_view")
    if isinstance(teacher_view, dict):
        for t_name, slots in teacher_view.items():
            if isinstance(slots, dict):
                for time_key, entry in slots.items():
                    if isinstance(entry, dict):
                        if entry.get("class") == original_name:
                            entry["class"] = new_name
                        if entry.get("class_name") == original_name:
                            entry["class_name"] = new_name

    return schedule_data


def _update_schedule_teacher_name(schedule_data, old_name, new_name):
    if not schedule_data or not isinstance(schedule_data, dict):
        return schedule_data

    # 1. Update assignments
    assignments = schedule_data.get("assignments") or []
    for assign in assignments:
        if isinstance(assign, dict):
            if assign.get("teacher_name") == old_name:
                assign["teacher_name"] = new_name
            if isinstance(assign.get("teacher"), dict) and assign["teacher"].get("name") == old_name:
                assign["teacher"]["name"] = new_name

    # 2. Update teacher_view keys
    teacher_view = schedule_data.get("teacher_view")
    if isinstance(teacher_view, dict) and old_name in teacher_view:
        teacher_view[new_name] = teacher_view.pop(old_name)

    # 3. Update class_view entries
    class_view = schedule_data.get("class_view")
    if isinstance(class_view, dict):
        for c_name, slots in class_view.items():
            if isinstance(slots, dict):
                for time_key, entry in slots.items():
                    if isinstance(entry, dict):
                        if entry.get("teacher") == old_name:
                            entry["teacher"] = new_name
                        if entry.get("teacher_name") == old_name:
                            entry["teacher_name"] = new_name

    return schedule_data


def cascade_rename_class(user_id, original_name, new_name):
    if not original_name or not new_name or original_name == new_name:
        return

    # 1. Update Teachers' allowed_classes & allowed_classes_by_subject
    teachers = get_teachers(user_id)
    teachers_modified = False
    for t in teachers:
        allowed = t.get("allowed_classes") or []
        if original_name in allowed:
            t["allowed_classes"] = [new_name if c == original_name else c for c in allowed]
            teachers_modified = True

        allowed_by_sub = t.get("allowed_classes_by_subject") or {}
        if isinstance(allowed_by_sub, dict):
            for sub, c_list in allowed_by_sub.items():
                if isinstance(c_list, list) and original_name in c_list:
                    allowed_by_sub[sub] = [new_name if c == original_name else c for c in c_list]
                    teachers_modified = True

    if teachers_modified:
        set_teachers(teachers, user_id)

    # 2. Update last_schedule
    last_sched = get_last_schedule(user_id)
    if last_sched:
        updated_last_sched = _update_schedule_class_name(last_sched, original_name, new_name)
        set_last_schedule(updated_last_sched, user_id)

    # 3. Update all saved Schedules in DB
    with get_db() as db:
        saved_schedules = db.query(Schedule).filter_by(user_id=user_id).all()
        for s in saved_schedules:
            if s.schedule_data:
                s.schedule_data = _update_schedule_class_name(dict(s.schedule_data), original_name, new_name)
        db.commit()
    _invalidate_cache(user_id)


def cascade_rename_teacher(user_id, old_name, new_name):
    if not old_name or not new_name or old_name == new_name:
        return

    # 1. Update Classes' tp_pairs and fixed_slots
    classes = get_classes(user_id)
    classes_modified = False
    for c in classes:
        tp_pairs = c.get("tp_pairs") or []
        for tp in tp_pairs:
            if tp.get("teacher1") == old_name:
                tp["teacher1"] = new_name
                classes_modified = True
            if tp.get("teacher2") == old_name:
                tp["teacher2"] = new_name
                classes_modified = True

        fixed_slots = c.get("fixed_slots") or []
        for fs in fixed_slots:
            if fs.get("teacher") == old_name:
                fs["teacher"] = new_name
                classes_modified = True

    if classes_modified:
        set_classes(classes, user_id)

    # 2. Update last_schedule
    last_sched = get_last_schedule(user_id)
    if last_sched:
        updated_last_sched = _update_schedule_teacher_name(last_sched, old_name, new_name)
        set_last_schedule(updated_last_sched, user_id)

    # 3. Update all saved Schedules in DB
    with get_db() as db:
        saved_schedules = db.query(Schedule).filter_by(user_id=user_id).all()
        for s in saved_schedules:
            if s.schedule_data:
                s.schedule_data = _update_schedule_teacher_name(dict(s.schedule_data), old_name, new_name)
        db.commit()
    _invalidate_cache(user_id)

