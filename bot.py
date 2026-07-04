"""
Бот для Monitor Mini App — "ультра" редакція.

Показує карту загроз через Telegram WebApp (Menu Button + inline-кнопка),
перевіряє підписку на канал перед відкриттям карти, вміє надсилати
персональні сповіщення користувачам, коли нова ціль з'являється поруч
з їхньою геолокацією, і пересилає /feedback адмінам.

Потрібні env-змінні:
  BOT_TOKEN        — токен бота (той самий, що і в parser.py)
  WEBAPP_URL       — https-адреса, де опубліковано index.html (напр. GitHub Pages)
  CHANNEL_USERNAME — (опційно) @username каналу, підписка на який обов'язкова
                      для відкриття карти. Бот має бути адміном цього каналу.
  TARGETS_URL      — (опційно) адреса targets.json для фонового опитування
                      персональних сповіщень. За замовчуванням WEBAPP_URL + targets.json
  ADMIN_IDS        — (опційно) user_id адмінів, отримують /feedback і /broadcast
"""
import asyncio
import json
import os
import logging
import time
from datetime import datetime
from math import radians, sin, cos, asin, sqrt

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
    CallbackQuery,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_TOKEN = os.environ['BOT_TOKEN']
WEBAPP_URL = os.environ['WEBAPP_URL']
CHANNEL_USERNAME = os.environ.get('CHANNEL_USERNAME', '').lstrip('@')
TARGETS_URL = os.environ.get('TARGETS_URL') or (WEBAPP_URL.rstrip('/') + '/targets.json')
ADMIN_IDS = {a.strip() for a in os.environ.get('ADMIN_IDS', '').split(',') if a.strip()}
DEFAULT_RADIUS_KM = float(os.environ.get('DEFAULT_ALERT_RADIUS_KM', '15'))
POLL_INTERVAL_SEC = int(os.environ.get('NOTIFY_POLL_INTERVAL', '20'))

SUBSCRIBERS_PATH = os.path.join(BASE_DIR, 'subscribers.json')

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

TYPE_LABELS = {
    "recon": "Розвід. БпЛА", "drone": "Шахед", "fpw": "FPV дрон",
    "lancet": "Ланцет", "molniya": "Молнія", "kab": "КАБ",
    "missile": "Ракета", "mrls": "РСЗО / Артилерія", "aircraft": "Літак",
}


# =============================================
# --- ЗБЕРІГАННЯ ПІДПИСНИКІВ (простий JSON-файл) ---
# =============================================
def load_subscribers() -> dict:
    if not os.path.exists(SUBSCRIBERS_PATH):
        return {}
    try:
        with open(SUBSCRIBERS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_subscribers(data: dict):
    with open(SUBSCRIBERS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


# =============================================
# --- ПЕРЕВІРКА ПІДПИСКИ НА КАНАЛ ---
# =============================================
async def is_subscribed(user_id: int) -> bool:
    if not CHANNEL_USERNAME:
        return True  # перевірка вимкнена
    try:
        member = await bot.get_chat_member(f"@{CHANNEL_USERNAME}", user_id)
        return member.status not in ("left", "kicked")
    except Exception as e:
        log.warning(f"Не вдалося перевірити підписку для {user_id}: {e}")
        return True  # у разі помилки API не блокуємо користувача


def webapp_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗺 Відкрити карту загроз", web_app=WebAppInfo(url=WEBAPP_URL))
    ]])


