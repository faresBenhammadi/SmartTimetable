"""OR-Tools CP-SAT solver for school timetabling."""

from ortools.sat.python import cp_model

from school import Assignment, Schedule

TEACHER_PREFERENCE_WEIGHT = 10
BLOCK_PENALTY_WEIGHT = 250
SUBJECT_PREFERENCE_WEIGHT = 200 
CLASS_SWITCH_WEIGHT = 300
TEACHER_GAP_WEIGHT = 300
CLASS_GAP_WEIGHT = 150


def _subjects_with_preferences(school):
    return [
        subject
        for subject in school.slot_preferences
        if school.get_subject_preferred_slots(subject)
    ]


def _build_assignment_vars(model, school, sessions, domains):
    """Create one BoolVar per valid (session, teacher, timeslot) triple."""
    assign = {}
    for session in sessions:
        entries = []
        for teacher, timeslot in domains.get(session, []):
            
            label = (
                f"s_{session.school_class.name}_{session.subject}_{session.number}"
                f"_t{teacher.id}_{timeslot.day}p{timeslot.period}"
            )
            var = model.NewBoolVar(label)
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


def _add_teacher_slot_constraints(model, school, sessions, assign):
    for teacher in school.teachers:
        for timeslot in school.timeslots:
            vars_at = [
                var
                for session in sessions
                for t, ts, var, _ in assign[session]
                if t == teacher and ts == timeslot
            ]
            if vars_at:
                model.Add(sum(vars_at) <= 1)


def _add_subject_preference_soft(model, school, sessions, assign, objective_terms):
    """
    Soft version: strongly penalize placing a subject outside its preferred slots,
    but don't make it impossible.
    """
    for session in sessions:
        preferred = school.get_subject_preferred_slots(session.subject)
        if not preferred:
            continue
        for t, ts, var, _ in assign[session]:
            if ts not in preferred:
                objective_terms.append(SUBJECT_PREFERENCE_WEIGHT * var)

def _add_class_slot_constraints(model, school, sessions, assign):
    for school_class in school.classes:
        for timeslot in school.timeslots:
            vars_at = [
                var
                for session in sessions
                if session.school_class == school_class
                for _, ts, var, _ in assign[session]
                if ts == timeslot
            ]
            if vars_at:
                model.Add(sum(vars_at) <= 1)


def _add_teacher_hour_constraints(model, school, sessions, assign):
    for teacher in school.teachers:
        vars_t = [
            var
            for session in sessions
            for t, _, var, _ in assign[session]
            if t == teacher
        ]
        if vars_t:
            model.Add(sum(vars_t) == teacher.required_hours)


def _add_subject_day_cap_constraints(model, school, sessions, assign):
    days = {ts.day for ts in school.timeslots}
    for school_class in school.classes:
        for subject in school_class.required_hours:
            max_per_day = school.get_subject_max_per_day(subject)
            for day in days:
                vars_day = [
                    var
                    for session in sessions
                    if session.school_class == school_class and session.subject == subject
                    for _, ts, var, _ in assign[session]
                    if ts.day == day
                ]
                if vars_day:
                    model.Add(sum(vars_day) <= max_per_day)


