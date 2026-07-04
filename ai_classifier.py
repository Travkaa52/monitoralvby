"""
AI-класифікатор повідомлень для Monitor.

Замінює/доповнює прості keyword-збіги: віддає повідомлення каналу в Gemini
і отримує назад структурований список цілей (тип, населений пункт, напрямок руху).

Потрібна env-змінна GEMINI_API_KEY (безкоштовний ключ можна взяти в Google AI Studio).
Якщо ключ не заданий або запит до Gemini впав — parser.py сам відкотиться
на старий keyword-парсинг (див. extract_targets_keywords у parser.py).
"""
import os
import json
import time
import requests

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

# Канонічний список типів — має 1-в-1 збігатись з ключами types.json і з
# назвами іконок/лейблів у index.html, інакше ціль намалюється з "поламаною" іконкою.
ALLOWED_TYPES = ["recon", "drone", "fpw", "lancet", "molniya", "kab", "missile", "mrls", "aircraft"]

SYSTEM_PROMPT = """Ти аналізуєш повідомлення з телеграм-каналів моніторингу повітряних загроз
в Україні (укр. та рос. мовою). Виділи з тексту всі згадані цілі (загрози).

Для кожної цілі визнач:
- type: один з ["recon", "drone", "fpw", "lancet", "molniya", "kab", "missile", "mrls", "aircraft"]
  (recon = розвідувальний БПЛА типу Zala/Орлан/SuperCam;
   drone = ударний БПЛА типу Shahed/Geran/Гербера;
   fpw = FPV-дрон/камікадзе;
   lancet = БПЛА "Ланцет"; molniya = БПЛА "Молнія";
   kab = керована авіабомба (КАБ/ФАБ/УМПК);
   missile = ракета, включно з балістичною і крилатою;
   mrls = реактивна/ствольна артилерія, обстріл;
   aircraft = пілотований літак)
- location: назва населеного пункту, як згадано в тексті (без обробки, мовою оригіналу)
- direction_text: короткий опис напрямку руху, якщо згадується (наприклад "на Харків",
  "у бік Чугуєва", "зі сходу"), інакше null
- bearing_degrees: якщо напрямок можна оцінити географічно — азимут 0-360 (0=північ,
  90=схід, 180=південь, 270=захід), інакше null
- confidence: число 0-1, наскільки ти впевнений, що це реальна ціль, а не флуд/реклама/коментар

Якщо в повідомленні немає жодної конкретної цілі (флуд, реклама, загальні коментарі,
підсумки без нових цілей) — поверни порожній список targets.
Відповідай ЛИШЕ у форматі JSON за схемою, без жодного додаткового тексту."""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "targets": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "type": {"type": "STRING", "enum": ALLOWED_TYPES},
                    "location": {"type": "STRING"},
                    "direction_text": {"type": "STRING", "nullable": True},
                    "bearing_degrees": {"type": "NUMBER", "nullable": True},
                    "confidence": {"type": "NUMBER"}
                },
                "required": ["type", "location", "confidence"]
            }
        }
    },
    "required": ["targets"]
}


def analyze_message(text: str, timeout: int = 15, retries: int = 2) -> list[dict]:
    """
    Повертає список цілей у форматі:
    [{"type": "drone", "location": "Чугуїв", "direction_text": "на Харків",
      "bearing_degrees": 270, "confidence": 0.9}, ...]

    Піднімає виняток, якщо ключа немає або всі спроби до Gemini впали —
    у цьому випадку parser.py сам відкотиться на keyword-парсинг.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY не задано")

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "temperature": 0.1
        }
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY}

    last_err = None
    for attempt in range(1, retries + 2):
        try:
            r = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                # тимчасова помилка/ліміт — варто повторити
                raise requests.HTTPError(f"{r.status_code}: {r.text[:150]}")
            r.raise_for_status()
            data = r.json()

            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(raw_text)
            targets = parsed.get("targets", [])

            clean = []
            for t in targets:
                if t.get("type") in ALLOWED_TYPES and t.get("location"):
                    clean.append({
                        "type": t["type"],
                        "location": str(t["location"]).strip(),
                        "direction_text": t.get("direction_text"),
                        "bearing_degrees": t.get("bearing_degrees"),
                        "confidence": float(t.get("confidence", 0.5))
                    })
            return clean
        except Exception as e:
            last_err = e
            if attempt <= retries:
                time.sleep(min(2 ** attempt, 6))
                continue
            raise last_err
