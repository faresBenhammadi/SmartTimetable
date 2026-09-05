class Teacher:
    def __init__(
        self,
        id,
        name,
        subjects,
        allowed_classes=None,
        preferred_slots=None,
        unavailable_slots=None,
        required_hours = 10
    ):
        self.id = id
        self.name = name
        self.subjects = subjects

        self.allowed_classes = allowed_classes or []
        self.preferred_slots = preferred_slots or []
        self.unavailable_slots = unavailable_slots or []

        self.required_hours = required_hours
    def __repr__(self):
        return self.name

class TimeSlot:
    def __init__(self, day, period):
        self.day = day
        self.period = period

    def __eq__(self, other):
        return isinstance(other, TimeSlot) and self.day == other.day and self.period == other.period

    def __hash__(self):
        return hash((self.day, self.period))

    def __repr__(self):
        return f"{self.day}-{self.period}"

class Subject:
    def __init__(self, name, max_per_day=7, min_per_day=0):
        if isinstance(name, dict):
            self.name = (name.get("name") or "").strip()
            max_per_day_value = name.get("max_per_day", max_per_day)
            min_per_day_value = name.get("min_per_day", min_per_day)
            try:
                self.max_per_day = max(1, int(max_per_day_value))
            except (TypeError, ValueError):
                self.max_per_day = max_per_day
            try:
                self.min_per_day = max(0, int(min_per_day_value))
            except (TypeError, ValueError):
                self.min_per_day = min_per_day
            self.min_per_day = min(self.min_per_day, self.max_per_day)
        else:
            self.name = name
            self.max_per_day = max_per_day
            self.min_per_day = max(0, min(min_per_day, max_per_day))

    def __repr__(self):
        return self.name

class SchoolClass:
    def __init__(
        self,
        name,
        required_hours,
        max_teachers,
        allowed_teachers,
        tp_pairs=None
    ):
        
        self.name = name

        # {"Arabic": 5, "French": 4}
        self.required_hours = required_hours

        # {"Arabic": 1, "French": 2}
        self.max_teachers = max_teachers

        # {
        #   "Arabic": [1,2],
        #   "French": [3,4,5]
        # }
        self.allowed_teachers = allowed_teachers

        # [{"subj1": "Physique", "subj2": "Science", "count": 1}]
        self.tp_pairs = tp_pairs or []

    def __repr__(self):
        return self.name
        

class Session:
    def __init__(self, school_class, subject, number, is_tp=False, tp_group_id=None, tp_slot_index=0, tp_partner_subject=None):
        self.school_class = school_class
        self.subject = subject
        self.number = number
        self.is_tp = is_tp
        self.tp_group_id = tp_group_id
        self.tp_slot_index = tp_slot_index
        self.tp_partner_subject = tp_partner_subject

    def __repr__(self):
        if self.is_tp:
            return f"{self.school_class.name} - TP {self.subject} ({self.number})"
        return f"{self.school_class.name} - {self.subject} ({self.number})"

class Assignment:
    def __init__(self, teacher, timeslot):
        self.teacher = teacher
        self.timeslot = timeslot
    def __repr__(self):
        return f"{self.teacher} @ {self.timeslot}"

class Schedule:
    def __init__(self):
        self.assignments = {}

    def assign(self, session, assignment):
        self.assignments[session] = assignment

    def unassign(self, session):
        self.assignments.pop(session, None)

    def is_assigned(self, session):
        return session in self.assignments

    def get_assignment(self, session):
        return self.assignments.get(session)




