import os
import time
import sqlite3
import requests
import schedule
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"
MADRID = ZoneInfo("Europe/Madrid")
DB_PATH = "/tmp/alcotracker.db"

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

# --- База данных ---

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            date TEXT PRIMARY KEY,
            status TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_record(date_iso, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO records (date, status) VALUES (?, ?)", (date_iso, status))
    conn.commit()
    conn.close()
    logger.info(f"Saved: {date_iso} = {status}")

def get_records(start, end):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT date, status FROM records WHERE date >= ? AND date <= ?", (start, end)).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

# --- Telegram API ---

def tg(method, **kwargs):
    try:
        r = requests.post(f"{API}/{method}", json=kwargs, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"TG error {method}: {e}")
        return {}

def get_updates(offset):
    try:
        r = requests.get(f"{API}/getUpdates", params={"offset": offset, "timeout": 25, "allowed_updates": ["callback_query"]}, timeout=30)
        return r.json().get("result", [])
    except Exception as e:
        logger.error(f"getUpdates error: {e}")
        return []

def send_question():
    logger.info("Sending daily question...")
    tg("sendMessage",
        chat_id=CHAT_ID,
        text="🍷 Сегодня день с алкоголем?",
        reply_markup={"inline_keyboard": [[
            {"text": "🍺 Алко", "callback_data": "alco"},
            {"text": "💧 Безалко", "callback_data": "no_alco"}
        ]]}
    )

def send_weekly_report():
    logger.info("Sending weekly report...")
    today = datetime.now(MADRID)
    from datetime import timedelta
    week_start = today - timedelta(days=6)
    records = get_records(week_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))

    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    emojis, alco, no_alco, empty = [], 0, 0, 0
    for i in range(7):
        from datetime import timedelta as td
        day = week_start + td(days=i)
        s = records.get(day.strftime("%Y-%m-%d"))
        if s == "alco":   emojis.append("🍺"); alco += 1
        elif s == "no_alco": emojis.append("💧"); no_alco += 1
        else:             emojis.append("⬜"); empty += 1

    period = f"{week_start.day} {MONTHS_RU[week_start.month]} — {today.day} {MONTHS_RU[today.month]}"
    text = (f"📊 Отчёт за неделю {period}\n\n"
            f"{'  '.join(days_ru)}\n{'  '.join(emojis)}\n\n"
            f"🍺 Алко: {alco} дн.\n💧 Безалко: {no_alco} дн.\n⬜ Без ответа: {empty} дн.")
    tg("sendMessage", chat_id=CHAT_ID, text=text)

# --- Обработка callback ---

def handle_callback(callback_query):
    cq_id = callback_query["id"]
    data = callback_query["data"]
    today = datetime.now(MADRID)
    date_iso = today.strftime("%Y-%m-%d")
    date_ru = f"{today.day} {MONTHS_RU[today.month]}"

    tg("answerCallbackQuery", callback_query_id=cq_id)

    if data == "alco":
        save_record(date_iso, "alco")
        reply = f"Супер! Сегодня, {date_ru} — алко-день. 🍺 Записано!"
    else:
        save_record(date_iso, "no_alco")
        reply = f"Супер! Сегодня, {date_ru} — трезвый день. 💧 Записано!"

    tg("sendMessage", chat_id=CHAT_ID, text=reply)

# --- Главный цикл ---

def main():
    init_db()
    logger.info("Bot started")

    # Расписание (Railway работает в UTC; 21:00 Madrid = 19:00 UTC летом CEST)
    schedule.every().day.at("19:00").do(send_question)
    schedule.every().sunday.at("18:00").do(send_weekly_report)

    offset = 0

    while True:
        # Проверяем расписание
        schedule.run_pending()

        # Получаем обновления
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            if "callback_query" in update:
                handle_callback(update["callback_query"])

        # Если не было обновлений — небольшая пауза
        if not updates:
            time.sleep(1)

if __name__ == "__main__":
    main()
