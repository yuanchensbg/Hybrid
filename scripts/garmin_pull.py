"""Holt die jüngste Laufaktivität aus Garmin Connect und schreibt sie als
garmin/latest.json – in genau dem Format, das die Hybrid-App erwartet."""

import json, os, pathlib, datetime
from garminconnect import Garmin

email = os.environ["GARMIN_EMAIL"]
passwort = os.environ["GARMIN_PASSWORD"]

api = Garmin(email, passwort)
api.login()

# Die letzten zehn Aktivitäten durchsehen und den jüngsten Lauf nehmen
aktivitaeten = api.get_activities(0, 10)
lauf = None
for a in aktivitaeten:
    typ = (a.get("activityType") or {}).get("typeKey", "")
    if "running" in typ:
        lauf = a
        break

if lauf is None:
    raise SystemExit("Keine Laufaktivität gefunden")

meter = float(lauf.get("distance") or 0)
sekunden = float(lauf.get("duration") or 0)
km = round(meter / 100) / 10
pace = round(sekunden / (meter / 1000)) if meter else 0

daten = {
    "geholt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
    "id": lauf.get("activityId"),
    "name": lauf.get("activityName") or "",
    "datum": (lauf.get("startTimeLocal") or "")[:10],
    "km": km,
    "sek": int(sekunden),
    "paceSek": pace,
    "hr": int(lauf.get("averageHR") or 0),
    "hrMax": int(lauf.get("maxHR") or 0),
    "kcal": int(lauf.get("calories") or 0),
}

ziel = pathlib.Path("garmin")
ziel.mkdir(exist_ok=True)
(ziel / "latest.json").write_text(
    json.dumps(daten, ensure_ascii=False, indent=2), encoding="utf-8")

print("Geschrieben:", daten)
