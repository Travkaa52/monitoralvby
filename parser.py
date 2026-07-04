import asyncio
import json
import os
import difflib
import logging
from datetime import datetime, timedelta, timezone

import requests
from telethon import TelegramClient
from telethon.errors import ChannelPrivateError, UsernameNotOccupiedError

import ai_classifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parser")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================
# --- НАЛАШТУВАННЯ TELEGRAM (з env / GitHub Secrets) ---
# =============================================
API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']
BOT_TOKEN = os.environ.get('BOT_TOKEN')

SOURCE_CHANNELS = [c.strip().lstrip('@') for c in os.environ.get('SOURCE_CHANNELS', '').split(',') if c.strip()]
if not SOURCE_CHANNELS:
    raise RuntimeError("Не задано жодного каналу у SOURCE_CHANNELS (env)")

ADMIN_IDS = [a.strip() for a in os.environ.get('ADMIN_IDS', '').split(',') if a.strip()]

USE_AI = bool(os.environ.get('GEMINI_API_KEY'))

TARGET_TTL_MIN = int(os.environ.get('TARGET_TTL_MIN', '40'))
DEDUP_RADIUS_KM = float(os.environ.get('DEDUP_RADIUS_KM', '3'))
DEDUP_WINDOW_MIN = int(os.environ.get('DEDUP_WINDOW_MIN', '15'))
# Скільки хвилин "старе" повідомлення ми ще готові обробити (щоб при першому
# запуску для каналу чи після довгої перерви не малювати старі цілі як нові).
MAX_MESSAGE_AGE_MIN = int(os.environ.get('MAX_MESSAGE_AGE_MIN', '15'))
# Запобіжник від "залпового" відновлення після довгого простою.
MAX_CATCHUP_MESSAGES = int(os.environ.get('MAX_CATCHUP_MESSAGES', '300'))

TARGETS_PATH = os.path.join(BASE_DIR, 'targets.json')
STATE_PATH = os.path.join(BASE_DIR, 'parser_state.json')


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

_stats = {"messages_seen": 0, "targets_created": 0, "ai_fallbacks": 0}


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


def extract_targets_keywords(text: str) -> list:
    text_l = text.lower()

    detected_type = None
    for t_name, cfg in TYPES_CFG.items():
        keywords = cfg if isinstance(cfg, list) else cfg.get('keywords', [])
        if any(word and word.lower() in text_l for word in keywords):
            detected_type = t_name
            break
    if not detected_type:
        return []

    found = []
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


def extract_targets(text: str) -> list:
    if USE_AI:
        try:
            return ai_classifier.analyze_message(text)
        except Exception as e:
            _stats["ai_fallbacks"] += 1
            log.warning(f"AI-класифікація впала, відкат на keyword-парсинг: {e}")
    return extract_targets_keywords(text)


