import os
import time
import sqlite3
import requests
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"
MADRID = timezone(timedelta(hours=2))  # CEST = UTC+2 (летом)
DB_PATH = "/tmp/alcotracker.db"

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}


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
    logger.info("DB ready")


def save_record(date_iso, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO records (date, status) VALUES (?, ?)", (date_iso, status))
    conn.commit()
    conn.close()
    logger.info(f"Saved: {date_iso} = {status}")


def get_records(start, end):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT date, status FROM records WHERE date >= ? AND date <= ?", (start, end)
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def tg(method, **kwargs):
    try:
        r = requests.post(f"{API}/{method}", json=kwargs, timeout=10)
        result = r.json()
        if not result.get("ok"):
            logger.error(f"TG {method} error: {result}")
        return result
    except Exception as e:
        logger.error(f"TG {method} exception: {e}")
        return {}


def get_updates(offset):
    try:
        r = requests.get(
            f"{API}/getUpdates",
            params={"offset": offset, "timeout": 0, "allowed_updates": ["callback_query"]},
            timeout=10
        )
        data = r.json()
        return data.get("result", [])
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
    week_start = today - timedelta(days=6)
    records = get_records(week_start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))

    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    emojis, alco_count, no_alco_count, empty = [], 0, 0, 0
    for i in range(7):
        day = week_start + timedelta(days=i)
        s = records.get(day.strftime("%Y-%m-%d"))
        if s == "alco":
            emojis.append("🍺"); alco_count += 1
        elif s == "no_alco":
            emojis.append("💧"); no_alco_count += 1
        else:
            emojis.append("⬜"); empty += 1

    period = f"{week_start.day} {MONTHS_RU[week_start.month]} — {today.day} {MONTHS_RU[today.month]}"
    text = (
        f"📊 Отчёт за неделю {period}\n\n"
        f"{'  '.join(days_ru)}\n{'  '.join(emojis)}\n\n"
        f"🍺 Алко: {alco_count} дн.\n💧 Безалко: {no_alco_count} дн.\n⬜ Без ответа: {empty} дн."
    )
    tg("sendMessage", chat_id=CHAT_ID, text=text)


def handle_callback(callback_query):
    cq_id = callback_query["id"]
    data = callback_query["data"]
    today = datetime.now(MADRID)
    date_iso = today.strftime("%Y-%m-%d")
    date_ru = f"{today.day} {MONTHS_RU[today.month]}"

    logger.info(f"Callback: {data} for {date_iso}")

    tg("answerCallbackQuery", callback_query_id=cq_id)

    if data == "alco":
        save_record(date_iso, "alco")
        tg("sendMessage", chat_id=CHAT_ID, text=f"Супер! Сегодня, {date_ru} — алко-день. 🍺 Записано!")
    elif data == "no_alco":
        save_record(date_iso, "no_alco")
        tg("sendMessage", chat_id=CHAT_ID, text=f"Супер! Сегодня, {date_ru} — трезвый день. 💧 Записано!")


def main():
    init_db()
    logger.info("Bot started. Entering main loop...")

    offset = 0
    last_question_date = None
    last_report_date = None

    while True:
        now = datetime.now(MADRID)
        today_str = now.strftime("%Y-%m-%d")
        hour = now.hour
        minute = now.minute
        weekday = now.weekday()  # 6 = Sunday

        # Ежедневный вопрос в 21:00
        if hour == 21 and minute < 5 and last_question_date != today_str:
            send_question()
            last_question_date = today_str

        # Воскресный отчёт в 20:00
        if weekday == 6 and hour == 20 and minute < 5 and last_report_date != today_str:
            send_weekly_report()
            last_report_date = today_str

        # Получаем обновления
        updates = get_updates(offset)
        if updates:
            logger.info(f"Got {len(updates)} update(s)")
        for update in updates:
            offset = update["update_id"] + 1
            if "callback_query" in update:
                handle_callback(update["callback_query"])

        time.sleep(2)


if __name__ == "__main__":
    main()