def _add_subject_min_per_day_constraints(model, school, sessions, assign):
    """
    Hard rule: when min_per_day > 0, each (class, subject, day) has either
    0 sessions or at least min_per_day consecutive sessions.
    """
    days = {ts.day for ts in school.timeslots}
    all_periods = sorted({ts.period for ts in school.timeslots})
    if not all_periods:
        return

    min_period = all_periods[0]
    max_period = all_periods[-1]

    for school_class in school.classes:
        for subject in school_class.required_hours:
            min_per_day = school.get_subject_min_per_day(subject)
            if min_per_day <= 0:
                continue

            for day in days:
                is_used = {}
                for session in sessions:
                    if session.school_class != school_class or session.subject != subject:
                        continue
                    for _, ts, var, _ in assign[session]:
                        if ts.day != day:
                            continue
                        used = is_used.get(ts.period)
                        if used is None:
                            used = model.NewBoolVar(
                                f"used_{school_class.name}_{subject}_{day}_p{ts.period}"
                            )
                            is_used[ts.period] = used
                        model.Add(var <= used)

                if not is_used:
                    continue

                for period, used in is_used.items():
                    vars_at = [
                        var
                        for session in sessions
                        if session.school_class == school_class
                        and session.subject == subject
                        for _, ts, var, _ in assign[session]
                        if ts.day == day and ts.period == period
                    ]
                    if vars_at:
                        model.Add(sum(vars_at) >= 1).OnlyEnforceIf(used)
                        model.Add(sum(vars_at) == 0).OnlyEnforceIf(used.Not())

                count = sum(is_used.values())
                has_any = model.NewBoolVar(
                    f"has_{school_class.name}_{subject}_{day}"
                )
                model.Add(count >= 1).OnlyEnforceIf(has_any)
                model.Add(count == 0).OnlyEnforceIf(has_any.Not())
                model.Add(count >= min_per_day).OnlyEnforceIf(has_any)

                min_occ = model.NewIntVar(
                    min_period,
                    max_period,
                    f"minocc_{school_class.name}_{subject}_{day}",
                )
                max_occ = model.NewIntVar(
                    min_period,
                    max_period,
                    f"maxocc_{school_class.name}_{subject}_{day}",
                )
                for period, used in is_used.items():
                    model.Add(min_occ <= period).OnlyEnforceIf(used)
                    model.Add(max_occ >= period).OnlyEnforceIf(used)

                span = model.NewIntVar(
                    0,
                    max_period - min_period + 1,
                    f"span_{school_class.name}_{subject}_{day}",
                )
                model.Add(span == max_occ - min_occ + 1).OnlyEnforceIf(has_any)
                model.Add(count == span).OnlyEnforceIf(has_any)


def _add_max_teachers_constraints(model, school, sessions, assign):
    for school_class in school.classes:
        for subject in school_class.required_hours:
            limit = school_class.max_teachers.get(subject, 1)
            allowed_ids = set(school_class.allowed_teachers.get(subject, []))
            allowed_teachers = [
                t for t in school.teachers if t.id in allowed_ids
            ]
            if not allowed_teachers:
                continue

            used_flags = []
            for teacher in allowed_teachers:
                session_vars = [
                    var
                    for session in sessions
                    if session.school_class == school_class
                    and session.subject == subject
                    for t, _, var, _ in assign[session]
                    if t == teacher
                ]
                if not session_vars:
                    continue

                used = model.NewBoolVar(
                    f"use_{school_class.name}_{subject}_t{teacher.id}"
                )
                for var in session_vars:
                    model.Add(var <= used)
                model.Add(sum(session_vars) >= used)
                used_flags.append(used)

            if used_flags:
                model.Add(sum(used_flags) <= limit)


def _add_block_penalty_objective(model, school, sessions, assign, objective_terms):
    """Penalize non-consecutive periods for the same (class, subject, day)."""
    days = {ts.day for ts in school.timeslots}
    for school_class in school.classes:
        for subject in school_class.required_hours:
            for day in days:
                day_sessions = [
                    session
                    for session in sessions
                    if session.school_class == school_class
                    and session.subject == subject
                ]
                if len(day_sessions) < 2:
                    continue

                period_to_vars = {}
                for session in day_sessions:
                    for _, ts, var, _ in assign[session]:
                        if ts.day == day:
                            period_to_vars.setdefault(ts.period, []).append(var)

                periods = sorted(period_to_vars)
                for i in range(len(periods) - 1):
                    gap = periods[i + 1] - periods[i] - 1
                    if gap <= 0:
                        continue
                    left = period_to_vars[periods[i]]
                    right = period_to_vars[periods[i + 1]]

                    has_left = model.NewBoolVar(
                        f"hl_{school_class.name}_{subject}_{day}_{periods[i]}"
                    )
                    has_right = model.NewBoolVar(
                        f"hr_{school_class.name}_{subject}_{day}_{periods[i+1]}"
                    )
                    both = model.NewBoolVar(
                        f"blk_{school_class.name}_{subject}_{day}_{periods[i]}_{periods[i+1]}"
                    )

                    model.Add(sum(left) >= 1).OnlyEnforceIf(has_left)
                    model.Add(sum(left) == 0).OnlyEnforceIf(has_left.Not())
                    model.Add(sum(right) >= 1).OnlyEnforceIf(has_right)
                    model.Add(sum(right) == 0).OnlyEnforceIf(has_right.Not())
                    model.AddBoolAnd([has_left, has_right]).OnlyEnforceIf(both)
                    model.AddBoolOr([has_left.Not(), has_right.Not()]).OnlyEnforceIf(
                        both.Not()
                    )
                    objective_terms.append(gap * BLOCK_PENALTY_WEIGHT * both)

