import os
import json
import time
import requests
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API = f"https://api.telegram.org/bot{TOKEN}"

# Часовой пояс с автоматическим переходом на зимнее/летнее время
try:
    from zoneinfo import ZoneInfo
    MADRID = ZoneInfo("Europe/Madrid")
except Exception as _tz_err:  # фолбэк, если нет базы tzdata
    logger.error(f"zoneinfo unavailable ({_tz_err}), falling back to fixed UTC+2")
    MADRID = timezone(timedelta(hours=2))

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря"
}

# В памяти: {date_iso: "alco"/"no_alco"}
records = {}
storage_message_id = None  # ID закреплённого сообщения с данными


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


def load_data():
    """Загружаем данные из закреплённого сообщения чата."""
    global records, storage_message_id

    result = tg("getChat", chat_id=CHAT_ID)
    pinned = result.get("result", {}).get("pinned_message")

    if pinned:
        text = pinned.get("text", "")
        if text.startswith("ALCOTRACKER_DATA:"):
            try:
                records = json.loads(text[len("ALCOTRACKER_DATA:"):])
                storage_message_id = pinned["message_id"]
                logger.info(f"Loaded {len(records)} records from pinned message {storage_message_id}")
                return
            except Exception as e:
                logger.error(f"Failed to parse pinned data: {e}")

    # Нет закреплённого сообщения — создаём новое
    logger.info("No storage message found, creating one...")
    save_data()


def save_data():
    """Сохраняем данные — обновляем или создаём закреплённое сообщение."""
    global storage_message_id

    text = "ALCOTRACKER_DATA:" + json.dumps(records, ensure_ascii=False)

    if storage_message_id:
        tg("editMessageText",
           chat_id=CHAT_ID,
           message_id=storage_message_id,
           text=text)
    else:
        result = tg("sendMessage", chat_id=CHAT_ID, text=text)
        msg_id = result.get("result", {}).get("message_id")
        if msg_id:
            storage_message_id = msg_id
            tg("pinChatMessage", chat_id=CHAT_ID, message_id=msg_id, disable_notification=True)
            logger.info(f"Created and pinned storage message {msg_id}")


def send_question():
    logger.info("Sending daily question...")
    # Дата вопроса зашивается в кнопки, чтобы ответ после полуночи
    # засчитался за тот день, когда был задан вопрос.
    date_iso = datetime.now(MADRID).strftime("%Y-%m-%d")
    tg("sendMessage",
       chat_id=CHAT_ID,
       text="🍷 Сегодня день с алкоголем?",
       reply_markup={"inline_keyboard": [[
           {"text": "🍺 Алко", "callback_data": f"alco:{date_iso}"},
           {"text": "💧 Безалко", "callback_data": f"no_alco:{date_iso}"}
       ]]}
       )


def send_weekly_report():
    logger.info("Sending weekly report...")
    today = datetime.now(MADRID)
    # Отчёт за прошлую неделю (пн–вс): вызывается в понедельник,
    # поэтому конец прошлой недели — вчера (воскресенье)
    week_end = today - timedelta(days=1)
    week_start = week_end - timedelta(days=6)
    days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    alco_c, noalco_c, empty_c = 0, 0, 0
    lines = []
    for i in range(7):
        day = week_start + timedelta(days=i)
        day_name = days_ru[day.weekday()]
        day_label = f"{day_name} {day.day} {MONTHS_RU[day.month]}"
        s = records.get(day.strftime("%Y-%m-%d"))
        if s == "alco":
            emoji = "🍺"; alco_c += 1
        elif s == "no_alco":
            emoji = "💧"; noalco_c += 1
        else:
            emoji = "—"; empty_c += 1
        lines.append(f"{day_label}  {emoji}")
    period = f"{week_start.day} {MONTHS_RU[week_start.month]} — {week_end.day} {MONTHS_RU[week_end.month]}"
    text = (f"📊 Отчёт за неделю {period}\n\n"
            + "\n".join(lines)
            + f"\n\n🍺 Алко: {alco_c} дн.  💧 Безалко: {noalco_c} дн.  — Без ответа: {empty_c} дн.")
    tg("sendMessage", chat_id=CHAT_ID, text=text)


def resolve_answer_date(callback_query, raw_data):
    """Дата, за которую засчитывается ответ.

    1) Если дата зашита в callback_data (новые кнопки) — берём её.
    2) Иначе — дата самого сообщения с вопросом (старые кнопки).
    3) В крайнем случае — текущая дата.
    """
    if ":" in raw_data:
        candidate = raw_data.split(":", 1)[1]
        try:
            return datetime.strptime(candidate, "%Y-%m-%d").date()
        except ValueError:
            logger.error(f"Bad date in callback_data: {raw_data}")

    msg_ts = (callback_query.get("message") or {}).get("date")
    if msg_ts:
        return datetime.fromtimestamp(msg_ts, tz=MADRID).date()

    return datetime.now(MADRID).date()


def handle_callback(callback_query):
    cq_id = callback_query["id"]
    raw_data = callback_query["data"]
    action = raw_data.split(":", 1)[0]

    answer_date = resolve_answer_date(callback_query, raw_data)
    date_iso = answer_date.strftime("%Y-%m-%d")
    date_ru = f"{answer_date.day} {MONTHS_RU[answer_date.month]}"

    # Если ответ пришёл позже, чем в день вопроса — говорим "За ...", а не "Сегодня"
    is_today = answer_date == datetime.now(MADRID).date()
    prefix = "Сегодня, " if is_today else "За "

    logger.info(f"Callback: {raw_data} -> {date_iso} (today={is_today})")
    tg("answerCallbackQuery", callback_query_id=cq_id)

    if action == "alco":
        records[date_iso] = "alco"
        save_data()
        tg("sendMessage", chat_id=CHAT_ID, text=f"Супер! {prefix}{date_ru} — алко-день. 🍺 Записано!")
    elif action == "no_alco":
        records[date_iso] = "no_alco"
        save_data()
        tg("sendMessage", chat_id=CHAT_ID, text=f"Супер! {prefix}{date_ru} — трезвый день. 💧 Записано!")


def main():
    tg("deleteWebhook", drop_pending_updates=False)
    logger.info("Bot started (polling mode)")

    load_data()

    offset = 0
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

        if wd == 0 and h == 10 and m < 5 and last_report_date != today_str:
            send_weekly_report()
            last_report_date = today_str

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
