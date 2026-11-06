// 서비스 워커: 앱 화면은 캐시해서 오프라인에서도 열리게, data.json은 항상 새로 받기(실패하면 캐시)
const CACHE = 'stock-finder-v6';
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icon-192.png', 'icon-512.png', 'apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  // data.json과 index.html은 네트워크 우선 (새 데이터·새 버전 반영)
  const networkFirst = url.pathname.endsWith('data.json') || url.pathname.endsWith('/') || url.pathname.endsWith('index.html');
  if (networkFirst) {
    e.respondWith(
      fetch(e.request).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(url.pathname.endsWith('data.json') ? 'data.json' : e.request, copy));
        return res;
      }).catch(() => caches.match(url.pathname.endsWith('data.json') ? 'data.json' : e.request))
    );
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});
