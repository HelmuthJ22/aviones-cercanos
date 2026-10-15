#!/usr/bin/env bash
# Enciende / apaga todo el sistema "Aviones cercanos" en la Raspberry Pi
# (K3s + pods + tareas de cron) sin apagar la Raspberry.
#   ./scripts/servicio.sh apagar     → libera RAM/CPU, no arranca al reiniciar
#   ./scripts/servicio.sh encender   → arranca todo y actualiza la URL del túnel
#   ./scripts/servicio.sh estado
set -uo pipefail
cd "$(dirname "$0")/.."
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
export PATH="/usr/local/bin:$PATH"
CRON_BACKUP="$HOME/.crontab.aviones"

case "${1:-}" in
  apagar)
    # 1. pausar tareas automáticas (se guardan para restaurarlas al encender)
    if crontab -l >/dev/null 2>&1; then crontab -l > "$CRON_BACKUP"; crontab -r; fi
    # 2. detener Kubernetes y sus pods
    sudo systemctl stop k3s
    sudo /usr/local/bin/k3s-killall.sh >/dev/null 2>&1 || true
    docker ps -q --filter "name=k8s_" | xargs -r docker stop >/dev/null
    # 3. que no arranque solo al encender la Raspberry
    sudo systemctl disable k3s >/dev/null 2>&1
    echo "Sistema APAGADO. La página de Azure sigue respondiendo con Azure Functions (serverless)."
    free -h | head -2
    ;;

  encender)
    sudo systemctl enable --now k3s
    [ -f "$CRON_BACKUP" ] && crontab "$CRON_BACKUP"
    echo "Esperando a Kubernetes..."
    until kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 5; done
    echo "Esperando a los pods (puede tardar 2-4 min)..."
    kubectl -n aviones wait --for=condition=available deployment --all --timeout=600s
    sleep 15   # el túnel necesita unos segundos para publicar su URL
    python3 scripts/actualizar_url.py
    echo "Sistema ENCENDIDO. En 1-2 min Azure publica la página con la URL nueva."
    ;;

  estado)
    echo "== K3s: $(systemctl is-active k3s) (al arrancar la RPi: $(systemctl is-enabled k3s 2>/dev/null))"
    kubectl -n aviones get pods 2>/dev/null || echo "   (Kubernetes apagado)"
    echo "== API:"; curl -s --max-time 3 localhost:30800/health || echo "   sin respuesta"; echo
    echo "== Cron:"; crontab -l 2>/dev/null | grep -v '^#' || echo "   tareas pausadas"
    echo "== Recursos:"; free -h | head -2; vcgencmd measure_temp 2>/dev/null
    ;;

  *)
    echo "Uso: $0 {apagar|encender|estado}"; exit 1 ;;
esac