def _add_class_continuity_penalty(model, school, sessions, assign, objective_terms):
    """
    Penalize a teacher teaching class A, then class B, then class A again
    on the same day. We do this by penalizing each (teacher, day, period)
    pair where the class differs from the previous period.
    """
    days = {ts.day for ts in school.timeslots}

    for teacher in school.teachers:
        for day in days:
            # Collect all periods this teacher could teach on this day
            # period -> list of (school_class, var)
            period_class_vars = {}
            for session in sessions:
                for t, ts, var, _ in assign[session]:
                    if t == teacher and ts.day == day:
                        p = ts.period
                        if p not in period_class_vars:
                            period_class_vars[p] = []
                        period_class_vars[p].append((session.school_class, var))

            periods = sorted(period_class_vars.keys())
            if len(periods) < 2:
                continue

            # For each consecutive pair of periods, penalize if different classes
            for i in range(len(periods) - 1):
                p1, p2 = periods[i], periods[i + 1]
                entries1 = period_class_vars[p1]
                entries2 = period_class_vars[p2]

                # For each pair of classes that are different
                classes1 = {cls for cls, _ in entries1}
                classes2 = {cls for cls, _ in entries2}

                for cls1 in classes1:
                    for cls2 in classes2:
                        if cls1 == cls2:
                            continue
                        # vars where teacher teaches cls1 at p1
                        vars1 = [v for c, v in entries1 if c == cls1]
                        # vars where teacher teaches cls2 at p2
                        vars2 = [v for c, v in entries2 if c == cls2]

                        if not vars1 or not vars2:
                            continue

                        # both = 1 if teacher switches class between p1 and p2
                        both = model.NewBoolVar(
                            f"switch_{teacher.id}_{day}_{p1}_{p2}_{cls1.name}_{cls2.name}"
                        )
                        sum1 = model.NewBoolVar(f"has_{teacher.id}_{day}_{p1}_{cls1.name}")
                        sum2 = model.NewBoolVar(f"has_{teacher.id}_{day}_{p2}_{cls2.name}")

                        model.Add(sum(vars1) >= 1).OnlyEnforceIf(sum1)
                        model.Add(sum(vars1) == 0).OnlyEnforceIf(sum1.Not())
                        model.Add(sum(vars2) >= 1).OnlyEnforceIf(sum2)
                        model.Add(sum(vars2) == 0).OnlyEnforceIf(sum2.Not())

                        model.AddBoolAnd([sum1, sum2]).OnlyEnforceIf(both)
                        model.AddBoolOr([sum1.Not(), sum2.Not()]).OnlyEnforceIf(both.Not())

                        objective_terms.append(CLASS_SWITCH_WEIGHT * both)

