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
