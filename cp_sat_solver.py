"""OR-Tools CP-SAT solver for school timetabling."""

import os
from collections import defaultdict
import time
t0 = time.perf_counter()
from ortools.sat.python import cp_model

from school import Assignment, Schedule
WORKING_DAY_WEIGHT = 50
TEACHER_PREFERENCE_WEIGHT = 10
BLOCK_PENALTY_WEIGHT = 250
SUBJECT_PREFERENCE_WEIGHT = 200
CLASS_SWITCH_WEIGHT = 300
TEACHER_GAP_WEIGHT = 300
CLASS_GAP_WEIGHT = 150


class _ModelContext:
    """Pre-built indexes and shared auxiliary variables for one CP-SAT model."""

    def __init__(self, model, school, sessions, assign):
        self.model = model
        self.school = school
        self.sessions = sessions
        self.assign = assign
        self.days = sorted({ts.day for ts in school.timeslots})
        self.all_periods = sorted({ts.period for ts in school.timeslots})
        self._var_id = 0

        self.by_teacher_slot = defaultdict(list)
        self.by_class_slot = defaultdict(list)
        self.by_class_subject = defaultdict(list)
        self.by_teacher_day_period = defaultdict(list)
        self.by_class_day_period = defaultdict(list)
        self.by_class_subject_day_period = defaultdict(list)
        self.by_teacher = defaultdict(list)
        self.by_class = defaultdict(list)

        self._teacher_busy = {}
        self._class_busy = {}

        for session in sessions:
            school_class = session.school_class
            for teacher, ts, var, _ in assign[session]:
                self.by_teacher_slot[(teacher, ts)].append(var)
                self.by_class_slot[(school_class, ts)].append(var)
                self.by_class_subject[(school_class, session.subject)].append(
                    (session, ts, var)
                )
                self.by_teacher_day_period[(teacher, ts.day, ts.period)].append(var)
                self.by_class_day_period[(school_class, ts.day, ts.period)].append(var)
                self.by_class_subject_day_period[
                    (school_class, session.subject, ts.day, ts.period)
                ].append(var)
                self.by_teacher[teacher].append(var)
                self.by_class[school_class].append(var)

    def new_bool(self):
        self._var_id += 1
        return self.model.NewBoolVar(f"v{self._var_id}")

    def _reify_has_any(self, literals):
        """Return a BoolVar that is true iff at least one literal in the list is true."""
        if not literals:
            return None
        has = self.new_bool()
        self.model.Add(sum(literals) >= 1).OnlyEnforceIf(has)
        self.model.Add(sum(literals) == 0).OnlyEnforceIf(has.Not())
        return has

    def teacher_busy(self, teacher, day, period):
        key = (teacher, day, period)
        if key in self._teacher_busy:
            return self._teacher_busy[key]
        vars_at = self.by_teacher_day_period.get(key)
        if not vars_at:
            return None
        busy = self._reify_has_any(vars_at)
        self._teacher_busy[key] = busy
        return busy

    def class_busy(self, school_class, day, period):
        key = (school_class, day, period)
        if key in self._class_busy:
            return self._class_busy[key]
        vars_at = self.by_class_day_period.get(key)
        if not vars_at:
            return None
        busy = self._reify_has_any(vars_at)
        self._class_busy[key] = busy
        return busy


def _subjects_with_preferences(school):
    return [
        subject
        for subject in school.slot_preferences
        if school.get_subject_preferred_slots(subject)
    ]


def _build_assignment_vars(model, school, sessions, domains):
    """Create one BoolVar per valid (session, teacher, timeslot) triple."""
    assign = {}
    var_id = 0
    for session in sessions:
        entries = []
        for teacher, timeslot in domains.get(session, []):
            var_id += 1
            var = model.NewBoolVar(f"a{var_id}")
            teacher_penalty = (
                TEACHER_PREFERENCE_WEIGHT
                if teacher.preferred_slots and timeslot not in teacher.preferred_slots
                else 0
            )
            entries.append((teacher, timeslot, var, teacher_penalty))
        assign[session] = entries
    return assign


def _add_session_constraints(model, assign):
    for session, entries in assign.items():
        if not entries:
            return False
        model.Add(sum(var for _, _, var, _ in entries) == 1)
    return True


