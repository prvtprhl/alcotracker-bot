import os
import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])

MADRID_TZ = ZoneInfo("Europe/Madrid")

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

DB_PATH = "/data/alcotracker.db"

def init_db():
    os.makedirs("/data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS records (
            date TEXT PRIMARY KEY,
            status TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def save_record(date_iso: str, status: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO records (date, status) VALUES (?, ?)", (date_iso, status))
    conn.commit()
    conn.close()

def get_records_range(start: str, end: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT date, status FROM records WHERE date >= ? AND date <= ?", (start, end)
    ).fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}

def get_today():
    return datetime.now(MADRID_TZ)

def format_date_ru(dt):
    return f"{dt.day} {MONTHS_RU[dt.month]}"

async def send_daily_question(app: Application):
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🍺 Алко", callback_data="alco"),
        InlineKeyboardButton("💧 Безалко", callback_data="no_alco"),
    ]])
    await app.bot.send_message(
        chat_id=CHAT_ID,
        text="🍷 Сегодня день с алкоголем?",
        reply_markup=keyboard
    )
    logger.info("Daily question sent")

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    today = get_today()
    date_str = format_date_ru(today)
    date_iso = today.strftime("%Y-%m-%d")

    if query.data == "alco":
        status = "alco"
        reply = f"Супер! Сегодня, {date_str} — алко-день. 🍺 Записано!"
    else:
        status = "no_alco"
        reply = f"Супер! Сегодня, {date_str} — трезвый день. 💧 Записано!"

    save_record(date_iso, status)
    await query.message.reply_text(reply)
    logger.info(f"Recorded: {date_iso} — {status}")

async def send_weekly_report(app: Application):
    today = get_today()
    week_start = today - timedelta(days=6)

    start_iso = week_start.strftime("%Y-%m-%d")
    end_iso = today.strftime("%Y-%m-%d")
    records = get_records_range(start_iso, end_iso)

    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    emojis = []
    alco_count = 0
    no_alco_count = 0
    no_data_count = 0

    for i in range(7):
        day = week_start + timedelta(days=i)
        key = day.strftime("%Y-%m-%d")
        status = records.get(key)
        if status == "alco":
            emojis.append("🍺")
            alco_count += 1
        elif status == "no_alco":
            emojis.append("💧")
            no_alco_count += 1
        else:
            emojis.append("⬜")
            no_data_count += 1

    header = "  ".join(days_ru)
    row = "  ".join(emojis)
    period = f"{format_date_ru(week_start)} — {format_date_ru(today)}"

    text = (
        f"📊 Отчёт за неделю {period}\n\n"
        f"{header}\n{row}\n\n"
        f"🍺 Алко: {alco_count} дн.\n"
        f"💧 Безалко: {no_alco_count} дн.\n"
        f"⬜ Без ответа: {no_data_count} дн."
    )

    await app.bot.send_message(chat_id=CHAT_ID, text=text)
    logger.info("Weekly report sent")

async def send_monthly_report(app: Application):
    today = get_today()
    # Первый день текущего месяца
    month_start = today.replace(day=1)
    start_iso = month_start.strftime("%Y-%m-%d")
    end_iso = today.strftime("%Y-%m-%d")
    records = get_records_range(start_iso, end_iso)

    # Строим календарь месяца
    days_in_month = (today.replace(month=today.month % 12 + 1, day=1) - timedelta(days=1)).day if today.month < 12 else 31
    
    alco_count = 0
    no_alco_count = 0
    no_data_count = 0
    calendar_rows = []
    week = []

    # Выравниваем начало по понедельнику
    first_weekday = month_start.weekday()
    for _ in range(first_weekday):
        week.append("  ")

    for day_num in range(1, days_in_month + 1):
        day = today.replace(day=day_num)
        if day > today:
            week.append("  ")
        else:
            key = day.strftime("%Y-%m-%d")
            status = records.get(key)
            if status == "alco":
                week.append("🍺")
                alco_count += 1
            elif status == "no_alco":
                week.append("💧")
                no_alco_count += 1
            else:
                week.append("⬜")
                no_data_count += 1

        if len(week) == 7:
            calendar_rows.append("  ".join(week))
            week = []

    if week:
        while len(week) < 7:
            week.append("  ")
        calendar_rows.append("  ".join(week))

    months_ru_nom = {
        1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
        5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
        9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
    }

    header = "Пн  Вт  Ср  Чт  Пт  Сб  Вс"
    calendar_text = "\n".join(calendar_rows)

    text = (
        f"📅 {months_ru_nom[today.month]} {today.year}\n\n"
        f"{header}\n{calendar_text}\n\n"
        f"🍺 Алко: {alco_count} дн.\n"
        f"💧 Безалко: {no_alco_count} дн.\n"
        f"⬜ Без ответа: {no_data_count} дн."
    )

    await app.bot.send_message(chat_id=CHAT_ID, text=text)
    logger.info("Monthly report sent")

async def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))

    scheduler = AsyncIOScheduler(timezone="Europe/Madrid")

    # Ежедневно в 21:00
    scheduler.add_job(
        send_daily_question,
        CronTrigger(hour=21, minute=0, timezone="Europe/Madrid"),
        args=[app]
    )
    # Еженедельно в воскресенье в 20:00
    scheduler.add_job(
        send_weekly_report,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone="Europe/Madrid"),
        args=[app]
    )
    # Ежемесячно — последний день месяца в 20:00
    scheduler.add_job(
        send_monthly_report,
        CronTrigger(day="last", hour=20, minute=0, timezone="Europe/Madrid"),
        args=[app]
    )

    scheduler.start()
    logger.info("Bot started. Scheduler running.")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown()
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
