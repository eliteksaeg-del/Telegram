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
from deep_translator import GoogleTranslator
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)

# 1. إعدادات التسجيل لمراقبة الأخطاء
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# ================= CONFIGURATION =================
TOKEN = "8468978393:AAGS2tu8Xj1O7bUOExicaWPGgFhcokLNLJo"
GROUP_CHAT_ID = "-5104938886"

# ================= 2. DUMMY SERVER FOR RENDER =================
# هذا الجزء يمنع Render من إغلاق البوت بسبب عدم وجود Port مفتوح
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    try:
        with socketserver.TCPServer(("", port), handler) as httpd:
            print(f"🌍 Dummy server active on port {port}")
            httpd.serve_forever()
    except Exception as e:
        print(f"ℹ️ Dummy Server Info: {e}")

# ================= GOOGLE SHEETS SETUP =================
def connect_google():
    try:
        raw_json = os.environ.get("GSPREAD_JSON", "").strip()
        if not raw_json:
            print("⚠️ GSPREAD_JSON is missing!")
            return None, None
            
        creds_info = json.loads(raw_json)
        if "private_key" in creds_info:
            creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
        
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open("Daily Summary")
        return spreadsheet.worksheet("Projects"), spreadsheet.sheet1
    except Exception as e:
        print(f"❌ Google Connection Error: {e}")
        return None, None

PROJECTS_SHEET, LOG_SHEET = connect_google()
user_data = {}

def load_projects():
    if not PROJECTS_SHEET: return {}
    try:
        rows = PROJECTS_SHEET.get_all_records()
        data = {}
        for r in rows:
            city_en = r.get("City_EN")
            if city_en:
                if city_en not in data:
                    data[city_en] = {"en": r["City_EN"], "ar": r["City_AR"], "projects": []}
                data[city_en]["projects"].append({"en": r["Project_EN"], "ar": r["Project_AR"], "odoo": r["Odoo"]})
        return data
    except: return {}

projects = load_projects()

# ================= HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"step": "language", "photos": []}
    keyboard = [[InlineKeyboardButton("English", callback_data="lang_en"), InlineKeyboardButton("عربي", callback_data="lang_ar")]]
    await update.message.reply_text("Choose Language / اختر اللغة:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = user_data.setdefault(user_id, {"photos": []})
    cb = query.data

    if cb.startswith("lang_"):
        data["language"] = "عربي" if cb == "lang_ar" else "English"
        data["step"] = "name"
        await query.message.reply_text("Enter your name / اكتب اسمك:")
    elif cb.startswith("city|"):
        city = cb.split("|")[1]
        data["city"], data["step"] = city, "project"
        lang = "ar" if data["language"] == "عربي" else "en"
        if city in projects:
            buttons = [[InlineKeyboardButton(p[lang], callback_data=f"project|{city}|{p['en']}|{p['odoo']}")] for p in projects[city]["projects"]]
            await query.message.reply_text("Select Project / اختر المشروع:", reply_markup=InlineKeyboardMarkup(buttons))
    elif cb.startswith("project|"):
        _, city, project_en, odoo = cb.split("|")
        data.update({"city": city, "project": project_en, "odoo": odoo, "step": "work"})
        await query.message.reply_text("Work done / الشغل المنجز:")
    elif cb == "photo_more":
        data["step"] = "photo_upload"
        await query.message.reply_text("📸 Send photo / ابعت الصورة:")
    elif cb == "photo_done":
        await save_report(user_id, context, query)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data: return
    data = user_data[user_id]

    if data.get("step") == "name":
        data.update({"name": update.message.text, "username": update.message.from_user.username, "step": "city"})
        lang = "ar" if data["language"] == "عربي" else "en"
        
        # التأكد من تحميل المشاريع
        global projects
        if not projects: projects = load_projects()
        
        if not projects:
            await update.message.reply_text("⚠️ No projects found in Sheets!")
            return

        buttons = [[InlineKeyboardButton(projects[c][lang], callback_data=f"city|{c}")] for c in projects]
        await update.message.reply_text("Select City / اختر المدينة:", reply_markup=InlineKeyboardMarkup(buttons))
    elif data.get("step") == "work":
        data.update({"work": update.message.text, "step": "issues"})
        await update.message.reply_text("Issues / المشاكل:")
    elif data.get("step") == "issues":
        data.update({"issues": update.message.text, "step": "photo_choice"})
        keyboard = [[InlineKeyboardButton("Yes 📸", callback_data="photo_more"), InlineKeyboardButton("No ❌", callback_data="photo_done")]]
        await update.message.reply_text("Upload photos? / ترفع صور؟", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data.get("step") == "photo_upload" and update.message.photo:
        data["photos"].append(update.message.photo[-1].file_id)
        keyboard = [[InlineKeyboardButton("➕ More", callback_data="photo_more"), InlineKeyboardButton("✅ Finish", callback_data="photo_done")]]
        await update.message.reply_text("✅ Photo received", reply_markup=InlineKeyboardMarkup(keyboard))

async def save_report(user_id, context, query):
    data = user_data[user_id]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cap = f"👷‍♂️ Worker: {data.get('name')}\n🏗 Project: {data.get('project')}\n📍 City: {data.get('city')}\n📝 Work: {data.get('work')}\n⚠️ Issues: {data.get('issues')}"
    
    try:
        if data["photos"]:
            for p_id in data["photos"]: await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=p_id, caption=cap)
        else: await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=cap)
        
        if LOG_SHEET:
            LOG_SHEET.append_row([date_str, data.get('name'), data.get('username'), user_id, data.get('language'), data.get('city'), data.get('project'), data.get('odoo'), data.get('work'), "", data.get('issues'), "", "Sent"])
        await query.message.reply_text("✅ Saved successfully / تم الحفظ بنجاح")
    except Exception as e:
        await query.message.reply_text(f"⚠️ Error during save: {e}")
    user_data[user_id] = {"step": "language", "photos": []}

# ================= RUN =================
async def main():
    # 3. تشغيل السيرفر الوهمي في Thread مستقل
    threading.Thread(target=run_dummy_server, daemon=True).start()

    # بناء التطبيق
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    print("🚀 Bot is starting with Dummy Server...")
    
    # تشغيل البوت يدوياً لتجنب مشاكل الإصدارات على ريندر
    async with app:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        while True:
            await asyncio.sleep(1000)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