def load_targets() -> list:
    try:
        with open(TARGETS_PATH, encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def save_targets(targets: list):
    with open(TARGETS_PATH, 'w', encoding='utf-8') as f:
        json.dump(targets, f, ensure_ascii=False, indent=2)


def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_expired(targets: list, now: datetime) -> list:
    kept = []
    for t in targets:
        try:
            if datetime.fromisoformat(t['expire_at']) > now:
                kept.append(t)
        except Exception:
            continue
    return kept


def is_duplicate(targets: list, lat: float, lng: float, detected_type: str, now: datetime) -> bool:
    cutoff = now - timedelta(minutes=DEDUP_WINDOW_MIN)
    for t in targets:
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


def process_message(text: str, source_chat: str, msg_id: int, now: datetime, targets: list):
    _stats["messages_seen"] += 1
    raw_targets = extract_targets(text)
    if not raw_targets:
        return

    for raw in raw_targets:
        if raw.get("confidence", 1.0) < 0.4:
            continue

        city_key, geo = match_city(raw.get("location"))
        if not geo:
            continue

        detected_type = raw["type"] if raw.get("type") in ALLOWED_TYPES else "drone"
        bearing = raw.get("bearing_degrees")
        if bearing is None:
            bearing = bearing_from_text(raw.get("direction_text") or text)

        if is_duplicate(targets, geo[0], geo[1], detected_type, now):
            continue

        target = {
            "id":         f"{source_chat}_{msg_id}_{city_key}",
            "type":       detected_type,
            "lat":        geo[0],
            "lng":        geo[1],
            "lon":        geo[1],
            "label":      geo[2] if len(geo) > 2 else city_key.capitalize(),
            "source":     source_chat,
            "direction":  raw.get("direction_text"),
            "bearing":    bearing,
            "time":       now.strftime("%H:%M"),
            "created_at": now.isoformat(),
            "expire_at":  (now + timedelta(minutes=TARGET_TTL_MIN)).isoformat()
        }
        targets.append(target)
        _stats["targets_created"] += 1
        dir_info = f" → напрямок: {raw.get('direction_text')}" if raw.get('direction_text') else ""
        log.info(f"🎯 [{source_chat}] {detected_type.upper()} → {target['label']}{dir_info}")


async def run_once():
    now = datetime.now()
    now_utc = datetime.now(timezone.utc)
    max_age = timedelta(minutes=MAX_MESSAGE_AGE_MIN)

    targets = prune_expired(load_targets(), now)
    state = load_state()

    if SESSION_STRING:
        await _run_catchup(targets, state, now, now_utc, max_age)
    else:
        await _run_live_listen(targets, now, now_utc, max_age)

    save_targets(targets)
    save_state(state)

    log.info(
        f"✅ Прохід завершено. Повідомлень: {_stats['messages_seen']}, "
        f"нових цілей: {_stats['targets_created']}, AI-фолбеків: {_stats['ai_fallbacks']}, "
        f"активних цілей всього: {len(targets)}"
    )


async def _run_catchup(targets: list, state: dict, now: datetime, now_utc: datetime, max_age: timedelta):
    """Режим для звичайного (не бот) акаунта через StringSession: наздоганяючий
    опит історії каналів через min_id. Тільки user-акаунти мають доступ до
    GetHistory — Telegram блокує цей метод для ботів на своєму боці, тому цей
    режим доступний лише якщо задано SESSION_STRING."""
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()
    log.info("🔗 Підключено до Telegram (user-сесія, режим 'наздоганяючий опит')")

    for channel in SOURCE_CHANNELS:
        last_id = int(state.get(channel, 0))
        new_last_id = last_id
        fetched = 0
        try:
            entity = await client.get_entity(channel)
            async for message in client.iter_messages(
                entity, min_id=last_id, reverse=True, limit=MAX_CATCHUP_MESSAGES
            ):
                fetched += 1
                new_last_id = max(new_last_id, message.id)

                if not message.raw_text:
                    continue
                if message.date and (now_utc - message.date) > max_age:
                    continue

                source_chat = getattr(entity, 'username', None) or str(entity.id)
                process_message(message.raw_text, source_chat, message.id, now, targets)

            state[channel] = new_last_id
            log.info(f"📡 {channel}: опрацьовано {fetched} нових повідомлень (last_id={new_last_id})")

        except (ChannelPrivateError, UsernameNotOccupiedError) as e:
            log.error(f"❌ Канал {channel} недоступний: {e}")
            notify_admins(f"канал {channel} недоступний: {e}")
        except Exception as e:
            log.error(f"❌ Помилка при обробці каналу {channel}: {e}")
            notify_admins(f"помилка обробки каналу {channel}: {e}")

    await client.disconnect()


async def _run_live_listen(targets: list, now: datetime, now_utc: datetime, max_age: timedelta):
    """Режим для бот-акаунта (BOT_TOKEN): Telegram забороняє ботам GetHistory
    (messages.getHistory), тож єдиний робочий спосіб — слухати НОВІ повідомлення
    "живцем" протягом обмеженого вікна й вийти. Через це можливий короткий
    розрив між прогонами (див. README) — щоб його прибрати повністю, потрібен
    SESSION_STRING (звичайний акаунт)."""
    if not BOT_TOKEN:
        raise RuntimeError("Задайте або SESSION_STRING (user-акаунт), або BOT_TOKEN (бот) — обидва відсутні")

    client = TelegramClient('parser_session', API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    log.info(f"🔗 Підключено до Telegram (бот, режим 'живе прослуховування' {LISTEN_SECONDS}с)")

    resolved = {}
    for channel in SOURCE_CHANNELS:
        try:
            resolved[channel] = await client.get_entity(channel)
        except Exception as e:
            log.error(f"❌ Канал {channel} недоступний: {e}")
            notify_admins(f"канал {channel} недоступний: {e}")

    from telethon import events

    @client.on(events.NewMessage(chats=list(resolved.values())))
    async def handler(event):
        text = event.raw_text
        if not text:
            return
        source_chat = getattr(event.chat, 'username', None) or str(event.chat_id)
        process_message(text, source_chat, event.id, datetime.now(), targets)
        save_targets(targets)  # інкрементальний збіг на випадок падіння посеред вікна

    await asyncio.sleep(LISTEN_SECONDS)
    await client.disconnect()


def main():
    log.info("🤖 Monitor Kharkiv — одноразовий прохід парсера")
    log.info(f"📡 Канали: {', '.join(SOURCE_CHANNELS)}")
    log.info(f"🧠 AI-класифікація: {'увімкнена (Gemini)' if USE_AI else 'вимкнена — keyword-фолбек'}")
    log.info(f"🗺️ Населених пунктів у базі: {len(GEO_DATA)}")
    try:
        asyncio.run(run_once())
    except Exception as e:
        notify_admins(f"парсер впав: {e}")
        raise


if __name__ == '__main__':
    main()
