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
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
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
            if not city:
                continue
            if city not in data:
                data[city] = {
                    "en": city,
                    "ar": str(r.get("City_AR", city)),
                    "projects": []
                }
            data[city]["projects"].append({
                "en": str(r.get("Project_EN", "")),
                "ar": str(r.get("Project_AR", "")),
                "odoo": str(r.get("Odoo", ""))
            })
        print(f"✅ Loaded {len(data)} cities: {list(data.keys())}")
        return data
    except Exception as e:
        import traceback
        print(f"❌ load_all_projects error: {e}")
        print(traceback.format_exc())
        return {}

# ================= 5. IN-MEMORY STORE =================
# user_data: { uid: { lang, step, name, city, project, odoo, work, issue, photos: [] } }
user_data = {}

# ================= 6. TRANSLATIONS =================
T = {
    "English": {
        "n":  "Enter your name:",
        "c":  "Select City:",
        "p":  "Select Project:",
        "w":  "Write the work done (include building number):",
        "i":  "Write any issues you faced today:",
        "ph": "Send photos, then press ✅ Done when finished:",
        "d":  "Report saved ✅"
    },
    "Arabic": {
        "n":  "اكتب اسمك:",
        "c":  "اختر المدينة:",
        "p":  "اختر المشروع:",
        "w":  "اكتب الشغل المنجز مع رقم العمارة:",
        "i":  "اكتب المشاكل التي واجهتك اليوم:",
        "ph": "ارسل الصور ثم اضغط ✅ إنهاء عند الانتهاء:",
        "d":  "تم حفظ التقرير ✅"
    }
}

# ================= 7. /start =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_data[uid] = {"photos": [], "step": "lang"}
    kb = [[
        InlineKeyboardButton("English", callback_data="l_en"),
        InlineKeyboardButton("عربي",    callback_data="l_ar")
    ]]
    await update.message.reply_text(
        "Language / اللغة:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ================= 8. BUTTON HANDLER =================
async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if uid not in user_data:
        await query.message.reply_text("Please send /start first.")
        return

    u = user_data[uid]
    lang = u.get("lang", "English")
    d = query.data

    # --- Language selection ---
    if d.startswith("l_"):
        u["lang"] = "Arabic" if d == "l_ar" else "English"
        u["step"] = "name"
        await query.message.reply_text(T[u["lang"]]["n"])

    # --- City selected ---
    elif d.startswith("c|"):
        city_key = d.split("|", 1)[1]
        u["city"] = city_key
        u["step"] = "proj"

        projects = load_all_projects()
        if city_key not in projects:
            await query.message.reply_text("❌ City not found, please try /start again.")
            return

        lang_idx = "ar" if lang == "Arabic" else "en"
        btns = [
            [InlineKeyboardButton(
                p[lang_idx],
                callback_data=f"p|{city_key}|{p['en']}|{p['odoo']}"
            )]
            for p in projects[city_key]["projects"]
        ]
        await query.message.reply_text(
            T[lang]["p"],
            reply_markup=InlineKeyboardMarkup(btns)
        )

    # --- Project selected ---
    elif d.startswith("p|"):
        parts = d.split("|", 3)   # ["p", city, proj_en, odoo]
        _, city, proj, odoo = parts
        u.update({"city": city, "project": proj, "odoo": odoo, "step": "work"})
        await query.message.reply_text(T[lang]["w"])

    # --- Done (finalize) ---
    elif d == "done":
        await finalize_report(uid, query, context)

# ================= 9. TEXT / PHOTO HANDLER =================
async def msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid not in user_data:
        return

    u = user_data[uid]
    lang = u.get("lang", "English")

    # --- Photo ---
    if update.message.photo and u.get("step") == "photo":
        u["photos"].append(update.message.photo[-1].file_id)
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text(
            "✅ Photo received. Send more or press Done.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    txt = update.message.text
    if not txt:
        return

    # --- Name ---
    if u.get("step") == "name":
        u.update({"name": txt, "step": "city"})

        projects = load_all_projects()
        print(f"DEBUG msg_handler projects keys: {list(projects.keys())}")
        if not projects:
            await update.message.reply_text(
                "❌ No cities found in the 'Projects' sheet. Check column names (City_EN, City_AR, Project_EN, Project_AR, Odoo)."
            )
            return

        lang_idx = "ar" if lang == "Arabic" else "en"
        btns = [
            [InlineKeyboardButton(info[lang_idx], callback_data=f"c|{city_key}")]
            for city_key, info in projects.items()
        ]
        await update.message.reply_text(
            T[lang]["c"],
            reply_markup=InlineKeyboardMarkup(btns)
        )

    # --- Work done ---
    elif u.get("step") == "work":
        u.update({"work": txt, "step": "issue"})
        await update.message.reply_text(T[lang]["i"])

    # --- Issues ---
    elif u.get("step") == "issue":
        u.update({"issue": txt, "step": "photo"})
        kb = [[InlineKeyboardButton("✅ Done / إنهاء", callback_data="done")]]
        await update.message.reply_text(
            T[lang]["ph"],
            reply_markup=InlineKeyboardMarkup(kb)
        )

# ================= 10. FINALIZE & SAVE =================
async def finalize_report(uid, query, context):
    u = user_data.get(uid, {})
    lang = u.get("lang", "English")
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 1. Send text report to group
    report = (
        f"📋 *DAILY REPORT*\n"
        f"👤 *Worker:* {u.get('name', '-')}\n"
        f"🏙 *City:* {u.get('city', '-')}\n"
        f"🏗 *Project:* {u.get('project', '-')}\n"
        f"📌 *Odoo:* {u.get('odoo', '-')}\n"
        f"🛠 *Work Done:*\n{u.get('work', '-')}\n"
        f"⚠️ *Issues:*\n{u.get('issue', '-')}\n"
        f"⏰ {date_now}"
    )
    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=report,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"❌ Error sending report to group: {e}")

    # 2. Send photos to group
    photos = u.get("photos", [])
    if photos:
        try:
            media = []
            from telegram import InputMediaPhoto
            for i, pid in enumerate(photos):
                media.append(InputMediaPhoto(media=pid))
            # Send as album (up to 10 at once)
            for i in range(0, len(media), 10):
                await context.bot.send_media_group(
                    chat_id=GROUP_CHAT_ID,
                    media=media[i:i+10]
                )
        except Exception as e:
            print(f"❌ Error sending photos to group: {e}")

    # 3. Save to Google Sheets
    try:
        row = [
            date_now,
            u.get("name", ""),
            u.get("city", ""),
            u.get("project", ""),
            u.get("odoo", ""),
            u.get("work", ""),
            u.get("issue", ""),
            len(photos)   # number of photos attached
        ]
        LOG_SHEET.append_row(row)
        print(f"✅ Row saved to sheet")
    except Exception as e:
        print(f"❌ Error saving to sheet: {e}")

    await query.message.reply_text(T[lang]["d"])

    # Reset user state
    user_data[uid] = {"photos": [], "step": "lang"}

# ================= 11. RUN =================
if __name__ == "__main__":
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(btn_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, msg_handler))
    print("🤖 Bot is running...")
    app.run_polling()