def _add_teacher_slot_constraints(model, ctx):
    for (teacher, timeslot), vars_at in ctx.by_teacher_slot.items():
        model.Add(sum(vars_at) <= 1)


def _add_class_slot_constraints(model, ctx):
    for (school_class, timeslot), vars_at in ctx.by_class_slot.items():
        model.Add(sum(vars_at) <= 1)


def _add_teacher_hour_constraints(model, school, ctx):
    for teacher in school.teachers:
        vars_t = ctx.by_teacher.get(teacher, [])
        if vars_t:
            model.Add(sum(vars_t) == teacher.required_hours)


def _add_subject_day_cap_constraints(model, school, ctx):
    for school_class in school.classes:
        for subject in school_class.required_hours:
            max_per_day = school.get_subject_max_per_day(subject)
            for day in ctx.days:
                vars_day = []
                for (cls, subj, d, _period), vars_at in ctx.by_class_subject_day_period.items():
                    if cls == school_class and subj == subject and d == day:
                        vars_day.extend(vars_at)
                if vars_day:
                    model.Add(sum(vars_day) <= max_per_day)


def _add_subject_min_per_day_constraints(model, ctx):
    """
    Hard rule: when min_per_day > 0, each (class, subject, day) has either
    0 sessions or at least min_per_day consecutive sessions.
    """
    if not ctx.all_periods:
        return

    min_period = ctx.all_periods[0]
    max_period = ctx.all_periods[-1]

    for school_class in ctx.school.classes:
        for subject in school_class.required_hours:
            min_per_day = ctx.school.get_subject_min_per_day(subject)
            if min_per_day <= 0:
                continue

            for day in ctx.days:
                is_used = {}
                for period in ctx.all_periods:
                    vars_at = ctx.by_class_subject_day_period.get(
                        (school_class, subject, day, period)
                    )
                    if not vars_at:
                        continue
                    used = is_used.get(period)
                    if used is None:
                        used = ctx.new_bool()
                        is_used[period] = used
                    for var in vars_at:
                        model.Add(var <= used)

                if not is_used:
                    continue

                for period, used in is_used.items():
                    vars_at = ctx.by_class_subject_day_period.get(
                        (school_class, subject, day, period), []
                    )
                    if vars_at:
                        model.Add(sum(vars_at) >= 1).OnlyEnforceIf(used)
                        model.Add(sum(vars_at) == 0).OnlyEnforceIf(used.Not())

                count = sum(is_used.values())
                has_any = ctx.new_bool()
                model.Add(count >= 1).OnlyEnforceIf(has_any)
                model.Add(count == 0).OnlyEnforceIf(has_any.Not())
                model.Add(count >= min_per_day).OnlyEnforceIf(has_any)

                min_occ = model.NewIntVar(
                    min_period, max_period, f"mn{ctx._var_id}"
                )
                ctx._var_id += 1
                max_occ = model.NewIntVar(
                    min_period, max_period, f"mx{ctx._var_id}"
                )
                ctx._var_id += 1
                for period, used in is_used.items():
                    model.Add(min_occ <= period).OnlyEnforceIf(used)
                    model.Add(max_occ >= period).OnlyEnforceIf(used)

                span = model.NewIntVar(
                    0, max_period - min_period + 1, f"sp{ctx._var_id}"
                )
                ctx._var_id += 1
                model.Add(span == max_occ - min_occ + 1).OnlyEnforceIf(has_any)
                model.Add(count == span).OnlyEnforceIf(has_any)


def _add_max_teachers_constraints(model, ctx):
    for school_class in ctx.school.classes:
        for subject in school_class.required_hours:
            limit = school_class.max_teachers.get(subject, 1)
            allowed_ids = set(school_class.allowed_teachers.get(subject, []))
            allowed_teachers = [
                t for t in ctx.school.teachers if t.id in allowed_ids
            ]
            if not allowed_teachers:
                continue

            used_flags = []
            for teacher in allowed_teachers:
                session_vars = [
                    var
                    for session in ctx.sessions
                    if session.school_class == school_class
                    and session.subject == subject
                    for t, _, var, _ in ctx.assign[session]
                    if t == teacher
                ]
                if not session_vars:
                    continue

                used = ctx.new_bool()
                for var in session_vars:
                    model.Add(var <= used)
                model.Add(sum(session_vars) >= used)
                used_flags.append(used)

            if used_flags:
                model.Add(sum(used_flags) <= limit)


