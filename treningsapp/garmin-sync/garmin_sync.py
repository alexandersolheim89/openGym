#!/usr/bin/env python3
"""garmin-sync: henter aktiviteter fra Garmin Connect og legger dem inn i openGym.

Kommandoer:
  login     Logg inn på Garmin én gang (e-post, passord, ev. engangskode). Lagrer bare
            innloggingsnøkler i GARMIN_TOKENS, aldri passordet.
  once      Én synk nå. Med --dry-run vises hva som ville blitt lagt inn, uten å lagre.
            Med --days N hentes de siste N dagene (f.eks. etter at nye aktivitetstyper er lagt til).
  styrke    Viser hva Garmin har lagret om styrkeøktene de siste 30 dagene. Lagrer ingenting.
  run       Synk med jevne mellomrom (standard for containeren).

Øktene skrives i samme form som openGyms egen CSV-import lager (lib/import-csv.js): en økt per
aktivitet med én kondisjonsøvelse fra katalogen (tid, fart, puls). Id-en er "gm<aktivitets-id>",
så samme aktivitet aldri legges inn to ganger, og en økt du sletter i appen kommer ikke tilbake.
"""
import base64
import getpass
import json
import os
import subprocess
import tempfile

import garmin_workouts
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

TOKENS = os.environ.get("GARMIN_TOKENS", "/tokens")
OPENGYM_URL = os.environ.get("OPENGYM_URL", "http://api:3000").rstrip("/")
OPENGYM_TOKEN = os.environ.get("GARMIN_SYNC_OPENGYM_TOKEN", "")
INTERVAL_MIN = int(os.environ.get("SYNC_INTERVAL_MIN", "30"))
FIRST_DAYS = int(os.environ.get("SYNC_FIRST_DAYS", "30"))
LOOKBACK_DAYS = int(os.environ.get("SYNC_LOOKBACK_DAYS", "3"))
STATE_FILE = os.path.join(TOKENS, "sync-state.json")

# Garmin-aktivitetstype -> (openGym-øvelse fra katalogen, navn hvis aktiviteten mangler navn)
# Styrke tas med som én økt med tid og puls (Garmins gjetning på øvelser er ikke til å stole på),
# men bare når openGym ikke allerede har en økt på samme tid.
TYPES = {
    "running": ("0685", "Løping"),
    "street_running": ("0685", "Løping"),
    "track_running": ("0685", "Løping"),
    "trail_running": ("0685", "Terrengløp"),
    "virtual_run": ("0685", "Løping"),
    "treadmill_running": ("2197", "Mølle"),
    "indoor_running": ("2197", "Mølle"),
    "walking": ("2258", "Gåtur"),
    "casual_walking": ("2258", "Gåtur"),
    "speed_walking": ("2258", "Gåtur"),
    "hiking": ("2258", "Fottur"),
    "cycling": ("5214", "Sykkel"),
    "road_biking": ("5214", "Sykkel"),
    "mountain_biking": ("5214", "Terrengsykkel"),
    "gravel_cycling": ("5214", "Sykkel"),
    "indoor_cycling": ("2138", "Innesykkel"),
    "virtual_ride": ("2138", "Innesykkel"),
    "e_bike_fitness": ("5214", "El-sykkel"),
    "e_bike_mountain": ("5214", "El-terrengsykkel"),
    "indoor_rowing": ("1161", "Romaskin"),
    "elliptical": ("2141", "Crosstrainer"),
    "strength_training": ("gmstrength", "Styrke"),
    "indoor_climbing": ("gmclimb", "Inneklatring"),
    "bouldering": ("gmclimb", "Buldring"),
    "rock_climbing": ("gmclimb", "Klatring"),
}

# Merkes i notatet, så el-sykkel skiller seg fra vanlig sykkel i historikken.
TAGS = {"e_bike_fitness": "el-sykkel", "e_bike_mountain": "el-sykkel"}

