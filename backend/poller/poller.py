"""
POLLER — trae posiciones de aviones SOLO para las celdas donde hay usuarios.

Fuente principal: adsb.lol (datos abiertos, ODbL)
Respaldo:         OpenSky Network (si adsb.lol falla)

Cada celda es un cuadro de 1° x 1° (~111 km). La API marca una celda como
"activa" cuando un usuario consulta desde ella; aquí la recorremos cada
POLL_INTERVAL segundos y guardamos el resultado en Valkey.
"""
import json
import logging
import os
import pathlib
import time

import httpx
import redis

VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
RADIUS_NM = int(os.getenv("CELL_RADIUS_NM", "100"))          # 100 NM ≈ 185 km
CELL_KEEP_S = 600                                             # dato viejo se guarda 10 min (modo degradado)
OPENSKY_ID = os.getenv("OPENSKY_CLIENT_ID", "")
OPENSKY_SECRET = os.getenv("OPENSKY_CLIENT_SECRET", "")
OPENSKY_TOKEN_URL = ("https://auth.opensky-network.org/auth/realms/opensky-network"
                     "/protocol/openid-connect/token")
HEARTBEAT = pathlib.Path("/tmp/heartbeat")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [poller] %(message)s")
log = logging.getLogger()
logging.getLogger("httpx").setLevel(logging.WARNING)   # no registrar cada petición

r = redis.Redis.from_url(VALKEY_URL, decode_responses=True)
http = httpx.Client(timeout=8, headers={"User-Agent": "aviones-cercanos/1.0 (proyecto academico)"})


# ---------- "Circuit breaker": si una fuente falla 3 veces seguidas, se descansa 60 s ----------
class Breaker:
    def __init__(self, name, max_fail=3, cooldown=60):
        self.name, self.max_fail, self.cooldown = name, max_fail, cooldown
        self.fails, self.open_until = 0, 0.0

    def available(self):
        return time.time() >= self.open_until

    def ok(self):
        self.fails = 0

    def fail(self):
        self.fails += 1
        if self.fails >= self.max_fail:
            self.open_until = time.time() + self.cooldown
            self.fails = 0
            log.warning("fuente %s deshabilitada %ss", self.name, self.cooldown)

    def pause(self, seconds):
        """Límite de peticiones (429): pausar ya, sin esperar 3 fallos."""
        self.open_until = time.time() + seconds
        self.fails = 0
        log.warning("fuente %s pide esperar (429): pausa de %ss", self.name, seconds)


breakers = {"adsb.lol": Breaker("adsb.lol"), "opensky": Breaker("opensky", cooldown=300)}


# ---------- Fuente 1: adsb.lol ----------
def from_adsblol(lat, lon):
    resp = http.get(f"https://api.adsb.lol/v2/point/{lat}/{lon}/{RADIUS_NM}")
    resp.raise_for_status()
    out = []
    for a in resp.json().get("ac") or []:
        if a.get("lat") is None or a.get("lon") is None:
            continue
        alt = a.get("alt_baro")
        on_ground = alt == "ground"
        out.append({
            "hex": a.get("hex"),
            "callsign": (a.get("flight") or "").strip(),
            "lat": a["lat"], "lon": a["lon"],
            "alt_m": round(alt * 0.3048) if isinstance(alt, (int, float)) else None,
            "speed_kmh": round(a["gs"] * 1.852) if isinstance(a.get("gs"), (int, float)) else None,
            "track": a.get("track"),
            "type": a.get("t"),
            "reg": a.get("r"),
            "on_ground": on_ground,
        })
    return out


# ---------- Fuente 2: OpenSky (respaldo) ----------
_token = {"value": None, "exp": 0.0}