def _add_teacher_gap_penalty(model, school, sessions, assign, objective_terms):
    days = {ts.day for ts in school.timeslots}
    all_periods = sorted({ts.period for ts in school.timeslots})

    for teacher in school.teachers:
        for day in days:
            # Build busy var for each period
            period_busy_vars = {}
            for period in all_periods:
                busy_vars = [
                    var
                    for session in sessions
                    for t, ts, var, _ in assign[session]
                    if t == teacher and ts.day == day and ts.period == period
                ]
                if busy_vars:
                    busy = model.NewBoolVar(f"busy_{teacher.id}_{day}_{period}")
                    model.Add(sum(busy_vars) >= 1).OnlyEnforceIf(busy)
                    model.Add(sum(busy_vars) == 0).OnlyEnforceIf(busy.Not())
                    period_busy_vars[period] = busy

            if len(period_busy_vars) < 2:
                continue

            periods = sorted(period_busy_vars.keys())

            # ever_busy_before[i] = True if teacher is busy at ANY period before periods[i]
            ever_busy_before = {}
            for i, p in enumerate(periods):
                if i == 0:
                    ever_busy_before[p] = None  # no period before first
                    continue
                prev_p = periods[i - 1]
                ebb = model.NewBoolVar(f"ebb_{teacher.id}_{day}_{p}")
                if prev_p not in ever_busy_before or ever_busy_before[prev_p] is None:
                    # only one period before: just use it directly
                    model.Add(ebb == period_busy_vars[prev_p])
                else:
                    # ebb = period_busy_vars[prev_p] OR ever_busy_before[prev_p]
                    model.AddBoolOr([
                        period_busy_vars[prev_p],
                        ever_busy_before[prev_p]
                    ]).OnlyEnforceIf(ebb)
                    model.AddBoolAnd([
                        period_busy_vars[prev_p].Not(),
                        ever_busy_before[prev_p].Not()
                    ]).OnlyEnforceIf(ebb.Not())
                ever_busy_before[p] = ebb

            # ever_busy_after[i] = True if teacher is busy at ANY period after periods[i]
            ever_busy_after = {}
            for i, p in enumerate(reversed(periods)):
                if i == 0:
                    ever_busy_after[p] = None  # no period after last
                    continue
                next_p = periods[len(periods) - i]
                eba = model.NewBoolVar(f"eba_{teacher.id}_{day}_{p}")
                if next_p not in ever_busy_after or ever_busy_after[next_p] is None:
                    model.Add(eba == period_busy_vars[next_p])
                else:
                    model.AddBoolOr([
                        period_busy_vars[next_p],
                        ever_busy_after[next_p]
                    ]).OnlyEnforceIf(eba)
                    model.AddBoolAnd([
                        period_busy_vars[next_p].Not(),
                        ever_busy_after[next_p].Not()
                    ]).OnlyEnforceIf(eba.Not())
                ever_busy_after[p] = eba

            # Now detect gaps: interior periods where not busy but sandwiched
            for i, p_curr in enumerate(periods):
                if i == 0 or i == len(periods) - 1:
                    continue
                if ever_busy_before.get(p_curr) is None or ever_busy_after.get(p_curr) is None:
                    continue

                is_gap = model.NewBoolVar(f"gap_{teacher.id}_{day}_{p_curr}")
                model.AddBoolAnd([
                    ever_busy_before[p_curr],
                    period_busy_vars[p_curr].Not(),
                    ever_busy_after[p_curr]
                ]).OnlyEnforceIf(is_gap)
                model.AddBoolOr([
                    ever_busy_before[p_curr].Not(),
                    period_busy_vars[p_curr],
                    ever_busy_after[p_curr].Not()
                ]).OnlyEnforceIf(is_gap.Not())

                objective_terms.append(TEACHER_GAP_WEIGHT * is_gap)


def _add_class_gap_penalty(model, school, sessions, assign, objective_terms):
    """
    Penalize gaps in a class's daily schedule.
    """
    days = {ts.day for ts in school.timeslots}
    all_periods = sorted({ts.period for ts in school.timeslots})

    for school_class in school.classes:
        for day in days:
            period_busy_vars = {}
            for period in all_periods:
                busy_vars = [
                    var
                    for session in sessions
                    if session.school_class == school_class
                    for _, ts, var, _ in assign[session]
                    if ts.day == day and ts.period == period
                ]
                if busy_vars:
                    busy = model.NewBoolVar(f"cbusy_{school_class.name}_{day}_{period}")
                    model.Add(sum(busy_vars) >= 1).OnlyEnforceIf(busy)
                    model.Add(sum(busy_vars) == 0).OnlyEnforceIf(busy.Not())
                    period_busy_vars[period] = busy

            periods = sorted(period_busy_vars.keys())
            if len(periods) < 3:
                continue

            for i in range(1, len(periods) - 1):
                p_prev = periods[i - 1]
                p_curr = periods[i]
                p_next = periods[i + 1]

                gap_size = p_next - p_prev - 1
                if gap_size <= 0:
                    continue

                is_gap = model.NewBoolVar(f"cgap_{school_class.name}_{day}_{p_curr}")
                model.AddBoolAnd([
                    period_busy_vars[p_prev],
                    period_busy_vars[p_curr].Not(),
                    period_busy_vars[p_next]
                ]).OnlyEnforceIf(is_gap)
                model.AddBoolOr([
                    period_busy_vars[p_prev].Not(),
                    period_busy_vars[p_curr],
                    period_busy_vars[p_next].Not()
                ]).OnlyEnforceIf(is_gap.Not())

                objective_terms.append(CLASS_GAP_WEIGHT * is_gap)


