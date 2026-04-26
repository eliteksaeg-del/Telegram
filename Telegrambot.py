from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

import gspread
from google.oauth2.service_account import Credentials
from deep_translator import GoogleTranslator
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from datetime import datetime
import os

# ================= TOKEN =================
TOKEN = "8468978393:AAH3cp0fA9kltxy5a1kzdfj_NuJwTiVsamA"

print("BOT STARTED")

# ================= GOOGLE SHEETS =================
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

creds = Credentials.from_service_account_file(
    "telegram-bot.json",
    scopes=scope
)

client = gspread.authorize(creds)

spreadsheet = client.open("Daily Summary")
PROJECTS_SHEET = spreadsheet.worksheet("Projects")
LOG_SHEET = spreadsheet.sheet1

# ================= DRIVE =================
drive_service = build("drive", "v3", credentials=creds)
DRIVE_FOLDER_ID = "YOUR_DRIVE_FOLDER_ID"

# ================= MEMORY =================
user_data = {}

# ================= LOAD PROJECTS =================
def load_projects():
    rows = PROJECTS_SHEET.get_all_records()

    data = {}

    for r in rows:
        city_en = r["City_EN"]

        if city_en not in data:
            data[city_en] = {
                "en": r["City_EN"],
                "ar": r["City_AR"],
                "projects": []
            }

        data[city_en]["projects"].append({
            "en": r["Project_EN"],
            "ar": r["Project_AR"],
            "odoo": r["Odoo"]
        })

    return data

projects = load_projects()

# ================= TRANSLATION =================
def to_en(text):
    try:
        return GoogleTranslator(source='auto', target='en').translate(text)
    except:
        return text

# ================= DRIVE UPLOAD =================
def upload_to_drive(file_path, filename):
    file_metadata = {
        "name": filename,
        "parents": [DRIVE_FOLDER_ID]
    }

    media = MediaFileUpload(file_path, resumable=True)

    file = drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id"
    ).execute()

    file_id = file["id"]

    drive_service.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"}
    ).execute()

    return f"https://drive.google.com/file/d/{file_id}/view"

# ================= MESSAGES =================
messages = {
    "choose_lang": {"en": "Choose Language:", "ar": "اختر اللغة:"},
    "enter_name": {"en": "Enter your name:", "ar": "اكتب اسمك:"},
    "select_city": {"en": "Select City:", "ar": "اختار المدينة:"},
    "select_project": {"en": "Select Project:", "ar": "اختار المشروع:"},
    "write_work": {"en": "Write work done in details , show the building No.:", "ar": "اكتب الشغل مع توضيح رقم العمارة:"},
    "write_issues": {"en": "Write issues that you faced today:", "ar": "اكتب المشاكل التي واجهتك اثناء العمل اليوم:"},
    "ask_photo": {"en": "Do you want to upload photos?", "ar": "هل تريد رفع صور؟"},
    "saved": {"en": "Saved ✅", "ar": "تم الحفظ ✅"}
}

def t(key, lang):
    return messages[key]["ar"] if lang == "عربي" else messages[key]["en"]

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    user_data[user_id] = {
        "step": "language",
        "photos": []
    }

    keyboard = [
        [
            InlineKeyboardButton("English", callback_data="lang_en"),
            InlineKeyboardButton("عربي", callback_data="lang_ar")
        ]
    ]

    await update.message.reply_text(
        messages["choose_lang"]["en"],
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= BUTTONS =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = user_data.setdefault(user_id, {})

    cb = query.data

    # -------- LANGUAGE --------
    if cb.startswith("lang_"):
        data["language"] = "عربي" if cb == "lang_ar" else "English"
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

        lang = "ar" if data["language"] == "عربي" else "en"

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
        _, city, project_en, odoo = cb.split("|")

        data["city"] = city
        data["project"] = project_en
        data["odoo"] = odoo
        data["step"] = "work"
        data["photos"] = []

        await query.message.reply_text(
            t("write_work", data["language"])
        )
        return

    # -------- PHOTO OPTIONS --------
    if cb == "photo_more":
        data["step"] = "photo_upload"
        await query.message.reply_text("📸 Send photo")
        return

    if cb == "photo_done":
        await save_report(user_id, query)
        return

# ================= MESSAGE =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id

    if user_id not in user_data:
        return

    data = user_data[user_id]

    # -------- NAME --------
    if data["step"] == "name":
        data["name"] = update.message.text
        data["username"] = update.message.from_user.username
        data["telegram_id"] = user_id
        data["step"] = "city"

        lang = "ar" if data["language"] == "عربي" else "en"

        buttons = [
            [InlineKeyboardButton(
                projects[c][lang],
                callback_data=f"city|{c}"
            )]
            for c in projects
        ]

        await update.message.reply_text(
            t("select_city", data["language"]),
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # -------- WORK --------
    if data["step"] == "work":
        data["work"] = update.message.text
        data["step"] = "issues"

        await update.message.reply_text(
            t("write_issues", data["language"])
        )
        return

    # -------- ISSUES --------
    if data["step"] == "issues":
        data["issues"] = update.message.text
        data["step"] = "photo_choice"

        keyboard = [
            [
                InlineKeyboardButton("Yes 📸", callback_data="photo_more"),
                InlineKeyboardButton("No ❌", callback_data="photo_done")
            ]
        ]

        await update.message.reply_text(
            t("ask_photo", data["language"]),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # -------- PHOTO UPLOAD --------
    if data["step"] == "photo_upload":
        if update.message.photo:

            file = await update.message.photo[-1].get_file()

            safe_project = data["project"].replace(" ", "_")
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

            filename = f"{safe_project}_{timestamp}.jpg"
            folder = "photos"
            os.makedirs(folder, exist_ok=True)

            file_path = os.path.join(folder, filename)

            await file.download_to_drive(file_path)

            data.setdefault("photos", []).append(file_path)

            await update.message.reply_text(
                "📸 Saved",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("➕ Add more", callback_data="photo_more"),
                        InlineKeyboardButton("✅ Done", callback_data="photo_done")
                    ]
                ])
            )

# ================= SAVE (FIXED) =================
async def save_report(user_id, update):
    data = user_data[user_id]

    base_row = [
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data.get("name"),
        data.get("username"),
        data.get("telegram_id"),
        data.get("language"),
        data.get("city"),
        data.get("project"),
        data.get("odoo"),
        data.get("work"),
        to_en(data.get("work")),
        data.get("issues"),
        to_en(data.get("issues")),
    ]

    # لو مفيش صور → صف واحد فقط
    if not data.get("photos"):
        LOG_SHEET.append_row(base_row + [""])
    else:
        for photo in data["photos"]:
            LOG_SHEET.append_row(base_row + [photo])

    await update.message.reply_text("✅ Saved successfully")

    user_data[user_id] = {
        "step": "language",
        "photos": []
    }

# ================= APP =================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

app.run_polling()