def opensky_headers():
    if not (OPENSKY_ID and OPENSKY_SECRET):
        return {}  # acceso anónimo (menos créditos diarios)
    if time.time() > _token["exp"] - 60:
        resp = http.post(OPENSKY_TOKEN_URL, data={
            "grant_type": "client_credentials",
            "client_id": OPENSKY_ID, "client_secret": OPENSKY_SECRET})
        resp.raise_for_status()
        j = resp.json()
        _token["value"] = j["access_token"]
        _token["exp"] = time.time() + j.get("expires_in", 1800)
    return {"Authorization": f"Bearer {_token['value']}"}


def from_opensky(lat, lon):
    d = 1.6  # grados ≈ 180 km
    params = {"lamin": lat - d, "lamax": lat + d, "lomin": lon - d, "lomax": lon + d}
    resp = http.get("https://opensky-network.org/api/states/all",
                    params=params, headers=opensky_headers())
    resp.raise_for_status()
    out = []
    for s in resp.json().get("states") or []:
        if s[5] is None or s[6] is None:
            continue
        out.append({
            "hex": s[0],
            "callsign": (s[1] or "").strip(),
            "lat": s[6], "lon": s[5],
            "alt_m": round(s[7]) if s[7] is not None and not s[8] else None,
            "speed_kmh": round(s[9] * 3.6) if s[9] is not None else None,
            "track": s[10],
            "type": None, "reg": None,
            "on_ground": bool(s[8]),
        })
    return out


SOURCES = [("adsb.lol", from_adsblol), ("opensky", from_opensky)]


def stat(field):
    """Contador compartido para observabilidad (lo publica la API en /metrics)."""
    try:
        r.hincrby("stats:poller", field, 1)
    except redis.RedisError:
        pass


def fetch(lat, lon):
    """Intenta cada fuente en orden; devuelve (lista, nombre_fuente) o (None, None)."""
    for name, fn in SOURCES:
        b = breakers[name]
        if not b.available():
            continue
        try:
            data = fn(lat, lon)
            b.ok()
            stat(f"ok:{name}")
            return data, name
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                stat(f"ratelimited:{name}")
                try:
                    wait = int(e.response.headers.get("Retry-After", "30"))
                except ValueError:
                    wait = 30
                b.pause(min(max(wait, 10), 600))
            else:
                log.warning("error en %s: %s", name, e)
                stat(f"fail:{name}")
                b.fail()
        except Exception as e:  # noqa: BLE001
            log.warning("error en %s: %s", name, e)
            stat(f"fail:{name}")
            b.fail()
    return None, None


def cell_center(cell):
    la, lo = map(int, cell.split("_"))
    return la + 0.5, lo + 0.5


def loop_once():
    now = time.time()
    r.zremrangebyscore("cells:active", 0, now)          # borra celdas sin usuarios
    cells = r.zrange("cells:active", 0, -1)
    ok = 0
    for cell in cells:
        lat, lon = cell_center(cell)
        data, src = fetch(lat, lon)
        if data is None:
            continue
        ok += 1
        r.set(f"cell:{cell}", json.dumps({"updated_at": time.time(), "source": src,
                                          "aircraft": data}), ex=CELL_KEEP_S)
        callsigns = {a["callsign"] for a in data if a["callsign"]}
        if callsigns:
            r.sadd("callsigns:pending", *callsigns)       # el enricher buscará sus rutas
        time.sleep(2)                                       # cortesía con las APIs públicas
    r.set("status:poller", json.dumps({"at": now, "cells": len(cells), "ok": ok}), ex=120)
    if cells:
        log.info("celdas activas=%d actualizadas=%d", len(cells), ok)


def main():
    log.info("iniciando (intervalo=%ss, radio=%s NM)", POLL_INTERVAL, RADIUS_NM)
    while True:
        start = time.time()
        try:
            loop_once()
            HEARTBEAT.touch()
        except redis.RedisError as e:
            log.error("Valkey no disponible: %s", e)
        time.sleep(max(1.0, POLL_INTERVAL - (time.time() - start)))


if __name__ == "__main__":
    main()
