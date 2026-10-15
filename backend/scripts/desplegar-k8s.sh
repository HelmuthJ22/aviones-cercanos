#!/usr/bin/env bash
# Despliegue continuo sobre K3s (pull-based). Lo ejecuta cron cada 5 minutos.
# Descarga las imágenes nuevas de ghcr.io y hace "rollout restart" SOLO de los
# Deployments cuya imagen cambió (RollingUpdate: la API no se cae durante la actualización).
set -euo pipefail
export KUBECONFIG=${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}
NS=aviones
SERVICIOS="api poller enricher"

img() { echo "ghcr.io/helmuthj22/aviones-$1:latest"; }
id_de() { docker image inspect -f '{{.Id}}' "$(img "$1")" 2>/dev/null || echo none; }

version_api() {   # espera hasta 2 min a que la API responda con la versión nueva
  for _ in $(seq 1 60); do
    v=$(curl -s --max-time 2 localhost:30800/health \
        | python3 -c "import sys,json;print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)
    [ -n "$v" ] && { echo "$v"; return; }
    sleep 2
  done
  echo "sin respuesta de /health"
}

cambiados=""
for s in $SERVICIOS; do
  antes=$(id_de "$s")
  docker pull -q "$(img "$s")" >/dev/null 2>&1 \
    || { echo "$(date '+%F %T') ERROR: no se pudo descargar $(img "$s")"; exit 1; }
  if [ "$antes" != "$(id_de "$s")" ]; then cambiados="$cambiados $s"; fi
done

if [ -z "$cambiados" ]; then
  echo "$(date '+%F %T') sin cambios"
  exit 0
fi

for s in $cambiados; do
  kubectl -n "$NS" rollout restart "deployment/$s" >/dev/null
done
for s in $cambiados; do
  kubectl -n "$NS" rollout status "deployment/$s" --timeout=240s >/dev/null \
    || { echo "$(date '+%F %T') ERROR: el rollout de $s no terminó (revisa: kubectl -n $NS get pods)"; exit 1; }
done
docker image prune -f >/dev/null
echo "$(date '+%F %T') nueva versión desplegada en K3s:$cambiados → $(version_api)"
