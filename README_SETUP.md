# Monitor Kharkiv — обновлённый проект

## Что изменилось
- **parser.py** — теперь слушает несколько каналов через `SOURCE_CHANNELS` (env), никаких токенов в коде.
- **ai_classifier.py** (новый) — отправляет текст сообщения в Gemini и получает структурированный JSON: тип цели, населённый пункт, направление движения и азимут (0-360°). Если `GEMINI_API_KEY` не задан или запрос упал — парсер автоматически откатывается на старый keyword-поиск, так что система не падает без ИИ.
- **app.js** — маркеры на карте теперь поворачиваются по азимуту цели (плагин `leaflet-rotatedMarker`, который уже был подключён), плюс направление показывается в попапе маркера.
- **bot.py** (новый) — бот на aiogram: Menu Button + inline-кнопка открывают карту как Telegram Mini App.
- **index.html** — исправлен сломанный дублирующийся скрипт (там был реальный синтаксис-баг), добавлен Telegram WebApp SDK.
- **requirements.txt** — добавлен aiogram.
- **.gitignore** — теперь реально исключает `.env` и файлы сессий Telethon.
- Из архива удалены `parser.py.bak`, `deepseek_html_*.bak` и `bot_session.session*` — в них были твои реальные `API_HASH`, `BOT_TOKEN` и GitHub PAT в открытом виде, и живая Telegram-сессия. **Их нужно перевыпустить/отозвать, если этот проект хоть раз попадал в публичный репозиторий.**

## Как работает AI-классификация
1. Приходит новое сообщение из любого канала в `SOURCE_CHANNELS`.
2. `parser.py` отправляет текст в Gemini (`ai_classifier.analyze_message`) с чёткой схемой ответа (JSON: `type`, `location`, `direction_text`, `bearing_degrees`, `confidence`).
3. Каждая найденная цель с `confidence >= 0.4` сопоставляется с известным населённым пунктом (точное совпадение → нечёткое совпадение через `difflib` → подстрока — на случай опечаток или иной формы слова).
4. Азимут (`bearing_degrees`) идёт в `targets.json` как `bearing` — фронтенд поворачивает иконку в эту сторону. Если Gemini не дал число, но есть текстовое направление ("на схід", "у бік Харкова") — азимут прикидывается по словарю сторон света (грубый фолбек).
5. Если Gemini недоступен (нет ключа, лимит, сетевая ошибка) — используется старый keyword-поиск по спискам `TARGET_TYPES`, без направления. Админам (`ADMIN_IDS`) уходит уведомление о переходе на фолбек.

Получить бесплатный `GEMINI_API_KEY` можно в Google AI Studio (aistudio.google.com) — просто сгенерировать ключ, без привязки карты для базового бесплатного лимита.

## ⚠️ Обязательно сделать прямо сейчас
1. Перевыпусти `API_HASH` на my.telegram.org (или как минимум проверь, не утекал ли он).
2. Отзови старый `BOT_TOKEN` через @BotFather → `/revoke`, получи новый.
3. Удали/отзови GitHub Personal Access Token, который был в parser.py.bak (Settings → Developer settings → PAT).

## GitHub Secrets (Settings → Secrets and variables → Actions)
- `API_ID`, `API_HASH` — с my.telegram.org
- `SOURCE_CHANNELS` — например `monitorkh1654,channel2,channel3`
- `BOT_TOKEN` — токен бота (используется и парсером, и для Mini App)
- `GEMINI_API_KEY` — (опционально, но нужен для AI-классификации) ключ из Google AI Studio
- `ADMIN_IDS` — (опционально) твой user_id для алертов о падении парсера/AI
- `GITHUB_TOKEN` — подставляется автоматически Actions, вручную не нужен

Встроенный `secrets.GITHUB_TOKEN` уже имеет право на запись в `targets.json`, т.к. в workflow добавлено `permissions: contents: write`.

## Локальный запуск бота (bot.py)
GitHub Actions не подходит для постоянно работающего бота (это cron, а не долгоживущий процесс).
Бота нужно держать где-то отдельно — на своём сервере / Railway / PythonAnywhere / VPS:

```bash
pip install -r requirements.txt
export BOT_TOKEN="..."
export WEBAPP_URL="https://<user>.github.io/<repo>/"
python bot.py
```

После этого у пользователей в чате с ботом появится:
- кнопка меню "Карта загроз" рядом с полем ввода,
- и кнопка "🗺 Открыть карту загроз" под `/start`.

Обе открывают `index.html` как Telegram Mini App (внутри Telegram, с автоматической темой оформления).

## Локальный запуск парсера (parser.py)
```bash
export API_ID="..."
export API_HASH="..."
export SOURCE_CHANNELS="monitorkh1654,channel2"
export BOT_TOKEN="..."
export GITHUB_TOKEN="..."           # только для локального запуска
export GITHUB_REPOSITORY="user/repo" # только для локального запуска
python parser.py
```

При первом запуске Telethon создаст `bot_session.session` — этот файл никогда не коммить (уже в `.gitignore`).
