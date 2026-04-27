import os
import json
import logging
import threading
import http.server
import socketserver
from datetime import datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
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

# ================= 1. CONFIGURATION =================
# حطينا الرقم هنا مباشرة عشان نخلص من وجع الدماغ بتاع الريندر
GROUP_CHAT_ID = -1003958795497  # تم التأكد من الرقم (يجب أن يبدأ بـ -100)

logging.basicConfig(level=logging.INFO)

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

# ================= 2. ENV VARIABLES =================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SHEET_NAME = os.environ.get("SHEET_NAME")

# ================= 3. GOOGLE SHEETS SETUP =================
def get_sheets_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    raw_creds = os.environ.get("GOOGLE_CREDENTIALS", "{}")
    creds_dict = json.loads(raw_creds)
    if "private_key" in creds_dict:
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    return gspread.authorize(creds)

try:
    _client = get_sheets_client()
    _spreadsheet = _client.open(SHEET_NAME)
    LOG_SHEET = _spreadsheet.sheet1
    print("✅ Connected to Google Sheets!")
except Exception as e:
    print(f"❌ Connection Error: {e}")
    LOG_SHEET = None

# ================= 4. LOAD DATA =================
def load_all_projects():
    try:
        c = get_sheets_client()
        sheet = c.open(SHEET_NAME).worksheet("Projects")
        rows = sheet.get_all_records()
        data = {}
        for r in rows:
            city = str(r.get("City_EN", "")).strip()
            if not city: continue
            if city not in data:
                data[city] = {"en": city, "ar": str(r.get("City_AR", city)), "projects": []}
            data[city]["projects"].append({
                "en": str(r.get("Project_EN", "")).strip(),
                "ar": str(r.get("Project_AR", "")).strip(),
                "odoo": str(r.get("Odoo", "")).strip()
            })
        return data
    except: return {}

user_data = {}

# ================= 5. TRANSLATIONS =================
T = {
    "English": {
        "n": "Enter name:", "c": "Select City:", "p": "Select Project:",
        "w": "Work done:", "i": "Issues:", "ph": "Send photos now, then press ✅ Done:", "d": "Saved ✅"
    },
    "Arabic": {
        "n": "اكتب اسمك:", "c": "اختر المدينة:", "p": "اختر المشروع:",
        "w": "اكتب الشغل:", "i": "اكتب المشاكل:", "ph": "ارسل الصور الآن، ثم اضغط ✅ إنهاء:", "d": "تم الحفظ ✅"
    }
}

# ================= 6. HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data[uid] = {
        "photos": [], 
        "step": "lang", 
        "tg_id": uid, 
        "username": update.effective_user.username or "None"
    }
    kb = [[InlineKeyboardButton("English", callback_data="l_en"), InlineKeyboardButton("عربي", callback_data="l_ar")]]
    await update.message.reply_text("Language / اللغة:", reply_markup=InlineKeyboardMarkup(kb))

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid not in user_data: return
    u, d = user_data[uid], query.data
    lang = u.get("lang", "English")

    if d.startswith("l_"):
        u["lang"] = "Arabic" if d == "l_ar" else "English"
        u["step"] = "name"
        await query.message.reply_text(T[u["lang"]]["n"])

    elif d.startswith("c|"):
        city = d.split("|")[1]
        u.update({"city": city, "step": "proj"})
        all_p = load_all_projects()
        projs = all_p.get(city, {}).get("projects", [])
        l_idx = "ar" if lang == "Arabic" else "en"
        btns = [[InlineKeyboardButton(p[l_idx], callback_data=f"p|{city}|{p['en']}|{p['odoo']}")] for p in projs]
        await query.message.reply_text(T[lang]["p"], reply_markup=InlineKeyboardMarkup(btns))

    elif d.startswith("p|"):
        _, city, proj, odoo = d.split("|", 3)
        u.update({"city": city, "project": proj, "odoo": odoo, "step": "work"})
        await query.message.reply_text(T[lang]["w"])

    elif d == "done":
        if u.get("step") == "saving": return
        u["step"] = "saving"
        await finalize_report(uid, query, context)

async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data: return
    u = user_data[uid]
    lang = u.get("lang", "English")

    if update.message.photo and u.get("step") == "photo":
        u["photos"].append(update.message.photo[-1].file_id)
        return

    txt = update.message.text
    if not txt: return

    if u["step"] == "name":
        u.update({"name": txt, "step": "city"})
        cities = load_all_projects()
        l_idx = "ar" if lang == "Arabic" else "en"
        btns = [[InlineKeyboardButton(cities[c][l_idx], callback_data=f"c|{c}")] for c in cities]
        await update.message.reply_text(T[lang]["c"], reply_markup=InlineKeyboardMarkup(btns))

    elif u["step"] == "work":
        u.update({"work": txt, "step": "issue"})
        await update.message.reply_text(T[lang]["i"])

    elif u["step"] == "issue":
        u.update({"issue": txt, "step": "photo"})
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text(T[lang]["ph"], reply_markup=InlineKeyboardMarkup(kb))

# ================= 7. FINALIZE & SAVE =================
async def finalize_report(uid, query, context):
    u = user_data[uid]
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

    report = (
        f"📋 *DAILY REPORT*\n"
        f"👤 *Worker:* {u.get('name')}\n"
        f"🆔 *ID:* `{u.get('tg_id')}` | @{u.get('username')}\n"
        f"🏙 *City:* {u.get('city')}\n"
        f"🏗 *Project:* {u.get('project')}\n"
        f"📌 *Odoo:* {u.get('odoo')}\n"
        f"📝 *Work:* {u.get('work')}\n"
        f"⚠️ *Issues:* {u.get('issue')}\n"
        f"⏰ {date_now}"
    )

    try:
        # 1. إرسال النص (استخدام الرقم المباشر)
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=report, parse_mode="Markdown")
        
        # 2. إرسال الصور كألبوم (فقط لو فيه صور)
        if u.get("photos"):
            for i in range(0, len(u["photos"]), 10):
                batch = [InputMediaPhoto(f_id) for f_id in u["photos"][i:i+10]]
                await context.bot.send_media_group(chat_id=GROUP_CHAT_ID, media=batch)
        
        # 3. حفظ في جوجل شيت
        if LOG_SHEET:
            LOG_SHEET.append_row([
                date_now, u.get("name"), u.get("tg_id"), f"@{u.get('username')}",
                u.get("city"), u.get("project"), u.get("odoo"), u.get("work"), u.get("issue"), len(u.get("photos", []))
            ])
            
        await query.message.reply_text(T[u.get("lang", "English")]["d"])
    except Exception as e:
        logging.error(f"Finalize Error: {e}")
        # إذا فشل بسبب الـ ID، هيقولك السبب هنا في التيليجرام
        await query.message.reply_text(f"❌ Error: {e}")

    # ريست
    user_data[uid] = {"photos": [], "step": "lang", "tg_id": uid, "username": u.get('username')}

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, msg_handler))
    app.run_polling()
