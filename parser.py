import asyncio
import json
import os
import base64
import threading
import difflib
import requests
from datetime import datetime, timedelta
from telethon import TelegramClient, events

import ai_classifier

# =============================================
# --- НАЛАШТУВАННЯ TELEGRAM (з env / GitHub Secrets) ---
# =============================================
API_ID = int(os.environ['API_ID'])
API_HASH = os.environ['API_HASH']

# Кілька каналів через кому: "monitorkh1654,another_channel,third_channel"
SOURCE_CHANNELS = [c.strip() for c in os.environ.get('SOURCE_CHANNELS', '').split(',') if c.strip()]
if not SOURCE_CHANNELS:
    raise RuntimeError("Не задано жодного каналу у SOURCE_CHANNELS (env)")

ADMIN_IDS = [a.strip() for a in os.environ.get('ADMIN_IDS', '').split(',') if a.strip()]
BOT_TOKEN = os.environ.get('BOT_TOKEN')

USE_AI = bool(os.environ.get('GEMINI_API_KEY'))

# =============================================
# --- НАЛАШТУВАННЯ GITHUB ---
# =============================================
GITHUB_TOKEN = os.environ['GITHUB_TOKEN']
_repo_full = os.environ['GITHUB_REPOSITORY']
GITHUB_OWNER, GITHUB_REPO = _repo_full.split('/', 1)
GITHUB_BRANCH = os.environ.get('GITHUB_BRANCH', 'main')
GITHUB_FILE = os.environ.get('GITHUB_FILE', 'targets.json')

# =============================================
# --- KEYWORD FALLBACK (якщо AI недоступний) ---
# =============================================
TARGET_TYPES = {
    "recon":    ["зала", "zala", "розвід", "орлан", "суперкам", "supercam", "безпілотник"],
    "kab":      ["каб", "пуск", "авіабомба", "фаб", "керована"],
    "aircraft": ["петухи", "су-34", "су-35", "борт", "літак", "сушка"],
    "mrls":     ["рсзв", "град", "смерч", "ураган", "вихід", "обстріл"],
    "drone":    ["шахед", "бпла", "мопед", "герань", "шахід"],
    "missile":  ["ракета", "іскандер", "х-101", "кинджал", "балістика"]
}

# Текстові напрямки → азимут (грубий фолбек, коли AI/текст не дає точного числа)
DIRECTION_WORDS = {
    "північ": 0, "півн": 0, "north": 0,
    "північний схід": 45, "півнсхід": 45,
    "схід": 90, "сх": 90, "east": 90,
    "південний схід": 135,
    "південь": 180, "півд": 180, "south": 180,
    "південний захід": 225,
    "захід": 270, "зах": 270, "west": 270,
    "північний захід": 315,
}

# =============================================
# --- ГЕОДАНІ ХАРКІВСЬКОЇ ОБЛАСТІ ---
# =============================================
GEO_DATA = {
    "харків":           [49.99, 36.23, "Обласний центр"],
    "харьков":          [49.99, 36.23, "Обласний центр"],
    "пісочин":          [49.95, 36.10, "Харківський р-н"],
    "солоницівка":      [50.01, 36.05, "Харківський р-н"],
    "люботин":          [49.94, 35.92, "Харківський р-н"],
    "мерефа":           [49.82, 36.05, "Харківський р-н"],
    "циркуни":          [50.07, 36.38, "Харківський р-н"],
    "липці":            [50.20, 36.42, "Харківський р-н"],
    "руська лозова":    [50.13, 36.29, "Харківський р-н"],
    "кутузівка":        [50.02, 36.46, "Харківський р-н"],
    "козача лопань":    [50.33, 36.19, "Харківський р-н"],
    "дергачі":          [50.11, 36.12, "Харківський р-н"],
    "чугуїв":           [49.83, 36.68, "Чугуївський р-н"],
    "вовчанськ":        [50.28, 36.93, "Чугуївський р-н"],
    "старий салтів":    [50.08, 36.79, "Чугуївський р-н"],
    "малинівка":        [49.79, 36.72, "Чугуївський р-н"],
    "печеніги":         [49.86, 36.93, "Чугуївський р-н"],
    "білий колодязь":   [50.20, 37.14, "Чугуївський р-н"],
    "вовчанські хутори":[50.28, 37.03, "Чугуївський р-н"],
    "куп'янськ":        [49.70, 37.61, "Куп'янський р-н"],
    "вузлова":          [49.67, 37.64, "Куп'янськ-Вузловий"],
    "ківшарівка":       [49.62, 37.68, "Куп'янський р-н"],
    "шевченкове":       [49.70, 37.17, "Куп'янський р-н"],
    "дворічна":         [49.85, 37.67, "Куп'янський р-н"],
    "боросте":          [49.33, 37.62, "Борівська громада"],
    "ізюм":             [49.19, 37.27, "Ізюмський р-н"],
    "балаклія":         [49.45, 36.85, "Ізюмський р-н"],
    "донець":           [49.46, 36.50, "Ізюмський р-н"],
    "савинці":          [49.40, 36.99, "Ізюмський р-н"],
    "богодухів":        [50.16, 35.52, "Богодухівський р-н"],
    "золочів":          [50.28, 35.97, "Богодухівський р-н"],
    "валки":            [49.83, 35.61, "Богодухівський р-н"],
    "відродженівське":  [50.31, 35.84, "Золочівська громада"],
    "лозова":           [48.88, 36.31, "Лозівський р-н"],
    "первомайський":    [49.38, 36.21, "Лозівський р-н"],
    "красноград":       [49.37, 35.45, "Красноградський р-н"]
}

