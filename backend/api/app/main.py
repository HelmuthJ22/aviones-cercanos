"""
API — recibe la ubicación del usuario y devuelve los aviones cercanos.

La ubicación llega en el cuerpo de un POST (no en la URL) para que no quede
escrita en los logs, y NO se guarda en ninguna parte: solo se usa para
calcular la celda y las distancias.
"""
import json
import math
import os
import time

import redis
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
CELL_ACTIVE_S = 120      # una celda sigue activa 2 min después de la última consulta
STALE_AFTER_S = 45       # si el dato tiene más de 45 s se marca como desactualizado
APP_VERSION = os.getenv("APP_VERSION", "dev")   # commit de git, lo inyecta el CI
ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]

r = redis.Redis.from_url(VALKEY_URL, decode_responses=True,
                         socket_timeout=2, socket_connect_timeout=2)

app = FastAPI(title="API Aviones Cercanos", version=APP_VERSION)
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS,
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


class Position(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(default=30, gt=0, le=100)


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def cell_of(lat, lon):
    return f"{math.floor(lat)}_{math.floor(lon)}"


def nearest_airport(lat, lon):
    try:
        res = r.geosearch("airports:geo", longitude=lon, latitude=lat, radius=150,
                          unit="km", sort="ASC", count=1, withdist=True)
        if not res:
            return None
        ident, dist = res[0]
        info = r.hgetall(f"airport:{ident}")
        return {"ident": ident, "distance_km": round(float(dist), 1), **info}
    except redis.RedisError:
        return None


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/docs")


@app.get("/health")
def health():
    status = {"api": "ok", "version": APP_VERSION, "valkey": False, "poller": None}
    try:
        status["valkey"] = bool(r.ping())
        raw = r.get("status:poller")
        status["poller"] = json.loads(raw) if raw else "sin datos"
    except redis.RedisError:
        pass
    return status


@app.post("/api/near")
def near(p: Position):
    cell = cell_of(p.lat, p.lon)
    now = time.time()
    try:
        r.zadd("cells:active", {cell: now + CELL_ACTIVE_S})   # avisa al poller
        raw = r.get(f"cell:{cell}")
    except redis.RedisError:
        raise HTTPException(503, "Almacenamiento temporalmente no disponible")

    if not raw:
        return {"status": "warming", "message": "Primera consulta en esta zona, "
                "los datos llegarán en unos segundos", "aircraft": [],
                "nearest_airport": nearest_airport(p.lat, p.lon)}

    data = json.loads(raw)
    found = []
    for a in data["aircraft"]:
        d = haversine_km(p.lat, p.lon, a["lat"], a["lon"])
        if d <= p.radius_km:
            found.append({**a, "distance_km": round(d, 1),
                          "bearing": round(bearing_deg(p.lat, p.lon, a["lat"], a["lon"]))})
    found.sort(key=lambda a: a["distance_km"])

    # agrega rutas (si el enricher ya las encontró)
    with_cs = [a for a in found if a["callsign"]]
    if with_cs:
        try:
            routes = r.mget([f"route:{a['callsign']}" for a in with_cs])
            for a, rt in zip(with_cs, routes):
                a["route"] = json.loads(rt) if rt else None
        except redis.RedisError:
            pass

    age = now - data["updated_at"]
    return {"status": "stale" if age > STALE_AFTER_S else "ok",
            "source": data["source"], "age_s": round(age),
            "radius_km": p.radius_km, "count": len(found), "aircraft": found,
            "nearest_airport": nearest_airport(p.lat, p.lon)}