def _add_block_penalty_objective(model, ctx, objective_terms):
    """Penalize non-consecutive periods for the same (class, subject, day)."""
    if not ctx.all_periods:
        return

    min_period = ctx.all_periods[0]
    max_period = ctx.all_periods[-1]

    seen = set()
    for (school_class, subject, day, _period) in ctx.by_class_subject_day_period:
        key = (school_class, subject, day)
        if key in seen:
            continue
        seen.add(key)

        is_used = {}
        for period in ctx.all_periods:
            vars_at = ctx.by_class_subject_day_period.get(
                (school_class, subject, day, period)
            )
            if not vars_at:
                continue
            used = is_used.get(period)
            if used is None:
                used = ctx.new_bool()
                is_used[period] = used
            for var in vars_at:
                model.Add(var <= used)

        if len(is_used) < 2:
            continue

        count = sum(is_used.values())
        has_any = ctx.new_bool()
        model.Add(count >= 1).OnlyEnforceIf(has_any)
        model.Add(count == 0).OnlyEnforceIf(has_any.Not())

        min_occ = model.NewIntVar(min_period, max_period, f"bm{ctx._var_id}")
        ctx._var_id += 1
        max_occ = model.NewIntVar(min_period, max_period, f"bx{ctx._var_id}")
        ctx._var_id += 1
        for period, used in is_used.items():
            model.Add(min_occ <= period).OnlyEnforceIf(used)
            model.Add(max_occ >= period).OnlyEnforceIf(used)

        span = model.NewIntVar(
            0, max_period - min_period + 1, f"bs{ctx._var_id}"
        )
        ctx._var_id += 1
        model.Add(span == max_occ - min_occ + 1).OnlyEnforceIf(has_any)
        gaps = model.NewIntVar(0, max_period - min_period, f"bg{ctx._var_id}")
        ctx._var_id += 1
        model.Add(gaps == span - count).OnlyEnforceIf(has_any)
        model.Add(gaps == 0).OnlyEnforceIf(has_any.Not())
        objective_terms.append(BLOCK_PENALTY_WEIGHT * gaps)


def _add_class_continuity_penalty(model, ctx, objective_terms):
    """
    Penalize a teacher teaching class A, then class B, then class A again
    on the same day. Uses O(classes) vars per period pair instead of O(C²).
    """
    for teacher in ctx.school.teachers:
        for day in ctx.days:
            period_class_vars = defaultdict(list)
            for session in ctx.sessions:
                for t, ts, var, _ in ctx.assign[session]:
                    if t == teacher and ts.day == day:
                        period_class_vars[ts.period].append((session.school_class, var))

            periods = sorted(period_class_vars)
            if len(periods) < 2:
                continue

            for i in range(len(periods) - 1):
                p1, p2 = periods[i], periods[i + 1]
                entries1 = period_class_vars[p1]
                entries2 = period_class_vars[p2]

                classes = {cls for cls, _ in entries1} | {cls for cls, _ in entries2}

                has_at = {}
                for cls in classes:
                    vars1 = [v for c, v in entries1 if c == cls]
                    vars2 = [v for c, v in entries2 if c == cls]
                    h1 = ctx._reify_has_any(vars1) if vars1 else None
                    h2 = ctx._reify_has_any(vars2) if vars2 else None
                    if h1 is not None:
                        has_at[(cls, p1)] = h1
                    if h2 is not None:
                        has_at[(cls, p2)] = h2

                busy1 = ctx._reify_has_any([v for _, v in entries1])
                busy2 = ctx._reify_has_any([v for _, v in entries2])

                same_class_flags = []
                for cls in classes:
                    h1 = has_at.get((cls, p1))
                    h2 = has_at.get((cls, p2))
                    if h1 is None or h2 is None:
                        continue
                    both = ctx.new_bool()
                    model.AddBoolAnd([h1, h2]).OnlyEnforceIf(both)
                    model.AddBoolOr([h1.Not(), h2.Not()]).OnlyEnforceIf(both.Not())
                    same_class_flags.append(both)

                if not same_class_flags:
                    switch = ctx.new_bool()
                    model.AddBoolAnd([busy1, busy2]).OnlyEnforceIf(switch)
                    model.AddBoolOr([busy1.Not(), busy2.Not()]).OnlyEnforceIf(
                        switch.Not()
                    )
                else:
                    same_class = ctx.new_bool()
                    model.AddBoolOr(same_class_flags).OnlyEnforceIf(same_class)
                    model.AddBoolAnd([f.Not() for f in same_class_flags]).OnlyEnforceIf(
                        same_class.Not()
                    )
                    switch = ctx.new_bool()
                    model.AddBoolAnd([busy1, busy2, same_class.Not()]).OnlyEnforceIf(
                        switch
                    )
                    model.AddBoolOr([busy1.Not(), busy2.Not(), same_class]).OnlyEnforceIf(
                        switch.Not()
                    )

                objective_terms.append(CLASS_SWITCH_WEIGHT * switch)

