// Cambia esta URL por la que te da el túnel de Cloudflare (ver README, paso 6).
window.APP_CONFIG = {
  API_URL: "https://words-docs-ferry-ensure.trycloudflare.com",
  POLL_MS: 10000,               // cada cuánto consultar la API
  REALERT_MIN: 15               // no repetir alerta del mismo avión antes de 15 min
};
