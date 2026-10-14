#!/usr/bin/env bash
# Despliegue continuo (pull-based): descarga las imágenes nuevas de ghcr.io y
# recrea SOLO los servicios cuya imagen cambió. Lo ejecuta cron cada 5 minutos.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICIOS="api poller enricher"

ids() { for s in $SERVICIOS; do docker image inspect -f '{{.Id}}' "ghcr.io/helmuthj22/aviones-$s:latest" 2>/dev/null || echo none; done; }

version_api() {   # espera hasta 60 s a que la API responda tras el reinicio
  for _ in $(seq 1 30); do
    v=$(curl -s --max-time 2 localhost:8000/health \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
    [ -n "$v" ] && { echo "$v"; return; }
    sleep 2
  done
  echo "sin respuesta de /health"
}

antes=$(ids)
docker compose pull -q $SERVICIOS >/dev/null 2>&1 \
  || { echo "$(date '+%F %T') ERROR: no se pudieron descargar las imágenes"; exit 1; }
despues=$(ids)

if [ "$antes" != "$despues" ]; then
  docker compose up -d --no-build $SERVICIOS >/dev/null 2>&1 \
    || { echo "$(date '+%F %T') ERROR: no se pudieron recrear los servicios"; exit 1; }
  docker image prune -f >/dev/null          # libera espacio en la microSD
  echo "$(date '+%F %T') nueva versión desplegada: $(version_api)"
else
  echo "$(date '+%F %T') sin cambios"
fi