def _add_teacher_working_days_penalty(model, ctx, objective_terms):
    """
    Soft objective:
    Prefer schedules where each teacher works on fewer days.

    Uses one BoolVar per (teacher, day), so the overhead is very small.
    """
    for teacher in ctx.school.teachers:
        for day in ctx.days:
            busy_periods = []

            for period in ctx.all_periods:
                busy = ctx.teacher_busy(teacher, day, period)
                if busy is not None:
                    busy_periods.append(busy)

            if not busy_periods:
                continue

            works_today = ctx.new_bool()

            model.AddBoolOr(busy_periods).OnlyEnforceIf(works_today)
            model.AddBoolAnd([b.Not() for b in busy_periods]).OnlyEnforceIf(
                works_today.Not()
            )

            objective_terms.append(WORKING_DAY_WEIGHT * works_today)

def _add_teacher_gap_penalty(model, ctx, objective_terms):
    for teacher in ctx.school.teachers:
        for day in ctx.days:
            busy_by_period = {}
            for period in ctx.all_periods:
                busy = ctx.teacher_busy(teacher, day, period)
                if busy is not None:
                    busy_by_period[period] = busy

            periods = sorted(busy_by_period)
            if len(periods) < 3:
                continue

            for i in range(1, len(periods) - 1):
                p_curr = periods[i]
                before_vars = [busy_by_period[p] for p in periods if p < p_curr]
                after_vars = [busy_by_period[p] for p in periods if p > p_curr]
                if not before_vars or not after_vars:
                    continue

                busy_before = ctx._reify_has_any(before_vars)
                busy_after = ctx._reify_has_any(after_vars)
                is_gap = ctx.new_bool()
                model.AddBoolAnd(
                    [busy_before, busy_by_period[p_curr].Not(), busy_after]
                ).OnlyEnforceIf(is_gap)
                model.AddBoolOr(
                    [
                        busy_before.Not(),
                        busy_by_period[p_curr],
                        busy_after.Not(),
                    ]
                ).OnlyEnforceIf(is_gap.Not())
                objective_terms.append(TEACHER_GAP_WEIGHT * is_gap)


def _add_class_gap_penalty(model, ctx, objective_terms):
    for school_class in ctx.school.classes:
        for day in ctx.days:
            busy_by_period = {}
            for period in ctx.all_periods:
                busy = ctx.class_busy(school_class, day, period)
                if busy is not None:
                    busy_by_period[period] = busy

            periods = sorted(busy_by_period)
            if len(periods) < 3:
                continue

            for i in range(1, len(periods) - 1):
                p_prev, p_curr, p_next = periods[i - 1], periods[i], periods[i + 1]
                gap_size = p_next - p_prev - 1
                if gap_size <= 0:
                    continue

                is_gap = ctx.new_bool()
                model.AddBoolAnd(
                    [
                        busy_by_period[p_prev],
                        busy_by_period[p_curr].Not(),
                        busy_by_period[p_next],
                    ]
                ).OnlyEnforceIf(is_gap)
                model.AddBoolOr(
                    [
                        busy_by_period[p_prev].Not(),
                        busy_by_period[p_curr],
                        busy_by_period[p_next].Not(),
                    ]
                ).OnlyEnforceIf(is_gap.Not())
                objective_terms.append(CLASS_GAP_WEIGHT * is_gap)


