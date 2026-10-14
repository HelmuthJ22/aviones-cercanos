"""
Configuración común de las pruebas.
- Reemplaza Valkey/Redis por una base en memoria (fakeredis) → no se necesita Docker.
- Las APIs externas (adsb.lol, OpenSky, adsbdb, OurAirports) se simulan con httpx.MockTransport.
"""
import pathlib
import sys

import fakeredis
import pytest
import redis

BACKEND = pathlib.Path(__file__).resolve().parent.parent
for sub in ("api", "poller", "enricher"):
    sys.path.insert(0, str(BACKEND / sub))

FAKE = fakeredis.FakeRedis(decode_responses=True)
redis.Redis.from_url = staticmethod(lambda *a, **k: FAKE)   # antes de importar los servicios


@pytest.fixture(autouse=True)
def db():
    FAKE.flushall()
    return FAKE
