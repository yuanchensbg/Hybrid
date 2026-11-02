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


def roh_holen(url, headers, method="GET", daten=None):
    """Gibt Text, Status und Kopfzeilen zurück – ohne JSON vorauszusetzen."""
    anfrage = urllib.request.Request(url, data=daten, headers=headers, method=method)
    with urllib.request.urlopen(anfrage, timeout=30) as antwort:
        return (antwort.read().decode("utf-8", "replace"),
                antwort.status, dict(antwort.headers))


def hole(url, headers, method="GET", daten=None):
    text, _, _ = roh_holen(url, headers, method, daten)
    if not text.strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_text": text}


# ── 1. Schlüssel gegen Token tauschen ────────────────────────────────
# Garmin hat mit 406 geantwortet. Die Ursache ist nicht eindeutig, deshalb
# werden mehrere Kopfzeilen-Kombinationen probiert. Die funktionierende
# wird im Log genannt, danach lässt sich die Liste kürzen.
CURL_UA = "curl/8.7.1"

VARIANTEN = [
    ("nur Authorization, Kennung curl",
     {"Authorization": "ApiKey " + SCHLUESSEL, "User-Agent": CURL_UA}),
    ("Authorization pur",
     {"Authorization": "ApiKey " + SCHLUESSEL}),
    ("curl-Kennung mit Accept */*",
     {"Authorization": "ApiKey " + SCHLUESSEL, "User-Agent": CURL_UA, "Accept": "*/*"}),
    ("curl-Kennung mit Accept application/json",
     {"Authorization": "ApiKey " + SCHLUESSEL, "User-Agent": CURL_UA,
      "Accept": "application/json"}),
    ("mit Content-Type application/json",
     {"Authorization": "ApiKey " + SCHLUESSEL, "User-Agent": CURL_UA,
      "Accept": "application/json", "Content-Type": "application/json"}),
    ("Browser-Kennung",
     {"Authorization": "ApiKey " + SCHLUESSEL,
      "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
      "Accept": "application/json"}),
]

text = status = kopf = None
for name, kopfzeilen in VARIANTEN:
    try:
        text, status, kopf = roh_holen(TOKEN_URL, kopfzeilen, "POST", b"")
        print(f"  {name} → {status}  ← diese Kombination funktioniert")
        break
    except urllib.error.HTTPError as e:
        koerper = e.read().decode("utf-8", "replace")[:160]
        print(f"  {name} → HTTP {e.code}  {koerper}")
        text = None
    except Exception as e:
        print(f"  {name} → {e}")
        text = None

if text is None:
    raise SystemExit("Keine der Kombinationen wurde angenommen. Log bitte weitergeben.")

print(f"Antwort {status}, Typ {kopf.get('Content-Type','?')}, {len(text)} Zeichen")

token = None
if text.strip():
    try:
        d = json.loads(text)
        if isinstance(d, str):
            token = d
        elif isinstance(d, dict):
            token = (d.get("access_token") or d.get("accessToken")
                     or d.get("token") or d.get("value"))
    except json.JSONDecodeError:
        token = text.strip().strip('"')

if not token:
    for name in ("Authorization", "X-Access-Token", "Access-Token"):
        if kopf.get(name):
            token = kopf[name].replace("Bearer ", "").strip()
            break

if not token:
    raise SystemExit(
        "Kein Token in der Antwort.\n"
        f"Status: {status}\nKopfzeilen: {json.dumps(kopf)[:300]}\n"
        f"Inhalt: {text[:300] if text.strip() else '(leer)'}")

print(f"Token erhalten, {len(token)} Zeichen, beginnt mit {token[:8]}…")

KOPF = {"Authorization": "Bearer " + token, "Accept": "application/json",
        "User-Agent": CURL_UA}

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
