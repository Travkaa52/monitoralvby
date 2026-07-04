/**
 * Монитор Тривог — фронтенд
 * Опитує targets.json (публікує GitHub Actions), малює карту, стрічки тривог,
 * канали (декоративний локальний фільтр) і налаштування.
 */

const TYPE_META = {
  drone:    { bucket: 'drone',   label: 'Шахед',              icon: 'drone.png',    cls: 't-purple' },
  fpw:      { bucket: 'drone',   label: 'FPV дрон',           icon: 'fpw.png',      cls: 't-purple' },
  lancet:   { bucket: 'drone',   label: 'Ланцет',             icon: 'lancet.png',   cls: 't-purple' },
  molniya:  { bucket: 'drone',   label: 'Молнія',             icon: 'molniya.png',  cls: 't-purple' },
  recon:    { bucket: 'other',   label: 'Розвід. БПЛА',       icon: 'recon.png',    cls: 't-green'  },
  kab:      { bucket: 'missile', label: 'КАБ',                icon: 'kab.png',      cls: ''         },
  missile:  { bucket: 'missile', label: 'Ракета',             icon: 'missile.png',  cls: ''         },
  mrls:     { bucket: 'missile', label: 'РСЗО / Артилерія',   icon: 'mrls.png',     cls: ''         },
  aircraft: { bucket: 'air',     label: 'Літак',              icon: 'aircraft.png', cls: 't-amber'  },
};
const DEFAULT_META = { bucket: 'other', label: 'Ціль', icon: 'images.png', cls: 't-green' };
function meta(type){ return TYPE_META[type] || DEFAULT_META; }

