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
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

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
        if not raw_json:
            return None, None
        creds_info = json.loads(raw_json)
        if "private_key" in creds_info:
            creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)
        
        # التأكد من اسم الملف
        spreadsheet = client.open("Daily Summary")
        return spreadsheet.worksheet("Projects"), spreadsheet.sheet1
    except Exception as e:
        print(f"❌ Google Connection Error: {e}")
        return None, None

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
        "no_proj": "⚠️ No projects found in the 'Projects' sheet!",
        "btn_yes": "Yes 📸", "btn_no": "No ❌", "btn_finish": "✅ Finish", "btn_more": "➕ Add More"
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
        "no_proj": "⚠️ لم يتم العثور على مشاريع في شيت Projects!",
        "btn_yes": "نعم 📸", "btn_no": "لا ❌", "btn_finish": "✅ إنهاء", "btn_more": "➕ صورة أخرى"
    }
}

user_data = {}

def get_projects_data():
    proj_sheet, _ = connect_google()
    if not proj_sheet: return {}
    try:
        rows = proj_sheet.get_all_records()
        data = {}
        for r in rows:
            c_en = str(r.get("City_EN", "")).strip()
            if c_en:
                if c_en not in data:
                    data[c_en] = {"en": c_en, "ar": str(r.get("City_AR", c_en)), "projects": []}
                data[c_en]["projects"].append({
                    "en": str(r.get("Project_EN", "")),
                    "ar": str(r.get("Project_AR", "")),
                    "odoo": str(r.get("Odoo", ""))
                })
        return data
    except Exception as e:
        print(f"Error reading rows: {e}")
        return {}

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
    if user_id not in user_data: user_data[user_id] = {"photos": []}
    data = user_data[user_id]

    cb = query.data
    lang = data.get("lang", "en")

    if cb.startswith("lang_"):
        data["lang"] = cb.split("_")[1]
        data["step"] = "name"
        await query.message.edit_text(TEXTS[data["lang"]]["ask_name"])

    elif cb.startswith("city|"):
        city_en = cb.split("|")[1]
        data["city_en"] = city_en
        data["step"] = "project"
        all_projs = get_projects_data()
        
        btns = []
        for p in all_projs.get(city_en, {}).get("projects", []):
            label = p[data["lang"]] if p[data["lang"]] else p["en"]
            btns.append([InlineKeyboardButton(label, callback_data=f"proj|{p['en']}|{p['odoo']}")] )
        
        await query.message.edit_text(TEXTS[data["lang"]]["ask_project"], reply_markup=InlineKeyboardMarkup(btns))

    elif cb.startswith("proj|"):
        _, p_en, odoo = cb.split("|")
        data["project_en"], data["odoo"], data["step"] = p_en, odoo, "work"
        await query.message.edit_text(TEXTS[data["lang"]]["ask_work"])

    elif cb == "photo_yes":
        data["step"] = "uploading"
        await query.message.edit_text(TEXTS[data["lang"]]["send_photo"])

    elif cb in ["photo_no", "finish"]:
        await save_report(update, context, data)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data: return
    data = user_data[user_id]
    lang = data.get("lang", "en")

    if data.get("step") == "name":
        data["name"] = update.message.text
        data["step"] = "city"
        all_projs = get_projects_data()
        if not all_projs:
            await update.message.reply_text(TEXTS[lang]["no_proj"])
            return
        
        btns = [[InlineKeyboardButton(v[lang], callback_data=f"city|{k}")] for k, v in all_projs.items()]
        await update.message.reply_text(TEXTS[lang]["ask_city"], reply_markup=InlineKeyboardMarkup(btns))

    elif data.get("step") == "work":
        data["work"], data["step"] = update.message.text, "issues"
        await update.message.reply_text(TEXTS[lang]["ask_issues"])

    elif data.get("step") == "issues":
        data["issues"], data["step"] = update.message.text, "photo_choice"
        btns = [[InlineKeyboardButton(TEXTS[lang]["btn_yes"], callback_data="photo_yes"),
                 InlineKeyboardButton(TEXTS[lang]["btn_no"], callback_data="photo_no")]]
        await update.message.reply_text(TEXTS[lang]["ask_photo"], reply_markup=InlineKeyboardMarkup(btns))

    elif data.get("step") == "uploading" and update.message.photo:
        data["photos"].append(update.message.photo[-1].file_id)
        btns = [[InlineKeyboardButton(TEXTS[lang]["btn_more"], callback_data="photo_yes"),
                 InlineKeyboardButton(TEXTS[lang]["btn_finish"], callback_data="finish")]]
        await update.message.reply_text("✅ OK", reply_markup=InlineKeyboardMarkup(btns))

async def save_report(update, context, data):
    user_id = update.effective_user.id
    lang = data.get("lang", "en")
    _, log_sheet = connect_google()
    
    caption = (f"👷‍♂️ Worker: {data.get('name')}\n"
               f"📍 City: {data.get('city_en')}\n"
               f"🏗 Project: {data.get('project_en')}\n"
               f"📝 Work: {data.get('work')}\n"
               f"⚠️ Issues: {data.get('issues')}")
    
    try:
        if data["photos"]:
            for p_id in data["photos"]:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=p_id, caption=caption)
        else:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=caption)
        
        if log_sheet:
            log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), data.get('name'), "", user_id, lang, data.get('city_en'), data.get('project_en'), data.get('odoo'), data.get('work'), "", data.get('issues'), "", "Sent"])
        
        msg = TEXTS[lang]["done"]
        if update.callback_query: await update.callback_query.message.edit_text(msg)
        else: await update.message.reply_text(msg)
    except Exception as e: print(f"Save Error: {e}")
    user_data[user_id] = {"photos": []}

async def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    print("🚀 Bot is LIVE...")
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        while True: await asyncio.sleep(1000)

if __name__ == '__main__':
    asyncio.run(main())
