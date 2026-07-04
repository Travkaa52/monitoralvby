"""
Одноразовий скрипт: логінить твій звичайний Telegram-акаунт через API_ID/API_HASH
і друкує SESSION_STRING, який потім можна покласти у змінну оточення SESSION_STRING
(або .env) на сервері — і більше ніколи не вводити номер телефону/код там.

Запуск:
    python generate_session.py

Спитає:
    - API_ID і API_HASH (з https://my.telegram.org -> API development tools)
    - номер телефону
    - код підтвердження з Telegram (і пароль 2FA, якщо увімкнений)

НІКОМУ НЕ ПОКАЗУЙ отриманий SESSION_STRING — це фактично повний доступ до
твого акаунта (без пароля). Зберігай як секрет, не комить у git.
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        print(f"\n✅ Успішний вхід як {me.first_name} (id={me.id})\n")
        print("Твій SESSION_STRING (збережи як секрет SESSION_STRING):\n")
        print(client.session.save())
        print("\n⚠️ Нікому не показуй цей рядок — це доступ до акаунта без пароля.")


if __name__ == "__main__":
    asyncio.run(main())
