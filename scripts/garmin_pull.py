"""Holt die jüngste Laufaktivität über die Garmin Personal API.

Kein Passwort, keine Zwei-Faktor-Abfrage: Der Personal-API-Schlüssel wird
gegen einen Zugangstoken getauscht, der 24 Stunden gilt.

Schlüssel anlegen: Garmin Connect im Browser → Einstellungen → Personal API
→ Create Key. Er wird nur einmal angezeigt.
"""

import json, os, pathlib, datetime, sys
import urllib.request, urllib.error, urllib.parse

SCHLUESSEL = os.environ.get("GARMIN_API_KEY", "").strip()
if not SCHLUESSEL:
    raise SystemExit("GARMIN_API_KEY fehlt – als Secret im Repository anlegen.")

TOKEN_URL = ("https://personal.connectapi.garmin.com/personalapitoken-service"
             "/personal-api/personal-api-key/token")


def hole(url, headers, method="GET", daten=None):
    anfrage = urllib.request.Request(url, data=daten, headers=headers, method=method)
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        roh = antwort.read().decode("utf-8")
    return json.loads(roh) if roh.strip() else {}


# ── 1. Schlüssel gegen Token tauschen ────────────────────────────────
try:
    antwort = hole(TOKEN_URL, {"Authorization": "ApiKey " + SCHLUESSEL}, "POST", b"")
except urllib.error.HTTPError as e:
    raise SystemExit(f"Token-Abruf fehlgeschlagen: {e.code} {e.read().decode('utf-8')[:300]}")

token = antwort.get("access_token") or antwort.get("accessToken") or antwort.get("token")
if not token:
    raise SystemExit("Kein Token in der Antwort: " + json.dumps(antwort)[:300])
print("Token erhalten.")

KOPF = {"Authorization": "Bearer " + token, "Accept": "application/json"}

# ── 2. Aktivitäten suchen ────────────────────────────────────────────
# Die genaue Adresse ist nicht öffentlich dokumentiert, deshalb werden
# mehrere plausible Varianten versucht. Die funktionierende wird im Log
# genannt – danach kann man die Liste kürzen.
heute = datetime.date.today()
von = heute - datetime.timedelta(days=14)
spanne = {"startDate": von.isoformat(), "endDate": heute.isoformat(),
          "start": "0", "limit": "20"}

KANDIDATEN = [
    ("https://personal.connectapi.garmin.com/activity-service/activities", spanne),
    ("https://personal.connectapi.garmin.com/activity-service/activity/search", spanne),
    ("https://personal.connectapi.garmin.com/personal-api/activities", spanne),
    ("https://apis.garmin.com/activity-service/activities", spanne),
    ("https://personal.connectapi.garmin.com/activity-service/activities/search", spanne),
]

liste, benutzt = None, None
for basis, params in KANDIDATEN:
    url = basis + "?" + urllib.parse.urlencode(params)
    try:
        ergebnis = hole(url, KOPF)
    except urllib.error.HTTPError as e:
        print(f"  {basis} → HTTP {e.code}")
        continue
    except Exception as e:
        print(f"  {basis} → {e}")
        continue
    if isinstance(ergebnis, list):
        liste, benutzt = ergebnis, basis
    elif isinstance(ergebnis, dict):
        for feld in ("activities", "activityList", "items", "content"):
            if isinstance(ergebnis.get(feld), list):
                liste, benutzt = ergebnis[feld], basis
                break
    if liste is not None:
        print(f"  {basis} → {len(liste)} Einträge  ← diese Adresse funktioniert")
        break

if liste is None:
    raise SystemExit(
        "Keine Aktivitätsliste gefunden. Alle Adressen oben haben nicht geantwortet.\n"
        "Schick mir dieses Log, dann trage ich die richtige Adresse ein.")

# ── 3. Jüngsten Lauf herausziehen ────────────────────────────────────
def feld(d, *namen, standard=None):
    for n in namen:
        if isinstance(d, dict) and d.get(n) not in (None, ""):
            return d[n]
    return standard

lauf = None
for a in liste:
    typ = str(feld(a, "activityType", "activityTypeDTO", standard="") or "")
    if isinstance(feld(a, "activityType"), dict):
        typ = str(feld(a, "activityType").get("typeKey", ""))
    if "run" in typ.lower():
        lauf = a
        break

if lauf is None:
    print("Erster Eintrag zur Ansicht:", json.dumps(liste[0], ensure_ascii=False)[:600])
    raise SystemExit("Kein Lauf unter den letzten Aktivitäten gefunden.")

meter = float(feld(lauf, "distance", "distanceInMeters", standard=0) or 0)
sekunden = float(feld(lauf, "duration", "durationInSeconds", standard=0) or 0)
km = round(meter / 100) / 10
pace = round(sekunden / (meter / 1000)) if meter else 0
start = str(feld(lauf, "startTimeLocal", "startTimeInSeconds", "startTime", standard=""))

daten = {
    "geholt": datetime.datetime.now(datetime.timezone.utc)
                 .isoformat(timespec="seconds").replace("+00:00", "Z"),
    "quelle": benutzt,
    "id": feld(lauf, "activityId", "summaryId"),
    "name": feld(lauf, "activityName", "activityType", standard="") or "",
    "datum": start[:10] if "-" in start[:10] else heute.isoformat(),
    "km": km,
    "sek": int(sekunden),
    "paceSek": pace,
    "hr": int(float(feld(lauf, "averageHR", "averageHeartRateInBeatsPerMinute", standard=0) or 0)),
    "hrMax": int(float(feld(lauf, "maxHR", "maxHeartRateInBeatsPerMinute", standard=0) or 0)),
    "kcal": int(float(feld(lauf, "calories", "activeKilocalories", standard=0) or 0)),
}

ziel = pathlib.Path("garmin")
ziel.mkdir(exist_ok=True)
(ziel / "latest.json").write_text(
    json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")

print("Geschrieben:", json.dumps(daten, ensure_ascii=False))
