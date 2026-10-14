#!/usr/bin/env bash
# Despliegue continuo (pull-based): descarga las imágenes nuevas de ghcr.io y
# recrea SOLO los servicios cuya imagen cambió. Lo ejecuta cron cada 5 minutos.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVICIOS="api poller enricher"

ids() { for s in $SERVICIOS; do docker image inspect -f '{{.Id}}' "ghcr.io/helmuthj22/aviones-$s:latest" 2>/dev/null || echo none; done; }

antes=$(ids)
docker compose pull -q $SERVICIOS
despues=$(ids)

if [ "$antes" != "$despues" ]; then
  docker compose up -d --no-build $SERVICIOS
  docker image prune -f >/dev/null          # libera espacio en la microSD
  version=$(curl -s localhost:8000/health | python3 -c "import sys,json;print(json.load(sys.stdin).get('version','?'))" 2>/dev/null || echo "?")
  echo "$(date '+%F %T') nueva versión desplegada: $version"
else
  echo "$(date '+%F %T') sin cambios"
fi
