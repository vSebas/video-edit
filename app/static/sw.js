/* Vlog Studio service worker — installable-PWA shell only.
 *
 * Caches the static app shell (HTML/JS/CSS/icons) so the app opens on the phone
 * without a fresh network round-trip, but NEVER caches /api, media, or video
 * responses — a cached cut/plan/render would show a stale review, exactly what
 * the design warns against. Those always hit the network.
 */
const CACHE = 'vlog-shell-v1';

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.add('/')).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Dynamic content is always fresh from the network — never cached.
  if (
    url.pathname.startsWith('/api/') ||
    url.pathname.startsWith('/media/') ||
    url.pathname.startsWith('/outputs/') ||
    request.destination === 'video' ||
    request.destination === 'audio'
  ) {
    return; // let the browser do its default (network)
  }

  const isNav = request.mode === 'navigate';
  const isShell =
    isNav ||
    url.pathname.startsWith('/icons/') ||
    ['document', 'script', 'style', 'image', 'manifest'].includes(request.destination);
  if (!isShell) return;

  // Network-first, fall back to the cached shell when offline.
  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((c) => c.put(isNav ? '/' : request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(isNav ? '/' : request).then((r) => r || caches.match('/')))
  );
});
