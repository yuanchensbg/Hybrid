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

# ── 2. Aktivitätsliste holen ─────────────────────────────────────────
# Adresse aus der Personal-API-Dokumentation. Die Liste liegt nicht beim
# activity-service, sondern beim activitylist-service.
LISTE_URL = ("https://personal.connectapi.garmin.com"
             "/activitylist-service/activities/search/activities")

st, txt = curl(LISTE_URL + "?start=0&limit=20&activityType=running", KOPF)
print(f"Aktivitätsliste: HTTP {st}, {len(txt)} Zeichen")

if st != 200:
    # Ohne Typfilter noch einmal versuchen
    st, txt = curl(LISTE_URL + "?start=0&limit=20", KOPF)
    print(f"  ohne Typfilter: HTTP {st}, {len(txt)} Zeichen")

if st != 200:
    raise SystemExit(f"Liste nicht abrufbar.\n{txt[:400]}")

d = als_json(txt)
liste = None
if isinstance(d, list):
    liste = d
elif isinstance(d, dict):
    for feld_name in ("activityList", "activities", "items", "content", "data"):
        if isinstance(d.get(feld_name), list):
            liste = d[feld_name]
            break

if not liste:
    raise SystemExit("Keine Einträge in der Antwort: " + txt[:400])

print(f"  {len(liste)} Aktivitäten erhalten")
benutzt = LISTE_URL

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

# ── 4. Einzelheiten dieser Aktivität nachladen ───────────────────────
# Dort stehen Splits, Schrittfrequenz, Höhenprofil und Trainingswirkung.
aid = ergebnis["id"]
detail = None
if aid:
    st_d, txt_d = curl(
        f"https://personal.connectapi.garmin.com/activity-service/activity/{aid}", KOPF)
    print(f"Einzelheiten: HTTP {st_d}, {len(txt_d)} Zeichen")
    if st_d == 200:
        detail = als_json(txt_d)

if isinstance(detail, dict):
    ergebnis["detail"] = detail
    # Ein paar häufig gebrauchte Werte nach oben ziehen
    for quelle_feld, ziel_feld in [
            ("averageRunningCadenceInStepsPerMinute", "schrittfrequenz"),
            ("maxRunningCadenceInStepsPerMinute", "schrittfrequenzMax"),
            ("avgStrideLength", "schrittlaenge"),
            ("aerobicTrainingEffect", "wirkungAerob"),
            ("anaerobicTrainingEffect", "wirkungAnaerob"),
            ("avgPower", "leistung"),
            ("maxPower", "leistungMax"),
            ("vO2MaxValue", "vo2max"),
            ("minActivityLapDuration", "kuerzesteRunde")]:
        wert = detail.get(quelle_feld)
        if wert is None and isinstance(detail.get("summaryDTO"), dict):
            wert = detail["summaryDTO"].get(quelle_feld)
        if wert not in (None, ""):
            ergebnis[ziel_feld] = wert

# ── 5. Zeitreihen und Runden holen ───────────────────────────────────
# Garmin liefert keine Bilder, sondern Messpunkte. Daraus zeichnet die App
# ihre eigenen Kurven. Die Punktzahl wird reduziert, damit die Datei klein
# bleibt – für eine Kurve auf dem Telefon reichen rund 200 Werte.
def ausduennen(punkte, ziel_anzahl=200):
    if len(punkte) <= ziel_anzahl:
        return punkte
    schritt = len(punkte) / ziel_anzahl
    return [punkte[int(i * schritt)] for i in range(ziel_anzahl)]


if aid:
    # Runden je Kilometer
    st_s, txt_s = curl(
        f"https://personal.connectapi.garmin.com/activity-service/activity/{aid}/splits",
        KOPF)
    print(f"Runden: HTTP {st_s}, {len(txt_s)} Zeichen")
    if st_s == 200:
        ds = als_json(txt_s)
        runden = None
        if isinstance(ds, dict):
            for f in ("lapDTOs", "splits", "laps"):
                if isinstance(ds.get(f), list):
                    runden = ds[f]
                    break
        elif isinstance(ds, list):
            runden = ds
        if runden:
            ergebnis["runden"] = [{
                "nr": i + 1,
                "km": round(float(r.get("distance") or 0) / 10) / 100,
                "sek": round(float(r.get("duration") or 0)),
                "hr": int(float(r.get("averageHR") or 0)),
                "hoehe": round(float(r.get("elevationGain") or 0)),
            } for i, r in enumerate(runden)]
            print(f"  {len(ergebnis['runden'])} Runden")

    # Messpunkte über die Zeit
    for pfad in (f"/activity-service/activity/{aid}/details?maxChartSize=1000",
                 f"/activity-service/activity/{aid}/details"):
        st_m, txt_m = curl("https://personal.connectapi.garmin.com" + pfad, KOPF)
        print(f"Messpunkte: HTTP {st_m}, {len(txt_m)} Zeichen  ({pfad.split('?')[0][-20:]})")
        if st_m != 200:
            continue
        dm = als_json(txt_m)
        if not isinstance(dm, dict):
            continue
        schluessel = dm.get("metricDescriptors") or []
        zeilen = dm.get("activityDetailMetrics") or []
        if not schluessel or not zeilen:
            print("  keine verwertbaren Messpunkte:", txt_m[:160])
            break
        # Spaltennamen auf Index abbilden
        spalte = {}
        for sd in schluessel:
            name = str(sd.get("key") or "")
            spalte[name] = sd.get("metricsIndex")
        gesucht = {
            "sek": "sumElapsedDuration",
            "km": "sumDistance",
            "hr": "directHeartRate",
            "tempo": "directSpeed",
            "hoehe": "directElevation",
            "frequenz": "directRunCadence",
        }
        reihen = []
        for z in ausduennen(zeilen):
            w = z.get("metrics") or []
            punkt = {}
            for kurz_name, lang_name in gesucht.items():
                idx = spalte.get(lang_name)
                if idx is not None and idx < len(w) and w[idx] is not None:
                    punkt[kurz_name] = round(float(w[idx]), 2)
            if punkt:
                reihen.append(punkt)
        if reihen:
            ergebnis["messpunkte"] = reihen
            print(f"  {len(reihen)} Messpunkte übernommen")
        break

ziel = pathlib.Path("garmin")
ziel.mkdir(exist_ok=True)
(ziel / "latest.json").write_text(
    json.dumps(ergebnis, ensure_ascii=False, indent=2), encoding="utf-8")

kurz = {k: v for k, v in ergebnis.items()
        if k not in ("roh", "detail", "messpunkte", "runden")}
kurz["runden"] = len(ergebnis.get("runden") or [])
kurz["messpunkte"] = len(ergebnis.get("messpunkte") or [])
print("Geschrieben:", json.dumps(kurz, ensure_ascii=False))
