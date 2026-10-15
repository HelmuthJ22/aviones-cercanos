"""
Observabilidad de la plataforma:
  - Métricas Prometheus de la API (peticiones, latencia, aviones por respuesta, rechazos).
  - Colector que publica en /metrics los contadores que poller y enricher guardan en Valkey
    (así se observan TODOS los microservicios desde un solo endpoint, sin abrir más puertos).
  - Límite de peticiones por cliente (ventana fija de 1 minuto, compartida entre réplicas vía Valkey).
"""
import hashlib
import os
import time

import redis
from prometheus_client import Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, REGISTRY

RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))

# ---------- Métricas propias de la API (por réplica) ----------
HTTP_REQUESTS = Counter("aviones_http_requests_total", "Peticiones HTTP atendidas por la API",
                        ["path", "method", "status"])
HTTP_LATENCY = Histogram("aviones_http_request_duration_seconds", "Latencia de la API (segundos)",
                         ["path"], buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5))
AIRCRAFT_RETURNED = Histogram("aviones_aircraft_returned", "Aviones devueltos por consulta /api/near",
                              buckets=(0, 1, 2, 5, 10, 20, 50))
RATE_LIMITED = Counter("aviones_rate_limited_total", "Peticiones rechazadas por límite de peticiones")

KNOWN_PATHS = {"/api/near", "/health", "/metrics", "/api/stats", "/docs", "/openapi.json", "/"}


def path_label(path: str) -> str:
    """Evita etiquetas sin límite (cardinalidad): rutas desconocidas → 'other'."""
    return path if path in KNOWN_PATHS else "other"


# ---------- Límite de peticiones ----------
def client_id(headers, client_host: str | None) -> str:
    """IP real del usuario (Cloudflare la envía en CF-Connecting-IP), guardada solo como hash."""
    ip = (headers.get("cf-connecting-ip")
          or headers.get("x-forwarded-for", "").split(",")[0].strip()
          or client_host or "desconocido")
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def allow_request(r: redis.Redis, cid: str) -> tuple[bool, int]:
    """Devuelve (permitido, segundos_para_reintentar). Si Valkey falla, se permite (fail-open)."""
    window = int(time.time() // 60)
    key = f"rl:{cid}:{window}"
    try:
        n = r.incr(key)
        if n == 1:
            r.expire(key, 70)
    except redis.RedisError:
        return True, 0
    if n > RATE_LIMIT_PER_MIN:
        RATE_LIMITED.inc()
        try:
            r.hincrby("stats:api", "rate_limited", 1)
        except redis.RedisError:
            pass
        return False, 60 - int(time.time()) % 60
    return True, 0


# ---------- Estado global (compartido por todos los servicios a través de Valkey) ----------
def platform_stats(r: redis.Redis) -> dict:
    s = {"valkey_up": False}
    try:
        pipe = r.pipeline()
        pipe.ping()
        pipe.hgetall("stats:poller")
        pipe.hgetall("stats:enricher")
        pipe.hgetall("stats:api")
        pipe.zcount("cells:active", time.time(), "+inf")
        pipe.zcard("airports:geo")
        pipe.get("status:poller")
        ping, poller, enricher, api, cells, airports, last = pipe.execute()
        aircraft = 0
        for cell in r.zrangebyscore("cells:active", time.time(), "+inf"):
            raw = r.get(f"cell:{cell}")
            if raw:
                aircraft += raw.count('"hex"')
        s.update(valkey_up=bool(ping), poller=poller, enricher=enricher, api=api,
                 active_cells=cells, airports_loaded=airports, aircraft_cached=aircraft,
                 poller_last=last)
    except redis.RedisError:
        pass
    return s


class PlatformCollector:
    """Se ejecuta en cada lectura de /metrics y traduce el estado de Valkey a métricas Prometheus."""

    def __init__(self, r: redis.Redis):
        self.r = r

    def collect(self):
        s = platform_stats(self.r)
        up = GaugeMetricFamily("aviones_valkey_up", "1 si Valkey responde")
        up.add_metric([], 1 if s["valkey_up"] else 0)
        yield up
        if not s["valkey_up"]:
            return

        fetch = CounterMetricFamily("aviones_poller_fetch", "Consultas del poller por fuente y resultado",
                                    labels=["source", "result"])
        for k, v in (s.get("poller") or {}).items():
            if ":" in k:
                result, source = k.split(":", 1)
                fetch.add_metric([source, result], float(v))
        yield fetch

        routes = CounterMetricFamily("aviones_enricher_routes", "Búsquedas de rutas del enricher",
                                     labels=["result"])
        for k, v in (s.get("enricher") or {}).items():
            routes.add_metric([k], float(v))
        yield routes

        for name, desc, val in (
            ("aviones_active_cells", "Zonas (celdas 1°x1°) con usuarios activos", s["active_cells"]),
            ("aviones_aircraft_cached", "Aviones en caché en las zonas activas", s["aircraft_cached"]),
            ("aviones_airports_loaded", "Aeropuertos cargados en el índice geográfico", s["airports_loaded"]),
        ):
            g = GaugeMetricFamily(name, desc)
            g.add_metric([], float(val))
            yield g


_registered = False


def register_collector(r: redis.Redis):
    global _registered
    if not _registered:
        REGISTRY.register(PlatformCollector(r))
        _registered = True