def _add_subject_preference_soft(model, school, ctx, objective_terms):
    for session in ctx.sessions:
        preferred = school.get_subject_preferred_slots(session.subject)
        if not preferred:
            continue
        for _, ts, var, _ in ctx.assign[session]:
            if ts not in preferred:
                objective_terms.append(SUBJECT_PREFERENCE_WEIGHT * var)


def _add_max_entry_period_constraint(model, ctx, max_entry_period):
    for school_class in ctx.school.classes:
        for day in ctx.days:
            all_day_vars = [
                var
                for session in ctx.sessions
                if session.school_class == school_class
                for _, ts, var, _ in ctx.assign[session]
                if ts.day == day
            ]
            if not all_day_vars:
                continue

            early_vars = [
                var
                for session in ctx.sessions
                if session.school_class == school_class
                for _, ts, var, _ in ctx.assign[session]
                if ts.day == day and ts.period <= max_entry_period
            ]

            has_session_today = ctx.new_bool()
            model.AddBoolOr(all_day_vars).OnlyEnforceIf(has_session_today)
            model.AddBoolAnd([v.Not() for v in all_day_vars]).OnlyEnforceIf(
                has_session_today.Not()
            )

            if early_vars:
                model.AddBoolOr(early_vars).OnlyEnforceIf(has_session_today)
            else:
                model.Add(has_session_today == 0)
                model.AddBoolAnd([v.Not() for v in all_day_vars])


def _add_min_exit_period_constraint(model, ctx, min_exit_period):
    for school_class in ctx.school.classes:
        for day in ctx.days:
            all_day_vars = [
                var
                for session in ctx.sessions
                if session.school_class == school_class
                for _, ts, var, _ in ctx.assign[session]
                if ts.day == day
            ]
            if not all_day_vars:
                continue

            late_vars = [
                var
                for session in ctx.sessions
                if session.school_class == school_class
                for _, ts, var, _ in ctx.assign[session]
                if ts.day == day and ts.period >= min_exit_period
            ]

            has_session_today = ctx.new_bool()
            model.AddBoolOr(all_day_vars).OnlyEnforceIf(has_session_today)
            model.AddBoolAnd([v.Not() for v in all_day_vars]).OnlyEnforceIf(
                has_session_today.Not()
            )

            if late_vars:
                model.AddBoolOr(late_vars).OnlyEnforceIf(has_session_today)
            else:
                model.Add(has_session_today == 0)
                model.AddBoolAnd([v.Not() for v in all_day_vars])


def _add_no_student_gap_constraint(model, ctx):
    for school_class in ctx.school.classes:
        for day in ctx.days:
            for p_idx, p_curr in enumerate(ctx.all_periods):
                if p_idx == 0 or p_idx == len(ctx.all_periods) - 1:
                    continue

                busy_curr = ctx.class_busy(school_class, day, p_curr)
                if busy_curr is None:
                    continue

                vars_before = [
                    ctx.class_busy(school_class, day, p)
                    for p in ctx.all_periods[:p_idx]
                    if ctx.class_busy(school_class, day, p) is not None
                ]
                vars_after = [
                    ctx.class_busy(school_class, day, p)
                    for p in ctx.all_periods[p_idx + 1 :]
                    if ctx.class_busy(school_class, day, p) is not None
                ]

                if not vars_before or not vars_after:
                    continue

                busy_before = ctx._reify_has_any(vars_before)
                busy_after = ctx._reify_has_any(vars_after)
                model.AddBoolOr(
                    [busy_before.Not(), busy_after.Not(), busy_curr]
                )


def _add_symmetry_breaking(model, school, ctx):
    """
    Order interchangeable sessions of the same (class, subject) by timeslot index.
    Does not change solution quality — only removes redundant search permutations.
    """
    if not school.timeslots:
        return

    slot_index = {ts: i for i, ts in enumerate(school.timeslots)}
    max_idx = len(school.timeslots) - 1
    groups = defaultdict(list)
    for session in ctx.sessions:
        groups[(session.school_class, session.subject)].append(session)

    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: s.number)
        slot_vars = []
        for session in group:
            sv = model.NewIntVar(0, max_idx, f"si{ctx._var_id}")
            ctx._var_id += 1
            for _, ts, var, _ in ctx.assign[session]:
                model.Add(sv == slot_index[ts]).OnlyEnforceIf(var)
            slot_vars.append(sv)
        for i in range(len(slot_vars) - 1):
            model.Add(slot_vars[i] <= slot_vars[i + 1])


