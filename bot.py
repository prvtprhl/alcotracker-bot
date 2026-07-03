import os
import sqlite3
import requests
import logging
import threading
from datetime import datetime, timezone, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")  # напр. https://xxx.railway.app
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
    emojis, alco_c, noalco_c, empty_c = [], 0, 0, 0
    for i in range(7):
        day = week_start + timedelta(days=i)
        s = records.get(day.strftime("%Y-%m-%d"))
        if s == "alco":     emojis.append("🍺"); alco_c += 1
        elif s == "no_alco": emojis.append("💧"); noalco_c += 1
        else:                emojis.append("⬜"); empty_c += 1
    period = f"{week_start.day} {MONTHS_RU[week_start.month]} — {today.day} {MONTHS_RU[today.month]}"
    text = (f"📊 Отчёт за неделю {period}\n\n"
            f"{'  '.join(days_ru)}\n{'  '.join(emojis)}\n\n"
            f"🍺 Алко: {alco_c} дн.\n💧 Безалко: {noalco_c} дн.\n⬜ Без ответа: {empty_c} дн.")
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


class WebhookHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # подавляем дефолтный лог HTTP

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AlcoTracker OK")

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            update = json.loads(body)
            logger.info(f"Webhook update received: {list(update.keys())}")
            if "callback_query" in update:
                handle_callback(update["callback_query"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        except Exception as e:
            logger.error(f"Webhook handler error: {e}")
            self.send_response(500)
            self.end_headers()


def schedule_thread():
    """Фоновый поток для ежедневного расписания."""
    import time
    last_question_date = None
    last_report_date = None
    while True:
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
        time.sleep(30)


def register_webhook():
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set — webhook not registered")
        return
    url = f"{WEBHOOK_URL}/webhook"
    result = tg("setWebhook", url=url, allowed_updates=["callback_query"])
    logger.info(f"setWebhook: {result}")


def main():
    init_db()
    logger.info("Bot starting...")

    register_webhook()

    # Запускаем поток расписания
    t = threading.Thread(target=schedule_thread, daemon=True)
    t.start()
    logger.info("Scheduler thread started")

    # Запускаем HTTP сервер
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), WebhookHandler)
    logger.info(f"Webhook server listening on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
