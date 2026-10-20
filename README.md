# ✈️ Aviones cercanos

Aplicación web que avisa al usuario cuando un avión pasa cerca de su ubicación, con datos como estos:

>
> **AVA9472 · A320** — Bogotá → Bucaramanga · Avianca · a 17,9 km al N · 4.884 m · 701 km/h
>

El sistema cruza en tiempo real las **posiciones ADS-B** de los aviones con el **GPS del celular** y con una base de **5.279 aeropuertos**. Usa solo **datos abiertos** y **software libre**.

- **Procesamiento:** Raspberry Pi 4 de 1 GB con Kubernetes (K3s), en el borde de la red (*edge computing*).
- **Frontend y respaldo:** Azure Static Web Apps y Azure Functions, ambos en el plan gratuito.
- **Costo de operación:** $0 al mes.

<!--DIAGRAMAS: quita esta línea de apertura y la de cierre cuando subas las imágenes a docs/


| Arquitectura de negocio                                                                    | Arquitectura técnica |
|--------------------------------------------------------------------------------------------|-----------------------|
| [![Arquitectura de negocio](docs/arquitectura-negocio.png)](docs/arquitectura-negocio.png) |
| [![Arquitectura técnica](docs/arquitectura-tecnica.png)](docs/arquitectura-tecnica.png)   |


