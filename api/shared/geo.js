// Utilidades compartidas por las funciones serverless.
// Sin dependencias externas: Node 20 trae fetch y AbortController.

const AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv";
const UA = "aviones-cercanos/1.0 (proyecto academico, serverless)";

function haversineKm(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const p1 = lat1 * r, p2 = lat2 * r, dp = (lat2 - lat1) * r, dl = (lon2 - lon1) * r;
  const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 6371 * 2 * Math.asin(Math.sqrt(a));
}

function bearingDeg(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const p1 = lat1 * r, p2 = lat2 * r, dl = (lon2 - lon1) * r;
  const x = Math.sin(dl) * Math.cos(p2);
  const y = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(x, y) * 180) / Math.PI + 360) % 360;
}

async function fetchJson(url, { timeoutMs = 6000, ...opts } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, {
      ...opts, signal: ctrl.signal,
      headers: { "User-Agent": UA, ...(opts.headers || {}) }
    });
    if (!resp.ok) {
      const err = new Error(`HTTP ${resp.status} en ${url}`);
      err.status = resp.status;
      throw err;
    }
    return await resp.json();
  } finally {
    clearTimeout(t);
  }
}

// ---------- CSV mínimo (soporta comillas, comas y saltos de línea dentro de campos) ----------
function parseCsv(text) {
  const rows = [];
  let row = [], field = "", inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
    } else if (c === '"') inQuotes = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (c !== "\r") field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows;
}

// ---------- Aeropuertos: se descargan una vez y quedan en memoria de la instancia ----------
let airportsCache = null;      // [{ident, name, iata, city, country, lat, lon}]
let airportsLoading = null;

function airportsFromCsv(text) {
  const [header, ...rows] = parseCsv(text);
  const idx = Object.fromEntries(header.map((h, i) => [h, i]));
  const out = [];
  for (const r of rows) {
    const type = r[idx.type];
    if (type !== "large_airport" && type !== "medium_airport") continue;
    const lat = parseFloat(r[idx.latitude_deg]), lon = parseFloat(r[idx.longitude_deg]);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    out.push({
      ident: r[idx.ident], name: r[idx.name], iata: r[idx.iata_code] || "",
      city: r[idx.municipality] || "", country: r[idx.iso_country] || "", lat, lon
    });
  }
  return out;
}

async function loadAirports() {
  if (airportsCache) return airportsCache;
  if (!airportsLoading) {
    airportsLoading = (async () => {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 20000);
      try {
        const resp = await fetch(AIRPORTS_URL, { signal: ctrl.signal, headers: { "User-Agent": UA } });
        if (!resp.ok) throw new Error(`OurAirports HTTP ${resp.status}`);
        airportsCache = airportsFromCsv(await resp.text());
        return airportsCache;
      } finally {
        clearTimeout(t);
        airportsLoading = null;
      }
    })();
  }
  return airportsLoading;
}

async function nearestAirports(lat, lon, n = 1, maxKm = 150) {
  const list = await loadAirports();
  return list
    .map((a) => ({ ...a, distance_km: Math.round(haversineKm(lat, lon, a.lat, a.lon) * 10) / 10 }))
    .filter((a) => a.distance_km <= maxKm)
    .sort((a, b) => a.distance_km - b.distance_km)
    .slice(0, n);
}

function json(status, body) {
  return {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    body
  };
}

module.exports = {
  haversineKm, bearingDeg, fetchJson, parseCsv, airportsFromCsv,
  loadAirports, nearestAirports, json, _setAirports: (a) => { airportsCache = a; }
};
