// Network first for everything; the cache only covers the shell when the server
// is unreachable, so an update to the page is never masked by a stale copy.
// The server fills in package.json's version when it serves this file, so a
// release retires the old shell cache without a hand-edited name.
const CACHE = 'annunciator-public-v__APP_VERSION__';
const SHELL = ['./', 'index.html', 'dashboard.css', 'instruments.css', 'motion.css', 'app.js', 'config.js', 'guide.html', 'fonts/Oxanium.ttf', 'fonts/FiraMono-Regular.ttf', 'fonts/FiraMono-Medium.ttf', 'manifest.webmanifest', 'icons/icon.svg'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith('annunciator-public-') && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith(fetch(e.request)
    .then((res) => {
      // Only a good copy may stand in offline; never cache a 404 or 500.
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
      }
      return res;
    })
    .catch(() => caches.match(e.request)));
});
