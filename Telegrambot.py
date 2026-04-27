import os
import json
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import gspread
from google.oauth2.service_account import Credentials
from deep_translator import GoogleTranslator

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)

print("BOT STARTED")

# ================= ENV =================
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROUP_CHAT_ID = int(os.environ["GROUP_CHAT_ID"])
SHEET_NAME = os.environ["SHEET_NAME"]

# ================= GOOGLE SHEETS (FROM ENV) =================
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])

creds = Credentials.from_service_account_info(
    creds_json,
    scopes=scope
)

client = gspread.authorize(creds)

spreadsheet = client.open(SHEET_NAME)
PROJECTS_SHEET = spreadsheet.worksheet("Projects")
LOG_SHEET = spreadsheet.sheet1

# ================= MEMORY =================
user_data = {}

# ================= LOAD PROJECTS =================
def load_projects():
    rows = PROJECTS_SHEET.get_all_records()

    data = {}

    for r in rows:
        city = r["City_EN"]

        if city not in data:
            data[city] = {
                "en": r["City_EN"],
                "ar": r["City_AR"],
                "projects": []
            }

        data[city]["projects"].append({
            "en": r["Project_EN"],
            "ar": r["Project_AR"],
            "odoo": r["Odoo"]
        })

    return data

projects = load_projects()

# ================= TRANSLATE =================
def to_en(text):
    try:
        return GoogleTranslator(source='auto', target='en').translate(text)
    except:
        return text

# ================= TEXTS =================
messages = {
    "choose_lang": {"en": "Choose Language:", "ar": "اختر اللغة:"},
    "enter_name": {"en": "Enter your name:", "ar": "اكتب اسمك:"},
    "select_city": {"en": "Select City:", "ar": "اختار المدينة:"},
    "select_project": {"en": "Select Project:", "ar": "اختار المشروع:"},
    "write_work": {"en": "Write work done:", "ar": "اكتب الشغل:"},
    "write_issues": {"en": "Write issues:", "ar": "اكتب المشاكل:"},
    "ask_photo": {"en": "Upload photos?", "ar": "هل تريد صور؟"},
}

def t(key, lang):
    return messages[key]["ar"] if lang == "Arabic" else messages[key]["en"]

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id

    user_data[uid] = {"step": "lang", "photos": []}

    keyboard = [
        [
            InlineKeyboardButton("English", callback_data="lang_en"),
            InlineKeyboardButton("عربي", callback_data="lang_ar")
        ]
    ]

    await update.message.reply_text(
        "Choose Language:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= BUTTONS =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = user_data.setdefault(uid, {})

    cb = query.data

    # -------- LANGUAGE --------
    if cb.startswith("lang_"):
        data["language"] = "Arabic" if cb == "lang_ar" else "English"
        data["step"] = "name"

        await query.message.reply_text(
            t("enter_name", data["language"])
        )
        return

    # -------- CITY --------
    if cb.startswith("city|"):
        city = cb.split("|")[1]

        data["city"] = city
        data["step"] = "project"

        lang = "ar" if data["language"] == "Arabic" else "en"

        buttons = [
            [InlineKeyboardButton(
                p[lang],
                callback_data=f"project|{city}|{p['en']}|{p['odoo']}"
            )]
            for p in projects[city]["projects"]
        ]

        await query.message.reply_text(
            t("select_project", data["language"]),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # -------- PROJECT --------
    if cb.startswith("project|"):
        _, city, project, odoo = cb.split("|")

        data.update({
            "city": city,
            "project": project,
            "odoo": odoo,
            "step": "work",
            "photos": []
        })

        await query.message.reply_text(
            t("write_work", data["language"])
        )
        return

    # -------- PHOTO DONE --------
    if cb == "photo_done":
        await save_report(uid, query, context)
        return

# ================= MESSAGES =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    text = update.message.text

    if uid not in user_data:
        return

    data = user_data[uid]

    # -------- NAME --------
    if data["step"] == "name":
        data["name"] = text
        data["step"] = "city"

        buttons = [
            [InlineKeyboardButton(city, callback_data=f"city|{city}")]
            for city in projects.keys()
        ]

        await update.message.reply_text(
            t("select_city", data["language"]),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # -------- WORK --------
    if data["step"] == "work":
        data["work"] = text
        data["step"] = "issues"

        await update.message.reply_text(
            t("write_issues", data["language"])
        )
        return

    # -------- ISSUES --------
    if data["step"] == "issues":
        data["issues"] = text
        data["step"] = "photo"

        keyboard = [
            [InlineKeyboardButton("Done", callback_data="photo_done")]
        ]

        await update.message.reply_text(
            t("ask_photo", data["language"]),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # -------- PHOTO UPLOAD --------
    if update.message.photo and data["step"] == "photo":
        file = await update.message.photo[-1].get_file()
        file_bytes = await file.download_as_bytearray()

        filename = f"{data['project']}_{datetime.now()}.jpg"

        # 🔥 SEND TO TELEGRAM GROUP مباشرة
        await context.bot.send_photo(
            chat_id=GROUP_CHAT_ID,
            photo=file_bytes,
            caption=f"""
📌 {data.get('project')}
👷 {data.get('name')}
🏙 {data.get('city')}
📝 {data.get('work')}
⚠ {data.get('issues')}
"""
        )

# ================= SAVE REPORT =================
async def save_report(uid, update, context):
    data = user_data[uid]

    LOG_SHEET.append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        data.get("name"),
        data.get("city"),
        data.get("project"),
        data.get("work"),
        data.get("issues")
    ])

    await update.message.reply_text("Saved ✅")

    user_data[uid] = {"step": "lang", "photos": []}

# ================= APP =================
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

app.run_polling()
