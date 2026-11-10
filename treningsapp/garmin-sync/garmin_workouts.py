"""openGym-rutiner som Garmin-økter på klokka, og Garmin-økter tilbake som ekte openGym-økter.

Rutinene i openGym lastes opp som styrkeøkter i Garmin Connect og sendes til klokka. Velger du
en av dem på klokka (Styrke -> Økter), vet Garmin hvilken øvelse hvert sett er. Da kan
aktiviteten leses tilbake med riktige øvelser i openGym, i stedet for Garmins gjetning.
"""
import hashlib
import json

# openGym-øvelse (katalog-id) -> (Garmin-kategori, Garmin-øvelse, per side).
# Navnene er Garmins egne (garminconnect.exercises), sjekket ved oppstart.
EXERCISES = {
    "1760": ("SQUAT", "GOBLET_SQUAT", False),                      # goblet squat
    "0292": ("ROW", "ONE_ARM_BENT_OVER_ROW", True),                # enarms roing
    "0289": ("BENCH_PRESS", "DUMBBELL_BENCH_PRESS", False),        # benkpress manualer
    "1459": ("DEADLIFT", "ROMANIAN_DEADLIFT", False),              # rumensk markløft manualer
    "0294": ("CURL", "DUMBBELL_BICEPS_CURL", False),               # bicepscurl
    "2290": ("LUNGE", "DUMBBELL_BULGARIAN_SPLIT_SQUAT", True),     # bulgarsk utfall
    "5661": ("ROW", "CHEST_SUPPORTED_DUMBBELL_ROW", False),        # roing mot skråbenk
    "0549": ("HIP_RAISE", "KETTLEBELL_SWING", False),              # kettlebell-swing
    "0314": ("BENCH_PRESS", "INCLINE_DUMBBELL_BENCH_PRESS", False),  # skråbenk
    "2188": ("TRICEPS_EXTENSION", "SEATED_DUMBBELL_OVERHEAD_TRICEPS_EXTENSION", False),
    "0043": ("SQUAT", "BARBELL_BACK_SQUAT", False),                # knebøy stang
    "0861": ("ROW", "SEATED_CABLE_ROW", False),                    # sittende kabelroing
    "0085": ("DEADLIFT", "ROMANIAN_DEADLIFT", False),              # rumensk markløft stang
    "0198": ("PULL_UP", "LAT_PULLDOWN", False),                    # nedtrekk
    "0868": ("CURL", "CABLE_BICEPS_CURL", False),                  # kabelcurl
    "0739": ("SQUAT", "LEG_PRESS", False),                         # beinpress
    "0599": ("LEG_CURL", "LEG_CURL", False),                       # lårcurl
    "0203": ("ROW", "FACE_PULL", False),                           # kabelroing øvre rygg (tau)
    "0201": ("TRICEPS_EXTENSION", "TRICEPS_PRESSDOWN", False),     # triceps-pushdown
}
# Samme Garmin-øvelse brukes av flere openGym-øvelser (rumensk markløft med manualer og stang).
# Tilbake fra Garmin velges den som står i rutinen aktiviteten ble laget fra.
REVERSE = {}
for _og, (_c, _n, _s) in EXERCISES.items():
    REVERSE.setdefault((_c, _n), []).append(_og)

WARMUP_IDS = {"2197"}  # mølle: oppvarming, ikke et styrkesett


def check_catalog():
    """Øvelsesnavn Garmin ikke kjenner gir 400 ved opplasting. Gir navnene som mangler."""
    from garminconnect import exercises as E
    known = {(c, n) for _, c, n in E._RAW}
    return [f"{c}/{n}" for c, n, _ in EXERCISES.values() if (c, n) not in known]


def last_weight(state, ex_id):
    """Tyngste arbeidssett sist øvelsen ble gjort, som startvekt på klokka."""
    for w in sorted(state.get("workouts") or [], key=lambda w: (w.get("d") or "", w.get("start") or 0), reverse=True):
        for e in w.get("entries") or []:
            if e.get("id") != ex_id:
                continue
            ws = [s.get("w") for s in e.get("sets") or [] if s.get("done") and s.get("phase") != "warmup" and s.get("w")]
            ws += [x.get("w") for s in e.get("sets") or [] for x in (s.get("sides") or {}).values() if x.get("done") and x.get("w")]
            if ws:
                return float(max(ws))
    return None


def routine_steps(routine, state):
    """Rutinens øvelser som Garmin-steg (plain dicts). Øvelser uten Garmin-motstykke hoppes over."""
    from garminconnect.workout import (create_repeat_group, create_strength_exercise_step,
                                       create_strength_rest_step, create_warmup_step)
    steps, order, skipped = [], 1, []
    ex = [e for e in routine.get("ex") or []]
    i = 0
    while i < len(ex):
        e = ex[i]
        if e.get("id") in WARMUP_IDS or e.get("mode") == "cardio":
            mins = float(e.get("min") or 5)
            steps.append(create_warmup_step(mins * 60, order))
            order += 1
            i += 1
            continue
        group = [e]
        if e.get("sg"):
            while i + len(group) < len(ex) and ex[i + len(group)].get("sg") == e["sg"]:
                group.append(ex[i + len(group)])
        mapped = [g for g in group if g.get("id") in EXERCISES]
        skipped += [g.get("id") for g in group if g.get("id") not in EXERCISES]
        i += len(group)
        if not mapped:
            continue
        rest = int(e.get("sgRest") or e.get("restSec") or 90)
        same_sets = len({int(g.get("sets") or 3) for g in mapped}) == 1
        blocks = [mapped] if same_sets else [[g] for g in mapped]
        for block in blocks:
            group_order = order
            order += 1
            inner = []
            for g in block:
                cat, name, side = EXERCISES[g["id"]]
                lo, hi = g.get("repsMin"), int(g.get("reps") or 10)
                desc = (f"{lo}–{hi} reps" if lo and lo != hi else f"{hi} reps") + (" per side" if side else "")
                inner.append(create_strength_exercise_step(cat, order, hi, exercise_name=name,
                                                           weight_kg=last_weight(state, g["id"]),
                                                           description=desc))
                order += 1
            inner.append(create_strength_rest_step(rest, order))
            order += 1
            steps.append(create_repeat_group(int(block[0].get("sets") or 3), inner, group_order))
    return steps, skipped


