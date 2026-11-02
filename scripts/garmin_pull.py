"""Holt die jüngste Laufaktivität über die Garmin Personal API.

Verwendet curl – genau den Aufruf aus Garmins eigener Dokumentation.
Pythons eingebauter HTTP-Client wurde von Garmin mit 406 abgewiesen.

Schlüssel anlegen: connect.garmin.com/app/settings/personalApiToken
Als Secret GARMIN_API_KEY im Repository hinterlegen.
"""

import json, os, pathlib, datetime, subprocess, shlex, sys

SCHLUESSEL = os.environ.get("GARMIN_API_KEY", "").strip()
if not SCHLUESSEL:
    raise SystemExit("GARMIN_API_KEY fehlt – als Secret im Repository anlegen.")

TOKEN_URL = ("https://personal.connectapi.garmin.com/personalapitoken-service"
             "/personal-api/personal-api-key/token")


def curl(url, kopfzeilen=None, methode="GET"):
    """Ruft curl auf und gibt (Status, Text) zurück."""
    befehl = ["curl", "-s", "-S", "--max-time", "30",
              "-w", "\n__STATUS__%{http_code}", "-X", methode, url]
    for k in (kopfzeilen or []):
        befehl += ["-H", k]
    ergebnis = subprocess.run(befehl, capture_output=True, text=True)
    if ergebnis.returncode != 0:
        raise RuntimeError(f"curl: {ergebnis.stderr.strip()[:200]}")
    roh = ergebnis.stdout
    if "__STATUS__" in roh:
        text, status = roh.rsplit("__STATUS__", 1)
        return int(status.strip()), text.rstrip("\n")
    return 0, roh


def als_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


# ── 1. Schlüssel gegen Token tauschen ────────────────────────────────
status, text = curl(TOKEN_URL, ["Authorization: ApiKey " + SCHLUESSEL], "POST")
print(f"Token-Abruf: HTTP {status}, {len(text)} Zeichen")

if status != 200:
    raise SystemExit(f"Token-Abruf fehlgeschlagen.\n{text[:400]}")

daten = als_json(text)
token = None
if isinstance(daten, dict):
    token = (daten.get("access_token") or daten.get("accessToken")
             or daten.get("token") or daten.get("value"))
elif isinstance(daten, str):
    token = daten
else:
    token = text.strip().strip('"')

if not token:
    raise SystemExit("Kein Token in der Antwort: " + text[:300])

print(f"Token erhalten, {len(token)} Zeichen, beginnt mit {token[:8]}…")
KOPF = ["Authorization: Bearer " + token]

# ── 2. Aktivitätsliste finden ────────────────────────────────────────
# Garmin dokumentiert die Adresse nicht öffentlich. Deshalb werden mehrere
# Varianten versucht; die funktionierende steht danach im Log.
heute = datetime.date.today()
von = heute - datetime.timedelta(days=14)
spanne = f"?startDate={von}&endDate={heute}&start=0&limit=20"

KANDIDATEN = [
    "https://personal.connectapi.garmin.com/activity-service/activities",
    "https://personal.connectapi.garmin.com/activity-service/activities/search",
    "https://personal.connectapi.garmin.com/activity-service/activity/search",
    "https://personal.connectapi.garmin.com/personal-api/activities",
    "https://personal.connectapi.garmin.com/wellness-api/rest/activities",
    "https://apis.garmin.com/activity-service/activities",
]

liste, benutzt, roh_antwort = None, None, None
for basis in KANDIDATEN:
    url = basis + spanne
    try:
        st, txt = curl(url, KOPF)
    except Exception as e:
        print(f"  {basis} → {e}")
        continue
    if st != 200:
        print(f"  {basis} → HTTP {st}  {txt[:120]}")
        continue
    d = als_json(txt)
    if isinstance(d, list):
        liste, benutzt, roh_antwort = d, basis, txt
    elif isinstance(d, dict):
        for feld in ("activities", "activityList", "items", "content", "data"):
            if isinstance(d.get(feld), list):
                liste, benutzt, roh_antwort = d[feld], basis, txt
                break
        if liste is None:
            print(f"  {basis} → 200, aber keine Liste: {txt[:160]}")
            continue
    if liste is not None:
        print(f"  {basis} → {len(liste)} Einträge  ← diese Adresse funktioniert")
        break

if liste is None:
    raise SystemExit("Keine Aktivitätsliste gefunden. Log bitte weitergeben.")

# ── 3. Jüngsten Lauf herausziehen ────────────────────────────────────
def feld(d, *namen, standard=None):
    for n in namen:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return standard

def typ_von(a):
    t = feld(a, "activityType", "activityTypeDTO", standard="")
    if isinstance(t, dict):
        return str(t.get("typeKey") or t.get("typeId") or "")
    return str(t or "")

lauf = None
for a in liste:
    if "run" in typ_von(a).lower():
        lauf = a
        break

if lauf is None:
    print("Erster Eintrag zur Ansicht:", json.dumps(liste[0], ensure_ascii=False)[:800])
    raise SystemExit("Kein Lauf unter den letzten Aktivitäten gefunden.")

meter = float(feld(lauf, "distance", "distanceInMeters", standard=0) or 0)
sekunden = float(feld(lauf, "duration", "durationInSeconds", standard=0) or 0)
km = round(meter / 100) / 10
pace = round(sekunden / (meter / 1000)) if meter else 0
start = str(feld(lauf, "startTimeLocal", "startTime", "startTimeInSeconds", standard=""))

ergebnis = {
    "geholt": datetime.datetime.now(datetime.timezone.utc)
                 .isoformat(timespec="seconds").replace("+00:00", "Z"),
    "quelle": benutzt,
    "id": feld(lauf, "activityId", "summaryId"),
    "name": feld(lauf, "activityName", standard="") or "",
    "datum": start[:10] if "-" in start[:10] else heute.isoformat(),
    "km": km,
    "sek": int(sekunden),
    "paceSek": pace,
    "hr": int(float(feld(lauf, "averageHR", "averageHeartRateInBeatsPerMinute", standard=0) or 0)),
    "hrMax": int(float(feld(lauf, "maxHR", "maxHeartRateInBeatsPerMinute", standard=0) or 0)),
    "kcal": int(float(feld(lauf, "calories", "activeKilocalories", standard=0) or 0)),
    "schritte": int(float(feld(lauf, "steps", standard=0) or 0)),
    "hoehe": int(float(feld(lauf, "elevationGain", "totalElevationGainInMeters", standard=0) or 0)),
    "roh": lauf,                      # alles, was Garmin mitgeliefert hat
}

ziel = pathlib.Path("garmin")
ziel.mkdir(exist_ok=True)
(ziel / "latest.json").write_text(
    json.dumps(ergebnis, ensure_ascii=False, indent=2), encoding="utf-8")

kurz = {k: v for k, v in ergebnis.items() if k != "roh"}
print("Geschrieben:", json.dumps(kurz, ensure_ascii=False))
