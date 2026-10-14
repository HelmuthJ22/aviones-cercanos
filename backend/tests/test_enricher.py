import httpx

import enricher

CSV = (
    "id,ident,type,name,latitude_deg,longitude_deg,iso_country,municipality,iata_code\n"
    "1,NZSP,medium_airport,South Pole,-90,-1,AQ,,\n"                 # fuera del índice geográfico
    "2,SKBO,large_airport,El Dorado,4.70159,-74.1469,CO,Bogota,BOG\n"
    "3,XX01,small_airport,Pequeño,4.8,-73.9,CO,Sopo,\n"              # se ignora (pequeño)
    '4,SKRG,large_airport,"Jose Maria, Cordova",6.16,-75.42,CO,Rionegro,MDE\n'
)
ROUTE = {"response": {"flightroute": {
    "airline": {"name": "Avianca"},
    "origin": {"iata_code": "BOG", "icao_code": "SKBO", "name": "El Dorado", "municipality": "Bogota"},
    "destination": {"iata_code": "MDE", "icao_code": "SKRG", "name": "JMC", "municipality": "Rionegro"}}}}


def handler(req):
    if "ourairports" in str(req.url):
        return httpx.Response(200, text=CSV)
    if "AVA9356" in req.url.path:
        return httpx.Response(200, json=ROUTE)
    return httpx.Response(404, json={"response": "unknown callsign"})


def setup_function():
    enricher.http = httpx.Client(transport=httpx.MockTransport(handler))


def test_carga_aeropuertos_y_omite_polos_y_pequenos(db):
    enricher.load_airports()
    assert sorted(db.zrange("airports:geo", 0, -1)) == ["SKBO", "SKRG"]
    assert db.hget("airport:SKRG", "name") == "Jose Maria, Cordova"   # coma dentro de comillas
    assert db.hget("airport:SKBO", "iata") == "BOG"


def test_ruta_conocida_y_desconocida(db):
    r = enricher.lookup_route("AVA9356")
    assert r["airline"] == "Avianca"
    assert r["origin"]["iata"] == "BOG" and r["destination"]["city"] == "Rionegro"
    assert enricher.lookup_route("ZZZ1") is None
