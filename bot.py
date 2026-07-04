"""
Бот для Monitor Kharkiv Mini App.
Показує карту загроз через Telegram WebApp — і через Menu Button (кнопка біля поля вводу),
і через inline-кнопку в повідомленні /start.

Потрібні env-змінні:
  BOT_TOKEN   — токен бота (той самий, що і в parser.py)
  WEBAPP_URL  — https-адреса, де опубліковано index.html (наприклад GitHub Pages)
"""
import asyncio
import os
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
    MenuButtonWebApp,
)

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ['BOT_TOKEN']
WEBAPP_URL = os.environ['WEBAPP_URL']  # напр. https://<user>.github.io/<repo>/

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="🗺 Відкрити карту загроз",
                web_app=WebAppInfo(url=WEBAPP_URL)
            )
        ]]
    )
    await message.answer(
        "Моніторинг повітряних загроз по Харківщині.\n"
        "Тисни кнопку нижче або кнопку меню поруч з полем вводу, щоб відкрити карту.",
        reply_markup=keyboard
    )


async def setup_menu_button():
    """Ставить постійну кнопку меню (зліва від поля вводу), яка відкриває Mini App."""
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Карта загроз",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    )


async def main():
    await setup_menu_button()
    await dp.start_polling(bot)


if __name__ == '__main__':
    asyncio.run(main())