# ─── Hard constraints from generation preferences ─────────────────────────────

def _add_max_entry_period_constraint(model, school, sessions, assign, max_entry_period):
    """
    Hard constraint: on any day a class has sessions, at least one must be
    at or before max_entry_period (students cannot start too late).
    """
    days = {ts.day for ts in school.timeslots}

    for school_class in school.classes:
        for day in days:
            all_day_vars = [
                var
                for session in sessions
                if session.school_class == school_class
                for _, ts, var, _ in assign[session]
                if ts.day == day
            ]
            if not all_day_vars:
                continue

            early_vars = [
                var
                for session in sessions
                if session.school_class == school_class
                for _, ts, var, _ in assign[session]
                if ts.day == day and ts.period <= max_entry_period
            ]

            has_session_today = model.NewBoolVar(
                f"has_session_{school_class.name}_{day}_entry"
            )
            model.AddBoolOr(all_day_vars).OnlyEnforceIf(has_session_today)
            model.AddBoolAnd([v.Not() for v in all_day_vars]).OnlyEnforceIf(
                has_session_today.Not()
            )

            if early_vars:
                # If class has any session today, must have one in early periods
                model.AddBoolOr(early_vars).OnlyEnforceIf(has_session_today)
            else:
                # No early slots exist at all → forbid any session on this day
                model.Add(has_session_today == 0)
                model.AddBoolAnd([v.Not() for v in all_day_vars])


def _add_min_exit_period_constraint(model, school, sessions, assign, min_exit_period):
    """
    Hard constraint: on any day a class has sessions, at least one must be
    at or after min_exit_period (students cannot leave too early).
    """
    days = {ts.day for ts in school.timeslots}

    for school_class in school.classes:
        for day in days:
            all_day_vars = [
                var
                for session in sessions
                if session.school_class == school_class
                for _, ts, var, _ in assign[session]
                if ts.day == day
            ]
            if not all_day_vars:
                continue

            late_vars = [
                var
                for session in sessions
                if session.school_class == school_class
                for _, ts, var, _ in assign[session]
                if ts.day == day and ts.period >= min_exit_period
            ]

            has_session_today = model.NewBoolVar(
                f"has_session_{school_class.name}_{day}_exit"
            )
            model.AddBoolOr(all_day_vars).OnlyEnforceIf(has_session_today)
            model.AddBoolAnd([v.Not() for v in all_day_vars]).OnlyEnforceIf(
                has_session_today.Not()
            )

            if late_vars:
                model.AddBoolOr(late_vars).OnlyEnforceIf(has_session_today)
            else:
                model.Add(has_session_today == 0)
                model.AddBoolAnd([v.Not() for v in all_day_vars])