# =============================================
# --- ГЛОБАЛЬНИЙ СТАН ---
# =============================================
active_targets = []
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_github_sha_cache = None


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
            print(f"⚠️  Не вдалося сповістити адміна {admin_id}: {e}")


# =============================================
# --- ВИЗНАЧЕННЯ ЦІЛЕЙ У ТЕКСТІ (AI + fallback) ---
# =============================================
def match_city(location_text: str):
    """Нечітке зіставлення довільного тексту з відомим населеним пунктом."""
    key = location_text.strip().lower()
    if key in GEO_DATA:
        return key, GEO_DATA[key]
    matches = difflib.get_close_matches(key, GEO_DATA.keys(), n=1, cutoff=0.72)
    if matches:
        return matches[0], GEO_DATA[matches[0]]
    # інколи AI/текст містить місто як частину довшої фрази
    for city in GEO_DATA:
        if city in key or key in city:
            return city, GEO_DATA[city]
    return None, None


def bearing_from_text(direction_text):
    if not direction_text:
        return None
    dt = direction_text.strip().lower()
    for word, deg in DIRECTION_WORDS.items():
        if word in dt:
            return deg
    return None


def extract_targets_keywords(text: str) -> list[dict]:
    """Старий keyword-парсинг — використовується як fallback, якщо AI недоступний."""
    text_l = text.lower()
    found = []

    detected_type = None
    for t_name, keywords in TARGET_TYPES.items():
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
                "bearing_degrees": None,
                "confidence": 0.5
            })
            break  # як і в оригіналі — одне місто на повідомлення
    return found


def extract_targets(text: str) -> list[dict]:
    if USE_AI:
        try:
            ai_targets = ai_classifier.analyze_message(text)
            return ai_targets
        except Exception as e:
            print(f"⚠️  AI-класифікація впала, відкат на keyword-парсинг: {e}")
            notify_admins(f"Gemini недоступний, працюю на keyword-фолбеку: {e}")
    return extract_targets_keywords(text)


# =============================================
# --- GITHUB: PUSH targets.json ---
# =============================================
def _get_github_sha():
    global _github_sha_cache
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        r = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=10)
        if r.status_code == 200:
            _github_sha_cache = r.json().get("sha")
        elif r.status_code == 404:
            _github_sha_cache = None
    except Exception as e:
        print(f"⚠️  GitHub GET error: {e}")
    return _github_sha_cache


def push_to_github():
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
            print(f"✅ GitHub: targets.json оновлено ({len(active_targets)} цілей)")
        else:
            print(f"⚠️  GitHub push error {r.status_code}: {r.text[:200]}")
            if r.status_code == 409:
                _github_sha_cache = None
    except Exception as e:
        print(f"⚠️  GitHub push exception: {e}")
        notify_admins(f"помилка push у GitHub: {e}")


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
        active_targets = [
            t for t in active_targets
            if datetime.fromisoformat(t['expire_at']) > now
        ]
        if len(active_targets) != before:
            save_targets()
            print(f"🧹 Видалено {before - len(active_targets)} застарілих цілей")
        await asyncio.sleep(60)


# =============================================
# --- TELEGRAM КЛІЄНТ (кілька джерел) ---
# =============================================
client = TelegramClient('bot_session', API_ID, API_HASH)


@client.on(events.NewMessage(chats=SOURCE_CHANNELS))
async def handler(event):
    text = event.raw_text
    now = datetime.now()
    source_chat = getattr(event.chat, 'username', None) or str(event.chat_id)

    targets = extract_targets(text)
    if not targets:
        return

    for raw in targets:
        if raw.get("confidence", 1.0) < 0.4:
            continue  # AI сама не впевнена — пропускаємо, щоб не смітити карту

        city_key, geo = match_city(raw["location"])
        if not geo:
            print(f"ℹ️  Невідомий населений пункт у повідомленні: '{raw['location']}' — пропускаю")
            continue

        detected_type = raw["type"]
        bearing = raw.get("bearing_degrees")
        if bearing is None:
            bearing = bearing_from_text(raw.get("direction_text"))

        is_duplicate = any(
            t['label'].startswith(city_key.capitalize()) and t['type'] == detected_type
            for t in active_targets
        )
        if is_duplicate:
            continue

        target = {
            "id":         f"{source_chat}_{event.id}_{city_key}",
            "type":       detected_type,
            "lat":        geo[0],
            "lng":        geo[1],
            "lon":        geo[1],
            "label":      f"{city_key.capitalize()} ({geo[2]})",
            "source":     source_chat,
            "direction":  raw.get("direction_text"),
            "bearing":    bearing,
            "time":       now.strftime("%H:%M"),
            "expire_at":  (now + timedelta(minutes=40)).isoformat()
        }
        active_targets.append(target)
        save_targets()
        dir_info = f" → напрямок: {raw.get('direction_text')}" if raw.get('direction_text') else ""
        print(f"🎯 [{source_chat}] {detected_type.upper()} → {city_key.capitalize()}{dir_info} | GitHub ↑")


# =============================================
# --- ЗАПУСК ---
# =============================================
async def main():
    print("🔗 Підключення до GitHub...")
    _get_github_sha()

    print("🤖 Система моніторингу Харківщини активована!")
    print(f"📡 Слухаємо канали: {', '.join(SOURCE_CHANNELS)}")
    print(f"🧠 AI-класифікація: {'увімкнена (Gemini)' if USE_AI else 'вимкнена — працюю на keyword-фолбеку'}")
    print(f"🐙 GitHub: https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{GITHUB_FILE}")

    asyncio.create_task(cleaner())
    await client.start(bot_token=os.environ.get('PARSER_BOT_TOKEN') or BOT_TOKEN)
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception as e:
        notify_admins(f"парсер впав: {e}")
        raise
