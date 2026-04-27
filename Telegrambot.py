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
print("🚀 BOT IS RUNNING...")

# ================= ENV =================
# تأكد من وضع هذه القيم في Render Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", 0))
SHEET_NAME = os.environ.get("SHEET_NAME")

# ================= GOOGLE SHEETS =================
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# قراءة الكريدنشالز من ENV
creds_json = json.loads(os.environ.get("GOOGLE_CREDENTIALS", "{}"))
creds = Credentials.from_service_account_info(creds_json, scopes=scope)
client = gspread.authorize(creds)

spreadsheet = client.open(SHEET_NAME)
PROJECTS_SHEET = spreadsheet.worksheet("Projects")
LOG_SHEET = spreadsheet.sheet1

# ================= MEMORY & DATA =================
user_data = {}

def load_projects():
    rows = PROJECTS_SHEET.get_all_records()
    data = {}
    for r in rows:
        city = str(r["City_EN"]).strip()
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

# تحميل المشاريع عند التشغيل
ALL_PROJECTS = load_projects()

# ================= TRANSLATE =================
def translate_to_en(text):
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
    "ask_photo": {"en": "Send photos now or click Done:", "ar": "ارسل الصور الآن أو اضغط إنهاء:"},
    "done": {"en": "Saved ✅", "ar": "تم الحفظ ✅"}
}

def t(key, lang):
    return messages[key]["ar"] if lang == "Arabic" else messages[key]["en"]

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data[uid] = {"step": "lang", "photos": []}
    
    keyboard = [[
        InlineKeyboardButton("English", callback_data="lang_en"),
        InlineKeyboardButton("عربي", callback_data="lang_ar")
    ]]
    await update.message.reply_text("Choose Language / اختر اللغة:", reply_markup=InlineKeyboardMarkup(keyboard))

# ================= BUTTONS =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = user_data.get(uid)
    if not data: return

    cb = query.data

    if cb.startswith("lang_"):
        data["language"] = "Arabic" if cb == "lang_ar" else "English"
        data["step"] = "name"
        await query.message.reply_text(t("enter_name", data["language"]))

    elif cb.startswith("city|"):
        city = cb.split("|")[1]
        data["city"] = city
        data["step"] = "project"
        
        lang_key = "ar" if data["language"] == "Arabic" else "en"
        buttons = [[InlineKeyboardButton(p[lang_key], callback_data=f"proj|{city}|{p['en']}|{p['odoo']}")] 
                   for p in ALL_PROJECTS[city]["projects"]]
        
        await query.message.reply_text(t("select_project", data["language"]), reply_markup=InlineKeyboardMarkup(buttons))

    elif cb.startswith("proj|"):
        _, city, proj, odoo = cb.split("|")
        data.update({"city": city, "project": proj, "odoo": odoo, "step": "work"})
        await query.message.reply_text(t("write_work", data["language"]))

    elif cb == "photo_done":
        await save_report(uid, query, context)

# ================= MESSAGES & PHOTOS =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data: return
    data = user_data[uid]

    # استقبال الصور في أي وقت وهي في مرحلة الصور
    if update.message.photo and data.get("step") == "photo":
        file = await update.message.photo[-1].get_file()
        photo_bytes = await file.download_as_bytearray()
        data["photos"].append(photo_bytes)
        await update.message.reply_text("📸 OK! Send more or press Done.")
        return

    text = update.message.text
    if not text: return

    if data["step"] == "name":
        data["name"] = text
        data["step"] = "city"
        lang_key = "ar" if data["language"] == "Arabic" else "en"
        
        buttons = [[InlineKeyboardButton(ALL_PROJECTS[c][lang_key], callback_data=f"city|{c}")] 
                   for c in ALL_PROJECTS.keys()]
        await update.message.reply_text(t("select_city", data["language"]), reply_markup=InlineKeyboardMarkup(buttons))

    elif data["step"] == "work":
        data["work"] = text
        data["step"] = "issues"
        await update.message.reply_text(t("write_issues", data["language"]))

    elif data["step"] == "issues":
        data["issues"] = text
        data["step"] = "photo"
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="photo_done")]]
        await update.message.reply_text(t("ask_photo", data["language"]), reply_markup=InlineKeyboardMarkup(kb))

# ================= SAVE & SEND =================
async def save_report(uid, query, context):
    data = user_data[uid]
    lang = data.get("language", "English")

    # إرسال البيانات للجروب
    report_msg = f"""
📌 *Project:* {data.get('project')}
👷 *Worker:* {data.get('name')}
🏙 *City:* {data.get('city')}
📝 *Work:* {data.get('work')}
⚠ *Issues:* {data.get('issues')}
"""
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=report_msg, parse_mode="Markdown")

    # إرسال الصور للجروب
    for p_bytes in data.get("photos", []):
        await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=p_bytes)

    # حفظ في جوجل شيت
    try:
        LOG_SHEET.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            data.get("name"),
            data.get("city"),
            data.get("project"),
            data.get("work"),
            data.get("issues")
        ])
    except Exception as e:
        logging.error(f"Sheet Save Error: {e}")

    await query.message.reply_text(t("done", lang))
    user_data[uid] = {"step": "lang", "photos": []}

# ================= RUN =================
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

app.run_polling()
