import os
import json
import logging
import threading
import http.server
import socketserver
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

# ================= 1. LOGGING & DUMMY SERVER =================
logging.basicConfig(level=logging.INFO)
print("🚀 BOT DEPLOYMENT STARTED...")

def run_dummy_server():
    """سيرفر وهمي عشان ريندر ما يقفلش البوت"""
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

# ================= 2. ENV VARIABLES =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
SHEET_NAME = os.environ.get("SHEET_NAME")

# ================= 3. GOOGLE SHEETS SETUP =================
def get_sheets_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    raw_creds = os.environ.get("GOOGLE_CREDENTIALS", "{}")
    creds_dict = json.loads(raw_creds)
    
    # معالجة مفتاح الخصوصية لحل مشكلة الـ JWT Signature
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    return client

try:
    client = get_sheets_client()
    spreadsheet = client.open(SHEET_NAME)
    PROJECTS_SHEET = spreadsheet.worksheet("Projects")
    LOG_SHEET = spreadsheet.sheet1
    print("✅ Connected to Google Sheets!")
except Exception as e:
    print(f"❌ Connection Error: {e}")

# ================= 4. LOAD DATA =================
# ================= 4. LOAD DATA (تعديل لضمان القراءة) =================
def load_all_projects():
    try:
        # بنعيد الاتصال للتأكد
        client = get_sheets_client()
        temp_sheet = client.open(SHEET_NAME).worksheet("Projects")
        rows = temp_sheet.get_all_records()
        data = {}
        for r in rows:
            city = str(r.get("City_EN", "")).strip()
            if city:
                if city not in data:
                    data[city] = {"en": city, "ar": str(r.get("City_AR", city)), "projects": []}
                data[city]["projects"].append({
                    "en": str(r.get("Project_EN", "")),
                    "ar": str(r.get("Project_AR", "")),
                    "odoo": str(r.get("Odoo", ""))
                })
        print(f"✅ Successfully loaded {len(data)} cities")
        return data
    except Exception as e:
        print(f"❌ Error inside load_all_projects: {e}")
        return {}

# ================= 6. UPDATED msg_handler =================
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data: return
    u = user_data[uid]
    
    if update.message.photo and u.get("step") == "photo":
        u["photos"].append(update.message.photo[-1].file_id)
        await update.message.reply_text("✅ Photo received. Send more or press Done.")
        return

    txt = update.message.text
    if not txt: return
    lang = u.get("lang", "English")

    if u.get("step") == "name":
        u.update({"name": txt, "step": "city"})
        
        # 🔥 أهم تعديل: بنحمل البيانات هنا عشان نضمن وجودها
        current_projects = load_all_projects()
        
        if not current_projects:
            await update.message.reply_text("❌ Error: No cities found in 'Projects' sheet. Please check column names.")
            return

        lang_idx = "ar" if lang == "Arabic" else "en"
        # إنشاء الأزرار
        btns = []
        for c_key, c_info in current_projects.items():
            btns.append([InlineKeyboardButton(c_info[lang_idx], callback_data=f"c|{c_key}")])
        
        await update.message.reply_text(
            T[lang]["c"], 
            reply_markup=InlineKeyboardMarkup(btns)
        )

    elif u.get("step") == "work":
        u.update({"work": txt, "step": "issue"})
        await update.message.reply_text(T[lang]["i"])

    elif u.get("step") == "issue":
        u.update({"issue": txt, "step": "photo"})
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text(T[lang]["ph"], reply_markup=InlineKeyboardMarkup(kb))

# ================= 5. TEXTS =================
T = {
    "English": {
        "n": "Enter your name:", "c": "Select City:", "p": "Select Project:",
        "w": "Write work done:", "i": "Write issues:", "ph": "Send photos or press Done:", "d": "Saved ✅"
    },
    "Arabic": {
        "n": "اكتب اسمك:", "c": "اختر المدينة:", "p": "اختر المشروع:",
        "w": "اكتب الشغل المنجز:", "i": "اكتب المشاكل:", "ph": "ارسل الصور أو اضغط إنهاء:", "d": "تم الحفظ ✅"
    }
}

# ================= 6. HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data[uid] = {"photos": []}
    kb = [[InlineKeyboardButton("English", callback_data="l_en"), InlineKeyboardButton("عربي", callback_data="l_ar")]]
    await update.message.reply_text("Language / اللغة:", reply_markup=InlineKeyboardMarkup(kb))

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid not in user_data: return
    u = user_data[uid]
    
    if query.data.startswith("l_"):
        u["lang"] = "Arabic" if query.data == "l_ar" else "English"
        u["step"] = "name"
        await query.message.reply_text(T[u["lang"]]["n"])

    elif query.data.startswith("c|"):
        city = query.data.split("|")[1]
        u.update({"city": city, "step": "proj"})
        lang_idx = "ar" if u["lang"] == "Arabic" else "en"
        btns = [[InlineKeyboardButton(p[lang_idx], callback_data=f"p|{city}|{p['en']}|{p['odoo']}")] for p in ALL_PROJECTS[city]["projects"]]
        await query.message.reply_text(T[u["lang"]]["p"], reply_markup=InlineKeyboardMarkup(btns))

    elif query.data.startswith("p|"):
        _, city, proj, odoo = query.data.split("|")
        u.update({"city": city, "project": proj, "odoo": odoo, "step": "work"})
        await query.message.reply_text(T[u["lang"]]["w"])

    elif query.data == "done":
        await finalize_report(uid, query, context)

async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data: return
    u = user_data[uid]
    
    # Handling Photos
    if update.message.photo and u.get("step") == "photo":
        u["photos"].append(update.message.photo[-1].file_id)
        await update.message.reply_text("✅ Photo received. Send more or press Done.")
        return

    # Handling Text
    txt = update.message.text
    if not txt: return
    lang = u.get("lang", "English")

    if u.get("step") == "name":
        u.update({"name": txt, "step": "city"})
        lang_idx = "ar" if lang == "Arabic" else "en"
        btns = [[InlineKeyboardButton(ALL_PROJECTS[c][lang_idx], callback_data=f"c|{c}")] for c in ALL_PROJECTS.keys()]
        await update.message.reply_text(T[lang]["c"], reply_markup=InlineKeyboardMarkup(btns))

    elif u.get("step") == "work":
        u.update({"work": txt, "step": "issue"})
        await update.message.reply_text(T[lang]["i"])

    elif u.get("step") == "issue":
        u.update({"issue": txt, "step": "photo"})
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text(T[lang]["ph"], reply_markup=InlineKeyboardMarkup(kb))

async def finalize_report(uid, query, context):
    u = user_data[uid]
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 1. Send Text Report to Group
    report = f"📋 *REPORT*\n👤 *Worker:* {u['name']}\n🏙 *City:* {u['city']}\n🏗 *Project:* {u['project']}\n🛠 *Work:* {u['work']}\n⚠️ *Issues:* {u['issue']}\n⏰ {date_now}"
    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=report, parse_mode="Markdown")
    
    # 2. Send Photos to Group
    for pid in u["photos"]:
        await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=pid)

    # 3. Save to Google Sheets
    try:
        LOG_SHEET.append_row([date_now, u['name'], u['city'], u['project'], u['work'], u['issue'], u['odoo']])
    except: pass

    await query.message.reply_text(T[u["lang"]]["d"])
    user_data[uid] = {"photos": []}

# ================= 7. RUN BOT =================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, msg_handler))
    print("🤖 Bot is polling...")
    app.run_polling()
