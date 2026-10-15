#!/usr/bin/env python3
"""
Mantiene sincronizada la URL del túnel rápido de Cloudflare con la página de Azure.

1. Lee la URL actual *.trycloudflare.com de los logs del contenedor del túnel.
2. Descarga config.js del repositorio de GitHub.
3. Si la URL cambió (p. ej. tras un corte de luz), sube config.js corregido.
   GitHub Actions publica de nuevo la página en Azure automáticamente.

Requiere en el archivo .env:
  GITHUB_TOKEN=<token con permiso "Contents: Read and write" sobre el repo>
  GITHUB_REPO=usuario/repositorio
  GITHUB_CONFIG_PATH=config.js        (opcional)
  GITHUB_BRANCH=main                  (opcional)

Uso: python3 scripts/actualizar_url.py   (lo ejecuta cron cada 2 minutos)
"""
import base64
import json
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
API_RE = re.compile(r'(API_URL:\s*")[^"]*(")')


def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def current_tunnel_url():
    """URL actual del túnel: primero en K3s (kubectl), si no, en Docker Compose."""
    import os
    env = {**os.environ, "KUBECONFIG": os.environ.get("KUBECONFIG", "/etc/rancher/k3s/k3s.yaml"),
           # cron usa un PATH mínimo (/usr/bin:/bin); kubectl de K3s está en /usr/local/bin
           "PATH": "/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")}
    fuentes = [
        ["kubectl", "-n", "aviones", "logs", "deploy/tunnel"],
        ["docker", "compose", "logs", "--no-color", "tunnel"],
    ]
    for cmd in fuentes:
        try:
            out = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=30, env=env)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
        urls = URL_RE.findall(out.stdout + out.stderr)
        if out.returncode == 0 and urls:
            return urls[-1]              # la más reciente
    return None


def gh(method, url, token, body=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "aviones-cercanos-updater")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def main():
    env = load_env()
    token, repo = env.get("GITHUB_TOKEN"), env.get("GITHUB_REPO")
    path = env.get("GITHUB_CONFIG_PATH", "config.js")
    branch = env.get("GITHUB_BRANCH", "main")
    if not token or not repo:
        sys.exit("Falta GITHUB_TOKEN o GITHUB_REPO en .env")

    new_url = current_tunnel_url()
    if not new_url:
        print("Aún no hay URL del túnel en los logs; se reintenta en la próxima ejecución.")
        return

    api = f"https://api.github.com/repos/{repo}/contents/{path}"
    try:
        info = gh("GET", f"{api}?ref={branch}", token)
    except urllib.error.HTTPError as e:
        sys.exit(f"No se pudo leer {path} en GitHub: {e.code} {e.reason}")

    content = base64.b64decode(info["content"]).decode()
    m = API_RE.search(content)
    if not m:
        sys.exit(f"No encontré API_URL en {path}")
    if new_url in m.group(0):
        print(f"Sin cambios: {new_url}")
        return

    updated = API_RE.sub(lambda mm: f"{mm.group(1)}{new_url}{mm.group(2)}", content, count=1)
    gh("PUT", api, token, {
        "message": f"Actualiza URL del túnel a {new_url}",
        "content": base64.b64encode(updated.encode()).decode(),
        "sha": info["sha"],
        "branch": branch,
    })
    print(f"config.js actualizado en GitHub → {new_url} (Azure se publicará en 1–2 min)")


if __name__ == "__main__":
    main()
