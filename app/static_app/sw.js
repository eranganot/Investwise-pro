/* InvestWise PWA service worker */
const VERSION = 'iw-v2';
const SHELL_CACHE = `${VERSION}-shell`;
const RUNTIME_CACHE = `${VERSION}-runtime`;

// App shell — cached on install so the app opens instantly and works offline.
const SHELL_ASSETS = [
  '/app/',
  '/app/index.html',
  '/app/manifest.webmanifest',
  '/app/icon-192.png',
  '/app/icon-512.png',
  '/app/icon-maskable-512.png',
  '/app/apple-touch-icon.png',
  '/app/favicon-32.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      // Add individually so one missing asset doesn't abort the whole install.
      Promise.allSettled(SHELL_ASSETS.map((url) => cache.add(url)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle GET; let the browser deal with POST/PUT/DELETE (intake, edits, etc.).
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // Navigations (opening the app): network-first, fall back to cached shell offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => caches.match('/app/index.html').then((r) => r || caches.match('/app/')))
    );
    return;
  }

  // Live API data: always go to the network so financial figures are never stale.
  // (No offline cache — a stale portfolio is worse than a clear failure.)
  if (url.origin === self.location.origin && url.pathname.startsWith('/api/')) {
    return; // default browser handling
  }

  // Same-origin static assets (icons, manifest, html): cache-first.
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.match(req).then((cached) =>
        cached ||
        fetch(req).then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy));
          return res;
        }).catch(() => cached)
      )
    );
    return;
  }

  // Cross-origin (e.g. Chart.js CDN): stale-while-revalidate.
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req).then((res) => {
        if (res && (res.ok || res.type === 'opaque')) {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy));
        }
        return res;
      }).catch(() => cached);
      return cached || network;
    })
  );
});

// --- Push notifications ---
self.addEventListener('push', (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; }
  catch (e) { payload = { title: 'InvestWise', body: event.data ? event.data.text() : '' }; }
  const title = payload.title || 'InvestWise';
  const options = {
    body: payload.body || '',
    icon: '/app/icon-192.png',
    badge: '/app/icon-192.png',
    tag: payload.tag || 'investwise',
    renotify: true,
    data: { url: payload.url || '/app/', ...(payload.data || {}) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/app/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      for (const c of list) {
        if (c.url.includes('/app') && 'focus' in c) return c.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
