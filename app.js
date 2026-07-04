/**
 * TACTICAL MONITOR CORE v3.6 - PERSISTENT EDITION
 * Features: Settings Memory, Layer Memory, Auto-Focus, Interactive Toggles
 */

const State = {
    targets: [],
    markers: new Map(),
    userCoords: null,
    
    // Налаштування, що зберігаються
    alertRadius: 50,
    activeLayerName: "Тактична (Темна)",
    missileAlerts: true,
    autoFocus: false,
    
    activePage: 'map',
    notifiedIds: new Set(),
    
    save() {
        const data = {
            alertRadius: this.alertRadius,
            activeLayerName: this.activeLayerName,
            missileAlerts: this.missileAlerts,
            autoFocus: this.autoFocus
        };
        localStorage.setItem('hud_state_v3', JSON.stringify(data));
    },
    
    load() {
        const saved = localStorage.getItem('hud_state_v3');
        if (saved) {
            const data = JSON.parse(saved);
            this.alertRadius = data.alertRadius ?? 50;
            this.activeLayerName = data.activeLayerName ?? "Тактична (Темна)";
            this.missileAlerts = data.missileAlerts ?? true;
            this.autoFocus = data.autoFocus ?? false;
        }
        this.syncUI();
    },

    syncUI() {
        // Оновлення текстових полів
        const radiusInput = document.getElementById('alert-radius');
        if (radiusInput) radiusInput.value = this.alertRadius;

        // Оновлення кнопок-перемикачів
        this.updateToggleVisual('toggle-missile', this.missileAlerts);
        this.updateToggleVisual('toggle-focus', this.autoFocus);
    },

    updateToggleVisual(id, isActive) {
        const btn = document.getElementById(id);
        if (!btn) return;
        const dot = btn.querySelector('div');
        if (isActive) {
            btn.classList.remove('bg-stone-800');
            btn.classList.add('bg-orange-600');
            dot.classList.add('translate-x-5'); // Плавне зміщення
        } else {
            btn.classList.remove('bg-orange-600');
            btn.classList.add('bg-stone-800');
            dot.classList.remove('translate-x-5');
        }
    }
};

// --- ДОПОМІЖНІ ФУНКЦІЇ ---

function getDistance(lat1, lon1, lat2, lon2) {
    const R = 6371; 
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
              Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
              Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c; 
}

// --- ОСНОВНИЙ ОБ'ЄКТ UI ---