const COMPASS = ['Північ','Північний схід','Схід','Південний схід','Південь','Південний захід','Захід','Північний захід'];
function bearingToCompass(deg){
  if (deg === null || deg === undefined || isNaN(deg)) return null;
  const idx = Math.round(((deg % 360) + 360) % 360 / 45) % 8;
  return COMPASS[idx];
}
function relTime(iso){
  if (!iso) return '';
  const diffMs = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return 'щойно';
  if (min < 60) return `${min} хв тому`;
  const h = Math.floor(min / 60);
  return `${h} год тому`;
}
function hhmm(iso){
  try { return new Date(iso).toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit' }); }
  catch(e){ return ''; }
}
function escapeHtml(s){
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function toast(msg){
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.remove('show'), 2200);
}

/* =============================================================
   STATE
   ============================================================= */
const Store = {
  KEY_SETTINGS: 'monitor_settings_v1',
  KEY_HISTORY:  'monitor_history_v1',
  KEY_WATCH:    'monitor_watch_v1',

  loadSettings(){
    const def = { sound: true, vibrate: true, priority: true, bgUpdate: 'Завжди', typeFilters: {}, mutedChannels: [] };
    try { return Object.assign(def, JSON.parse(localStorage.getItem(this.KEY_SETTINGS)) || {}); }
    catch(e){ return def; }
  },
  saveSettings(s){ localStorage.setItem(this.KEY_SETTINGS, JSON.stringify(s)); },

  loadHistory(){
    try { return JSON.parse(localStorage.getItem(this.KEY_HISTORY)) || []; }
    catch(e){ return []; }
  },
  saveHistory(h){ localStorage.setItem(this.KEY_HISTORY, JSON.stringify(h.slice(0, 300))); },

  loadWatch(){
    try { return JSON.parse(localStorage.getItem(this.KEY_WATCH)) || []; }
    catch(e){ return []; }
  },
  saveWatch(w){ localStorage.setItem(this.KEY_WATCH, JSON.stringify(w)); },
};

const State = {
  targets: [],       // текущий список из targets.json (после клиентских фильтров)
  rawTargets: [],     // без фильтрів (для лічильників всього)
  history: Store.loadHistory(),
  settings: Store.loadSettings(),
  channels: [],
  readIds: new Set(JSON.parse(sessionStorage.getItem('monitor_read_ids') || '[]')),
  activePage: 'map',
  alertsMode: 'active',
  channelsMode: 'mine',
  knownIds: new Set(),
  firstLoad: true,
};

function markRead(id){
  State.readIds.add(id);
  sessionStorage.setItem('monitor_read_ids', JSON.stringify([...State.readIds]));
}

function isChannelMuted(source){
  return State.settings.mutedChannels.includes((source || '').toLowerCase());
}
function isTypeEnabled(type){
  return State.settings.typeFilters[type] !== false;
}
function applyClientFilters(list){
  return list.filter(t => isTypeEnabled(t.type) && !isChannelMuted(t.source));
}

/* =============================================================
   MAP
   ============================================================= */
const MapView = {
  map: null,
  markers: new Map(),
  userMarker: null,
  layerIdx: 0,
  layers: [
    { name: 'Тактична', tile: () => L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', { subdomains: 'abcd', maxZoom: 19 }) },
    { name: 'Супутник', tile: () => L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 19 }) },
    { name: 'Топографія', tile: () => L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', { subdomains: 'abc', maxZoom: 17 }) },
  ],
  currentLayer: null,

  init(){
    this.map = L.map('map', { zoomControl: false, attributionControl: false }).setView([49.0, 31.0], 6);
    this.currentLayer = this.layers[0].tile().addTo(this.map);
  },
  cycleLayer(){
    this.layerIdx = (this.layerIdx + 1) % this.layers.length;
    this.map.removeLayer(this.currentLayer);
    this.currentLayer = this.layers[this.layerIdx].tile().addTo(this.map);
    toast('Шар карти: ' + this.layers[this.layerIdx].name);
  },
  iconFor(t){
    const m = meta(t.type);
    return L.divIcon({
      className: 'target-divicon',
      html: `<div style="width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;
              background:rgba(10,16,17,.85);border:1.5px solid ${this.colorFor(m.bucket)};box-shadow:0 0 10px ${this.colorFor(m.bucket)}66;">
              <img src="img/${m.icon}" style="width:18px;height:18px;object-fit:contain;" onerror="this.style.display='none'"/>
            </div>`,
      iconSize: [34, 34], iconAnchor: [17, 17],
    });
  },
  colorFor(bucket){
    return { drone: '#9b6bff', missile: '#ff4747', air: '#ffb020', other: '#2fe17f' }[bucket] || '#ff4747';
  },
  render(targets){
    const seen = new Set();
    targets.forEach(t => {
      seen.add(t.id);
      const lat = t.lat, lng = t.lng ?? t.lon;
      if (typeof lat !== 'number' || typeof lng !== 'number') return;
      let marker = this.markers.get(t.id);
      if (!marker) {
        marker = L.marker([lat, lng], { icon: this.iconFor(t), rotationOrigin: 'center center' }).addTo(this.map);
        marker.bindPopup(this.popupHtml(t));
        marker.on('click', () => Modal.open(t, 'active'));
        this.markers.set(t.id, marker);
      }
      if (typeof t.bearing === 'number' && marker.setRotationAngle) marker.setRotationAngle(t.bearing);
    });
    for (const [id, marker] of this.markers) {
      if (!seen.has(id)) { this.map.removeLayer(marker); this.markers.delete(id); }
    }
  },
  popupHtml(t){
    const m = meta(t.type);
    const dir = bearingToCompass(t.bearing);
    return `<div style="font-family:sans-serif;font-size:12px;min-width:140px;">
      <b>${escapeHtml(m.label)}</b><br/>${escapeHtml(t.label || '')}
      ${dir ? `<br/>Напрямок: ${dir}` : ''}<br/><span style="opacity:.6">${escapeHtml(t.time || '')}</span></div>`;
  },
  fitAll(){
    if (this.markers.size === 0) { this.map.setView([49.0, 31.0], 6); return; }
    const group = L.featureGroup([...this.markers.values()]);
    this.map.fitBounds(group.getBounds().pad(0.3));
  },
  locate(){
    if (!navigator.geolocation) { toast('Геолокація недоступна'); return; }
    navigator.geolocation.getCurrentPosition(pos => {
      const { latitude, longitude } = pos.coords;
      if (this.userMarker) this.map.removeLayer(this.userMarker);
      this.userMarker = L.circleMarker([latitude, longitude], {
        radius: 7, color: '#2fe17f', fillColor: '#2fe17f', fillOpacity: .5, weight: 2,
      }).addTo(this.map);
      this.map.setView([latitude, longitude], 10);
    }, () => toast('Не вдалося визначити місцезнаходження'));
  },
};

/* =============================================================
   RENDER: top bar / stats / radar / ppo / banner
   ============================================================= */
function renderStats(){
  const t = State.targets;
  const count = bucket => t.filter(x => meta(x.type).bucket === bucket).length;
  document.getElementById('stat-all').textContent = t.length;
  document.getElementById('stat-drone').textContent = count('drone');
  document.getElementById('stat-missile').textContent = count('missile');
  document.getElementById('stat-air').textContent = count('air');

  const badge = document.getElementById('nav-badge-alerts');
  if (t.length > 0) { badge.style.display = 'flex'; badge.textContent = t.length > 99 ? '99+' : t.length; }
  else badge.style.display = 'none';

  renderRadarBlips(t.length);

  document.getElementById('ppo-destroyed').textContent = State.history.length;
  const fillPct = Math.max(6, Math.min(100, 30 + t.length * 6));
  document.getElementById('ppo-fill').style.width = fillPct + '%';
}

function renderRadarBlips(count){
  const g = document.getElementById('radar-blips');
  g.innerHTML = '';
  const n = Math.min(count, 8);
  for (let i = 0; i < n; i++) {
    const angle = (i / Math.max(n, 1)) * Math.PI * 2 + i * 0.7;
    const r = 14 + (i % 3) * 9;
    const x = 50 + Math.cos(angle) * r, y = 50 + Math.sin(angle) * r;
    const c = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    c.setAttribute('cx', x.toFixed(1)); c.setAttribute('cy', y.toFixed(1));
    c.setAttribute('r', '2.4'); c.setAttribute('class', 'blip');
    g.appendChild(c);
  }
}

const SEVERITY_ORDER = { missile: 0, drone: 1, air: 2, other: 3 };
function renderBanner(){
  const banner = document.getElementById('alert-banner');
  const titleEl = document.getElementById('banner-title');
  const timeEl = document.getElementById('banner-time');
  const subEl = document.getElementById('banner-sub');

  if (State.targets.length === 0) {
    banner.classList.add('empty');
    titleEl.textContent = 'Загроз не виявлено';
    timeEl.textContent = '';
    subEl.textContent = 'Моніторинг триває';
    return;
  }
  banner.classList.remove('empty');
  const sorted = [...State.targets].sort((a, b) =>
    (SEVERITY_ORDER[meta(a.type).bucket] - SEVERITY_ORDER[meta(b.type).bucket]) ||
    (new Date(b.created_at) - new Date(a.created_at))
  );
  const top = sorted[0];
  titleEl.textContent = `${meta(top.type).label.toUpperCase()} — ${top.label || ''}`;
  timeEl.textContent = top.time || hhmm(top.created_at);
  subEl.textContent = relTime(top.created_at);
  banner.onclick = () => Modal.open(top, 'active');
}

/* =============================================================
   RENDER: alerts / history list
   ============================================================= */
function itemCardHtml(t, kind){
  const m = meta(t.type);
  const dir = bearingToCompass(t.bearing);
  const unread = kind === 'active' && !State.readIds.has(t.id);
  return `
  <div class="item-card" data-id="${escapeHtml(t.id)}" data-kind="${kind}">
    <div class="item-icon ${m.cls}"><img src="img/${m.icon}" onerror="this.style.display='none'"/></div>
    <div class="item-body">
      <div class="item-title ${m.cls}">${escapeHtml(m.label.toUpperCase())}${kind === 'history' ? ' · ЗНЕШКОДЖЕНО' : ''}</div>
      <div class="item-sub">${escapeHtml(t.label || '')}${dir ? ' · ' + dir : ''}</div>
      <div class="item-meta">${escapeHtml(t.source ? '@' + t.source : '')}</div>
    </div>
    <div class="item-right">
      <div class="item-time">${t.time || hhmm(t.created_at)}</div>
      <div style="font-size:9px;color:var(--text-faint);margin-top:2px;">${relTime(t.created_at)}</div>
      ${unread ? `<div class="item-dot ${m.cls}"></div>` : ''}
    </div>
  </div>`;
}

function renderAlertsList(){
  document.getElementById('count-active').textContent = State.targets.length;
  const list = document.getElementById('alerts-list');
  let items = [];
  if (State.alertsMode === 'active') items = State.targets.map(t => ({ t, kind: 'active' }));
  else if (State.alertsMode === 'history') items = State.history.map(t => ({ t, kind: 'history' }));
  else items = [
    ...State.targets.map(t => ({ t, kind: 'active' })),
    ...State.history.map(t => ({ t, kind: 'history' })),
  ].sort((a, b) => new Date(b.t.created_at) - new Date(a.t.created_at));

  if (items.length === 0) {
    list.innerHTML = emptyState('🛰', 'Тривог немає', 'Тут з’являться нові загрози');
    return;
  }
  list.innerHTML = items.map(({ t, kind }) => itemCardHtml(t, kind)).join('');
}

function renderHistoryPage(){
  const list = document.getElementById('history-list');
  if (State.history.length === 0) {
    list.innerHTML = emptyState('🕓', 'Історія порожня', 'Завершені тривоги з’являться тут');
    return;
  }
  list.innerHTML = State.history.map(t => itemCardHtml(t, 'history')).join('');
}

function emptyState(ic, msg, sub){
  return `<div class="empty-state"><div class="ic">${ic}</div><div class="msg">${escapeHtml(msg)}</div><div class="sub">${escapeHtml(sub)}</div></div>`;
}

function bindListClicks(containerId, kindResolver){
  document.getElementById(containerId).addEventListener('click', e => {
    const card = e.target.closest('.item-card');
    if (!card) return;
    const id = card.dataset.id;
    const kind = card.dataset.kind;
    const pool = kind === 'active' ? State.targets : State.history;
    const t = pool.find(x => String(x.id) === id);
    if (t) Modal.open(t, kind);
  });
}

/* =============================================================
   MODAL (detail sheet)
   ============================================================= */
const Modal = {
  open(t, kind){
    if (kind === 'active') { markRead(t.id); renderAlertsList(); }
    const m = meta(t.type);
    const dir = bearingToCompass(t.bearing) || t.direction || '—';
    const danger = m.bucket === 'missile' ? { text: 'ВИСОКИЙ РІВЕНЬ НЕБЕЗПЕКИ', cls: '' }
                 : m.bucket === 'drone' || m.bucket === 'air' ? { text: 'СЕРЕДНІЙ РІВЕНЬ НЕБЕЗПЕКИ', cls: '' }
                 : { text: 'НИЗЬКИЙ РІВЕНЬ НЕБЕЗПЕКИ', cls: '' };

    const sheet = document.getElementById('modal-sheet');
    sheet.innerHTML = `
      <div class="modal-head">
        <div>
          <div class="title">${escapeHtml(m.label)}</div>
          <div class="sub">${escapeHtml(t.label || '')} · ${relTime(t.created_at)}</div>
        </div>
        <button class="modal-close" id="modal-close-btn">✕</button>
      </div>
      <div class="modal-row"><span class="k">Тип загрози</span><span class="v">${escapeHtml(m.label)}</span></div>
      <div class="modal-row"><span class="k">Напрямок</span><span class="v">${escapeHtml(dir)}</span></div>
      <div class="modal-row"><span class="k">Ймовірна ціль</span><span class="v">${escapeHtml(t.label || '—')}</span></div>
      <div class="modal-row"><span class="k">Час виявлення</span><span class="v">${t.time || hhmm(t.created_at)}</span></div>
      <div class="modal-row"><span class="k">Джерело</span><span class="v">${t.source ? '@' + escapeHtml(t.source) : '—'}</span></div>
      <div class="modal-danger">${danger.text}</div>
      <div class="modal-actions">
        <button id="modal-share">↗ Поділитись</button>
        <button id="modal-mark">${kind === 'active' ? '✓ Позначити як прочитане' : 'Закрити'}</button>
      </div>`;

    document.getElementById('modal-close-btn').onclick = () => Modal.close();
    document.getElementById('modal-share').onclick = () => Modal.share(t, m);
    document.getElementById('modal-mark').onclick = () => Modal.close();

    document.getElementById('modal-overlay').classList.add('show');
  },
  close(){ document.getElementById('modal-overlay').classList.remove('show'); },
  share(t, m){
    const text = `${m.label}: ${t.label || ''} (${t.time || ''})`;
    if (navigator.share) navigator.share({ text }).catch(() => {});
    else if (navigator.clipboard) { navigator.clipboard.writeText(text); toast('Скопійовано'); }
    else toast(text);
  },
};
document.getElementById('modal-overlay').addEventListener('click', e => {
  if (e.target.id === 'modal-overlay') Modal.close();
});

/* =============================================================
   CHANNELS PAGE
   ============================================================= */
function renderChannels(){
  document.getElementById('count-channels').textContent = State.channels.length;
  const view = document.getElementById('channels-view');

  if (State.channelsMode === 'mine') {
    if (State.channels.length === 0) {
      view.innerHTML = emptyState('📡', 'Канали не завантажено', 'channels.json відсутній або порожній');
      return;
    }
    view.innerHTML = State.channels.map(c => {
      const on = !isChannelMuted(c.username);
      return `<div class="channel-card">
        <div class="channel-avatar">${c.emoji || '📡'}</div>
        <div style="flex:1;min-width:0;">
          <div class="channel-name">${escapeHtml(c.name)}</div>
          <div class="channel-handle">@${escapeHtml(c.username)}</div>
        </div>
        <div class="toggle ${on ? 'on' : ''}" data-channel="${escapeHtml(c.username)}"><i></i></div>
      </div>`;
    }).join('') + `<div class="hint-box">Перемикач приховує цілі з цього каналу лише на цьому пристрої. Щоб додати новий канал-джерело на сервері, потрібно оновити <b>SOURCE_CHANNELS</b> у секретах репозиторію (див. README).</div>`;

    view.querySelectorAll('.toggle').forEach(el => {
      el.addEventListener('click', () => {
        const username = el.dataset.channel.toLowerCase();
        const muted = State.settings.mutedChannels;
        const idx = muted.indexOf(username);
        if (idx >= 0) muted.splice(idx, 1); else muted.push(username);
        Store.saveSettings(State.settings);
        applyFiltersAndRender();
        renderChannels();
      });
    });
  } else {
    const watch = Store.loadWatch();
    view.innerHTML = `
      <div class="field">
        <label>Username каналу (без @)</label>
        <input id="add-channel-input" placeholder="наприклад: my_alert_channel" />
      </div>
      <div class="add-channel-btn" id="add-channel-btn">+ Додати до особистого списку</div>
      <div class="hint-box">Це особиста позначка на цьому пристрої — вона підсвічує канал у списку, але <b>не</b> додає його як джерело парсингу на сервері. Щоб канал реально почав скануватись, власник репозиторію додає його у <b>SOURCE_CHANNELS</b>.</div>
      ${watch.length ? '<div class="settings-label">Особистий список</div>' + watch.map(w => `
        <div class="channel-card"><div class="channel-avatar">⭐</div>
          <div style="flex:1;"><div class="channel-name">${escapeHtml(w)}</div><div class="channel-handle">@${escapeHtml(w)} · особисте</div></div>
        </div>`).join('') : ''}
    `;
    document.getElementById('add-channel-btn').onclick = () => {
      const input = document.getElementById('add-channel-input');
      const val = input.value.trim().replace(/^@/, '');
      if (!val) { toast('Вкажіть username каналу'); return; }
      const w = Store.loadWatch();
      if (!w.includes(val)) { w.push(val); Store.saveWatch(w); }
      toast('Додано до особистого списку');
      renderChannels();
    };
  }
}

/* =============================================================
   SETTINGS PAGE
   ============================================================= */
function renderSettingsToggles(){
  ['sound', 'vibrate', 'priority'].forEach(key => {
    const el = document.querySelector(`.toggle[data-setting="${key}"]`);
    el.classList.toggle('on', !!State.settings[key]);
  });

  const wrap = document.getElementById('type-filters');
  const seen = new Set();
  const rows = [];
  Object.entries(TYPE_META).forEach(([type, m]) => {
    if (seen.has(m.label)) return;
    seen.add(m.label);
    const on = isTypeEnabled(type);
    rows.push(`<div class="settings-row"><span class="ic"><img src="img/${m.icon}" style="width:16px;height:16px;object-fit:contain;" onerror="this.style.display='none'"/></span>
      <span class="label">${escapeHtml(m.label)}</span>
      <div class="toggle ${on ? 'on' : ''}" data-type-filter="${type}"><i></i></div></div>`);
  });
  wrap.innerHTML = rows.join('');
  wrap.querySelectorAll('[data-type-filter]').forEach(el => {
    el.addEventListener('click', () => {
      const type = el.dataset.typeFilter;
      State.settings.typeFilters[type] = !isTypeEnabled(type);
      Store.saveSettings(State.settings);
      el.classList.toggle('on', isTypeEnabled(type));
      applyFiltersAndRender();
    });
  });

  document.getElementById('row-bg-update').onclick = () => {
    const options = ['Завжди', 'Тільки при відкритому додатку', 'Ніколи'];
    const idx = (options.indexOf(State.settings.bgUpdate) + 1) % options.length;
    State.settings.bgUpdate = options[idx];
    Store.saveSettings(State.settings);
    document.querySelector('#row-bg-update .val').textContent = State.settings.bgUpdate;
    Poller.reschedule();
  };
  document.querySelector('#row-bg-update .val').textContent = State.settings.bgUpdate;

  document.getElementById('row-clear-history').onclick = () => {
    if (!confirm('Очистити всю історію тривог на цьому пристрої?')) return;
    State.history = [];
    Store.saveHistory([]);
    renderHistoryPage();
    renderStats();
    toast('Історію очищено');
  };

  document.getElementById('row-about').onclick = () => {
    const sheet = document.getElementById('modal-sheet');
    sheet.innerHTML = `<div class="modal-head"><div><div class="title" style="color:var(--green)">Про додаток</div>
      <div class="sub">Монитор Тривог</div></div><button class="modal-close" id="modal-close-btn">✕</button></div>
      <div style="font-size:13px;line-height:1.6;color:var(--text-dim);">
      Агрегує повідомлення з публічних Telegram-каналів моніторингу повітряних загроз
      і показує їх на карті. Дані оновлюються через GitHub Actions і можуть мати
      затримку 5–15 хвилин — це обмеження платформи, а не додатку.<br/><br/>
      Це допоміжний інструмент ситуативної обізнаності, а не офіційне джерело
      сповіщень про повітряну тривогу. Завжди орієнтуйтесь на офіційні сирени та
      застосунок «Повітряна тривога».</div>`;
    document.getElementById('modal-close-btn').onclick = () => Modal.close();
    document.getElementById('modal-overlay').classList.add('show');
  };

  document.querySelectorAll('.toggle[data-setting]').forEach(el => {
    el.addEventListener('click', () => {
      const key = el.dataset.setting;
      State.settings[key] = !State.settings[key];
      Store.saveSettings(State.settings);
      el.classList.toggle('on', State.settings[key]);
    });
  });
}

/* =============================================================
   NAV / TABS
   ============================================================= */
function goPage(page){
  State.activePage = page;
  document.querySelectorAll('.page').forEach(p => p.classList.toggle('active', p.id === 'page-' + page));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.toggle('active', b.dataset.page === page));
  if (page === 'map') setTimeout(() => MapView.map.invalidateSize(), 50);
}
document.querySelectorAll('.nav-btn').forEach(b => b.addEventListener('click', () => goPage(b.dataset.page)));

