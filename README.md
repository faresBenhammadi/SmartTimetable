---
title: Emploi Du Temps
emoji: 📅
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

# Emploi du Temps Scolaire

Application de génération automatique d'emplois du temps scolaires.


📅 School Timetable Scheduler

A web-based school timetable generation system built with Flask and Google OR-Tools CP-SAT. The application automatically generates optimized weekly schedules while respecting academic and operational constraints.

✨ Features
🔐 User authentication and role-based access
👨‍🏫 Teacher management
👨‍🎓 Class management
📚 Subject management
🕒 Automatic timetable generation using constraint programming
⚙️ Teacher availability and preferred time slots
📅 Subject scheduling preferences
💾 Database-backed data storage
🏫 Multi-school (multi-tenant) support
📱 Responsive web interface
🛠️ Tech Stack
Backend: Python, Flask
Optimization Solver: Google OR-Tools (CP-SAT)
Database: SQLAlchemy
Authentication: Werkzeug Security
Deployment: Docker + Gunicorn
📂 Project Structure
.
├── app.py                  # Flask application
├── cp_sat_solver.py        # Timetable optimization engine
├── solver_adapter.py       # Solver interface
├── school.py               # Scheduling models
├── data_store.py           # Data access layer
├── database/               # Database models and configuration
├── templates/              # HTML templates
├── static/                 # CSS, JavaScript and assets
├── requirements.txt
└── Dockerfile

🧠 Scheduling Engine

The timetable generator is powered by Google OR-Tools CP-SAT, a state-of-the-art constraint programming solver.

The solver considers multiple constraints, including:

No teacher conflicts
No class conflicts
Required number of sessions per subject
Teacher preferred time slots
Subject preferred scheduling periods
Gap minimization
Class switching reduction
Balanced timetable generation

The optimization model uses weighted penalties to produce practical and efficient schedules.