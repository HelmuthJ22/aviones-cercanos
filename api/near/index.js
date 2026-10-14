// POST /api/near — respaldo SERVERLESS de la API de la Raspberry Pi.
// Mismo contrato de entrada/salida, pero consulta las fuentes directamente desde Azure.
// No guarda la ubicación del usuario.
const { haversineKm, bearingDeg, fetchJson, nearestAirports, json } = require("../shared/geo");

async function fromAdsbLol(lat, lon, radiusKm) {
  const nm = Math.min(250, Math.ceil(radiusKm / 1.852) + 1);
  const data = await fetchJson(`https://api.adsb.lol/v2/point/${lat}/${lon}/${nm}`);
  return (data.ac || [])
    .filter((a) => a.lat != null && a.lon != null)
    .map((a) => {
      const alt = a.alt_baro;
      return {
        hex: a.hex, callsign: (a.flight || "").trim(), lat: a.lat, lon: a.lon,
        alt_m: typeof alt === "number" ? Math.round(alt * 0.3048) : null,
        speed_kmh: typeof a.gs === "number" ? Math.round(a.gs * 1.852) : null,
        track: a.track ?? null, type: a.t ?? null, reg: a.r ?? null,
        on_ground: alt === "ground"
      };
    });
}

async function fromOpenSky(lat, lon, radiusKm) {
  const d = Math.max(0.3, radiusKm / 100);     // grados aproximados
  const q = `lamin=${lat - d}&lamax=${lat + d}&lomin=${lon - d}&lomax=${lon + d}`;
  const data = await fetchJson(`https://opensky-network.org/api/states/all?${q}`);
  return (data.states || [])
    .filter((s) => s[5] != null && s[6] != null)
    .map((s) => ({
      hex: s[0], callsign: (s[1] || "").trim(), lat: s[6], lon: s[5],
      alt_m: s[7] != null && !s[8] ? Math.round(s[7]) : null,
      speed_kmh: s[9] != null ? Math.round(s[9] * 3.6) : null,
      track: s[10], type: null, reg: null, on_ground: !!s[8]
    }));
}

async function lookupRoute(callsign) {
  try {
    const data = await fetchJson(`https://api.adsbdb.com/v0/callsign/${encodeURIComponent(callsign)}`,
      { timeoutMs: 2500 });
    const fr = data && typeof data.response === "object" ? data.response.flightroute : null;
    if (!fr) return null;
    const ap = (a = {}) => ({ iata: a.iata_code, icao: a.icao_code, name: a.name,
                              city: a.municipality, country: a.country_name });
    return { airline: fr.airline ? fr.airline.name : null, origin: ap(fr.origin), destination: ap(fr.destination) };
  } catch {
    return null;   // ruta desconocida o adsbdb no respondió a tiempo
  }
}

module.exports = async function (context, req) {
  let body = req.body || {};
  if (typeof body === "string") { try { body = JSON.parse(body); } catch { body = {}; } }
  const lat = Number(body.lat), lon = Number(body.lon);
  const radius = Math.min(Math.max(Number(body.radius_km) || 30, 1), 100);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    context.res = json(422, { detail: "lat/lon inválidos" });
    return;
  }

  let aircraft = null, source = null;
  for (const [name, fn] of [["adsb.lol", fromAdsbLol], ["opensky", fromOpenSky]]) {
    try { aircraft = await fn(lat, lon, radius); source = name; break; }
    catch (e) { context.log.warn(`fuente ${name} falló: ${e.message}`); }
  }
  if (!aircraft) {
    context.res = json(503, { detail: "Ninguna fuente de datos disponible" });
    return;
  }

  const found = aircraft
    .map((a) => ({ ...a,
      distance_km: Math.round(haversineKm(lat, lon, a.lat, a.lon) * 10) / 10,
      bearing: Math.round(bearingDeg(lat, lon, a.lat, a.lon)) }))
    .filter((a) => a.distance_km <= radius)
    .sort((a, b) => a.distance_km - b.distance_km);

  // rutas en paralelo (máx. 8 para no exceder tiempos ni límites de adsbdb)
  const withCs = found.filter((a) => a.callsign).slice(0, 8);
  const routes = await Promise.all(withCs.map((a) => lookupRoute(a.callsign)));
  withCs.forEach((a, i) => { a.route = routes[i]; });

  let nearest = null;
  try {
    const [ap] = await nearestAirports(lat, lon, 1, 150);
    if (ap) nearest = { ident: ap.ident, distance_km: ap.distance_km, name: ap.name,
                        iata: ap.iata, city: ap.city, country: ap.country };
  } catch (e) { context.log.warn(`aeropuertos no disponibles: ${e.message}`); }

  context.res = json(200, {
    status: "ok", source, served_by: "azure-functions", age_s: 0,
    radius_km: radius, count: found.length, aircraft: found, nearest_airport: nearest
  });
};
