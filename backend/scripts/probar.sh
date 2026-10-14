#!/usr/bin/env bash
# Prueba rápida de la API desde la propia Raspberry Pi.
# Uso: ./scripts/probar.sh [lat] [lon] [radio_km]
LAT=${1:-4.70}     # por defecto: cerca de El Dorado, Bogotá
LON=${2:--74.14}
RAD=${3:-50}
API=${API:-http://localhost:8000}

echo "== /health"
curl -s "$API/health"; echo
echo "== /api/near ($LAT, $LON, ${RAD} km) — primera llamada (activa la celda)"
curl -s -X POST "$API/api/near" -H 'Content-Type: application/json' \
     -d "{\"lat\":$LAT,\"lon\":$LON,\"radius_km\":$RAD}" | head -c 400; echo
echo "... esperando 15 s a que el poller traiga datos ..."
sleep 15
curl -s -X POST "$API/api/near" -H 'Content-Type: application/json' \
     -d "{\"lat\":$LAT,\"lon\":$LON,\"radius_km\":$RAD}" | python3 -m json.tool | head -60
