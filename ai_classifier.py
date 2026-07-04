"""
AI-класифікатор повідомлень для Monitor Kharkiv.

Замінює/доповнює прості keyword-збіги: віддає повідомлення каналу в Gemini
і отримує назад структурований список цілей (тип, населений пункт, напрямок руху).

Потрібна env-змінна GEMINI_API_KEY (безкоштовний ключ можна взяти в Google AI Studio).
Якщо ключ не заданий або запит до Gemini впав — parser.py сам відкотиться
на старий keyword-парсинг (див. extract_targets_keywords у parser.py).
"""
import os
import json
import requests

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

ALLOWED_TYPES = ["recon", "kab", "aircraft", "mrls", "drone", "missile"]

SYSTEM_PROMPT = """Ти аналізуєш повідомлення з телеграм-каналів моніторингу повітряних загроз
Харківської області України. Виділи з тексту всі згадані цілі (загрози).

Для кожної цілі визнач:
- type: один з ["recon", "kab", "aircraft", "mrls", "drone", "missile"]
  (recon = розвідувальний БПЛА типу Zala/Орлан/SuperCam; kab = керована авіабомба;
   aircraft = літак; mrls = реактивна/ствольна артилерія; drone = ударний БПЛА типу Shahed;
   missile = ракета, включно з балістикою)
- location: назва населеного пункту Харківської області, як згадано в тексті (без обробки)
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


def analyze_message(text: str, timeout: int = 15) -> list[dict]:
    """
    Повертає список цілей у форматі:
    [{"type": "drone", "location": "Чугуїв", "direction_text": "на Харків",
      "bearing_degrees": 270, "confidence": 0.9}, ...]
    Порожній список, якщо цілей немає або AI недоступний (виклик main.py має
    зробити fallback на keyword-парсинг у цьому випадку — дивись has_error).
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
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY
    }
    r = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    parsed = json.loads(raw_text)
    targets = parsed.get("targets", [])

    # Санітизація на випадок, якщо модель трохи відхилиться від схеми
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