def subscribe_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if CHANNEL_USERNAME:
        rows.append([InlineKeyboardButton(text="📢 Підписатись на канал", url=f"https://t.me/{CHANNEL_USERNAME}")])
    rows.append([InlineKeyboardButton(text="✅ Я підписався, перевірити", callback_data="recheck_sub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


LOCATION_KB = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📍 Надіслати геолокацію", request_location=True)]],
    resize_keyboard=True, one_time_keyboard=True
)


# =============================================
# --- КОМАНДИ ---
# =============================================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            f"Щоб відкрити карту загроз, спершу підпишись на канал @{CHANNEL_USERNAME}.",
            reply_markup=subscribe_keyboard()
        )
        return
    await message.answer(
        "Моніторинг повітряних загроз у реальному часі.\n\n"
        "🗺 Тисни кнопку нижче або кнопку меню поруч з полем вводу, щоб відкрити карту.\n"
        "📍 /notify — увімкнути персональні сповіщення, коли загроза наближається до тебе.\n"
        "🛑 /stop_notify — вимкнути сповіщення.\n"
        "💬 /feedback <текст> — надіслати пропозицію або баг-репорт.",
        reply_markup=webapp_keyboard()
    )


@dp.callback_query(F.data == "recheck_sub")
async def recheck_sub(call: CallbackQuery):
    if await is_subscribed(call.from_user.id):
        await call.message.edit_text("✅ Підписку підтверджено! Тисни /start, щоб відкрити карту.")
    else:
        await call.answer("Підписки поки не видно. Спробуй ще раз за кілька секунд.", show_alert=True)


@dp.message(Command("notify"))
async def cmd_notify(message: Message):
    await message.answer(
        f"Надішли свою геолокацію — і я повідомлю, коли ціль з'явиться в радіусі "
        f"{DEFAULT_RADIUS_KM:.0f} км від тебе. Радіус можна змінити командою /radius <км>.",
        reply_markup=LOCATION_KB
    )


@dp.message(F.location)
async def on_location(message: Message):
    subs = load_subscribers()
    uid = str(message.from_user.id)
    existing = subs.get(uid, {})
    subs[uid] = {
        "lat": message.location.latitude,
        "lon": message.location.longitude,
        "radius_km": existing.get("radius_km", DEFAULT_RADIUS_KM),
        "seen_ids": existing.get("seen_ids", []),
        "updated_at": datetime.now().isoformat(),
    }
    save_subscribers(subs)
    await message.answer(
        "✅ Геолокацію збережено. Персональні сповіщення увімкнено.\n"
        f"Поточний радіус: {subs[uid]['radius_km']:.0f} км.",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(Command("radius"))
async def cmd_radius(message: Message):
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].replace('.', '', 1).isdigit():
        await message.answer("Використання: /radius 20  (число кілометрів)")
        return
    subs = load_subscribers()
    uid = str(message.from_user.id)
    if uid not in subs:
        await message.answer("Спершу увімкни сповіщення командою /notify.")
        return
    subs[uid]["radius_km"] = float(parts[1])
    save_subscribers(subs)
    await message.answer(f"Радіус сповіщень оновлено: {float(parts[1]):.0f} км.")


@dp.message(Command("stop_notify"))
async def cmd_stop_notify(message: Message):
    subs = load_subscribers()
    uid = str(message.from_user.id)
    if uid in subs:
        del subs[uid]
        save_subscribers(subs)
    await message.answer("🛑 Персональні сповіщення вимкнено.", reply_markup=ReplyKeyboardRemove())


@dp.message(Command("feedback"))
async def cmd_feedback(message: Message):
    text = message.text.partition(' ')[2].strip()
    if not text:
        await message.answer("Використання: /feedback опис проблеми або пропозиції")
        return
    who = f"@{message.from_user.username}" if message.from_user.username else message.from_user.full_name
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"💬 Фідбек від {who} ({message.from_user.id}):\n{text}")
        except Exception as e:
            log.warning(f"Не вдалося переслати фідбек адміну {admin_id}: {e}")
    await message.answer("Дякую! Твоє повідомлення передано команді проєкту.")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if str(message.from_user.id) not in ADMIN_IDS:
        return
    text = message.text.partition(' ')[2].strip()
    if not text:
        await message.answer("Використання: /broadcast текст повідомлення для всіх підписників")
        return
    subs = load_subscribers()
    sent, failed = 0, 0
    for uid in subs:
        try:
            await bot.send_message(int(uid), f"📣 {text}")
            sent += 1
        except Exception:
            failed += 1
    await message.answer(f"Розіслано: {sent}, помилок: {failed}")


# =============================================
# --- ФОНОВЕ ОПИТУВАННЯ target.json ДЛЯ ПЕРСОНАЛЬНИХ СПОВІЩЕНЬ ---
# =============================================
async def fetch_targets(session: aiohttp.ClientSession):
    try:
        async with session.get(f"{TARGETS_URL}?t={int(time.time())}", timeout=10) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            return data if isinstance(data, list) else data.get("items", [])
    except Exception as e:
        log.warning(f"Не вдалося отримати targets.json: {e}")
        return []


async def personal_alerts_loop():
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            targets = await fetch_targets(session)
            if not targets:
                continue

            subs = load_subscribers()
            if not subs:
                continue

            changed = False
            for uid, sub in subs.items():
                seen = set(sub.get("seen_ids", []))
                for t in targets:
                    tid = str(t.get("id"))
                    if tid in seen:
                        continue
                    try:
                        lat, lon = float(t["lat"]), float(t.get("lon", t.get("lng")))
                    except (KeyError, TypeError, ValueError):
                        continue
                    dist = haversine_km(sub["lat"], sub["lon"], lat, lon)
                    if dist <= sub.get("radius_km", DEFAULT_RADIUS_KM):
                        label = TYPE_LABELS.get(t.get("type"), t.get("type", "Ціль"))
                        try:
                            await bot.send_message(
                                int(uid),
                                f"⚠️ {label} за {dist:.1f} км від тебе — {t.get('label', '')}",
                            )
                        except Exception as e:
                            log.warning(f"Не вдалося надіслати сповіщення {uid}: {e}")
                    seen.add(tid)
                if len(seen) != len(sub.get("seen_ids", [])):
                    sub["seen_ids"] = list(seen)[-500:]  # обмежуємо розмір
                    changed = True

            if changed:
                save_subscribers(subs)


# =============================================
# --- MENU BUTTON ---
# =============================================
async def setup_menu_button():
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Карта загроз", web_app=WebAppInfo(url=WEBAPP_URL))
    )


async def main():
    await setup_menu_button()
    asyncio.create_task(personal_alerts_loop())
    log.info("Бот запущено. WebApp: %s | Канал: %s | Адмінів: %d", WEBAPP_URL, CHANNEL_USERNAME or "—", len(ADMIN_IDS))
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
