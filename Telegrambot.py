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

# ================= 1. LOGGING & DUMMY SERVER =================
logging.basicConfig(level=logging.INFO)
print("🚀 BOT DEPLOYMENT STARTED...")

def run_dummy_server():
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

# ================= 4. LOAD PROJECTS =================
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
                "en": str(r.get("Project_EN", "")),
                "ar": str(r.get("Project_AR", "")),
                "odoo": str(r.get("Odoo", ""))
            })
        return data
    except Exception as e:
        print(f"❌ load_all_projects error: {e}")
        return {}

# ================= 5. IN-MEMORY STORE =================
user_data = {}

# ================= 6. TRANSLATIONS =================
T = {
    "English": {
        "n": "Enter your name:", "c": "Select City:", "p": "Select Project:",
        "w": "Write work done (include building number):", "i": "Write issues:",
        "ph": "Send photos, then press ✅ Done:", "d": "Report saved ✅"
    },
    "Arabic": {
        "n": "اكتب اسمك:", "c": "اختر المدينة:", "p": "اختر المشروع:",
        "w": "اكتب الشغل مع رقم العمارة:", "i": "اكتب المشاكل:",
        "ph": "ارسل الصور ثم اضغط ✅ إنهاء:", "d": "تم حفظ التقرير ✅"
    }
}

# ================= 7. HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    # حفظ الداتا الأساسية من التيليجرام
    user_data[uid] = {
        "photos": [], 
        "step": "lang",
        "tg_id": uid,
        "username": update.effective_user.username or "No Username"
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
        city_key = d.split("|", 1)[1]
        u.update({"city": city_key, "step": "proj"})
        projects = load_all_projects()
        lang_idx = "ar" if lang == "Arabic" else "en"
        btns = [[InlineKeyboardButton(p[lang_idx], callback_data=f"p|{city_key}|{p['en']}|{p['odoo']}")] for p in projects.get(city_key, {}).get("projects", [])]
        await query.message.reply_text(T[lang]["p"], reply_markup=InlineKeyboardMarkup(btns))

    elif d.startswith("p|"):
        _, city, proj, odoo = d.split("|", 3)
        u.update({"city": city, "project": proj, "odoo": odoo, "step": "work"})
        await query.message.reply_text(T[lang]["w"])

    elif d == "done":
        await finalize_report(uid, query, context)

async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in user_data: return
    u = user_data[uid]
    lang = u.get("lang", "English")

    if update.message.photo and u.get("step") == "photo":
        u["photos"].append(update.message.photo[-1].file_id)
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text("✅ Received. Send more or press Done.", reply_markup=InlineKeyboardMarkup(kb))
        return

    txt = update.message.text
    if not txt: return

    if u.get("step") == "name":
        u.update({"name": txt, "step": "city"})
        projects = load_all_projects()
        lang_idx = "ar" if lang == "Arabic" else "en"
        btns = [[InlineKeyboardButton(info[lang_idx], callback_data=f"c|{ck}")] for ck, info in projects.items()]
        await update.message.reply_text(T[lang]["c"], reply_markup=InlineKeyboardMarkup(btns))

    elif u.get("step") == "work":
        u.update({"work": txt, "step": "issue"})
        await update.message.reply_text(T[lang]["i"])

    elif u.get("step") == "issue":
        u.update({"issue": txt, "step": "photo"})
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text(T[lang]["ph"], reply_markup=InlineKeyboardMarkup(kb))

# ================= 10. FINALIZE & SAVE =================
async def finalize_report(uid, query, context):
    u = user_data.get(uid, {})
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # التقرير النصي شامل بيانات التيليجرام
    report = (
        f"📋 *DAILY REPORT*\n"
        f"👤 *Worker:* {u.get('name', '-')}\n"
        f"🆔 *TG ID:* `{u.get('tg_id', '-')}`\n"
        f"🔗 *User:* @{u.get('username', 'None')}\n"
        f"🏙 *City:* {u.get('city', '-')}\n"
        f"🏗 *Project:* {u.get('project', '-')}\n"
        f"📌 *Odoo:* {u.get('odoo', '-')}\n"
        f"🛠 *Work Done:* {u.get('work', '-')}\n"
        f"⚠️ *Issues:* {u.get('issue', '-')}\n"
        f"⏰ {date_now}"
    )

    # 1. إرسال التقرير النصي
    try:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=report, parse_mode="Markdown")
    except Exception as e:
        print(f"❌ Error sending text: {e}")

    # 2. إرسال الصور (بأفضل طريقة: Media Group)
    photos = u.get("photos", [])
    if photos:
        try:
            # تقسيم الصور لمجموعات (كل مجموعة 10 صور كحد أقصى)
            for i in range(0, len(photos), 10):
                batch = photos[i:i+10]
                media = [InputMediaPhoto(m) for m in batch]
                await context.bot.send_media_group(chat_id=GROUP_CHAT_ID, media=media)
        except Exception as e:
            print(f"❌ MediaGroup failed, trying individual: {e}")
            for pid in photos:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=pid)

    # 3. حفظ في جوجل شيت (مع زيادة أعمدة الـ ID والـ Username)
    try:
        row = [
            date_now, u.get("name"), u.get("tg_id"), f"@{u.get('username')}",
            u.get("city"), u.get("project"), u.get("odoo"), u.get("work"), u.get("issue"), len(photos)
        ]
        LOG_SHEET.append_row(row)
    except Exception as e:
        print(f"❌ Sheet Error: {e}")

    await query.message.reply_text(T[u.get("lang", "English")]["d"])
    user_data[uid] = {"photos": [], "step": "lang"}

if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, msg_handler))
    app.run_polling()
