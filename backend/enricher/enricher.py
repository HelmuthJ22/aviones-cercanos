"""
ENRICHER — agrega información que el ADS-B no trae.

1. Aeropuertos: carga OurAirports (dominio público) en un índice geográfico de Valkey.
2. Rutas: para cada callsign visto por el poller consulta adsbdb (open source)
   y guarda origen/destino/aerolínea en caché.
"""
import csv
import io
import json
import logging
import os
import pathlib
import time

import httpx
import redis

VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
AIRPORTS_URL = os.getenv("AIRPORTS_URL",
                         "https://davidmegginson.github.io/ourairports-data/airports.csv")
ROUTE_TTL = 6 * 3600       # ruta conocida: 6 h
UNKNOWN_TTL = 3600         # ruta desconocida: reintentar en 1 h
HEARTBEAT = pathlib.Path("/tmp/heartbeat")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [enricher] %(message)s")
log = logging.getLogger()
logging.getLogger("httpx").setLevel(logging.WARNING)   # no registrar cada petición

r = redis.Redis.from_url(VALKEY_URL, decode_responses=True)
http = httpx.Client(timeout=15, headers={"User-Agent": "aviones-cercanos/1.0 (proyecto academico)"})


def load_airports():
    if r.exists("airports:loaded"):
        return
    log.info("descargando aeropuertos de OurAirports...")
    resp = http.get(AIRPORTS_URL, timeout=90, follow_redirects=True)
    resp.raise_for_status()
    pipe = r.pipeline(transaction=False)
    n = 0
    for row in csv.DictReader(io.StringIO(resp.text)):
        if row.get("type") not in ("large_airport", "medium_airport"):
            continue
        try:
            lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
        except (KeyError, ValueError):
            continue
        if abs(lat) > 85.05 or abs(lon) > 180:   # límite del índice geográfico (polos)
            continue
        ident = row["ident"]
        pipe.geoadd("airports:geo", (lon, lat, ident))
        pipe.hset(f"airport:{ident}", mapping={
            "name": row.get("name", ""), "iata": row.get("iata_code", ""),
            "city": row.get("municipality", ""), "country": row.get("iso_country", "")})
        n += 1
        if n % 500 == 0:
            pipe.execute()
    pipe.execute()
    r.set("airports:loaded", n, ex=7 * 86400)   # se recarga cada semana
    log.info("aeropuertos cargados: %d", n)


def _airport(a):
    a = a or {}
    return {"iata": a.get("iata_code"), "icao": a.get("icao_code"), "name": a.get("name"),
            "city": a.get("municipality"), "country": a.get("country_name")}


def lookup_route(callsign):
    resp = http.get(f"https://api.adsbdb.com/v0/callsign/{callsign}")
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    body = resp.json().get("response")
    if not isinstance(body, dict) or not isinstance(body.get("flightroute"), dict):
        return None
    fr = body["flightroute"]
    return {"airline": (fr.get("airline") or {}).get("name"),
            "origin": _airport(fr.get("origin")),
            "destination": _airport(fr.get("destination"))}


def main():
    log.info("iniciando")
    last_airport_try = 0.0
    while True:
        try:
            if time.time() - last_airport_try > 600:
                last_airport_try = time.time()
                try:
                    load_airports()
                except httpx.HTTPError as e:
                    log.warning("no se pudo cargar aeropuertos (reintento en 10 min): %s", e)

            cs = r.spop("callsigns:pending")
            HEARTBEAT.touch()
            if not cs:
                time.sleep(2)
                continue
            if r.exists(f"route:{cs}"):
                continue
            try:
                route = lookup_route(cs)
                r.set(f"route:{cs}", json.dumps(route),
                      ex=ROUTE_TTL if route else UNKNOWN_TTL)
                r.hincrby("stats:enricher", "found" if route else "unknown", 1)
            except httpx.HTTPError as e:
                log.warning("adsbdb falló para %s: %s", cs, e)
                r.hincrby("stats:enricher", "error", 1)
                time.sleep(10)
            time.sleep(1.2)   # máx ~1 consulta/s
        except redis.RedisError as e:
            log.error("error de Valkey: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
