// Offline support. The stats file is always fetched fresh when online (falls back to the
// last saved copy offline); the page and its code are also network-first, so updates show right away.
const CACHE = '5ive-lab-v60';
const SHELL = ['./', 'index.html', 'engine.js', 'manifest.webmanifest', 'logo.png', 'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;

  if (url.pathname.includes('/data/')) { // network first
    e.respondWith(fetch(e.request).then((res) => {
      const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return res;
    }).catch(() => caches.match(e.request)));
    return;
  }

  // The page itself and its code: network first so updates show up immediately,
  // falling back to the saved copy when offline.
  const isShell = e.request.mode === 'navigate' || /\/(index\.html|engine\.js)?$/.test(url.pathname) || url.pathname.endsWith('.js') || url.pathname.endsWith('.json');
  if (isShell) {
    e.respondWith(fetch(e.request, { cache: 'no-cache' }).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => caches.match(e.request).then((r) => r || caches.match('index.html'))));
    return;
  }

  // icons, logo, manifest: stale-while-revalidate
  e.respondWith(caches.match(e.request).then((cached) => {
    const fresh = fetch(e.request).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
      return res;
    }).catch(() => cached);
    return cached || fresh;
  }));
});