document.getElementById('alerts-subtabs').addEventListener('click', e => {
  const btn = e.target.closest('.subtab'); if (!btn) return;
  State.alertsMode = btn.dataset.mode;
  document.querySelectorAll('#alerts-subtabs .subtab').forEach(b => b.classList.toggle('active', b === btn));
  renderAlertsList();
});
document.getElementById('channels-subtabs').addEventListener('click', e => {
  const btn = e.target.closest('.subtab'); if (!btn) return;
  State.channelsMode = btn.dataset.mode;
  document.querySelectorAll('#channels-subtabs .subtab').forEach(b => b.classList.toggle('active', b === btn));
  renderChannels();
});

bindListClicks('alerts-list');
bindListClicks('history-list');

/* map FAB bindings */
document.getElementById('btn-layers').onclick = () => MapView.cycleLayer();
document.getElementById('btn-locate').onclick = () => MapView.locate();
document.getElementById('btn-filter').onclick = () => { goPage('settings'); };
document.getElementById('btn-zoom-in').onclick = () => MapView.map.zoomIn();
document.getElementById('btn-zoom-out').onclick = () => MapView.map.zoomOut();
document.getElementById('btn-center').onclick = () => MapView.fitAll();

/* =============================================================
   DATA CYCLE
   ============================================================= */
