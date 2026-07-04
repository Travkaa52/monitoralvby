import asyncio
import json
import os
import base64
import threading
import difflib
import time
import logging
from datetime import datetime, timedelta

import requests
from telethon import TelegramClient, events

import ai_classifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parser")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================
# --- НАЛАШТУВАННЯ TELEGRAM (з env / GitHub Secrets) ---
# =============================================
API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']

SOURCE_CHANNELS = [c.strip() for c in os.environ.get('SOURCE_CHANNELS', '').split(',') if c.strip()]
if not SOURCE_CHANNELS:
    raise RuntimeError("Не задано жодного каналу у SOURCE_CHANNELS (env)")

ADMIN_IDS = [a.strip() for a in os.environ.get('ADMIN_IDS', '').split(',') if a.strip()]
BOT_TOKEN = os.environ.get('BOT_TOKEN')

USE_AI = bool(os.environ.get('GEMINI_API_KEY'))

TARGET_TTL_MIN = int(os.environ.get('TARGET_TTL_MIN', '40'))
DEDUP_RADIUS_KM = float(os.environ.get('DEDUP_RADIUS_KM', '3'))
DEDUP_WINDOW_MIN = int(os.environ.get('DEDUP_WINDOW_MIN', '15'))

# =============================================
# --- НАЛАШТУВАННЯ GITHUB ---
# =============================================
GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
_repo_full = os.environ['GITHUB_REPOSITORY']
GITHUB_OWNER, GITHUB_REPO = _repo_full.split('/', 1)
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_FILE = os.environ.get('GITHUB_FILE', 'targets.json')

# =============================================
# --- КОНФІГ З ДИСКУ: geo.json + types.json ---
# Ці файли можна редагувати без змін коду.
# =============================================
def _load_json(name, default):
    path = os.path.join(BASE_DIR, name)
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Не вдалося прочитати {name}: {e} — використовую вбудований дефолт")
        return default


GEO_DATA = _load_json('geo.json', {})
TYPES_CFG = _load_json('types.json', {})

ALLOWED_TYPES = list(TYPES_CFG.keys()) or ["drone", "recon", "kab", "missile", "mrls", "aircraft"]

# Текстові напрямки → азимут (укр + рос, грубий фолбек)
DIRECTION_WORDS = {
    "північний схід": 45, "півнсхід": 45, "северо-восток": 45, "северовосток": 45,
    "південний схід": 135, "юго-восток": 135, "юговосток": 135,
    "південний захід": 225, "юго-запад": 225, "юго запад": 225,
    "північний захід": 315, "северо-запад": 315, "северозапад": 315,
    "північ": 0, "півн": 0, "север": 0, "north": 0,
    "схід": 90, "сх": 90, "восток": 90, "east": 90,
    "південь": 180, "півд": 180, "юг": 180, "south": 180,
    "захід": 270, "зах": 270, "запад": 270, "west": 270,
}

# =============================================
# --- ГЛОБАЛЬНИЙ СТАН ---
# =============================================
active_targets = []
_github_sha_cache = None
_consecutive_ai_failures = 0
_started_at = datetime.now()
_stats = {"messages_seen": 0, "targets_created": 0, "ai_fallbacks": 0, "github_push_fail": 0}


def notify_admins(text: str):
    if not BOT_TOKEN or not ADMIN_IDS:
        return
    for admin_id in ADMIN_IDS:
        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": admin_id, "text": f"⚠️ Monitor parser: {text}"},
                timeout=10
            )
        except Exception as e:
            log.warning(f"Не вдалося сповістити адміна {admin_id}: {e}")


# =============================================
# --- ГЕО: нечітке зіставлення населеного пункту ---
# =============================================
def match_city(location_text: str):
    if not location_text:
        return None, None
    key = location_text.strip().lower()
    if key in GEO_DATA:
        return key, GEO_DATA[key]
    matches = difflib.get_close_matches(key, GEO_DATA.keys(), n=1, cutoff=0.72)
    if matches:
        return matches[0], GEO_DATA[matches[0]]
    best = None
    for city in GEO_DATA:
        if city in key or key in city:
            if best is None or len(city) > len(best):
                best = city
    if best:
        return best, GEO_DATA[best]
    return None, None


