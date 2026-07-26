---
title: Emploi Du Temps
emoji: 📅
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---
<div align="center">

# 📅 SmartTimetable

### Intelligent School Timetable Generator using Constraint Programming

An optimization-based web application that automatically generates conflict-free school timetables using **Google OR-Tools CP-SAT**.

Designed for educational institutions to replace hours—or even days—of manual scheduling with optimized schedules generated in minutes.

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Flask](https://img.shields.io/badge/Flask-Web_App-black)
![OR-Tools](https://img.shields.io/badge/Google-OR--Tools-green)
![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-ORM-red)
![Docker](https://img.shields.io/badge/Docker-Container-blue)
![License](https://img.shields.io/badge/License-MIT-yellow)

</div>

---

# 📖 Overview

Creating school timetables manually is one of the most difficult administrative tasks in education.

A valid timetable must satisfy dozens of constraints simultaneously:

- Teacher availability
- Weekly teaching hours
- Subject distribution
- Classroom conflicts
- Student conflicts
- Teacher preferences
- Gap minimization
- Schedule balancing

This project automates the entire scheduling process using **Constraint Programming**, producing optimized timetables while respecting both mandatory and preferred constraints.

---

# ✨ Features

## School Management

- Secure authentication
- Multi-school support
- Teacher management
- Class management
- Subject management
- Weekly timetable visualization
- Persistent database

---

## Scheduling

- Automatic timetable generation
- Conflict-free schedules
- Teacher availability management
- Teacher preferred periods
- Subject preferences
- Weekly hour constraints
- Multiple working days
- Configurable school periods

---

## Optimization

The scheduling engine optimizes:

✅ Teacher satisfaction

✅ Student satisfaction

✅ Timetable quality

by minimizing

- Teacher idle gaps
- Student idle gaps
- Classroom switching
- Unwanted teaching periods

while respecting every mandatory scheduling rule.

---

# 🧠 Optimization Engine

The heart of the application is Google's **CP-SAT Solver**, one of the world's most powerful Constraint Programming solvers.

Unlike simple scheduling algorithms, CP-SAT explores millions of possible schedules while proving constraint satisfaction.

The scheduling problem belongs to the family of **NP-hard combinatorial optimization problems**, making exhaustive search impractical for real-world schools.

Constraint Programming allows the system to efficiently search the solution space while optimizing timetable quality.

---

# ⚙ Constraints

## Hard Constraints

These constraints are **never violated**.

- A teacher cannot teach two classes simultaneously.
- A class cannot attend two lessons simultaneously.
- Every subject receives its required weekly hours.
- Teacher availability must always be respected.
- Lessons cannot be placed outside school hours.
- Only one lesson may occupy a classroom slot.

---

## Soft Constraints

These constraints improve timetable quality.

The solver attempts to:

- maximize teacher preferences
- maximize subject preferences
- reduce teacher gaps
- reduce class gaps
- reduce unnecessary classroom switches
- balance schedules throughout the week

Each soft constraint is associated with a configurable penalty weight.

---

# 🏗 Architecture

```
                    Browser
                        │
                        ▼
                  Flask Application
                        │
        ┌───────────────┴────────────────┐
        │                                │
 Authentication                  Web Interface
        │                                │
        └───────────────┬────────────────┘
                        ▼
                  Scheduling Engine
               (Google OR-Tools CP-SAT)
                        │
                        ▼
                 SQLAlchemy ORM
                        │
                        ▼
                    SQLite Database
```

---

# 🛠 Tech Stack

## Backend

- Python
- Flask
- Flask-Login
- SQLAlchemy
- Google OR-Tools CP-SAT

## Frontend

- HTML
- CSS
- JavaScript
- Bootstrap

## Database

- SQLite

## Deployment

- Docker
- Gunicorn
- Hugging Face Spaces

---

# 🚀 Workflow

```
Create School
      │
      ▼
Add Teachers
      │
      ▼
Add Classes
      │
      ▼
Add Subjects
      │
      ▼
Configure Constraints
      │
      ▼
Launch Optimization
      │
      ▼
CP-SAT Solver
      │
      ▼
Optimized Weekly Timetable
```

---

# 📊 Example Optimization Goals

The scheduler attempts to generate timetables that satisfy objectives such as

- reducing idle periods
- respecting teacher preferences
- distributing subjects evenly
- minimizing timetable fragmentation
- producing practical weekly schedules

---

# 📂 Project Structure

```
SmartTimetable/

│
├── app/
│   ├── models/
│   ├── routes/
│   ├── templates/
│   ├── static/
│   ├── scheduler/
│   ├── authentication/
│   └── database/
│
├── migrations/
├── instance/
├── Dockerfile
├── requirements.txt
├── run.py
└── README.md
```

*(Adapt this tree to your actual repository structure.)*

---

# 📈 Why Constraint Programming?

Traditional scheduling algorithms often rely on greedy heuristics.

This project instead uses Google's CP-SAT solver, allowing it to

- guarantee hard constraints,
- optimize multiple objectives simultaneously,
- scale to realistic school sizes,
- produce significantly better schedules.

---

# 💻 Installation

```bash
git clone https://github.com/yourusername/SmartTimetable.git

cd SmartTimetable

pip install -r requirements.txt

python run.py
```

---

# 📸 Screenshots

> Add screenshots here.

Suggested screenshots:

- Login page
- Dashboard
- Teacher management
- Subject management
- Availability editor
- Preference editor
- Generated timetable

---

# 🔮 Future Improvements

- PDF export
- Excel export
- Multiple optimization profiles
- Classroom assignment optimization
- AI-assisted schedule recommendations
- Public REST API
- Mobile-friendly interface
- Analytics dashboard

---

# 🎯 Educational Value

This project demonstrates practical experience with

- Constraint Programming
- Operations Research
- Optimization Algorithms
- Web Development
- Database Design
- Authentication
- Docker Deployment

---

# 👨‍💻 Author

**Mohammed Fares Benhammadi**

AI Engineering Student

Passionate about Artificial Intelligence, Optimization, Machine Learning and Software Engineering.

---

# ⭐ Support

If you found this project interesting, consider giving it a ⭐ on GitHub.