def _configure_solver(solver, time_limit_seconds):
    solver.parameters.max_time_in_seconds = time_limit_seconds
    workers = os.cpu_count() or 8
    solver.parameters.num_search_workers = max(1, workers)
    solver.parameters.cp_model_presolve = True
    solver.parameters.linearization_level = 2
    solver.parameters.use_lns = True
    solver.parameters.diversify_lns_params = True


def _extract_schedule(school, sessions, assign, solver):
    schedule = Schedule()
    for session in sessions:
        for teacher, timeslot, var, _ in assign[session]:
            if solver.Value(var) == 1:
                schedule.assign(session, Assignment(teacher, timeslot))
                break
    return schedule


def _empty_strict_domain_errors(school, domains):
    errors = []
    for session, domain in domains.items():
        if not domain:
            preferred = school.get_subject_preferred_slots(session.subject)
            if preferred:
                errors.append(
                    f"- {session} : aucun enseignant disponible dans les créneaux préférés de "
                    f"{session.subject}"
                )
            else:
                errors.append(f"- {session} : aucune combinaison enseignant/créneau valide")
    return errors


def _count_subject_slot_violations(school, schedule):
    violations = {}
    for session, assignment in schedule.assignments.items():
        preferred = school.get_subject_preferred_slots(session.subject)
        if preferred and assignment.timeslot not in preferred:
            violations[session.subject] = violations.get(session.subject, 0) + 1
    return violations


def validate_strict_subject_preferences(school):
    """
    Check that every session can be placed in a preferred slot (when defined).
    Returns a list of human-readable error strings.
    """
    school.generate_sessions()
    domains = school.generate_domains()
    return _empty_strict_domain_errors(school, domains)