function applyFiltersAndRender(){
  State.targets = applyClientFilters(State.rawTargets);
  MapView.render(State.targets);
  renderStats();
  renderBanner();
  renderAlertsList();
}

async function fetchTargets(){
  try {
    const res = await fetch(`targets.json?nocache=${Date.now()}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();
    document.getElementById('conn-status').textContent = 'ONLINE';
    return Array.isArray(data) ? data : [];
  } catch (e) {
    document.getElementById('conn-status').textContent = 'НЕМАЄ ЗВ\u2019ЯЗКУ';
    return null;
  }
}

async function cycle(){
  const data = await fetchTargets();
  if (data === null) return;

  const newIds = new Set(data.map(t => t.id));
  if (!State.firstLoad) {
    for (const t of State.rawTargets) {
      if (!newIds.has(t.id)) {
        State.history.unshift(Object.assign({}, t, { resolved_at: new Date().toISOString() }));
      }
    }
    Store.saveHistory(State.history);
  }
  for (const t of data) {
    if (!State.knownIds.has(t.id) && !State.firstLoad) {
      // нова ціль — легка вібро-подія, якщо дозволено
      if (State.settings.vibrate && navigator.vibrate) navigator.vibrate(120);
    }
  }
  State.knownIds = newIds;
  State.rawTargets = data;
  State.firstLoad = false;

  applyFiltersAndRender();
  renderHistoryPage();
}

const Poller = {
  timer: null,
  reschedule(){
    clearInterval(this.timer);
    if (State.settings.bgUpdate === 'Ніколи') return;
    this.timer = setInterval(cycle, 5000);
  },
};

/* =============================================================
   INIT
   ============================================================= */
async function init(){
  MapView.init();
  renderSettingsToggles();

  try {
    const res = await fetch('channels.json', { cache: 'no-store' });
    State.channels = res.ok ? await res.json() : [];
  } catch (e) { State.channels = []; }
  renderChannels();

  renderHistoryPage();
  await cycle();
  Poller.reschedule();
}

document.addEventListener('DOMContentLoaded', init);