def bearing_from_text(direction_text):
    if not direction_text:
        return None
    dt = direction_text.strip().lower()
    for word, deg in sorted(DIRECTION_WORDS.items(), key=lambda kv: -len(kv[0])):
        if word in dt:
            return deg
    return None


def haversine_km(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    r = 6371
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


# =============================================
# --- KEYWORD FALLBACK (якщо AI недоступний) ---
# =============================================
def extract_targets_keywords(text: str) -> list[dict]:
    text_l = text.lower()
    found = []

    detected_type = None
    for t_name, cfg in TYPES_CFG.items():
        keywords = cfg.get('keywords', [])
        if any(word in text_l for word in keywords):
            detected_type = t_name
            break
    if not detected_type:
        return []

    for city in GEO_DATA:
        if city in text_l:
            found.append({
                "type": detected_type,
                "location": city,
                "direction_text": None,
                "bearing_degrees": bearing_from_text(text_l),
                "confidence": 0.5
            })
            break
    return found


def extract_targets(text: str) -> list[dict]:
    global _consecutive_ai_failures
    if USE_AI:
        try:
            ai_targets = ai_classifier.analyze_message(text)
            _consecutive_ai_failures = 0
            return ai_targets
        except Exception as e:
            _consecutive_ai_failures += 1
            _stats["ai_fallbacks"] += 1
            log.warning(f"AI-класифікація впала ({_consecutive_ai_failures}), відкат на keyword-парсинг: {e}")
            if _consecutive_ai_failures in (1, 5, 20) or _consecutive_ai_failures % 50 == 0:
                notify_admins(f"Gemini недоступний ({_consecutive_ai_failures} раз поспіль), працюю на keyword-фолбеку: {e}")
    return extract_targets_keywords(text)


# =============================================
# --- GITHUB: PUSH targets.json (з ретраями) ---
# =============================================
def _get_github_sha():
    global _github_sha_cache
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
        if r.status_code == 200:
            _github_sha_cache = r.json().get("sha")
        elif r.status_code == 404:
            _github_sha_cache = None
    except Exception as e:
        log.warning(f"GitHub GET error: {e}")
    return _github_sha_cache


def push_to_github(retries=3):
    global _github_sha_cache

    local_path = os.path.join(BASE_DIR, 'targets.json')
    if not os.path.exists(local_path):
        return

    with open(local_path, 'rb') as f:
        content_b64 = base64.b64encode(f.read()).decode('utf-8')

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }

    for attempt in range(1, retries + 1):
        if _github_sha_cache is None:
            _get_github_sha()

        payload = {
            "message": f"update targets {datetime.now().strftime('%H:%M:%S')}",
            "content": content_b64,
            "branch": GITHUB_BRANCH
        }
        if _github_sha_cache:
            payload["sha"] = _github_sha_cache

        try:
            r = requests.put(url, json=payload, headers=headers, timeout=15)
            if r.status_code in (200, 201):
                _github_sha_cache = r.json().get("content", {}).get("sha")
                log.info(f"✅ GitHub: targets.json оновлено ({len(active_targets)} цілей)")
                return
            if r.status_code == 409:
                _github_sha_cache = None
                continue
            log.warning(f"GitHub push error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            log.warning(f"GitHub push exception (спроба {attempt}/{retries}): {e}")
        time.sleep(min(2 ** attempt, 10))

    _stats["github_push_fail"] += 1
    if _stats["github_push_fail"] in (1, 5, 20):
        notify_admins(f"не вдалось запушити targets.json у GitHub після {retries} спроб")


def push_to_github_async():
    threading.Thread(target=push_to_github, daemon=True).start()


def save_targets():
    path = os.path.join(BASE_DIR, 'targets.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(active_targets, f, ensure_ascii=False, indent=4)
    push_to_github_async()


async def cleaner():
    global active_targets
    while True:
        now = datetime.now()
        before = len(active_targets)
        active_targets = [t for t in active_targets if datetime.fromisoformat(t['expire_at']) > now]
        if len(active_targets) != before:
            save_targets()
            log.info(f"🧹 Видалено {before - len(active_targets)} застарілих цілей")
        await asyncio.sleep(60)


async def healthcheck_loop():
    """Раз на годину надсилає адмінам короткий звіт — щоб було видно, що парсер живий."""
    while True:
        await asyncio.sleep(3600)
        uptime = datetime.now() - _started_at
        notify_admins(
            "health-check ✅\n"
            f"uptime: {uptime}\n"
            f"повідомлень оброблено: {_stats['messages_seen']}\n"
            f"цілей створено: {_stats['targets_created']}\n"
            f"AI-фолбеків: {_stats['ai_fallbacks']}\n"
            f"невдалих push у GitHub: {_stats['github_push_fail']}\n"
            f"активних цілей зараз: {len(active_targets)}"
        )


# =============================================
# --- TELEGRAM КЛІЄНТ (кілька джерел) ---
# =============================================
client = TelegramClient('bot_session', API_ID, API_HASH)


def is_duplicate(lat: float, lng: float, detected_type: str, now: datetime) -> bool:
    """Дедуп не лише по точному місту, а й по близькості (щоб різні написання
    одного й того ж села не плодили дублікати) в межах вікна часу."""
    cutoff = now - timedelta(minutes=DEDUP_WINDOW_MIN)
    for t in active_targets:
        if t['type'] != detected_type:
            continue
        try:
            t_time = datetime.fromisoformat(t.get('created_at', t['expire_at']))
        except Exception:
            t_time = now
        if t_time < cutoff:
            continue
        if haversine_km(lat, lng, t['lat'], t['lng']) <= DEDUP_RADIUS_KM:
            return True
    return False


@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    text = event.raw_text
    if not text:
        return
    _stats["messages_seen"] += 1
    now = datetime.now()
    source_chat = getattr(event.chat, 'username', None) or str(event.chat_id)

    targets = extract_targets(text)
    if not targets:
        return

    for raw in targets:
        if raw.get("confidence", 1.0) < 0.4:
            continue

        city_key, geo = match_city(raw.get("location"))
        if not geo:
            log.info(f"ℹ️  Невідомий населений пункт у повідомленні: '{raw.get('location')}' — пропускаю")
            continue

        detected_type = raw["type"] if raw.get("type") in ALLOWED_TYPES else "drone"
        bearing = raw.get("bearing_degrees")
        if bearing is None:
            bearing = bearing_from_text(raw.get("direction_text") or text)

        if is_duplicate(geo[0], geo[1], detected_type, now):
            continue

        target = {
            "id":         f"{source_chat}_{event.id}_{city_key}",
            "type":       detected_type,
            "lat":        geo[0],
            "lng":        geo[1],
            "lon":        geo[1],
            "label":      f"{geo[2] if len(geo) > 2 else city_key.capitalize()}",
            "source":     source_chat,
            "direction":  raw.get("direction_text"),
            "bearing":    bearing,
            "time":       now.strftime("%H:%M"),
            "created_at": now.isoformat(),
            "expire_at":  (now + timedelta(minutes=TARGET_TTL_MIN)).isoformat()
        }
        active_targets.append(target)
        _stats["targets_created"] += 1
        save_targets()
        dir_info = f" → напрямок: {raw.get('direction_text')}" if raw.get('direction_text') else ""
        log.info(f"🎯 [{source_chat}] {detected_type.upper()} → {target['label']}{dir_info} | GitHub ↑")


# =============================================
# --- ЗАПУСК ---
# =============================================
async def main():
    log.info("🔗 Підключення до GitHub...")
    _get_github_sha()

    log.info("🤖 Система моніторингу активована!")
    log.info(f"📡 Слухаємо канали: {', '.join(SOURCE_CHANNELS)}")
    log.info(f"🧠 AI-класифікація: {'увімкнена (Gemini)' if USE_AI else 'вимкнена — працюю на keyword-фолбеку'}")
    log.info(f"🗺️ Населених пунктів у базі: {len(GEO_DATA)}")
    log.info(f"🏷️ Типів цілей: {len(ALLOWED_TYPES)} ({', '.join(ALLOWED_TYPES)})")
    log.info(f"🐙 GitHub: https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}")

    asyncio.create_task(cleaner())
    asyncio.create_task(healthcheck_loop())
    await client.start(bot_token=os.environ.get('PARSER_BOT_TOKEN') or BOT_TOKEN)
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        notify_admins(f"парсер впав: {e}")
        raise
