import os
import json
import gspread
import asyncio
import logging
import http.server
import socketserver
import threading
from google.oauth2.service_account import Credentials
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# 1. إعدادات التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= CONFIGURATION =================
TOKEN = "8468978393:AAGS2tu8Xj1O7bUOExicaWPGgFhcokLNLJo"
GROUP_CHAT_ID = "-5104938886"

# ================= DUMMY SERVER FOR RENDER =================
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            httpd.serve_forever()
    except: pass

# ================= GOOGLE SHEETS SETUP =================
def connect_google():
    try:
        raw_json = os.environ.get("GSPREAD_JSON", "").strip()
        creds_info = json.loads(raw_json)
        if "private_key" in creds_info:
            creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open("Daily Summary")
        return spreadsheet.worksheet("Projects"), spreadsheet.sheet1
    except Exception as e:
        print(f"❌ Google Error: {e}")
        return None, None

PROJECTS_SHEET, LOG_SHEET = connect_google()
user_data = {}

def load_projects():
    if not PROJECTS_SHEET: return {}
    try:
        rows = PROJECTS_SHEET.get_all_records()
        data = {}
        for r in rows:
            c_en, c_ar = str(r.get("City_EN")), str(r.get("City_AR"))
            if c_en not in data:
                data[c_en] = {"en": c_en, "ar": c_ar, "projects": []}
            data[c_en]["projects"].append({
                "en": str(r.get("Project_EN")), 
                "ar": str(r.get("Project_AR")), 
                "odoo": str(r.get("Odoo"))
            })
        return data
    except: return {}

# ================= TEXTS DICTIONARY =================
TEXTS = {
    "en": {
        "ask_name": "Please enter your name:",
        "ask_city": "Select City:",
        "ask_project": "Select Project:",
        "ask_work": "What work was done today?",
        "ask_issues": "Any issues faced?",
        "ask_photo": "Do you want to upload a photo?",
        "send_photo": "Please send the photo now:",
        "done": "✅ Report saved successfully!",
        "btn_yes": "Yes 📸",
        "btn_no": "No ❌",
        "btn_finish": "✅ Finish",
        "btn_more": "➕ Add More"
    },
    "ar": {
        "ask_name": "من فضلك اكتب اسمك:",
        "ask_city": "اختر المدينة:",
        "ask_project": "اختر المشروع:",
        "ask_work": "ما هو الشغل المنجز اليوم؟",
        "ask_issues": "هل واجهت أي مشاكل؟",
        "ask_photo": "هل تريد رفع صورة؟",
        "send_photo": "من فضلك ارسل الصورة الآن:",
        "done": "✅ تم حفظ التقرير بنجاح!",
        "btn_yes": "نعم 📸",
        "btn_no": "لا ❌",
        "btn_finish": "✅ إنهاء",
        "btn_more": "➕ صورة أخرى"
    }
}

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"photos": []}
    keyboard = [[InlineKeyboardButton("English", callback_data="lang_en"), 
                 InlineKeyboardButton("عربي", callback_data="lang_ar")]]
    await update.message.reply_text("Choose Language / اختر اللغة:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.get(user_id)
    if not data: return

    cb = query.data
    lang = data.get("lang", "en")

    if cb.startswith("lang_"):
        data["lang"] = cb.split("_")[1]
        data["step"] = "name"
        await query.message.reply_text(TEXTS[data["lang"]]["ask_name"])

    elif cb.startswith("city|"):
        city_en = cb.split("|")[1]
        data["city_en"] = city_en
        data["step"] = "project"
        projects_dict = load_projects()
        current_lang = data["lang"]
        
        btns = []
        for p in projects_dict.get(city_en, {}).get("projects", []):
            label = p[current_lang]
            btns.append([InlineKeyboardButton(label, callback_data=f"proj|{p['en']}|{p['odoo']}")] )
        
        await query.message.reply_text(TEXTS[current_lang]["ask_project"], reply_markup=InlineKeyboardMarkup(btns))

    elif cb.startswith("proj|"):
        _, p_en, odoo = cb.split("|")
        data["project_en"] = p_en
        data["odoo"] = odoo
        data["step"] = "work"
        await query.message.reply_text(TEXTS[data["lang"]]["ask_work"])

    elif cb == "photo_yes":
        data["step"] = "uploading"
        await query.message.reply_text(TEXTS[data["lang"]]["send_photo"])

    elif cb == "photo_no" or cb == "finish":
        await save_report(update, context, data)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id)
    if not data: return
    
    step = data.get("step")
    lang = data.get("lang", "en")

    if step == "name":
        data["name"] = update.message.text
        data["step"] = "city"
        projects_dict = load_projects()
        btns = [[InlineKeyboardButton(v[lang], callback_data=f"city|{k}")] for k, v in projects_dict.items()]
        await update.message.reply_text(TEXTS[lang]["ask_city"], reply_markup=InlineKeyboardMarkup(btns))

    elif step == "work":
        data["work"] = update.message.text
        data["step"] = "issues"
        await update.message.reply_text(TEXTS[lang]["ask_issues"])

    elif step == "issues":
        data["issues"] = update.message.text
        data["step"] = "photo_choice"
        btns = [[InlineKeyboardButton(TEXTS[lang]["btn_yes"], callback_data="photo_yes"),
                 InlineKeyboardButton(TEXTS[lang]["btn_no"], callback_data="photo_no")]]
        await update.message.reply_text(TEXTS[lang]["ask_photo"], reply_markup=InlineKeyboardMarkup(btns))

    elif step == "uploading" and update.message.photo:
        data["photos"].append(update.message.photo[-1].file_id)
        btns = [[InlineKeyboardButton(TEXTS[lang]["btn_more"], callback_data="photo_yes"),
                 InlineKeyboardButton(TEXTS[lang]["btn_finish"], callback_data="finish")]]
        await update.message.reply_text("✅ Done", reply_markup=InlineKeyboardMarkup(btns))

async def save_report(update, context, data):
    user_id = update.effective_user.id
    lang = data.get("lang", "en")
    caption = f"👷‍♂️ Worker: {data.get('name')}\n📍 City: {data.get('city_en')}\n🏗 Project: {data.get('project_en')}\n📝 Work: {data.get('work')}\n⚠️ Issues: {data.get('issues')}"
    
    try:
        if data["photos"]:
            for p_id in data["photos"]:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=p_id, caption=caption)
        else:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=caption)
        
        if LOG_SHEET:
            LOG_SHEET.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), data.get('name'), "", user_id, lang, data.get('city_en'), data.get('project_en'), data.get('odoo'), data.get('work'), "", data.get('issues'), "", "Sent"])
        
        msg = TEXTS[lang]["done"]
        if update.callback_query: await update.callback_query.message.reply_text(msg)
        else: await update.message.reply_text(msg)
    except Exception as e:
        print(f"Error: {e}")
    
    user_data[user_id] = {"photos": []}

# ================= MAIN =================
async def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(1000)

if __name__ == '__main__':
    asyncio.run(main())
