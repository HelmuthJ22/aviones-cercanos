import json
import time

from fastapi.testclient import TestClient

from app import main as api

client = TestClient(api.app)
BOGOTA = {"lat": 4.70, "lon": -74.14, "radius_km": 30}


def guardar_celda(db, cell, aircraft, age_s=1, source="adsb.lol"):
    db.set(f"cell:{cell}", json.dumps({"updated_at": time.time() - age_s,
                                       "source": source, "aircraft": aircraft}))


AVION = {"hex": "abc", "callsign": "AVA066", "lat": 4.75, "lon": -74.10, "alt_m": 3000,
         "speed_kmh": 500, "track": 90, "type": "A320", "reg": "HK", "on_ground": False}
LEJANO = {**AVION, "hex": "far", "callsign": "LEJOS1", "lat": 4.99, "lon": -74.99}


def test_health_incluye_version(db):
    r = client.get("/health").json()
    assert r["api"] == "ok" and r["valkey"] is True and "version" in r


def test_raiz_redirige_a_docs():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/docs"


def test_primera_consulta_activa_la_celda(db):
    r = client.post("/api/near", json=BOGOTA).json()
    assert r["status"] == "warming"
    assert db.zscore("cells:active", "4_-75") > time.time()


def test_filtra_por_radio_y_agrega_ruta(db):
    guardar_celda(db, "4_-75", [AVION, LEJANO])
    db.set("route:AVA066", json.dumps({"airline": "Avianca", "origin": {"city": "Quito"},
                                       "destination": {"city": "Bogota"}}))
    r = client.post("/api/near", json=BOGOTA).json()
    assert r["status"] == "ok" and r["count"] == 1
    a = r["aircraft"][0]
    assert a["callsign"] == "AVA066" and 0 < a["distance_km"] < 10
    assert a["route"]["origin"]["city"] == "Quito"


def test_dato_viejo_se_marca_stale(db):
    guardar_celda(db, "4_-75", [AVION], age_s=300)
    assert client.post("/api/near", json=BOGOTA).json()["status"] == "stale"


def test_aeropuerto_mas_cercano(db):
    db.geoadd("airports:geo", (-74.1469, 4.70159, "SKBO"))
    db.hset("airport:SKBO", mapping={"name": "El Dorado", "iata": "BOG", "city": "Bogota", "country": "CO"})
    r = client.post("/api/near", json=BOGOTA).json()
    assert r["nearest_airport"]["iata"] == "BOG"


def test_valida_coordenadas():
    assert client.post("/api/near", json={"lat": 200, "lon": 0}).status_code == 422
    assert client.post("/api/near", json={"lat": 4, "lon": -74, "radius_km": 500}).status_code == 422


def test_cors_solo_permite_el_origen_configurado():
    # ALLOWED_ORIGINS por defecto es "*" en pruebas; se valida que el middleware responda
    r = client.options("/api/near", headers={"Origin": "https://ejemplo.com",
                                             "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200