>
> Los diagramas se pueden editar en [draw.io](https://app.diagrams.net) desde `docs/aviones_cercanos_arquitectura.drawio`.
> -->
>

---

## Contenido

- [Arquitectura](#arquitectura)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Tecnologías](#tecnolog%C3%ADas)
- [API](#api)
- [Puesta en marcha](#puesta-en-marcha)
- [CI/CD](#cicd)
- [Resiliencia](#resiliencia)
- [Observabilidad](#observabilidad)
- [Seguridad y privacidad](#seguridad-y-privacidad)
- [Operación diaria](#operaci%C3%B3n-diaria)
- [Lecciones aprendidas en una RPi de 1 GB](#lecciones-aprendidas-en-una-rpi-de-1-gb)
- [Fuentes de datos y licencias](#fuentes-de-datos-y-licencias)

---

## Arquitectura

**Cómo funciona una consulta**

1. El celular carga la página desde Azure y obtiene la ubicación con la Geolocation API.
2. Cada 10 s envía su posición a la API de la RPi (`POST /api/near`) a través de un túnel de Cloudflare. La RPi no abre ningún puerto.
3. La API redondea la posición a una **celda de 1° × 1°** y busca en Valkey los aviones de esa celda. Luego calcula distancia y rumbo, agrega la ruta del vuelo y el aeropuerto más cercano (`GEOSEARCH`).
4. El **poller** consulta adsb.lol solo para las celdas con usuarios activos, con una consulta por celda sin importar cuántos usuarios haya. El **enricher** completa las rutas de vuelo y carga los aeropuertos.
5. Si la RPi no responde en 6 s, la página usa **Azure Functions** (mismo contrato de API) durante 60 s y después vuelve a la RPi sola.


### Microservicios (5 contenedores)


| Contenedor | Imagen                                | Puerto | RAM (req–lím) | Función                                       |
|------------|---------------------------------------|--------|------------------|------------------------------------------------|
| `api`      | `ghcr.io/helmuthj22/aviones-api`      | 8000   | 48–128 Mi      | API REST, métricas, límite de peticiones     |
| `poller`   | `ghcr.io/helmuthj22/aviones-poller`   | —    | 32–96 Mi       | Posiciones ADS-B (adsb.lol → OpenSky)        |
| `enricher` | `ghcr.io/helmuthj22/aviones-enricher` | —    | 32–160 Mi      | Rutas por callsign y 5.279 aeropuertos         |
| `valkey`   | `valkey/valkey:8-alpine`              | 6379   | 32–96 Mi       | Datos en memoria con TTL e índice geográfico |
| `tunnel`   | `cloudflare/cloudflared`              | —    | 24–64 Mi       | HTTPS público sin abrir puertos               |


Todo el sistema usa **~136 MB de RAM**.

---

## Estructura del repositorio

```.
 
├── index.html, app.js, style.css    # Frontend (Azure Static Web Apps)  
├── config.js                        # URL de la API (se actualiza sola)  
├── estado.html                      # Panel de estado del sistema  
├── sw.js                            # Service Worker (notificaciones)  
├── staticwebapp.config.json         # Rutas, runtime Node 20, bloquea /backend/*  
├── api/                             # Azure Functions: /api/near y /api/airports (respaldo)  
├── backend/  
│   ├── api/                         # FastAPI  
│   ├── poller/                      # Recolector de posiciones  
│   ├── enricher/                    # Rutas y aeropuertos  
│   ├── tests/                       # 19 pruebas (pytest)  
│   ├── k8s/aviones.yaml             # Manifiestos de Kubernetes  
│   ├── docker-compose.yml           # Alternativa sin Kubernetes  
│   └── scripts/                     # despliegue, URL del túnel, encendido/apagado  
└── .github/workflows/               # CI/CD del backend y despliegue de la SWA
```

---

## Tecnologías


| Capa           | Tecnología                                                  |
|----------------|--------------------------------------------------------------|
| Frontend       | HTML + JavaScript, Leaflet, MapLibre GL, teselas OpenFreeMap |
| Backend        | Python 3, FastAPI, Uvicorn, redis-py                         |
| Datos          | Valkey 8 (fork libre de Redis): GEO, TTL, LRU                |
| Contenedores   | Docker (imágenes ARM64)                                     |
| Orquestación  | K3s (Kubernetes certificado por la CNCF)                     |
| Serverless     | Azure Functions (Node 20, plan gratuito)                     |
| Red            | Cloudflare Quick Tunnel                                      |
| CI/CD          | GitHub Actions, GHCR, Trivy                                  |
| Observabilidad | Prometheus client (`/metrics`), panel `estado.html`          |


---

## API

Documentación interactiva (OpenAPI 3.1): `https://<url-del-túnel>/docs`

### `POST /api/near`

```json
{ "lat": 4.71, "lon": -74.07, "radius_km": 30 }
```

Respuesta:

```json
{  
  "status": "ok",  
  "source": "adsb.lol",  
  "age_s": 4,  
  "radius_km": 30,  
  "count": 2,  
  "aircraft": [  
    { "callsign": "AVA9472", "type": "A320", "distance_km": 17.9, "bearing": 2,  
      "alt_m": 4884, "speed_kmh": 701,  
      "route": { "airline": "Avianca",  
                 "origin": { "iata": "BOG", "city": "Bogota" },  
                 "destination": { "iata": "BGA", "city": "Bucaramanga" } } }  
  ],  
  "nearest_airport": { "ident": "SKBO", "name": "El Dorado International Airport",  
                       "iata": "BOG", "distance_km": 32.2 }  
}
```

`status` puede valer:

- `ok`: datos frescos.
- `warming`: es la primera consulta en esa zona y los datos llegan en unos segundos.
- `stale`: se entrega el último dato conocido.


| Endpoint         | Descripción                                                            |
|------------------|-------------------------------------------------------------------------|
| `GET /health`    | Estado de la API, Valkey y poller, más la versión (commit) desplegada |
| `GET /api/stats` | Estadísticas de la plataforma (usado por `estado.html`)                |
| `GET /metrics`   | Métricas en formato Prometheus                                         |
| `GET /docs`      | Swagger UI                                                              |


---

## Puesta en marcha

### Requisitos

- Raspberry Pi 4 (1 GB es suficiente) con **Raspberry Pi OS de 64 bits**. Verifica que `uname -m` muestre `aarch64`.
- Cuenta de GitHub y suscripción de Azure (plan gratuito).

### 1. Docker y K3s en la RPi

```bash
curl -fsSL https://get.docker.com | sudo sh  
sudo usermod -aG docker $USER          # cerrar sesión y volver a entrar  

# Límites de memoria para contenedores  
sudo sed -i '1 s/$/ cgroup_enable=memory cgroup_memory=1/' /boot/firmware/cmdline.txt  
sudo reboot  

# K3s ligero (usa Docker como runtime, sin componentes innecesarios)  
sudo mkdir -p /etc/rancher/k3s  
sudo tee /etc/rancher/k3s/config.yaml <<'EOF'  
docker: true  
disable: [traefik, servicelb, local-storage]  
disable-cloud-controller: true  
disable-helm-controller: true  
disable-network-policy: true  
write-kubeconfig-mode: "0644"  
EOF  
curl -sfL https://get.k3s.io | sh -
```

### 2. Desplegar el backend

```bash
git clone https://github.com/<usuario>/<repo>.git ~/aviones-cercanos  
cd ~/aviones-cercanos/backend  
cp .env.example .env                   # GITHUB_TOKEN, GITHUB_REPO, ALLOWED_ORIGINS…  
kubectl apply -f k8s/aviones.yaml  
kubectl -n aviones get pods            # todos en Running  
curl localhost:30800/health
```

### 3. Frontend en Azure

1. Azure Portal → **Static Web Apps** → Crear (plan **Free**, origen: este repositorio).
2. App location: `/` · Api location: `api` · Output: *(vacío)*.
3. Pon la URL de la Static Web App en `ALLOWED_ORIGINS` del `.env`, sin `*` y sin `/` final.


### 4. Tareas automáticas (cron)

```cron
PATH=/usr/local/bin:/usr/bin:/bin  
*/5 * * * * ~/aviones-cercanos/backend/scripts/desplegar-k8s.sh >> ~/desplegar.log 2>&1  
*/2 * * * * cd ~/aviones-cercanos/backend && python3 scripts/actualizar_url.py >> ~/actualizar_url.log 2>&1
```

---

## CI/CD

```
push a backend/** ──► pytest (19 pruebas) ──► build ARM64 nativo ──► Trivy (CRITICAL) ──► ghcr.io  
                                                                                            │  
          RPi (cron cada 5 min): desplegar-k8s.sh ──► docker pull ──► kubectl rollout restart ◄┘
```

- **Despliegue tipo pull (GitOps):** la RPi descarga las imágenes; no se exponen puertos ni se instala un runner.
- Cada imagen se publica como `:latest` y `:<sha7>`. La versión en ejecución aparece en `GET /health`.
- Solo se reinician los servicios cuya imagen cambió, con **RollingUpdate** (`maxUnavailable: 0`), sin caída del servicio.
- Si una prueba falla o Trivy encuentra una vulnerabilidad crítica, la imagen no se publica.
- El frontend se publica automáticamente con el workflow de Azure Static Web Apps.

Pruebas locales:

```bash
cd backend  
pip install -r requirements-dev.txt  
python -m pytest -v tests
```

---

## Resiliencia


| Mecanismo                      | Qué pasa                                                                                           |
|--------------------------------|-----------------------------------------------------------------------------------------------------|
| **Circuit breaker por fuente** | 3 fallos (o un 429) pausan la fuente. adsb.lol pasa automáticamente a **OpenSky**                  |
| **Degradación controlada**    | Si no hay datos nuevos, se entrega el último con `status: "stale"` en lugar de fallar              |
| **Respaldo serverless**        | Si la RPi cae, la página usa Azure Functions y después vuelve sola a la RPi                       |
| **Autorecuperación**          | Liveness y readiness probes; Kubernetes recrea los pods que fallan                                  |
| **Actualizaciones sin corte**  | `preStop: sleep 5` + `terminationGracePeriodSeconds: 20` + PodDisruptionBudget                      |
| **Autoescalado**               | HPA de la API: 1–2 réplicas al 60 % de CPU                                                       |
| **URL del túnel**             | Tras un corte de luz o de internet, `actualizar_url.py` publica la URL nueva y Azure se redespliega |


**Prueba de cambio de fuente** (simula la caída de adsb.lol sin tocar el código):

```bash
kubectl -n aviones patch deployment poller --type merge \  
  -p '{"spec":{"template":{"spec":{"hostAliases":[{"ip":"127.0.0.1","hostnames":["api.adsb.lol"]}]}}}}'  
curl -s localhost:30800/metrics | grep aviones_poller_fetch  
# → result="fail",source="adsb.lol"  y  result="ok",source="opensky"  
  
# Volver a la normalidad  
kubectl -n aviones patch deployment poller --type json \  
  -p '[{"op":"remove","path":"/spec/template/spec/hostAliases"}]'
```

---

## Observabilidad

- **`/metrics`** (formato Prometheus):- API: `aviones_http_requests_total`, `aviones_http_request_duration_seconds`, `aviones_rate_limited_total`.
- Poller y enricher: `aviones_poller_fetch_total{source,result}`, `aviones_enricher_routes_total`.
- Estado de la plataforma: `aviones_airports_loaded`, `aviones_active_cells`, `aviones_valkey_up`.
- **`estado.html`:** panel en vivo con el estado de edge, Valkey, serverless, fuentes y rutas.
- El pod de la API tiene anotaciones `prometheus.io/*`, así que cualquier Prometheus lo descubre solo. No se instala Prometheus ni Grafana en la RPi porque ocupan más de 300 MB.

---

## Seguridad y privacidad

- **Sin puertos entrantes:** el túnel de Cloudflare es una conexión saliente.
- **CORS** restringido a la URL de la Static Web App (`ALLOWED_ORIGINS`).
- **Límite de 120 peticiones por minuto** por usuario. La IP se guarda solo como hash SHA-256 con TTL de 70 s; al pasar el límite responde `429` + `Retry-After`.
- **Trivy** escanea cada imagen en el pipeline.
- **Ubicación:** viaja en el cuerpo del POST (no queda en logs) y **no se almacena**.
- Los secretos (`.env`, token de GitHub) no se suben al repositorio.

---

## Operación diaria

```bash
cd ~/aviones-cercanos/backend  
./scripts/servicio.sh estado     # pods, API, cron, RAM y temperatura  
./scripts/servicio.sh apagar     # detiene K3s y cron; la página sigue con Azure Functions  
./scripts/servicio.sh encender   # arranca todo y publica la URL nueva del túnel
```

>
> Valkey no guarda datos en disco. Después de encender, el enricher recarga los aeropuertos. Si `aviones_airports_loaded` sigue en 0, ejecuta `kubectl -n aviones rollout restart deployment/enricher`.
>

---

## Lecciones aprendidas en una RPi de 1 GB

- **K3s + Docker ocupan ~370 MB:** la API arranca con 1 réplica y el HPA sube a 2 solo con carga.
- **Nunca correr Docker Compose y K3s a la vez:** se duplican los servicios, el sistema empieza a usar swap y el load sube a 12 o más.
- **Probes con `timeoutSeconds` de 3–5 s:** con 1 s, la sobrecarga reiniciaba pods sanos.
- **Sin `preStop`** se perdía ~1 de cada 150 peticiones en cada actualización. Con `preStop` no se pierde ninguna.
- **cron usa un `PATH` mínimo:** hay que agregar `/usr/local/bin` para que encuentre `kubectl`.

---

## Fuentes de datos y licencias


| Fuente                                                    | Uso                          | Licencia                          |
|-----------------------------------------------------------|------------------------------|-----------------------------------|
| [adsb.lol](https://adsb.lol)                              | Posiciones ADS-B (principal) | ODbL                              |
| [OpenSky Network](https://opensky-network.org)            | Posiciones ADS-B (respaldo)  | Uso no comercial / investigación |
| [adsbdb](https://www.adsbdb.com)                          | Rutas por callsign           | MIT                               |
| [OurAirports](https://ourairports.com)                    | Aeropuertos                  | Dominio público                  |
| [OpenFreeMap](https://openfreemap.org) / © OpenStreetMap | Mapa base                    | ODbL                              |


La cobertura depende de los receptores voluntarios de cada red: si en una zona no hay receptores, no se ven aviones aunque los haya.

---

**Autor:** Helmuth Rodriguez — Ingeniería de Telecomunicaciones, Universidad Militar Nueva Granada.

 