# Øvelser som ikke finnes i openGyms katalog. Lages som egne øvelser første gang de trengs.
CUSTOM = {
    "gmclimb": {"id": "gmclimb", "n": "Klatring", "bp": "cardio", "eq": "body weight",
                "desc": "Lagt til av garmin-sync for klatreøkter fra Garmin.", "custom": True},
    "gmstrength": {"id": "gmstrength", "n": "Styrkeøkt (Garmin)", "bp": "cardio", "eq": "body weight",
                   "desc": "Styrkeøkt registrert på Garmin-klokka, med tid og puls. Legg gjerne inn "
                           "øvelsene du gjorde ved å redigere økta.", "custom": True},
}


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


# ---------------------------------------------------------------- Garmin

def garmin_client():
    from garminconnect import Garmin
    g = Garmin()
    g.login(TOKENS)
    return g


def cmd_login():
    from garminconnect import Garmin
    os.makedirs(TOKENS, mode=0o700, exist_ok=True)
    email = input("Garmin e-post: ").strip()
    password = getpass.getpass("Garmin passord (vises ikke): ")
    g = Garmin(email, password, prompt_mfa=lambda: input("Engangskode fra Garmin (e-post/SMS): ").strip())
    g.login(TOKENS)
    g.client.dump(TOKENS)
    name = g.get_full_name() if hasattr(g, "get_full_name") else ""
    log(f"Innlogget på Garmin{(' som ' + name) if name else ''}. Nøklene ligger i {TOKENS}.")


# ---------------------------------------------------------------- konvertering

