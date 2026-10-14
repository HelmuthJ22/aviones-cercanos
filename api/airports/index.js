// GET /api/airports?lat=4.7&lon=-74.1&n=5&max_km=200
// Aeropuertos (grandes y medianos de OurAirports) más cercanos a un punto. 100 % serverless.
const { nearestAirports, json } = require("../shared/geo");

module.exports = async function (context, req) {
  const q = req.query || {};
  const lat = Number(q.lat), lon = Number(q.lon);
  const n = Math.min(Math.max(parseInt(q.n, 10) || 5, 1), 50);
  const maxKm = Math.min(Math.max(Number(q.max_km) || 200, 1), 2000);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) {
    context.res = json(422, { detail: "Parámetros requeridos: lat y lon (ej. ?lat=4.7&lon=-74.1)" });
    return;
  }
  try {
    const list = await nearestAirports(lat, lon, n, maxKm);
    context.res = json(200, { served_by: "azure-functions", source: "OurAirports",
                              count: list.length, airports: list });
  } catch (e) {
    context.log.error(e);
    context.res = json(503, { detail: "No se pudo cargar la base de aeropuertos" });
  }
};
