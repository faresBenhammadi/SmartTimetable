from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Text, DateTime
from .database import Base
import json
from datetime import datetime


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True)
    password = Column(String)
    is_approved = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)


class SchoolConfig(Base):
    __tablename__ = "school_configs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)
    subjects_json = Column(Text, default="[]")
    teachers_json = Column(Text, default="[]")
    classes_json = Column(Text, default="[]")
    timeslots_config_json = Column(Text, default="{}")
    slot_preferences_json = Column(Text, default="{}")
    last_schedule_json = Column(Text, nullable=True)

    @property
    def subjects(self):
        return json.loads(self.subjects_json or "[]")

    @subjects.setter
    def subjects(self, value):
        self.subjects_json = json.dumps(value, ensure_ascii=False)

    @property
    def teachers(self):
        return json.loads(self.teachers_json or "[]")

    @teachers.setter
    def teachers(self, value):
        self.teachers_json = json.dumps(value, ensure_ascii=False)

    @property
    def classes(self):
        return json.loads(self.classes_json or "[]")

    @classes.setter
    def classes(self, value):
        self.classes_json = json.dumps(value, ensure_ascii=False)

    @property
    def timeslots_config(self):
        return json.loads(self.timeslots_config_json or "{}")

    @timeslots_config.setter
    def timeslots_config(self, value):
        self.timeslots_config_json = json.dumps(value, ensure_ascii=False)

    @property
    def slot_preferences(self):
        return json.loads(self.slot_preferences_json or "{}")

    @slot_preferences.setter
    def slot_preferences(self, value):
        self.slot_preferences_json = json.dumps(value, ensure_ascii=False)

    @property
    def last_schedule(self):
        return json.loads(self.last_schedule_json) if self.last_schedule_json else None

    @last_schedule.setter
    def last_schedule(self, value):
        self.last_schedule_json = json.dumps(value, ensure_ascii=False) if value is not None else None


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    schedule_data_json = Column(Text, default="{}")

    @property
    def schedule_data(self):
        return json.loads(self.schedule_data_json or "{}")

    @schedule_data.setter
    def schedule_data(self, value):
        self.schedule_data_json = json.dumps(value, ensure_ascii=False)