class School:
    def __init__(self,slot_preferences=None):
        self.teachers = []
        self.classes = []
        self.subjects = []
        self.timeslots = []
        self.sessions = []
        self.schedule = Schedule()
        self.slot_preferences = slot_preferences or {}
        self.strict_subject_slots = False
        self.nodes = 0
        self.max_nodes = 1000000

    def generate_sessions(self):
        self.sessions = []
        for school_class in self.classes:
            for subject, hours in school_class.required_hours.items():
                for i in range(hours):
                    self.sessions.append(
                        Session(school_class, subject, i + 1)
                    )

            tp_pairs = getattr(school_class, "tp_pairs", []) or []
            for tp_idx, tp in enumerate(tp_pairs):
                subj1 = tp.get("subj1")
                subj2 = tp.get("subj2")
                count = tp.get("count", 0)
                if not subj1 or not subj2 or count <= 0:
                    continue
                for k in range(count):
                    tp_group_id = f"TP_{school_class.name}_{subj1}_{subj2}_{tp_idx}_{k}"
                    # Slot 0 of TP (period P)
                    s_a1 = Session(school_class, subj1, f"TP_{tp_idx+1}_{k+1}_A1", is_tp=True, tp_group_id=tp_group_id, tp_slot_index=0, tp_partner_subject=subj2)
                    s_b1 = Session(school_class, subj2, f"TP_{tp_idx+1}_{k+1}_B1", is_tp=True, tp_group_id=tp_group_id, tp_slot_index=0, tp_partner_subject=subj1)
                    # Slot 1 of TP (period P+1)
                    s_a2 = Session(school_class, subj1, f"TP_{tp_idx+1}_{k+1}_A2", is_tp=True, tp_group_id=tp_group_id, tp_slot_index=1, tp_partner_subject=subj2)
                    s_b2 = Session(school_class, subj2, f"TP_{tp_idx+1}_{k+1}_B2", is_tp=True, tp_group_id=tp_group_id, tp_slot_index=1, tp_partner_subject=subj1)
                    self.sessions.extend([s_a1, s_b1, s_a2, s_b2])

        return self.sessions

    def get_subject_preferred_slots(self, subject):
        return self.slot_preferences.get(subject, [])

    def get_subject_max_per_day(self, subject):
        for subj in self.subjects:
            if subj.name == subject:
                return subj.max_per_day
        return 7

    def get_subject_min_per_day(self, subject):
        for subj in self.subjects:
            if subj.name == subject:
                return subj.min_per_day
        return 0

    def subject_day_periods(self, school_class, subject, day, schedule):
        return sorted(
            assignment.timeslot.period
            for session, assignment in schedule.assignments.items()
            if session.school_class == school_class
            and session.subject == subject
            and assignment.timeslot.day == day
        )

    def subject_day_meets_min_per_day(self, school_class, subject, day, schedule):
        """
        If min_per_day > 0: the day must have 0 sessions or at least min_per_day
        consecutive sessions for this (class, subject).
        """
        min_per_day = self.get_subject_min_per_day(subject)
        if min_per_day <= 0:
            return True

        periods = self.subject_day_periods(school_class, subject, day, schedule)
        count = len(periods)
        if count == 0:
            return True
        if count < min_per_day:
            return False
        return periods[-1] - periods[0] + 1 == count

    def validate_min_per_day_schedule(self, schedule):
        """Return list of human-readable violations for min_per_day rules."""
        violations = []
        checked = set()
        for session, assignment in schedule.assignments.items():
            key = (session.school_class.name, session.subject, assignment.timeslot.day)
            if key in checked:
                continue
            checked.add(key)
            school_class = session.school_class
            subject = session.subject
            day = assignment.timeslot.day
            min_per_day = self.get_subject_min_per_day(subject)
            if min_per_day <= 0:
                continue
            periods = self.subject_day_periods(school_class, subject, day, schedule)
            count = len(periods)
            if count == 0:
                continue
            if count < min_per_day:
                violations.append(
                    f"{school_class.name} / {subject} / {day} : "
                    f"{count} cours (< minimum {min_per_day})"
                )
            elif periods[-1] - periods[0] + 1 != count:
                violations.append(
                    f"{school_class.name} / {subject} / {day} : "
                    f"les cours ne sont pas consécutifs"
                )
        return violations

    def is_subject_slot_allowed(self, subject, timeslot):
        preferred = self.get_subject_preferred_slots(subject)
        if not preferred:
            return True
        return timeslot in preferred
        
    def generate_domains(self, strict_subject_slots=None):

        if strict_subject_slots is None:
            strict_subject_slots = self.strict_subject_slots

        domains = {}

        for session in self.sessions:

            domains[session] = []

            allowed_teacher_ids = (
                    session.school_class.allowed_teachers[session.subject]
                )

            for teacher in self.teachers:

                    # matière
                if session.subject not in teacher.subjects:
                    continue

                # classe autorisée
                if teacher.id not in allowed_teacher_ids:
                    continue

                for timeslot in self.timeslots:

                        # indisponibilité
                    if timeslot in teacher.unavailable_slots:
                            continue

                    if strict_subject_slots and not self.is_subject_slot_allowed(
                        session.subject, timeslot
                    ):
                        continue

                    domains[session].append(
                            (teacher, timeslot)
                        )
        
        return domains
    def get_unassigned_sessions(self):
        return [
        s for s in self.sessions
        if not self.schedule.is_assigned(s)
        ]
    
    ##check if its consistent
    def is_consistent(self, session, teacher, timeslot):
        for other_session, assignment in self.schedule.assignments.items():

            if (
                assignment.teacher == teacher
                and assignment.timeslot == timeslot
            ):
                return False
            if (
                other_session.school_class == session.school_class
                and assignment.timeslot == timeslot
            ):
                return False

        hours = 0
        for assignment in self.schedule.assignments.values():
            if assignment.teacher == teacher:
                hours += 1
        if hours >= teacher.required_hours:
            return False

        teachers_used = set()
        for other_session, assignment in self.schedule.assignments.items():
            if (
                other_session.school_class == session.school_class
                and other_session.subject == session.subject
            ):
                teachers_used.add(assignment.teacher.id)
        teachers_used.add(teacher.id)

        limit = session.school_class.max_teachers[session.subject]
        if len(teachers_used) > limit:
            return False

        # ── AJOUT ──────────────────────────────────────────────────────
        subject_count = sum(
            1 for other_session, assignment in self.schedule.assignments.items()
            if other_session.school_class == session.school_class
            and other_session.subject == session.subject
            and assignment.timeslot.day == timeslot.day
        )
        max_per_day = self.get_subject_max_per_day(session.subject)
        if subject_count >= max_per_day:
            return False

        if timeslot in teacher.unavailable_slots:
            return False

        if not self.is_subject_slot_allowed(session.subject, timeslot):
            return False

        return True
    
    def select_unassigned_variable(self, domains):
        unassigned = self.get_unassigned_sessions()

        best_session = None
        best_score = (float("inf"), float("inf"))

        for session in unassigned:
            domain_size = sum(
            1
            for teacher, timeslot in domains.get(session, [])
            if self.is_consistent(session, teacher, timeslot)
            )

            # degré = combien de contraintes avec autres sessions (approx)
            degree = sum(
                1 for s in unassigned
                if s.school_class == session.school_class
                or s.subject == session.subject
            )

            score = (domain_size, -degree)  # MRV puis degree

            if score < best_score:
                best_score = score
                best_session = session

        return best_session
    
    def forward_check(self, domains, session, teacher, timeslot):

        new_domains = {}

        for s, domain in domains.items():

            # copie du domaine
            new_domains[s] = domain.copy()

            # variable déjà assignée
            if s == session:
                continue

            filtered = []

            for t, ts in domain:
                hours_used = sum(
                1 for a in self.schedule.assignments.values()
                if a.teacher == t
                 )
                if hours_used >= t.required_hours:
                    continue
                # même prof au même créneau
                if t == teacher and ts == timeslot:
                    continue

                # même classe au même créneau
                if (
                    s.school_class == session.school_class
                    and ts == timeslot
                ):
                    continue
                subject_count = sum(
                    1
                    for other_session, assignment
                    in self.schedule.assignments.items()
                    if other_session.school_class == s.school_class
                    and other_session.subject == s.subject
                    and assignment.timeslot.day == ts.day
                )

                max_per_day = self.get_subject_max_per_day(s.subject)
                if subject_count >= max_per_day:
                    continue

                filtered.append((t, ts))

            new_domains[s] = filtered

            # domaine vide => dead end
            if (
                not self.schedule.is_assigned(s)
                and len(filtered) == 0
            ):
                return None
        for teacher in self.teachers:

            assigned = sum(
            1
            for a in self.schedule.assignments.values()
            if a.teacher == teacher
             )

            possible_remaining = 0

            for s in new_domains:

                if self.schedule.is_assigned(s):
                    continue

                for t, ts in new_domains.get(s, []):

                    if t == teacher:
                        possible_remaining += 1
                        break

            if assigned + possible_remaining < teacher.required_hours:
                return None
        for school_class in self.classes:

            for subject in school_class.required_hours:

                remaining_sessions = 0

                for s in self.get_unassigned_sessions():

                    if (
                        s.school_class == school_class
                        and s.subject == subject
                    ):
                        remaining_sessions += 1

                available_positions = 0

                for day in {ts.day for ts in self.timeslots}:

                    already_used = sum(
                        1
                        for other_session, assignment
                        in self.schedule.assignments.items()
                        if other_session.school_class == school_class
                        and other_session.subject == subject
                        and assignment.timeslot.day == day
                    )

                    available_positions += max(
                        0,
                        5 - already_used
                    )

                if remaining_sessions > available_positions:
                    return None
        return new_domains
    
    def order_domain_values(self, session, domains):

        scored_values = []

        for teacher, timeslot in domains[session]:

            if not self.is_consistent(session, teacher, timeslot):
                continue

            preference_penalty = 0

            # Préférence matière
            preferred = self.slot_preferences.get(
                session.subject,
                []
            )

            if preferred and timeslot not in preferred:
                preference_penalty += 100

            # Préférence prof
            if (
                teacher.preferred_slots
                and timeslot not in teacher.preferred_slots
            ):
                preference_penalty += 20

            # LCV
            removed = 0

            for other_session in self.get_unassigned_sessions():

                if other_session == session:
                    continue

                for t, ts in domains.get(other_session, []):

                    if t == teacher and ts == timeslot:
                        removed += 1

                    elif (
                        other_session.school_class == session.school_class
                        and ts == timeslot
                    ):
                        removed += 1

            scored_values.append(
                (
                    preference_penalty,
                    removed,
                    (teacher, timeslot)
                )
            )

        scored_values.sort(
            key=lambda x: (x[0], x[1])
        )

        return [
            value
            for _, _, value in scored_values
        ]
    def check_required_hours(self):

        teacher_hours = {}

        for assignment in self.schedule.assignments.values():
            t = assignment.teacher
            teacher_hours[t] = teacher_hours.get(t, 0) + 1

        for teacher in self.teachers:

            if teacher_hours.get(teacher, 0) != teacher.required_hours:
                return False

        return True
    
    def backtrack(self, domains):
        self.nodes += 1
        if self.nodes > self.max_nodes:
            return False
        if self.nodes % 10000 == 0:
             print("Nodes explored:", self.nodes)

        # 1. si tout est assigné → succès
        if len(self.schedule.assignments) == len(self.sessions):
            return self.check_required_hours()

        # 2. choisir une session non assignée (MRV)
        session = self.select_unassigned_variable(domains)

        if session is None:
            return False

        # 3. essayer chaque (teacher, timeslot)
        for teacher, timeslot in self.order_domain_values(
    session,
    domains):

            # 4. vérifier contraintes
            if self.is_consistent(session, teacher, timeslot):

                # 5. assigner
                self.schedule.assign(
                    session,
                    Assignment(teacher, timeslot)
                )

                new_domains = self.forward_check(
                    domains,
                    session,
                    teacher,
                    timeslot
                )

                if new_domains is not None:
                    if self.backtrack(new_domains):
                        return True

                self.schedule.unassign(session)

        # 8. échec
        return False




    def compute_cost(self, schedule):
        cost = 0

        for session, assignment in schedule.assignments.items():
            teacher = assignment.teacher
            timeslot = assignment.timeslot

            # Préférence matière → créneau (hard when strict; huge penalty as safety net)
            preferred = self.get_subject_preferred_slots(session.subject)
            if preferred and timeslot not in preferred:
                cost += 100000

            # Préférence personnelle du prof
            if teacher.preferred_slots and timeslot not in teacher.preferred_slots:
                cost += 3

        cost += self.compute_gaps_penalty(schedule)
        cost += self.compute_block_penalty(schedule)
        

        return cost

    # ─── TROUS DANS LE PLANNING D'UN PROF ──────────────────────────────────────

    def compute_gaps_penalty(self, schedule):
        """
        Pour chaque prof, par jour :
        si ses périodes sont [1, 4] → trous à 2 et 3 → pénalité 2
        """
        cost = 0
        teacher_slots = {}  # { teacher: { day: [period, ...] } }

        for assignment in schedule.assignments.values():
            t = assignment.teacher
            ts = assignment.timeslot
            if t not in teacher_slots:
                teacher_slots[t] = {}
            if ts.day not in teacher_slots[t]:
                teacher_slots[t][ts.day] = []
            teacher_slots[t][ts.day].append(ts.period)

        for teacher, days in teacher_slots.items():
            for day, periods in days.items():
                periods.sort()
                for i in range(len(periods) - 1):
                    gap = periods[i+1] - periods[i] - 1  # nb de périodes vides entre deux cours
                    cost += gap * 5

        return cost

    # ─── BLOCS CONSÉCUTIFS PAR MATIÈRE ─────────────────────────────────────────

    def compute_block_penalty(self, schedule):
        """
        Pour chaque (classe, matière), par jour :
        on veut que les cours soient consécutifs.
        Si arabe a périodes [1,2,3] → parfait, coût 0
        Si arabe a périodes [1,3,5] → trous → pénalité
        """
        cost = 0
        # { (classe, sujet, jour): [period, ...] }
        blocks = {}

        for session, assignment in schedule.assignments.items():
            key = (session.school_class, session.subject, assignment.timeslot.day)
            if key not in blocks:
                blocks[key] = []
            blocks[key].append(assignment.timeslot.period)

        for key, periods in blocks.items():
            periods.sort()
            for i in range(len(periods) - 1):
                gap = periods[i+1] - periods[i] - 1
                cost += gap * 4  # poids plus faible que les trous prof

        return cost
    

    def compute_day_balance_penalty(self, schedule):

        cost = 0

        class_days = {}

        for session, assignment in schedule.assignments.items():

            cls = session.school_class
            day = assignment.timeslot.day

            if cls not in class_days:
                class_days[cls] = {}

            class_days[cls][day] = (
                class_days[cls].get(day, 0) + 1
            )

        for cls, days in class_days.items():

            counts = list(days.values())

            if len(counts) <= 1:
                continue

            cost += (max(counts) - min(counts)) * 5

        return cost

    # ─── OPÉRATEUR DE VOISINAGE ─────────────────────────────────────────────────

    def get_neighbors(self, schedule, max_violations=None, sample_size=100):
        import random
        neighbors = []
        items = list(schedule.assignments.items())
        
        pairs = [(i, j) for i in range(len(items)) for j in range(i+1, len(items))]
        sampled = random.sample(pairs, min(sample_size, len(pairs)))

        for i, j in sampled:
            session_a, assign_a = items[i]
            session_b, assign_b = items[j]

            if assign_a.timeslot == assign_b.timeslot:
                continue

            temp = Schedule()
            for k, (s, a) in enumerate(items):
                if k != i and k != j:
                    temp.assign(s, a)

            if (self.is_consistent_with(session_a, assign_a.teacher, assign_b.timeslot, temp) and
                self.is_consistent_with(session_b, assign_b.teacher, assign_a.timeslot, temp)):

                new_schedule = Schedule()
                for k, (s, a) in enumerate(items):
                    if k == i:
                        new_schedule.assign(s, Assignment(assign_a.teacher, assign_b.timeslot))
                    elif k == j:
                        new_schedule.assign(s, Assignment(assign_b.teacher, assign_a.timeslot))
                    else:
                        new_schedule.assign(s, a)

                if max_violations is not None:
                    if self._count_subject_violations(new_schedule) > max_violations:
                        continue

                neighbors.append(new_schedule)

        return neighbors

    def is_consistent_with(self, session, teacher, timeslot, schedule, ignore_subject_slot_allowed=False):
        for other_session, assignment in schedule.assignments.items():
            if other_session == session:
                continue
            if assignment.teacher == teacher and assignment.timeslot == timeslot:
                return False
            if other_session.school_class == session.school_class and assignment.timeslot == timeslot:
                return False

        teachers_used = set()
        for other_session, assignment in schedule.assignments.items():
            if other_session == session:
                continue
            if (other_session.school_class == session.school_class and
                    other_session.subject == session.subject):
                teachers_used.add(assignment.teacher.id)
        teachers_used.add(teacher.id)

        if len(teachers_used) > session.school_class.max_teachers[session.subject]:
            return False

        subject_count = sum(
            1 for other_session, assignment in schedule.assignments.items()
            if other_session != session
            and other_session.school_class == session.school_class
            and other_session.subject == session.subject
            and assignment.timeslot.day == timeslot.day
        )
        max_per_day = self.get_subject_max_per_day(session.subject)
        if subject_count >= max_per_day:
            return False

        if timeslot in teacher.unavailable_slots:
            return False

        if not ignore_subject_slot_allowed and not self.is_subject_slot_allowed(session.subject, timeslot):
            return False

        return True

    # ─── SIMULATED ANNEALING ────────────────────────────────────────────────────

    def simulated_annealing(self, T=100.0, cooling=0.9995, iterations=15000):
        import math, random

        current = self.schedule
        current_cost = self.compute_cost(current)
        current_violations = self._count_subject_violations(current)

        best = current
        best_cost = current_cost

        for i in range(iterations):
            max_viol = 0 if self.strict_subject_slots else current_violations
            neighbors = self.get_neighbors(current, max_violations=max_viol)
            if not neighbors:
                break

            candidate = random.choice(neighbors)
            candidate_cost = self.compute_cost(candidate)
            candidate_violations = self._count_subject_violations(candidate)

            delta = candidate_cost - current_cost
            violations_delta = candidate_violations - current_violations

            accept = False
            if violations_delta < 0:
                accept = True
            elif violations_delta == 0 and (
                delta < 0 or random.random() < math.exp(-delta / T)
            ):
                accept = True

            if accept:
                current = candidate
                current_cost = candidate_cost
                current_violations = candidate_violations

            if current_violations < self._count_subject_violations(best) or (
                current_violations == self._count_subject_violations(best)
                and current_cost < best_cost
            ):
                best = current
                best_cost = current_cost

            T *= cooling

            if self._count_subject_violations(best) == 0 and best_cost == 0:
                break

        self.schedule = best
        return best, best_cost

    def _count_subject_violations(self, schedule):
        count = 0
        for session, assignment in schedule.assignments.items():
            preferred = self.get_subject_preferred_slots(session.subject)
            if preferred and assignment.timeslot not in preferred:
                count += 1
        return count
    
    def solve(self, time_limit_seconds=600):
        from cp_sat_solver import solve_with_cp_sat

        schedule, message = solve_with_cp_sat(self, time_limit_seconds)
        if message:
            print(message)
        return schedule
    