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
import getpass
import json
import os
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
# Styrke hoppes over med vilje: den logges i openGym, og ville ellers blitt dobbel.
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


def to_workout(a):
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
    if avg:
        bits.append(f"snittpuls {round(avg)}")
    if _num(a.get("elevationGain")):
        bits.append(f"{round(_num(a.get('elevationGain')))} høydemeter")
    if _num(a.get("calories")):
        bits.append(f"{round(_num(a.get('calories')))} kcal")

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
    }


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
    fresh = [w for w in workouts if w["id"] not in have and w["id"] not in gone
             and not any(e["id"] in blocked for e in w["entries"])]
    if fresh:
        need = {e["id"] for w in fresh for e in w["entries"]} & set(CUSTOM)
        add = [dict(CUSTOM[i], _ts=int(time.time() * 1000)) for i in sorted(need) if i not in known]
        if add:
            state["customEx"] = (state.get("customEx") or []) + add
        state["workouts"] = sorted((state.get("workouts") or []) + fresh,
                                   key=lambda w: (w.get("d") or "", w.get("start") or 0))
        state["_ts"] = max(int(time.time() * 1000), int(state.get("_ts") or 0) + 1)
    return fresh


def push(workouts, dry_run=False):
    st, got = og("GET", "/api/data")
    if st != 200:
        raise RuntimeError(f"openGym svarte {st} på GET /api/data: {got.get('error', '')}")
    for attempt in range(3):
        state = got.get("state")
        if not isinstance(state, dict):
            raise RuntimeError("profilen har ingen data på serveren ennå: åpne openGym og logg inn én gang")
        fresh = merge_into(state, workouts)
        if not fresh or dry_run:
            return fresh
        body = {"state": state, "baseRev": got.get("rev") or 0}
        if isinstance(state.get("_wid"), str):
            body["baseWid"] = state["_wid"]
        st, res = og("PUT", "/api/data", body)
        if st == 200:
            return fresh
        if st == 409 and isinstance(res.get("state"), dict):
            got = {"state": res["state"], "rev": res.get("rev")}
            continue
        raise RuntimeError(f"openGym svarte {st} på PUT /api/data: {res.get('error', '')}")
    raise RuntimeError("profilen endret seg hele tiden under lagring; prøver igjen neste runde")


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
    workouts, skipped = [], {}
    for a in acts:
        w = to_workout(a)
        if w:
            workouts.append(w)
        else:
            k = (a.get("activityType") or {}).get("typeKey") or "ukjent"
            skipped[k] = skipped.get(k, 0) + 1
    fresh = push(workouts, dry_run=dry_run)
    for w in fresh:
        log(("VILLE LAGT INN " if dry_run else "Lagt inn ") + f"{w['d']} {w['name']}: {w['note']}")
    log(f"{len(acts)} aktiviteter siste {days} dager, {len(fresh)} nye"
        + (f", hoppet over: {skipped}" if skipped else ""))
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
