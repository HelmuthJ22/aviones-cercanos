import json
import time

import httpx
from fastapi.testclient import TestClient

import poller
from app import main as api
from app import observability as obs

client = TestClient(api.app)
BOGOTA = {"lat": 4.70, "lon": -74.14, "radius_km": 30}


def test_metrics_expone_api_y_plataforma(db):
    db.hset("stats:poller", mapping={"ok:adsb.lol": 7, "fail:opensky": 2})
    db.hset("stats:enricher", mapping={"found": 3, "unknown": 1})
    db.zadd("cells:active", {"4_-75": time.time() + 60})
    db.set("cell:4_-75", json.dumps({"updated_at": time.time(), "source": "adsb.lol",
                                     "aircraft": [{"hex": "a"}, {"hex": "b"}]}))
    client.get("/health")
    body = client.get("/metrics").text
    assert 'aviones_http_requests_total{method="GET",path="/health",status="200"}' in body
    assert "aviones_http_request_duration_seconds_bucket" in body
    assert 'aviones_poller_fetch_total{result="ok",source="adsb.lol"} 7.0' in body
    assert 'aviones_enricher_routes_total{result="found"} 3.0' in body
    assert "aviones_active_cells 1.0" in body
    assert "aviones_aircraft_cached 2.0" in body
    assert "aviones_valkey_up 1.0" in body


def test_rutas_desconocidas_no_crean_etiquetas_nuevas():
    client.get("/una/ruta/inventada/123")
    assert 'path="/una/ruta/inventada/123"' not in client.get("/metrics").text


def test_stats_json(db):
    db.hset("stats:api", "near_requests", 5)
    s = client.get("/api/stats").json()
    assert s["valkey_up"] is True and s["api"]["near_requests"] == "5"
    assert s["rate_limit_per_min"] == obs.RATE_LIMIT_PER_MIN


def test_limite_de_peticiones_por_cliente(db, monkeypatch):
    monkeypatch.setattr(obs, "RATE_LIMIT_PER_MIN", 3)
    cabeceras = {"CF-Connecting-IP": "203.0.113.7"}
    codigos = [client.post("/api/near", json=BOGOTA, headers=cabeceras).status_code for _ in range(5)]
    assert codigos == [200, 200, 200, 429, 429]
    r = client.post("/api/near", json=BOGOTA, headers=cabeceras)
    assert 0 < int(r.headers["Retry-After"]) <= 60
    # otro usuario (otra IP) no se ve afectado
    assert client.post("/api/near", json=BOGOTA, headers={"CF-Connecting-IP": "198.51.100.9"}).status_code == 200
    # la IP no se guarda en claro
    assert not any("203.0.113.7" in k for k in db.keys("rl:*"))


def test_poller_registra_resultados_por_fuente(db):
    def handler(req):
        if "adsb.lol" in req.url.host:
            return httpx.Response(500)
        return httpx.Response(200, json={"states": []})
    poller.http = httpx.Client(transport=httpx.MockTransport(handler))
    for b in poller.breakers.values():
        b.fails, b.open_until = 0, 0.0
    poller.fetch(4.5, -73.5)
    assert db.hgetall("stats:poller") == {"fail:adsb.lol": "1", "ok:opensky": "1"}
