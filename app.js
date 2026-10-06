// Aviones cercanos: GPS del navegador → API en la Raspberry Pi → alertas
(() => {
  const CFG = window.APP_CONFIG;
  const API = CFG.API_URL.replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);

  let pos = null, timer = null, swReg = null, firstFix = true;
  const alerted = new Map();          // hex -> momento de la última alerta
  const markers = new Map();          // hex -> marcador Leaflet

  // ---------- Mapa ----------
  const map = L.map("map").setView([4.65, -74.1], 8);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "© OpenStreetMap"
  }).addTo(map);
  const userMarker = L.circleMarker([0, 0], { radius: 8, color: "#38bdf8", fillOpacity: 1 });
  const radiusCircle = L.circle([0, 0], { radius: 30000, color: "#38bdf8", fillOpacity: 0.05 });

  // ---------- Utilidades ----------
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const place = (a) => a ? (a.city || a.name || a.iata || a.icao || "?") : "?";
  const routeText = (r) => r ? `${place(r.origin)} → ${place(r.destination)}` : "ruta desconocida";
  const compass = (deg) => ["N", "NE", "E", "SE", "S", "SO", "O", "NO"][Math.round(deg / 45) % 8];

  function setStatus(text, cls = "") {
    $("status").textContent = text;
    $("status").className = "status " + cls;
  }

  function toast(text) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    $("toasts").appendChild(el);
    setTimeout(() => el.remove(), 8000);
  }

  async function notify(title, body) {
    toast(`${title} — ${body}`);
    if (navigator.vibrate) navigator.vibrate([150, 80, 150]);
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    try {
      if (swReg) await swReg.showNotification(title, { body, tag: title });
      else new Notification(title, { body });
    } catch (e) { /* algunos navegadores no lo permiten; queda el toast */ }
  }

  // ---------- Consulta a la API ----------
  async function poll() {
    if (!pos) return;
    const radius = Number($("radius").value);
    let data;
    try {
      const resp = await fetch(`${API}/api/near`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: pos.lat, lon: pos.lon, radius_km: radius })
      });
      if (resp.status === 503) { setStatus("El servidor está degradado, reintentando…", "warn"); return; }
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      data = await resp.json();
    } catch (e) {
      setStatus("No se pudo contactar la API (" + e.message + "). Reintentando…", "err");
      return;
    }

    if (data.status === "warming") setStatus(data.message, "warn");
    else if (data.status === "stale") setStatus(`Datos desactualizados (hace ${data.age_s}s) — fuente: ${data.source}`, "warn");
    else setStatus(`Actualizado hace ${data.age_s}s — fuente: ${data.source}`);

    render(data);
    checkAlerts(data.aircraft || []);
  }

  function render(data) {
    const list = data.aircraft || [];
    $("count").textContent = `(${list.length})`;
    $("list").innerHTML = list.map((a) => `
      <li><b>${esc(a.callsign || a.hex)}</b> ${a.type ? "· " + esc(a.type) : ""}<br>
        ${esc(routeText(a.route))}${a.route && a.route.airline ? " · " + esc(a.route.airline) : ""}<br>
        <span class="muted">${a.distance_km} km al ${compass(a.bearing)} ·
        ${a.alt_m != null ? a.alt_m + " m" : (a.on_ground ? "en tierra" : "alt. ?")} ·
        ${a.speed_kmh != null ? a.speed_kmh + " km/h" : ""}</span></li>`).join("")
      || '<li class="muted">No hay aviones en tu radio ahora mismo.</li>';

    const ap = data.nearest_airport;
    $("airport").textContent = ap ? `Aeropuerto más cercano: ${ap.name} (${ap.iata || ap.ident}) a ${ap.distance_km} km` : "";

    // marcadores: actualizar, crear y borrar
    const seen = new Set();
    for (const a of list) {
      seen.add(a.hex);
      const icon = L.divIcon({
        className: "", iconSize: [22, 22],
        html: `<div class="plane" style="transform:rotate(${(a.track || 0) - 45}deg)">✈</div>`
      });
      const popup = `<b>${esc(a.callsign || a.hex)}</b><br>${esc(routeText(a.route))}<br>${a.distance_km} km`;
      if (markers.has(a.hex)) markers.get(a.hex).setLatLng([a.lat, a.lon]).setIcon(icon).setPopupContent(popup);
      else markers.set(a.hex, L.marker([a.lat, a.lon], { icon }).bindPopup(popup).addTo(map));
    }
    for (const [hex, m] of markers) if (!seen.has(hex)) { m.remove(); markers.delete(hex); }
  }

  function checkAlerts(list) {
    const now = Date.now();
    for (const a of list) {
      const last = alerted.get(a.hex);
      if (last && now - last < CFG.REALERT_MIN * 60000) continue;
      alerted.set(a.hex, now);
      const title = `✈ ${a.callsign || a.hex} cerca de ti`;
      const body = `${routeText(a.route)} · a ${a.distance_km} km al ${compass(a.bearing)}` +
                   (a.alt_m != null ? ` · ${a.alt_m} m` : "");
      notify(title, body);
    }
  }

  // ---------- GPS ----------
  function onPosition(p) {
    pos = { lat: p.coords.latitude, lon: p.coords.longitude };
    const ll = [pos.lat, pos.lon];
    userMarker.setLatLng(ll).addTo(map);
    radiusCircle.setLatLng(ll).setRadius(Number($("radius").value) * 1000).addTo(map);
    if (firstFix) { map.setView(ll, 9); firstFix = false; poll(); }
  }

  function onGeoError(e) {
    setStatus("No se pudo obtener tu ubicación: " + e.message +
              ". Revisa que el GPS y el permiso de ubicación estén activos.", "err");
  }

  $("start").addEventListener("click", async () => {
    if (!("geolocation" in navigator)) { setStatus("Este navegador no tiene geolocalización.", "err"); return; }
    if ("serviceWorker" in navigator) {
      try { swReg = await navigator.serviceWorker.register("sw.js"); } catch (e) { swReg = null; }
    }
    if ("Notification" in window && Notification.permission === "default") {
      try { await Notification.requestPermission(); } catch (e) { /* ignorar */ }
    }
    setStatus("Buscando tu ubicación…");
    navigator.geolocation.watchPosition(onPosition, onGeoError,
      { enableHighAccuracy: true, maximumAge: 15000, timeout: 20000 });
    if (!timer) timer = setInterval(poll, CFG.POLL_MS);
    $("start").textContent = "GPS activo";
    $("start").disabled = true;
  });

  $("radius").addEventListener("change", () => {
    radiusCircle.setRadius(Number($("radius").value) * 1000);
    poll();
  });
})();