const ui = {
    map: null,
    userMarker: null,
    ICON_PATH: 'img/',
    
    tileLayers: {
        "Тактична (Темна)": L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png'),
        "Супутник": L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'),
        "Топографія": L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png'),
        "Теплова (Light)": L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png')
    },

    init() {
        State.load(); // Завантажуємо пам'ять перед стартом

        this.map = L.map('map', { zoomControl: false, attributionControl: false }).setView([49.0, 31.0], 6);

        // Встановлюємо збережений шар
        const baseLayer = this.tileLayers[State.activeLayerName] || this.tileLayers["Тактична (Темна)"];
        baseLayer.addTo(this.map);

        L.control.layers(this.tileLayers, null, { collapsed: true }).addTo(this.map);

        // Зберігаємо вибір шару при зміні
        this.map.on('baselayerchange', (e) => {
            State.activeLayerName = e.name;
            State.save();
        });

        this.initGeolocation();
        if ("Notification" in window) Notification.requestPermission();

        // Події для налаштувань
        document.getElementById('alert-radius')?.addEventListener('change', (e) => {
            State.alertRadius = parseFloat(e.target.value) || 50;
            State.save();
            State.notifiedIds.clear();
            this.notify(`РАДІУС: ${State.alertRadius} КМ`, "info");
        });
    },

    toggleSetting(key) {
        State[key] = !State[key];
        State.save();
        State.syncUI();
        const label = key === 'missileAlerts' ? 'СПОВІЩЕННЯ' : 'АВТОФОКУС';
        this.notify(`${label}: ${State[key] ? 'УВІМК' : 'ВИМК'}`, "warning");
    },

    initGeolocation() {
        if (!("geolocation" in navigator)) return;
        navigator.geolocation.watchPosition(
            (pos) => {
                State.userCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
                this.updateUserMarker();
            },
            (err) => console.error("GPS Error:", err),
            { enableHighAccuracy: true }
        );
    },

    updateUserMarker() {
        if (!State.userCoords) return;
        const coords = [State.userCoords.lat, State.userCoords.lng];
        if (this.userMarker) {
            this.userMarker.setLatLng(coords);
        } else {
            this.userMarker = L.circleMarker(coords, {
                radius: 8, color: '#22c55e', fillColor: '#22c55e', fillOpacity: 0.8
            }).addTo(this.map).bindPopup("ВАША ПОЗИЦІЯ");
        }
    },

    updateMarkers() {
        const currentIds = new Set(State.targets.map(t => String(t.id)));

        State.markers.forEach((marker, id) => {
            if (!currentIds.has(id)) {
                this.map.removeLayer(marker);
                State.markers.delete(id);
                this.notify(`ОБ'ЄКТ ${id} ЗНИК`, "warning");
            }
        });

        State.targets.forEach(t => {
            const id = String(t.id);
            const iconUrl = `${this.ICON_PATH}${t.type}.png`;
            const rotation = (typeof t.bearing === 'number') ? t.bearing : 0;

            const customIcon = L.icon({
                iconUrl: iconUrl,
                iconSize: [32, 32],
                iconAnchor: [16, 16],
                popupAnchor: [0, -16],
                className: (t.type === 'missile' || t.type === 'kab') ? 'threat-pulse' : ''
            });

            if (State.markers.has(id)) {
                const m = State.markers.get(id);
                m.setLatLng([t.lat, t.lng]);
                m.setIcon(customIcon);
                if (m.setRotationAngle) m.setRotationAngle(rotation);
            } else {
                const m = L.marker([t.lat, t.lng], {
                    icon: customIcon,
                    rotationAngle: rotation,
                    rotationOrigin: 'center center'
                }).addTo(this.map);
                const dirLine = t.direction ? `<br><span style="opacity:.7">Напрямок: ${t.direction}</span>` : '';
                m.bindPopup(`<b>${t.label}</b>${dirLine}`);
                State.markers.set(id, m);
            }
        });
    },

checkThreats() {
        if (!State.userCoords || !State.missileAlerts) return;

        State.targets.forEach(t => {
            const distance = getDistance(State.userCoords.lat, State.userCoords.lng, t.lat, t.lng);
            
            // Якщо ціль увійшла в радіус і ми про неї ще не сповіщали
            if (distance <= State.alertRadius && !State.notifiedIds.has(t.id)) {
                
                this.sendPush(t, distance);
                State.notifiedIds.add(t.id); // Фіксуємо, що сповіщення відправлено

                if (State.autoFocus) {
                    this.focusTarget(t.lat, t.lng);
                }
            } 
            
            // Опціонально: якщо ціль вийшла далеко за межі радіуса (наприклад, +10км), 
            // можна видалити її з notifiedIds, щоб при повторному наближенні знову спрацювало.
            // Якщо хочете суворо 1 раз за весь час сесії — цей блок можна видалити.
            if (distance > State.alertRadius + 10 && State.notifiedIds.has(t.id)) {
                State.notifiedIds.delete(t.id);
            }
        });
    },

    sendPush(target, dist) {
        const title = "⚠️ ТАКТИЧНА ЗАГРОЗА";
        const options = {
            body: `Ціль: ${target.label} | Дистанція: ${dist.toFixed(1)} км`,
            icon: `${this.ICON_PATH}${target.type}.png`,
            badge: `${this.ICON_PATH}missile.png`, // Маленька іконка для статус-бару
            vibrate: [500, 110, 500, 110, 450],
            tag: target.id, // Важливо: повідомлення з однаковим tag замінюють одне одного
            renotify: true,
            data: { lat: target.lat, lng: target.lng }
        };

        // Виклик через Service Worker дозволяє сповіщенню з'явитися, навіть якщо браузер згорнуто
        if ('serviceWorker' in navigator && Notification.permission === 'granted') {
            navigator.serviceWorker.ready.then(registration => {
                registration.showNotification(title, options);
            });
        }
        
        this.notify(`PUSH: ${target.label} (${dist.toFixed(1)} км)`, "danger");
    },

    focusTarget(lat, lng) {
        router.go('map');
        setTimeout(() => {
            if (this.map) this.map.flyTo([lat, lng], 10, { duration: 1.5 });
        }, 300);
    },

    renderTargetsList() {
        const container = document.getElementById('targets-container');
        if (!container) return;

        container.innerHTML = State.targets.map(t => {
            const iconUrl = `${this.ICON_PATH}${t.type}.png`;
            const dist = State.userCoords ? 
                getDistance(State.userCoords.lat, State.userCoords.lng, t.lat, t.lng).toFixed(1) : '--';

            return `
                <div class="glass p-3 rounded-lg flex items-center gap-3 border-l-4 ${t.type === 'missile' ? 'border-red-600' : 'border-orange-500'} active:scale-95 transition-transform" 
                     onclick="ui.focusTarget(${t.lat}, ${t.lng})">
                    <div class="w-10 h-10 flex-shrink-0 bg-black/40 rounded flex items-center justify-center border border-white/10">
                        <img src="${iconUrl}" class="w-8 h-8 object-contain" onerror="this.src='${this.ICON_PATH}default.png'">
                    </div>
                    <div class="flex-grow overflow-hidden text-left">
                        <h4 class="font-bold text-[11px] truncate uppercase text-orange-400">${t.label}</h4>
                        <p class="text-[10px] opacity-60 font-mono">DIST: ${dist} KM | ${t.id}</p>
                    </div>
                    <div class="text-right font-mono text-[10px] text-orange-500">
                        ${t.lat.toFixed(2)}<br>${t.lng.toFixed(2)}
                    </div>
                </div>
            `;
        }).join('');
    },

    notify(text, type) {
        const log = document.getElementById('logs-container');
        if (!log) return;
        const entry = document.createElement('div');
        const color = type === 'danger' ? 'border-red-600' : (type === 'warning' ? 'border-yellow-600' : 'border-blue-600');
        entry.className = `p-2 border-l-2 ${color} bg-white/5 mb-1 text-[10px] font-mono`;
        entry.innerHTML = `<span class="opacity-40">[${new Date().toLocaleTimeString()}]</span> ${text}`;
        log.prepend(entry);
        if (log.children.length > 30) log.lastChild.remove();
    }
};

