// Service worker mínimo: en Android/Chrome las notificaciones del sistema
// solo funcionan a través de un service worker registrado.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: "window" }).then((cs) => cs[0] && cs[0].focus()));
});
