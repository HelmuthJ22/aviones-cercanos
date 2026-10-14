import json
import time

import httpx

import poller

ADSB_OK = {"ac": [
    {"hex": "abc123", "flight": "AVA9356 ", "lat": 4.75, "lon": -73.95, "alt_baro": 12000,
     "gs": 300, "track": 90, "t": "A320", "r": "HK-1"},
    {"hex": "def456", "flight": "", "lat": 4.9, "lon": -74.0, "alt_baro": "ground", "gs": 0},
    {"hex": "sinpos"},
]}
OPENSKY_OK = {"states": [["e8001a", "AVA111  ", "Colombia", 0, 0, -74.0, 4.8, 3000.0,
                          False, 200.0, 180.0, 0, None, 0, None, False, 0]]}


def use_transport(handler):
    poller.http = httpx.Client(transport=httpx.MockTransport(handler))
    for b in poller.breakers.values():
        b.fails, b.open_until = 0, 0.0


def test_normaliza_datos_de_adsb_lol(db):
    use_transport(lambda req: httpx.Response(200, json=ADSB_OK))
    data, src = poller.fetch(4.5, -73.5)
    assert src == "adsb.lol"
    assert len(data) == 2                       # descarta el avión sin posición
    a = data[0]
    assert a["callsign"] == "AVA9356"           # sin espacios
    assert a["alt_m"] == 3658                   # pies → metros
    assert a["speed_kmh"] == 556                # nudos → km/h
    assert data[1]["on_ground"] is True and data[1]["alt_m"] is None


def test_respaldo_opensky_si_adsb_lol_falla(db):
    def handler(req):
        if "adsb.lol" in req.url.host:
            return httpx.Response(500)
        return httpx.Response(200, json=OPENSKY_OK)
    use_transport(handler)
    data, src = poller.fetch(4.5, -73.5)
    assert src == "opensky"
    assert data[0]["callsign"] == "AVA111" and data[0]["speed_kmh"] == 720


def test_429_pausa_la_fuente_inmediatamente(db):
    calls = []

    def handler(req):
        calls.append(req.url.host)
        if "adsb.lol" in req.url.host:
            return httpx.Response(429, headers={"Retry-After": "45"})
        return httpx.Response(200, json={"states": []})
    use_transport(handler)
    poller.fetch(4.5, -73.5)
    assert poller.breakers["adsb.lol"].open_until - time.time() > 40
    calls.clear()
    poller.fetch(4.5, -73.5)
    assert calls == ["opensky-network.org"]      # ya no insiste con adsb.lol


def test_ciclo_guarda_celda_y_encola_callsigns(db):
    use_transport(lambda req: httpx.Response(200, json=ADSB_OK))
    poller.time.sleep = lambda s: None           # sin esperas en la prueba
    db.zadd("cells:active", {"4_-74": time.time() + 60})
    db.zadd("cells:active", {"10_-75": time.time() - 1})   # celda vencida
    poller.loop_once()
    cell = json.loads(db.get("cell:4_-74"))
    assert cell["source"] == "adsb.lol" and len(cell["aircraft"]) == 2
    assert db.smembers("callsigns:pending") == {"AVA9356"}
    assert db.zrange("cells:active", 0, -1) == ["4_-74"]   # la vencida se borró