// --- ПАРСИНГ ТА РОУТЕР (Без змін) ---

const Parser = {
    async fetchData() {
        try {
            const response = await fetch(`targets.json?nocache=${Date.now()}`);
            if (!response.ok) throw new Error("Link Lost");
            return await response.json();
        } catch (e) {
            ui.notify("ПОМИЛКА СИНХРОНІЗАЦІЇ", "danger");
            return null;
        }
    }
};

const router = {
    go(pageId) {
        if (State.activePage === pageId) return;
        const oldP = `#page-${State.activePage}`, newP = `#page-${pageId}`;
        gsap.to(oldP, { x: -20, opacity: 0, duration: 0.2, onComplete: () => {
            document.querySelector(oldP).classList.remove('active');
            document.querySelector(newP).classList.add('active');
            gsap.fromTo(newP, { x: 20, opacity: 0 }, { x: 0, opacity: 1, duration: 0.3 });
            if (pageId === 'map') ui.map.invalidateSize();
        }});
        State.activePage = pageId;
        this.updateNav();
    },
    updateNav() {
        document.querySelectorAll('.nav-btn').forEach(btn => {
            const isAct = btn.id === `nav-${State.activePage}`;
            btn.style.opacity = isAct ? "1" : "0.5";
            btn.style.color = isAct ? "#f97316" : "#a8a29e";
        });
    }
};

async function engine() {
    const data = await Parser.fetchData();
    if (data) {
        State.targets = data;
        ui.updateMarkers();
        ui.renderTargetsList();
        ui.checkThreats();
        const cnt = document.getElementById('obj-count');
        if (cnt) cnt.innerText = data.length;
    }
}

window.onload = () => {
    ui.init();
    router.updateNav();
    setInterval(() => {
        const c = document.getElementById('clock');
        if (c) c.innerText = new Date().toLocaleTimeString('uk-UA');
    }, 1000);
    engine();
    setInterval(engine, 5000);
};
