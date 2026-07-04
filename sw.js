const CACHE_NAME = 'hud-v3-cache-v1';
const ASSETS = [
    './',
    './index.html',
    './app.js',
    './manifest.json', // Обов'язково додаємо маніфест у кеш
    'https://cdn.tailwindcss.com',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
];

// Встановлення: кешуємо ресурси та активуємо SW негайно
self.addEventListener('install', e => {
    e.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
    );
    self.skipWaiting(); 
});

// Активація: очищаємо старі кеші
self.addEventListener('activate', e => {
    e.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.map(key => {
                if (key !== CACHE_NAME) return caches.delete(key);
            })
        ))
    );
    return self.clients.claim();
});

// Обробка запитів (Стратегія: мережа, якщо не вдалося — кеш)
self.addEventListener('fetch', e => {
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});

// --- ОБРОБКА КЛІКУ НА СПОВІЩЕННЯ ---
self.addEventListener('notificationclick', e => {
    e.notification.close(); // Закриваємо сповіщення

    e.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
            // Якщо додаток вже відкритий — фокусуємося на ньому
            for (const client of clientList) {
                if (client.url === '/' && 'focus' in client) return client.focus();
            }
            // Якщо закритий — відкриваємо нове вікно
            if (clients.openWindow) return clients.openWindow('./');
        })
    );
});