def routine_workout(routine, state):
    """En openGym-rutine som Garmins StrengthWorkout, eller None hvis den ikke har styrkeøvelser."""
    from garminconnect.workout import StrengthWorkout, WorkoutSegment
    steps, skipped = routine_steps(routine, state)
    if not any(getattr(s, "workoutSteps", None) for s in steps):
        return None, skipped
    seg = WorkoutSegment(segmentOrder=1, sportType={"sportTypeId": 5, "sportTypeKey": "strength_training"},
                         workoutSteps=steps)
    return StrengthWorkout(workoutName=(routine.get("name") or "openGym")[:40], estimatedDurationInSecs=0,
                           workoutSegments=[seg]), skipped


def fingerprint(workout):
    return hashlib.sha1(json.dumps(workout.to_dict(), sort_keys=True, default=str).encode()).hexdigest()


def sync_routines(g, state, st, log, dry_run=False):
    """Laster opp nye og endrede rutiner som Garmin-økter, sletter de som er fjernet, og sender dem
    til klokka. Husker Garmin-id og innhold per rutine i `st`, så uendrede rutiner ikke røres."""
    known = st.setdefault("garmin_workouts", {})
    routines = {r.get("id"): r for r in state.get("routines") or [] if r.get("id")}
    changed = []
    for rid, r in routines.items():
        workout, skipped = routine_workout(r, state)
        if workout is None:
            continue
        fp = fingerprint(workout)
        cur = known.get(rid) or {}
        if cur.get("fp") == fp:
            continue
        if skipped:
            log(f"«{r.get('name')}»: {len(skipped)} øvelse(r) uten Garmin-motstykke er ikke med på klokka")
        if dry_run:
            log(f"VILLE SENDT «{r.get('name')}» til Garmin og klokka")
            continue
        if cur.get("id"):
            try:
                g.delete_workout(cur["id"])
            except Exception:
                pass
        res = g.upload_strength_workout(workout)
        wid = (res or {}).get("workoutId")
        if not wid:
            raise RuntimeError(f"Garmin ga ingen id for «{r.get('name')}»")
        try:
            g.push_workout_to_device(wid)
        except Exception as e:
            log(f"«{r.get('name')}» ligger i Garmin Connect, men ble ikke sendt til klokka: {e}")
        known[rid] = {"id": wid, "fp": fp, "name": r.get("name")}
        changed.append(r.get("name"))
    for rid in [k for k in known if k not in routines]:
        if dry_run:
            log(f"VILLE SLETTET «{known[rid].get('name')}» fra Garmin")
            continue
        try:
            g.delete_workout(known[rid]["id"])
        except Exception:
            pass
        known.pop(rid)
    if changed:
        log("Økter sendt til Garmin og klokka: " + ", ".join(changed))
    return changed


def activity_to_entries(sets_data, routine=None):
    """Settene fra en Garmin-aktivitet laget fra en openGym-økt, som openGym-øvelser.
    Gir None når settene ikke kan leses sikkert (Garmins gjetning, eller ukjente øvelser)."""
    in_routine = {e.get("id") for e in (routine or {}).get("ex") or []}
    active = [s for s in (sets_data or {}).get("exerciseSets") or [] if s.get("setType") == "ACTIVE"]
    if not active:
        return None
    entries, known = [], 0
    for s in active:
        ex = (s.get("exercises") or [{}])
        best = max(ex, key=lambda x: x.get("probability") or 0) if ex else {}
        key = (best.get("category"), best.get("name"))
        ids = REVERSE.get(key) or []
        og = next((i for i in ids if i in in_routine), ids[0] if ids else None)
        if not og:
            continue
        known += 1
        kg = round((float(s.get("weight") or 0)) / 1000, 2)
        reps = int(s.get("repetitionCount") or 0)
        row = {"w": kg, "r": reps, "done": True}
        # Supersett veksler mellom øvelsene på klokka; i openGym samles settene per øvelse.
        cur = next((x for x in entries if x["id"] == og), None)
        if cur:
            cur["sets"].append(row)
        else:
            entries.append({"id": og, "sets": [row]})
    # Garmins egen gjetning har bare kategori, ikke øvelse, og havner aldri her. Krev at nesten
    # alle settene ble kjent igjen, så en halvt gjettet økt ikke blir lest som ekte.
    if known < max(1, int(len(active) * 0.8)):
        return None
    sg = {e.get("id"): e.get("sg") for e in (routine or {}).get("ex") or [] if e.get("sg")}
    for e in entries:
        e["topW"] = max((x["w"] for x in e["sets"]), default=0) or None
        if sg.get(e["id"]):
            e["sg"] = sg[e["id"]]
    # Et supersett er øvelser som står ved siden av hverandre (som history.js cleanupSg krever).
    for i, e in enumerate(entries):
        if e.get("sg") and not any(entries[j].get("sg") == e["sg"] for j in (i - 1, i + 1) if 0 <= j < len(entries)):
            e.pop("sg")
    return entries
