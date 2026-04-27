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

# ================= CONFIGURATION =================
TOKEN = "8468978393:AAGS2tu8Xj1O7bUOExicaWPGgFhcokLNLJo"
GROUP_CHAT_ID = "-5104938886"

# ================= DUMMY SERVER FOR RENDER =================
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"Serving at port {port}")
        httpd.serve_forever()

# ================= GOOGLE SHEETS SETUP =================
def connect_google():
    try:
        # هنا هنقرأ بيانات الـ JSON من متغير بيئة اسمه GSPREAD_JSON في ريندر
        raw_json = os.environ.get("GSPREAD_JSON")
        if not raw_json:
            # لو مش موجود في ريندر، هيحاول يقرأ من ملف محلي للتجربة
            creds = Credentials.from_service_account_file("telegram-bot.json", 
                    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        else:
            creds_info = json.loads(raw_json)
            creds = Credentials.from_service_account_info(creds_info, 
                    scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
        
        client = gspread.authorize(creds)
        spreadsheet = client.open("Daily Summary")
        return spreadsheet.worksheet("Projects"), spreadsheet.sheet1
    except Exception as e:
        print(f"❌ Connection Error: {e}")
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

# ================= TRANSLATION =================
def to_en(text):
    try: return GoogleTranslator(source='auto', target='en').translate(text)
    except: return text

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
        prompt = "Enter your name:" if data["language"] == "English" else "اكتب اسمك:"
        await query.message.reply_text(prompt)

    elif cb.startswith("city|"):
        city = cb.split("|")[1]
        data["city"], data["step"] = city, "project"
        lang = "ar" if data["language"] == "عربي" else "en"
        buttons = [[InlineKeyboardButton(p[lang], callback_data=f"project|{city}|{p['en']}|{p['odoo']}")] for p in projects[city]["projects"]]
        prompt = "Select Project:" if data["language"] == "English" else "اختر المشروع:"
        await query.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(buttons))

    elif cb.startswith("project|"):
        _, city, project_en, odoo = cb.split("|")
        data.update({"city": city, "project": project_en, "odoo": odoo, "step": "work", "photos": []})
        prompt = "Write work done:" if data["language"] == "English" else "اكتب الشغل المنجز:"
        await query.message.reply_text(prompt)

    elif cb == "photo_more":
        data["step"] = "photo_upload"
        await query.message.reply_text("📸 Send photo...")

    elif cb == "photo_done":
        await save_report(user_id, context, query)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data: return
    data = user_data[user_id]

    if data["step"] == "name":
        data.update({"name": update.message.text, "username": update.message.from_user.username, "step": "city"})
        lang = "ar" if data["language"] == "عربي" else "en"
        buttons = [[InlineKeyboardButton(projects[c][lang], callback_data=f"city|{c}")] for c in projects]
        prompt = "Select City:" if data["language"] == "English" else "اختر المدينة:"
        await update.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(buttons))

    elif data["step"] == "work":
        data.update({"work": update.message.text, "step": "issues"})
        prompt = "Issues faced?" if data["language"] == "English" else "المشاكل التي واجهتك:"
        await update.message.reply_text(prompt)

    elif data["step"] == "issues":
        data.update({"issues": update.message.text, "step": "photo_choice"})
        keyboard = [[InlineKeyboardButton("Yes 📸", callback_data="photo_more"), InlineKeyboardButton("No ❌", callback_data="photo_done")]]
        prompt = "Upload photos?" if data["language"] == "English" else "هل تريد رفع صور؟"
        await update.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data["step"] == "photo_upload" and update.message.photo:
        data["photos"].append(update.message.photo[-1].file_id)
        keyboard = [[InlineKeyboardButton("➕ Add more", callback_data="photo_more"), InlineKeyboardButton("✅ Done", callback_data="photo_done")]]
        await update.message.reply_text("✅ Received", reply_markup=InlineKeyboardMarkup(keyboard))

async def save_report(user_id, context, query):
    data = user_data[user_id]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cap = f"👷‍♂️ Worker: {data.get('name')}\n📍 City: {data.get('city')}\n🏗 Project: {data.get('project')}\n📝 Work: {data.get('work')}\n⚠️ Issues: {data.get('issues')}"
    
    try:
        if data["photos"]:
            for p_id in data["photos"]: await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=p_id, caption=cap)
        else: await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=cap)
        
        if LOG_SHEET:
            LOG_SHEET.append_row([date_str, data.get('name'), data.get('username'), user_id, data.get('language'), data.get('city'), data.get('project'), data.get('odoo'), data.get('work'), to_en(data.get('work')), data.get('issues'), to_en(data.get('issues'))])
        await query.message.reply_text("✅ Saved!")
    except Exception as e:
        await query.message.reply_text(f"Error: {e}")
    user_data[user_id] = {"step": "language", "photos": []}

# ================= RUN =================
if __name__ == "__main__":
    # تشغيل سيرفر وهمي في الخلفية عشان Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    app.run_polling()