def _add_no_student_gap_constraint(model, school, sessions, assign):
    """
    Hard constraint: for each class on each day, if a class is busy before
    period P and after period P, it must also be busy at period P (no gaps).
    """
    days = {ts.day for ts in school.timeslots}
    all_periods = sorted({ts.period for ts in school.timeslots})

    for school_class in school.classes:
        for day in days:
            # Build busy var for each period
            period_busy_vars = {}
            for period in all_periods:
                busy_vars = [
                    var
                    for session in sessions
                    if session.school_class == school_class
                    for _, ts, var, _ in assign[session]
                    if ts.day == day and ts.period == period
                ]
                if busy_vars:
                    busy = model.NewBoolVar(
                        f"cng_busy_{school_class.name}_{day}_{period}"
                    )
                    model.Add(sum(busy_vars) >= 1).OnlyEnforceIf(busy)
                    model.Add(sum(busy_vars) == 0).OnlyEnforceIf(busy.Not())
                    period_busy_vars[period] = busy

            if len(period_busy_vars) < 3:
                continue

            for p_idx, p_curr in enumerate(all_periods):
                if p_curr not in period_busy_vars:
                    continue
                if p_idx == 0 or p_idx == len(all_periods) - 1:
                    continue

                vars_before = [
                    period_busy_vars[p]
                    for p in all_periods[:p_idx]
                    if p in period_busy_vars
                ]
                vars_after = [
                    period_busy_vars[p]
                    for p in all_periods[p_idx + 1:]
                    if p in period_busy_vars
                ]

                if not vars_before or not vars_after:
                    continue

                busy_before = model.NewBoolVar(
                    f"cng_before_{school_class.name}_{day}_{p_curr}"
                )
                busy_after = model.NewBoolVar(
                    f"cng_after_{school_class.name}_{day}_{p_curr}"
                )

                model.AddBoolOr(vars_before).OnlyEnforceIf(busy_before)
                model.AddBoolAnd([v.Not() for v in vars_before]).OnlyEnforceIf(
                    busy_before.Not()
                )
                model.AddBoolOr(vars_after).OnlyEnforceIf(busy_after)
                model.AddBoolAnd([v.Not() for v in vars_after]).OnlyEnforceIf(
                    busy_after.Not()
                )

                # busy_before AND busy_after => must be busy at p_curr
                # Equivalent: NOT busy_before OR NOT busy_after OR busy_curr
                model.AddBoolOr([
                    busy_before.Not(),
                    busy_after.Not(),
                    period_busy_vars[p_curr],
                ])


def _set_quality_objective(model, assign, school, sessions):
    objective_terms = []
    for entries in assign.values():
        for _, _, var, teacher_penalty in entries:
            if teacher_penalty:
                objective_terms.append(teacher_penalty * var)
    _add_subject_preference_soft(model, school, sessions, assign, objective_terms)
    _add_block_penalty_objective(model, school, sessions, assign, objective_terms)
    # 4. Continuité des classes pour un prof (nouveau)
    _add_class_continuity_penalty(model, school, sessions, assign, objective_terms)

    # 5. Trous dans le planning des profs (nouveau)
    _add_teacher_gap_penalty(model, school, sessions, assign, objective_terms)

    # 6. Trous dans le planning des classes (nouveau)
    _add_class_gap_penalty(model, school, sessions, assign, objective_terms)
    
    if objective_terms:
        model.Minimize(sum(objective_terms))



def _configure_solver(solver, time_limit_seconds):
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.num_search_workers = 8


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


def solve_with_cp_sat(school, time_limit_seconds=300, generation_prefs=None):
    """
    Find a timetable using CP-SAT.

    Subjects with configured preferred slots (francais, arabe, …) may ONLY
    be scheduled in those slots — hard constraint.

    generation_prefs dict (all optional):
      - max_entry_period (int): latest first period allowed per class per day
      - min_exit_period  (int): earliest last period allowed per class per day
      - allow_student_gaps (bool): if False, gaps in class schedules are forbidden

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

    if not _add_session_constraints(model, assign):
        return None, "Une leçon n'a pas d'options de placement valides."

    _add_teacher_slot_constraints(model, school, sessions, assign)
    _add_class_slot_constraints(model, school, sessions, assign)
    _add_teacher_hour_constraints(model, school, sessions, assign)
    _add_subject_day_cap_constraints(model, school, sessions, assign)
    _add_subject_min_per_day_constraints(model, school, sessions, assign)
    _add_max_teachers_constraints(model, school, sessions, assign)

    # ── Optional hard constraints from generation preferences ──────────────────
    max_entry = generation_prefs.get("max_entry_period")
    min_exit = generation_prefs.get("min_exit_period")
    allow_gaps = generation_prefs.get("allow_student_gaps", True)

    if max_entry is not None:
        _add_max_entry_period_constraint(model, school, sessions, assign, int(max_entry))
    if min_exit is not None:
        _add_min_exit_period_constraint(model, school, sessions, assign, int(min_exit))
    if not allow_gaps:
        _add_no_student_gap_constraint(model, school, sessions, assign)

    _set_quality_objective(model, assign, school, sessions)


    solver = cp_model.CpSolver()
    _configure_solver(solver, time_limit_seconds)
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