def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _fmt_dur(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _comma(x, d=1):
    return f"{x:.{d}f}".replace(".", ",")


CATEGORY_NO = {
    "BENCH_PRESS": "Benkpress", "ROW": "Roing", "CURL": "Curl", "SQUAT": "Knebøy", "LUNGE": "Utfall",
    "PULL_UP": "Pull-ups", "PUSH_UP": "Push-ups", "DEADLIFT": "Markløft", "SHOULDER_PRESS": "Skulderpress",
    "LATERAL_RAISE": "Sidehev", "PLANK": "Planke", "TRICEPS_EXTENSION": "Triceps", "SHRUG": "Skuldertrekk",
    "CALF_RAISE": "Tåhev", "CRUNCH": "Situps", "HIP_RAISE": "Hoftehev", "LEG_CURL": "Lårcurl",
    "CHEST_FLY": "Flyes", "HIP_STABILITY": "Hofte", "CARRY": "Bæring", "CORE": "Kjerne",
}


def strength_summary(sets_data):
    """Garmins sett for en styrkeøkt, oppsummert per øvelse i rekkefølge: 'Benkpress 5 sett 30–45 kg'."""
    groups = {}
    for st in (sets_data or {}).get("exerciseSets") or []:
        if st.get("setType") != "ACTIVE":
            continue
        ex = st.get("exercises") or [{}]
        cat = (max(ex, key=lambda x: x.get("probability") or 0) or {}).get("category") or "UNKNOWN"
        if cat == "UNKNOWN":
            continue
        gr = groups.setdefault(cat, {"n": 0, "kg": [], "reps": []})
        gr["n"] += 1
        kg = (_num(st.get("weight")) or 0) / 1000
        if kg > 0:
            gr["kg"].append(kg)
        if _num(st.get("repetitionCount")):
            gr["reps"].append(int(_num(st.get("repetitionCount"))))
    parts = []
    for cat, gr in groups.items():
        name = CATEGORY_NO.get(cat, cat.replace("_", " ").capitalize())
        t = f"{name} {gr['n']} sett"
        if gr["reps"]:
            lo, hi = min(gr["reps"]), max(gr["reps"])
            t += f" × {lo}" if lo == hi else f" × {lo}–{hi}"
        if gr["kg"]:
            lo, hi = min(gr["kg"]), max(gr["kg"])
            t += f" {_comma(lo, 1).replace(',0', '')} kg" if lo == hi else f" {_comma(lo, 1).replace(',0', '')}–{_comma(hi, 1).replace(',0', '')} kg"
        parts.append(t)
    return ("Garmin gjettet: " + " · ".join(parts)) if parts else ""


def to_workout(a, strength_sets=None, routines=None):
    """En Garmin-aktivitet -> en openGym-økt, eller None for typer vi ikke tar med."""
    type_key = (a.get("activityType") or {}).get("typeKey") or ""
    if type_key not in TYPES:
        return None
    ex_id, fallback = TYPES[type_key]
    act_id = a.get("activityId")
    dur = _num(a.get("duration")) or _num(a.get("movingDuration"))
    local, gmt = a.get("startTimeLocal"), a.get("startTimeGMT")
    if not act_id or not dur or dur <= 0 or not local or not gmt:
        return None
    start = int(datetime.strptime(gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    day = local[:10]
    km = (_num(a.get("distance")) or 0) / 1000
    minutes = round(dur / 60, 2)
    speed = round(km / (dur / 3600), 1) if km > 0 else 0
    s = {"min": minutes, "speed": speed, "done": True}
    avg, mx = _num(a.get("averageHR")), _num(a.get("maxHR"))
    if avg and mx:
        s["hr"] = {"avg": round(avg), "max": round(mx)}

    bits = ["Fra Garmin"] + ([TAGS[type_key]] if type_key in TAGS else [])
    if km > 0:
        bits.append(f"{_comma(km, 2)} km")
    bits.append(_fmt_dur(dur))
    if km > 0 and ex_id in ("0685", "2197", "2258"):
        bits.append(f"{_fmt_dur(dur / km)} /km")
    if ex_id == "gmstrength":
        summary = strength_summary(strength_sets)
        bits.append(summary or "øvelser ikke registrert")
        bits.append("rediger økta for å legge inn øvelsene")
    if avg:
        bits.append(f"snittpuls {round(avg)}")
    if _num(a.get("elevationGain")):
        bits.append(f"{round(_num(a.get('elevationGain')))} høydemeter")
    if _num(a.get("calories")):
        bits.append(f"{round(_num(a.get('calories')))} kcal")

    if ex_id == "gmstrength":
        name = (a.get("activityName") or "").strip()
        routine = next((r for r in routines or [] if (r.get("name") or "").strip().lower() == name.lower()), None)
        entries = garmin_workouts.activity_to_entries(strength_sets, routine)
        if entries:
            extra = [b for b in bits if b.startswith("snittpuls") or b.endswith("kcal")]
            return {
                "id": f"gm{act_id}", "d": day, "start": start, "end": start + int(round(dur * 1000)),
                "routineId": routine.get("id") if routine else None,
                "name": (routine or {}).get("name") or name or "Styrke",
                "entries": entries, "prs": [],
                "vol": round(sum((x["w"] or 0) * (x["r"] or 0) for e in entries for x in e["sets"]), 1),
                "note": " · ".join(["Logget på Garmin-klokka"] + extra),
                "_strength": True,
            }
    return {
        "id": f"gm{act_id}",
        "d": day,
        "start": start,
        "end": start + int(round(dur * 1000)),
        "routineId": None,
        "name": (a.get("activityName") or fallback).strip()[:80],
        "entries": [{"id": ex_id, "sets": [s], "topW": None}],
        "prs": [],
        "vol": 0,
        "note": " · ".join(bits),
        **({"_strength": True} if ex_id == "gmstrength" else {}),
    }


# ---------------------------------------------------------------- dagsform

# Garmin-tall som legges inn som egne felt under Kroppsmål i openGym (én innsjekk per dag).
HEALTH_FIELDS = [
    ("gm_rhr", "Hvilepuls"),
    ("gm_hrv", "HRV natt (ms)"),
    ("gm_sleep", "Søvn (timer)"),
    ("gm_sleepscore", "Søvnscore"),
    ("gm_bb", "Body Battery morgen"),
    ("gm_steps", "Skritt"),
    ("gm_vo2", "VO2max"),
]
HEALTH_DAYS = int(os.environ.get("HEALTH_DAYS", "2"))
# openGyms innebygde kroppsmål. En ny dag får dem tomme, slik appen selv lager en innsjekk.
MEASUREMENT_KEYS = ["neck", "shoulders", "chest", "waist", "hips", "abdomen", "upperArmLeft",
                    "upperArmRight", "forearmLeft", "forearmRight", "wristLeft", "wristRight",
                    "thighLeft", "thighRight", "calfLeft", "calfRight", "ankleLeft", "ankleRight", "bodyFat"]


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def _first(*vals):
    for v in vals:
        n = _num(v)
        if n is not None and n > 0:
            return n
    return None


def health_for_day(g, day):
    """Dagsformen for én dag fra Garmin, som {felt-id: verdi}. Mangler et tall, er det utelatt."""
    out = {}
    st = _try(g.get_stats, day) or {}
    out["gm_rhr"] = _first(st.get("restingHeartRate"))
    out["gm_steps"] = _first(st.get("totalSteps"))
    out["gm_bb"] = _first(st.get("bodyBatteryAtWakeTime"), st.get("bodyBatteryHighestValue"))
    hrv = _try(g.get_hrv_data, day) or {}
    out["gm_hrv"] = _first((hrv.get("hrvSummary") or {}).get("lastNightAvg"))
    sl = (_try(g.get_sleep_data, day) or {}).get("dailySleepDTO") or {}
    secs = _first(sl.get("sleepTimeSeconds"))
    out["gm_sleep"] = round(secs / 3600, 1) if secs else None
    out["gm_sleepscore"] = _first(((sl.get("sleepScores") or {}).get("overall") or {}).get("value"))
    mm = _try(g.get_max_metrics, day)
    rows = mm if isinstance(mm, list) else [mm] if isinstance(mm, dict) else []
    for r in rows:
        gen = (r or {}).get("generic") or {}
        v = _first(gen.get("vo2MaxPreciseValue"), gen.get("vo2MaxValue"))
        if v:
            out["gm_vo2"] = round(v, 1)
    return {k: (round(v) if k in ("gm_rhr", "gm_steps", "gm_bb", "gm_sleepscore", "gm_hrv") else v)
            for k, v in out.items() if v is not None}


def merge_health(state, days, created):
    """Legger dagsformen inn under Kroppsmål. Endrer bare Garmin-feltene, aldri dine egne mål.
    Et felt du har slettet i appen lages ikke igjen (`created` husker hvilke vi har laget)."""
    custom = state.get("customMeasurements") or []
    have = {c.get("id") for c in custom}
    fields = []
    for fid, name in HEALTH_FIELDS:
        if fid in have:
            fields.append(fid)
        elif fid not in created:
            custom.append({"id": fid, "name": name, "enabled": True, "unit": "none"})
            created.append(fid)
            fields.append(fid)
    state["customMeasurements"] = custom
    names = dict(HEALTH_FIELDS)
    gone = set(((state.get("deleted") or {}).get("measurements") or {}).keys())
    entries = state.get("measurements") or []
    by_day = {e.get("d"): e for e in entries if isinstance(e, dict)}
    now = int(time.time() * 1000)
    changed = []
    for day, vals in days.items():
        vals = {k: v for k, v in vals.items() if k in fields}
        if not vals or day in gone:
            continue
        e = by_day.get(day)
        if e is None:
            e = {"d": day, "t": now, **{k: None for k in MEASUREMENT_KEYS}, "other": []}
            entries.append(e)
            by_day[day] = e
        other = {o.get("id"): o for o in e.get("other") or [] if isinstance(o, dict)}
        diff = False
        for k, v in vals.items():
            if (other.get(k) or {}).get("value") != v:
                other[k] = {"id": k, "name": names[k], "value": v, "unit": "none"}
                diff = True
        if diff:
            e["other"] = list(other.values())
            e["t"] = now
            changed.append(day)
    if changed:
        state["measurements"] = sorted(entries, key=lambda e: (e.get("d") or "", e.get("t") or 0))
        state["_ts"] = max(now, int(state.get("_ts") or 0) + 1)
    return changed


def weights_from_garmin(g, start, end):
    """Innveiinger fra Garmin (lagt inn på klokka, i appen eller fra vekt): [(dag, kl, kg, id)]."""
    data = _try(g.get_body_composition, start, end) or {}
    out = []
    for r in data.get("dateWeightList") or []:
        grams, day = _num(r.get("weight")), r.get("calendarDate")
        if not grams or not day:
            continue
        local = _num(r.get("date"))  # Garmin gir tidspunktet i lokal tid som ms
        at = datetime.fromtimestamp(local / 1000, tz=timezone.utc).strftime("%H:%M") if local else None
        out.append((day, at, round(grams / 1000, 1), f"gmw{r.get('samplePk') or day}"))
    return out


def merge_weights(state, readings):
    """Legger Garmin-innveiinger i vektgrafen. En dag der du har veid deg i openGym selv, lar den være,
    og en dag du har slettet i appen, kommer ikke tilbake."""
    bw = state.get("bodyweight") or []
    by_day = {e.get("d"): e for e in bw if isinstance(e, dict)}
    gone = set(((state.get("deleted") or {}).get("bodyweight") or {}).keys())
    lb = state.get("unit") == "lb"
    now = int(time.time() * 1000)
    changed = []
    for day, at, kg, rid in readings:
        w = round(kg * 2.2046226218, 1) if lb else kg
        e = by_day.get(day)
        if e is None:
            if day in gone:
                continue
            r = {"id": rid, "at": at, "w": w, "t": now, "src": "garmin"}
            e = {"d": day, "w": w, "t": now, "ins": [r], "src": "garmin"}
            bw.append(e)
            by_day[day] = e
            changed.append(day)
            continue
        ins = e.get("ins") if isinstance(e.get("ins"), list) else None
        mine = [x for x in ins or [] if isinstance(x, dict) and x.get("id") == rid]
        only_garmin = ins and all(isinstance(x, dict) and x.get("src") == "garmin" for x in ins)
        if mine and only_garmin and mine[0].get("w") != w:
            mine[0].update({"w": w, "at": at, "t": now})
            if len(ins) == 1:
                e["w"], e["t"] = w, now
            changed.append(day)
    if changed:
        state["bodyweight"] = sorted(bw, key=lambda e: e.get("d") or "")
        state["_ts"] = max(now, int(state.get("_ts") or 0) + 1)
    return changed


# ---------------------------------------------------------------- openGym

def og(method, path, body=None):
    req = urllib.request.Request(OPENGYM_URL + path, method=method,
                                 headers={"Authorization": "Bearer " + OPENGYM_TOKEN,
                                          "Content-Type": "application/json"},
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def merge_into(state, workouts):
    """Legger nye økter inn i profilen. Hopper over de som finnes eller er slettet i appen."""
    have = {w.get("id") for w in state.get("workouts") or []}
    gone = set(((state.get("deleted") or {}).get("workouts") or {}).keys())
    known = {c.get("id") for c in state.get("customEx") or []}
    gone_ex = set(((state.get("deleted") or {}).get("customEx") or {}).keys())
    # En egen øvelse du har slettet i appen lages ikke igjen, og øktene som ville brukt den hoppes over.
    blocked = {i for i in CUSTOM if i in gone_ex and i not in known}
    # En styrkeøkt fra klokka hoppes over når du har logget en økt i openGym på samme tid
    # (overlapp, med 30 minutter slingringsmonn), så den ikke blir dobbel.
    own = [(int(w.get("start") or 0), int(w.get("end") or w.get("start") or 0))
           for w in state.get("workouts") or [] if not str(w.get("id", "")).startswith("gm")]
    slack = 30 * 60 * 1000
    def overlaps(w):
        return any(w["start"] - slack <= e and s <= w["end"] + slack for s, e in own)
    fresh = [w for w in workouts if w["id"] not in have and w["id"] not in gone
             and not any(e["id"] in blocked for e in w["entries"])
             and not (w.get("_strength") and overlaps(w))]
    for w in fresh:
        w.pop("_strength", None)
    # Vektforslagene i appen følger det tyngste settet sist øvelsen ble gjort, som ved import.
    exw = state.setdefault("exWeights", {})
    for w in fresh:
        for e in w["entries"]:
            mx = max([x.get("w") or 0 for x in e.get("sets") or []] + [e.get("topW") or 0])
            cur = exw.get(e["id"])
            if mx > 0 and (not isinstance(cur, dict) or w["d"] >= (cur.get("d") or "")):
                exw[e["id"]] = {"w": mx, "d": w["d"]}
    if fresh:
        need = {e["id"] for w in fresh for e in w["entries"]} & set(CUSTOM)
        add = [dict(CUSTOM[i], _ts=int(time.time() * 1000)) for i in sorted(need) if i not in known]
        if add:
            state["customEx"] = (state.get("customEx") or []) + add
        state["workouts"] = sorted((state.get("workouts") or []) + fresh,
                                   key=lambda w: (w.get("d") or "", w.get("start") or 0))
        state["_ts"] = max(int(time.time() * 1000), int(state.get("_ts") or 0) + 1)
    return fresh


def push(workouts, health=None, created=None, dry_run=False, weights=None):
    """Henter profilen, legger inn nye økter og dagsform, og lagrer. Prøver igjen ved konflikt."""
    st, got = og("GET", "/api/data")
    if st != 200:
        raise RuntimeError(f"openGym svarte {st} på GET /api/data: {got.get('error', '')}")
    for attempt in range(3):
        state = got.get("state")
        if not isinstance(state, dict):
            raise RuntimeError("profilen har ingen data på serveren ennå: åpne openGym og logg inn én gang")
        made = list(created or [])
        fresh = merge_into(state, workouts)
        days = merge_health(state, health or {}, made) if health else []
        wdays = merge_weights(state, weights) if weights else []
        if wdays:
            log(("VILLE LAGT INN" if dry_run else "Vekt fra Garmin:") + " " + ", ".join(wdays))
        if dry_run or not (fresh or days or wdays):
            return fresh, days, made
        body = {"state": state, "baseRev": got.get("rev") or 0}
        if isinstance(state.get("_wid"), str):
            body["baseWid"] = state["_wid"]
        st, res = og("PUT", "/api/data", body)
        if st == 200:
            return fresh, days, made
        if st == 409 and isinstance(res.get("state"), dict):
            got = {"state": res["state"], "rev": res.get("rev")}
            continue
        raise RuntimeError(f"openGym svarte {st} på PUT /api/data: {res.get('error', '')}")
    raise RuntimeError("profilen endret seg hele tiden under lagring; prøver igjen neste runde")


# ---------------------------------------------------------------- openGym -> Garmin

PUSH = os.environ.get("PUSH_TO_GARMIN", "1") == "1"
# Uten en Garmin-aktivitet å fylle inn lastes økta opp som egen aktivitet, men først når klokka
# har hatt tid til å synke (ellers kunne den samme økta kommet inn to ganger).
UPLOAD_AFTER_MIN = int(os.environ.get("UPLOAD_AFTER_MIN", "180"))
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "og_fit.mjs")


def is_strength(w, customs):
    cardio = {c.get("id") for c in customs if c.get("bp") == "cardio"}
    for e in w.get("entries") or []:
        if e.get("id") in cardio:
            continue
        for st in e.get("sets") or []:
            if st.get("done") and st.get("min") is None and (st.get("r") or st.get("sides") or st.get("sec")):
                return True
    return False


def to_garmin(w, state):
    """openGym-økta som Garmin-sett og .fit, via openGyms egen eksport (og_fit.mjs)."""
    inp = json.dumps({"workout": w, "customEx": state.get("customEx") or [], "unit": state.get("unit") or "kg"})
    out = subprocess.run(["node", HELPER], input=inp, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError("og_fit.mjs: " + (out.stderr.strip().splitlines() or ["ukjent feil"])[-1])
    res = json.loads(out.stdout)
    # Kondisjon (oppvarming på mølla) hører ikke hjemme blant styrkesettene i Garmin.
    sets, skip_rest = [], False
    for st in res["sets"]:
        cats = [e.get("category") for e in st.get("exercises") or []]
        if st["setType"] == "ACTIVE" and any(c in ("RUN", "CARDIO") for c in cats):
            skip_rest = True
            continue
        if st["setType"] == "REST" and skip_rest:
            skip_rest = False
            continue
        skip_rest = False
        sets.append(dict(st, messageIndex=len(sets)))
    return sets, base64.b64decode(res["fit"])


def garmin_window(a):
    gmt = a.get("startTimeGMT")
    if not gmt:
        return None
    s = int(datetime.strptime(gmt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    return s, s + int((_num(a.get("duration")) or 0) * 1000)


def push_strength(g, acts, st, dry_run=False):
    """Styrkeøkter logget i openGym sendes til Garmin: inn i klokkas aktivitet, eller som egen."""
    if not PUSH:
        return
    now = int(time.time() * 1000)
    since = st.setdefault("push_since", now)  # bare økter fra og med første kjøring, ikke historikken
    pushed = st.setdefault("pushed", {})
    code, got = og("GET", "/api/data")
    state = got.get("state") if code == 200 else None
    if not isinstance(state, dict):
        log(f"FEIL: fikk ikke lest openGym ({code}) for synk til Garmin")
        return
    customs = state.get("customEx") or []
    strength_acts = [(a, garmin_window(a)) for a in acts
                     if (a.get("activityType") or {}).get("typeKey") == "strength_training" and garmin_window(a)]
    slack = 30 * 60 * 1000
    for w in state.get("workouts") or []:
        wid = str(w.get("id", ""))
        start, end = int(w.get("start") or 0), int(w.get("end") or 0)
        if wid.startswith("gm") or wid in pushed or not end or end < since or not is_strength(w, customs):
            continue
        match = next((a for a, (s, e) in strength_acts if s - slack <= end and start <= e + slack), None)
        if not match and now - end < UPLOAD_AFTER_MIN * 60 * 1000:
            continue  # klokka kan fortsatt være på vei inn; prøv igjen senere
        sets, fit = to_garmin(w, state)
        name = w.get("name") or "Styrke"
        if match:
            aid = match.get("activityId")
            if dry_run:
                log(f"VILLE FYLT INN {len(sets)} sett fra «{name}» i Garmin-aktiviteten {match.get('activityName')}")
                continue
            g.set_activity_exercise_sets(aid, {"activityId": aid, "exerciseSets": sets})
            _try(g.set_activity_name, str(aid), name)
            pushed[wid] = {"garmin": aid, "at": now}
            log(f"Til Garmin: settene fra «{name}» er fylt inn i aktiviteten fra klokka")
        else:
            if dry_run:
                log(f"VILLE LASTET OPP «{name}» ({w.get('d')}) som egen styrkeaktivitet i Garmin")
                continue
            with tempfile.TemporaryDirectory() as d:
                path = os.path.join(d, f"opengym-{wid}.fit")
                with open(path, "wb") as f:
                    f.write(fit)
                g.upload_activity(path)
            pushed[wid] = {"garmin": "upload", "at": now}
            log(f"Til Garmin: «{name}» ({w.get('d')}) er lastet opp som styrkeaktivitet")


# ---------------------------------------------------------------- synk

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(s):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_FILE)


def sync_once(dry_run=False, days=None):
    if not OPENGYM_TOKEN:
        raise RuntimeError("GARMIN_SYNC_OPENGYM_TOKEN mangler i .env")
    st = load_state()
    days = days or (LOOKBACK_DAYS if st.get("last_ok") else FIRST_DAYS)
    end = datetime.now().date()
    start = end - timedelta(days=days)
    g = garmin_client()
    acts = g.get_activities_by_date(start.isoformat(), end.isoformat()) or []
    try:
        g.client.dump(TOKENS)  # fornyede nøkler tas vare på
    except Exception:
        pass
    code, prof = og("GET", "/api/data")
    og_state = prof.get("state") if code == 200 and isinstance(prof.get("state"), dict) else {}
    routines = og_state.get("routines") or []
    if PUSH and og_state:
        try:
            garmin_workouts.sync_routines(g, og_state, st, log, dry_run=dry_run)
        except Exception as e:
            log("FEIL ved sending av økter til Garmin:", e)
    workouts, skipped = [], {}
    for a in acts:
        sets_data = None
        if (a.get("activityType") or {}).get("typeKey") == "strength_training":
            sets_data = _try(g.get_activity_exercise_sets, a.get("activityId"))
        w = to_workout(a, sets_data, routines)
        if w:
            workouts.append(w)
        else:
            k = (a.get("activityType") or {}).get("typeKey") or "ukjent"
            skipped[k] = skipped.get(k, 0) + 1
    hdays = days if days else (HEALTH_DAYS if st.get("last_ok") else FIRST_DAYS)
    health = {}
    for i in range(hdays):
        d = (end - timedelta(days=i)).isoformat()
        vals = health_for_day(g, d)
        if vals:
            health[d] = vals
    weights = weights_from_garmin(g, (end - timedelta(days=hdays - 1)).isoformat(), end.isoformat())
    fresh, hchanged, made = push(workouts, health, st.get("health_fields_created") or [], dry_run=dry_run,
                                 weights=weights)
    if dry_run and health:
        last = max(health)
        log(f"Dagsform {last}: " + ", ".join(f"{dict(HEALTH_FIELDS)[k]} {v}" for k, v in health[last].items()))
    if hchanged:
        log(("VILLE OPPDATERT" if dry_run else "Dagsform oppdatert for") + f" {len(hchanged)} dager")
    if not dry_run:
        st["health_fields_created"] = made
    for w in fresh:
        log(("VILLE LAGT INN " if dry_run else "Lagt inn ") + f"{w['d']} {w['name']}: {w['note']}")
    log(f"{len(acts)} aktiviteter siste {days} dager, {len(fresh)} nye"
        + (f", hoppet over: {skipped}" if skipped else ""))
    try:
        push_strength(g, acts, st, dry_run=dry_run)
    except Exception as e:
        log("FEIL ved synk til Garmin:", e)
    if not dry_run:
        st["last_ok"] = datetime.now().isoformat(timespec="seconds")
        save_state(st)


def cmd_strength(days=30):
    """Viser hva Garmin har lagret om styrkeøktene (øvelser, reps, vekt). Lagrer ingenting."""
    g = garmin_client()
    end = datetime.now().date()
    acts = g.get_activities_by_date((end - timedelta(days=days)).isoformat(), end.isoformat()) or []
    strength = [a for a in acts if (a.get("activityType") or {}).get("typeKey") == "strength_training"]
    log(f"{len(strength)} styrkeøkter siste {days} dager")
    for a in strength:
        dur = _num(a.get("duration")) or 0
        print(f"\n== {a.get('startTimeLocal', '')[:16]}  {a.get('activityName') or ''}  "
              f"{_fmt_dur(dur)}  snittpuls {round(_num(a.get('averageHR')) or 0)}  "
              f"{round(_num(a.get('calories')) or 0)} kcal  aktive sett: {a.get('activeSets', '?')}", flush=True)
        try:
            data = g.get_activity_exercise_sets(a.get("activityId")) or {}
        except Exception as e:
            print("   (fikk ikke hentet settene:", e, ")")
            continue
        n = 0
        for st in data.get("exerciseSets") or []:
            if st.get("setType") != "ACTIVE":
                continue
            n += 1
            ex = (st.get("exercises") or [{}])
            best = max(ex, key=lambda x: x.get("probability") or 0) if ex else {}
            kg = (_num(st.get("weight")) or 0) / 1000
            print(f"   sett {n:2d}: {best.get('category') or '?'} / {best.get('name') or '-'}"
                  f"  reps {st.get('repetitionCount') if st.get('repetitionCount') is not None else '?'}"
                  f"  {_comma(kg, 1) + ' kg' if kg > 0 else 'ingen vekt'}"
                  f"  {_fmt_dur(_num(st.get('duration')) or 0)}")
        if not n:
            print("   (ingen sett lagret, bare tid og puls)")


def cmd_run():
    log(f"garmin-sync startet: hvert {INTERVAL_MIN}. minutt mot {OPENGYM_URL}")
    while True:
        try:
            sync_once()
        except Exception as e:  # en feil skal aldri stoppe tjenesten
            log("FEIL:", e)
        time.sleep(INTERVAL_MIN * 60)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "login":
        cmd_login()
    elif cmd == "once":
        days = None
        if "--days" in sys.argv:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        sync_once(dry_run="--dry-run" in sys.argv, days=days)
    elif cmd == "styrke":
        cmd_strength(int(sys.argv[2]) if len(sys.argv) > 2 else 30)
    elif cmd == "run":
        cmd_run()
    else:
        print(__doc__)
        sys.exit(2)
