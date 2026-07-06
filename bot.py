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
MADRID = timezone(timedelta(hours=2))  # CEST UTC+2
DB_PATH = "/tmp/alcotracker.db"

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS records (
        date TEXT PRIMARY KEY, status TEXT NOT NULL
    )""")
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
        r = requests.post(f"{API}/{method}", json=kwargs, timeout=15)
        result = r.json()
        if not result.get("ok"):
            logger.error(f"TG {method} failed: {result}")
        else:
            logger.info(f"TG {method} OK")
        return result
    except Exception as e:
        logger.error(f"TG {method} exception: {e}")
        return {}


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
    alco_c, noalco_c, empty_c = 0, 0, 0
    lines = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_name = days_ru[day.weekday()]
        day_label = f"{day_name} {day.day} {MONTHS_RU[day.month]}"
        s = records.get(day.strftime("%Y-%m-%d"))
        if s == "alco":      emoji = "🍺"; alco_c += 1
        elif s == "no_alco": emoji = "💧"; noalco_c += 1
        else:                emoji = "⬜"; empty_c += 1
        lines.append(f"{day_label}  {emoji}")
    period = f"{week_start.day} {MONTHS_RU[week_start.month]} — {today.day} {MONTHS_RU[today.month]}"
    text = (f"📊 Отчёт за неделю {period}\n\n"
            + "\n".join(lines)
            + f"\n\n🍺 Алко: {alco_c} дн.  💧 Безалко: {noalco_c} дн.  ⬜ Без ответа: {empty_c} дн.")
    tg("sendMessage", chat_id=CHAT_ID, text=text)


def handle_callback(callback_query):
    cq_id = callback_query["id"]
    data = callback_query["data"]
    today = datetime.now(MADRID)
    date_iso = today.strftime("%Y-%m-%d")
    date_ru = f"{today.day} {MONTHS_RU[today.month]}"
    logger.info(f"Callback: {data} / {date_iso}")
    tg("answerCallbackQuery", callback_query_id=cq_id)
    if data == "alco":
        save_record(date_iso, "alco")
        tg("sendMessage", chat_id=CHAT_ID, text=f"Супер! Сегодня, {date_ru} — алко-день. 🍺 Записано!")
    elif data == "no_alco":
        save_record(date_iso, "no_alco")
        tg("sendMessage", chat_id=CHAT_ID, text=f"Супер! Сегодня, {date_ru} — трезвый день. 💧 Записано!")


def main():
    init_db()

    # Удаляем webhook если вдруг был зарегистрирован
    tg("deleteWebhook", drop_pending_updates=False)
    logger.info("Bot started (polling mode)")

    offset = 0
    last_question_date = None
    last_report_date = None

    while True:
        # --- Расписание ---
        now = datetime.now(MADRID)
        today_str = now.strftime("%Y-%m-%d")
        h, m = now.hour, now.minute
        wd = now.weekday()  # 6 = воскресенье

        if h == 21 and m < 5 and last_question_date != today_str:
            send_question()
            last_question_date = today_str

        if wd == 6 and h == 20 and m < 5 and last_report_date != today_str:
            send_weekly_report()
            last_report_date = today_str

        # --- Polling ---
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["callback_query"]},
                timeout=40
            )
            data = r.json()
            if not data.get("ok"):
                logger.error(f"getUpdates failed: {data}")
                time.sleep(5)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    handle_callback(update["callback_query"])

        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