def solve_with_cp_sat(school, time_limit_seconds=300, generation_prefs=None, cancel_token=None):
    """
    Find a timetable using CP-SAT.

    Subjects with configured preferred slots (francais, arabe, …) may ONLY
    be scheduled in those slots — hard constraint.

    generation_prefs dict (all optional):
      - max_entry_period (int): latest first period allowed per class per day
      - min_exit_period  (int): earliest last period allowed per class per day
      - allow_student_gaps (bool): if False, gaps in class schedules are forbidden

    cancel_token is an optional dict with keys:
      - event: threading.Event() used to signal cancellation
      - solver: CpSolver instance once solve starts

    Returns (schedule, status_message) where schedule is None on failure.
    """
    if generation_prefs is None:
        generation_prefs = {}

    school.generate_sessions()
    sessions = school.sessions

    teacher_capacity = sum(t.required_hours for t in school.teachers)
    if teacher_capacity != len(sessions):
        return None, (
            f"Inadéquation des capacités : les enseignants fournissent {teacher_capacity} heures "
            f"mais {len(sessions)} séances de cours sont requises."
        )

    domains = school.generate_domains()

    model = cp_model.CpModel()
    assign = _build_assignment_vars(model, school, sessions, domains)
    ctx = _ModelContext(model, school, sessions, assign)

    if not _add_session_constraints(model, assign):
        return None, "Une leçon n'a pas d'options de placement valides."

    _add_teacher_slot_constraints(model, ctx)
    _add_class_slot_constraints(model, ctx)
    _add_teacher_hour_constraints(model, school, ctx)
    _add_subject_day_cap_constraints(model, school, ctx)
    _add_subject_min_per_day_constraints(model, ctx)
    _add_max_teachers_constraints(model, ctx)

    max_entry = generation_prefs.get("max_entry_period")
    min_exit = generation_prefs.get("min_exit_period")
    allow_gaps = generation_prefs.get("allow_student_gaps", True)

    if max_entry is not None:
        _add_max_entry_period_constraint(model, ctx, int(max_entry))
    if min_exit is not None:
        _add_min_exit_period_constraint(model, ctx, int(min_exit))
    if not allow_gaps:
        _add_no_student_gap_constraint(model, ctx)

    _add_symmetry_breaking(model, school, ctx)

    objective_terms = []
    for entries in ctx.assign.values():
        for _, _, var, teacher_penalty in entries:
            if teacher_penalty:
                objective_terms.append(teacher_penalty * var)
    _add_subject_preference_soft(model, school, ctx, objective_terms)
    _add_block_penalty_objective(model, ctx, objective_terms)
    _add_class_continuity_penalty(model, ctx, objective_terms)
    _add_teacher_gap_penalty(model, ctx, objective_terms)
    _add_class_gap_penalty(model, ctx, objective_terms)
    _add_teacher_working_days_penalty(model, ctx, objective_terms)

    print("Model build time:", time.perf_counter() - t0, "seconds")
    solver = cp_model.CpSolver()
    if cancel_token is not None:
        cancel_token["solver"] = solver
    _configure_solver(solver, time_limit_seconds)

    if cancel_token and cancel_token.get("event") and cancel_token["event"].is_set():
        return None, "La génération a été annulée."

    # Phase 1: find any feasible timetable quickly (no objective).
    phase1_limit = min(30.0, time_limit_seconds * 0.15)
    solver.parameters.max_time_in_seconds = phase1_limit
    status = solver.Solve(model)
    print("=== PHASE 1 ===")
    print("Status:", solver.StatusName(status))
    print("Wall time:", solver.WallTime())
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        print("Objective:", solver.ObjectiveValue())
        print("Best bound:", solver.BestObjectiveBound())
    print("Conflicts:", solver.NumConflicts())
    print("Branches:", solver.NumBranches())

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for entries in assign.values():
            for _, _, var, _ in entries:
                model.AddHint(var, solver.Value(var))
        if objective_terms:
            if cancel_token and cancel_token.get("event") and cancel_token["event"].is_set():
                return None, "La génération a été annulée."
            model.Minimize(sum(objective_terms))
            remaining = max(5.0, time_limit_seconds - phase1_limit)
            solver.parameters.max_time_in_seconds = remaining
            status = solver.Solve(model)
            print("=== PHASE 2 ===")
            print("Total time:", time.perf_counter() - t0, "seconds")
            print("Status:", solver.StatusName(status))
            print("Wall time:", solver.WallTime())
            if cancel_token and cancel_token.get("event") and cancel_token["event"].is_set():
                return None, "La génération a été annulée."
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                print("Objective:", solver.ObjectiveValue())
                print("Best bound:", solver.BestObjectiveBound())
            print("Conflicts:", solver.NumConflicts())
            print("Branches:", solver.NumBranches())
    elif status == cp_model.UNKNOWN and objective_terms:
        if cancel_token and cancel_token.get("event") and cancel_token["event"].is_set():
            return None, "La génération a été annulée."
        model.Minimize(sum(objective_terms))
        solver.parameters.max_time_in_seconds = time_limit_seconds
        status = solver.Solve(model)

    if status == cp_model.INFEASIBLE:
        pref_subjects = _subjects_with_preferences(school)
        min_subjects = [
            subj.name
            for subj in school.subjects
            if subj.min_per_day > 0
        ]
        details = []
        if pref_subjects:
            details.append(
                f"créneaux préférés pour {', '.join(pref_subjects)}"
            )
        if min_subjects:
            details.append(
                f"minimum quotidien pour {', '.join(min_subjects)}"
            )
        detail_text = (
            f" ({'; '.join(details)})" if details else ""
        )
        return None, (
            "Aucun emploi du temps complet n'existe avec les contraintes actuelles"
            f"{detail_text}. "
            "Assouplissez les minimums par jour, les créneaux préférés, "
            "ou ajustez les heures des enseignants/classes."
        )
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, (
            f"CP-SAT n'a pas trouvé de solution en {time_limit_seconds}s. "
            "Réessayez — une école plus grande peut nécessiter plusieurs minutes."
        )

    schedule = _extract_schedule(school, sessions, assign, solver)
    school.schedule = schedule

    if len(schedule.assignments) != len(sessions):
        return None, "Erreur interne : emploi du temps incomplet extrait de CP-SAT."

    pref_subjects = _subjects_with_preferences(school)
    status_label = "optimal" if status == cp_model.OPTIMAL else "réalisable"
    violations_after = _count_subject_slot_violations(school, school.schedule)
    total = sum(violations_after.values()) if violations_after else 0
    return (
        school.schedule,
        f"Emploi du temps {status_label} trouvé. "
        f"Violations des préférences : {total} séances placées en dehors des créneaux préférés.",
    )
