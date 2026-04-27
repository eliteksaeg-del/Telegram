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
        if not raw_json: return None, None
        creds_info = json.loads(raw_json)
        if "private_key" in creds_info:
            creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open("Daily Summary")
        return spreadsheet.worksheet("Projects"), spreadsheet.sheet1
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None, None

def get_projects_data():
    proj_sheet, _ = connect_google()
    if not proj_sheet: return {}
    try:
        # قراءة كل الصفوف كقائمة لتجنب مشاكل العناوين (Headers)
        all_values = proj_sheet.get_all_values()
        if len(all_values) < 2: return {} # الشيت فاضي
        
        headers = all_values[0] # أول صف (العناوين)
        rows = all_values[1:]   # البيانات
        
        # تحديد أماكن الأعمدة ديناميكياً
        def get_idx(name):
            try: return next(i for i, h in enumerate(headers) if name.lower() in h.lower())
            except: return None

        c_en_idx = get_idx("City_EN")
        c_ar_idx = get_idx("City_AR")
        p_en_idx = get_idx("Project_EN")
        p_ar_idx = get_idx("Project_AR")
        odoo_idx = get_idx("Odoo")

        data = {}
        for r in rows:
            # التأكد من وجود بيانات في الصف
            if len(r) <= max(c_en_idx or 0, p_en_idx or 0): continue
            
            city_en = r[c_en_idx].strip() if c_en_idx is not None else ""
            if not city_en: continue

            if city_en not in data:
                data[city_en] = {
                    "en": city_en,
                    "ar": r[c_ar_idx].strip() if c_ar_idx is not None else city_en,
                    "projects": []
                }
            
            data[city_en]["projects"].append({
                "en": r[p_en_idx].strip() if p_en_idx is not None else "",
                "ar": r[p_ar_idx].strip() if p_ar_idx is not None else "",
                "odoo": r[odoo_idx].strip() if odoo_idx is not None else ""
            })
        return data
    except Exception as e:
        print(f"❌ Error parsing sheets: {e}")
        return {}

# ================= TEXTS =================
TEXTS = {
    "en": {
        "ask_name": "Please enter your name:",
        "ask_city": "Select City:",
        "ask_project": "Select Project:",
        "ask_work": "What work was done today?",
        "ask_issues": "Any issues faced?",
        "ask_photo": "Do you want to upload a photo?",
        "send_photo": "Please send the photo:",
        "done": "✅ Report saved!",
        "no_proj": "⚠️ No projects found!",
        "btn_yes": "Yes 📸", "btn_no": "No ❌", "btn_finish": "✅ Finish", "btn_more": "➕ More"
    },
    "ar": {
        "ask_name": "اكتب اسمك:",
        "ask_city": "اختر المدينة:",
        "ask_project": "اختر المشروع:",
        "ask_work": "ما هو الشغل المنجز؟",
        "ask_issues": "هل توجد مشاكل؟",
        "ask_photo": "هل تريد رفع صورة؟",
        "send_photo": "ارسل الصورة الآن:",
        "done": "✅ تم الحفظ!",
        "no_proj": "⚠️ لا توجد مشاريع!",
        "btn_yes": "نعم 📸", "btn_no": "لا ❌", "btn_finish": "✅ إنهاء", "btn_more": "➕ صورة أخرى"
    }
}

user_data = {}

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
    data = user_data.setdefault(user_id, {"photos": []})
    cb = query.data
    lang = data.get("lang", "en")

    if cb.startswith("lang_"):
        data["lang"] = cb.split("_")[1]
        data["step"] = "name"
        await query.message.edit_text(TEXTS[data["lang"]]["ask_name"])

    elif cb.startswith("city|"):
        city_key = cb.split("|")[1]
        data["city_key"] = city_key
        data["step"] = "project"
        all_projs = get_projects_data()
        
        btns = []
        for p in all_projs.get(city_key, {}).get("projects", []):
            label = p[data["lang"]] if p[data["lang"]] else p["en"]
            btns.append([InlineKeyboardButton(label, callback_data=f"proj|{p['en']}|{p['odoo']}")])
        
        await query.message.edit_text(TEXTS[data["lang"]]["ask_project"], reply_markup=InlineKeyboardMarkup(btns))

    elif cb.startswith("proj|"):
        _, p_en, odoo = cb.split("|")
        data.update({"project_en": p_en, "odoo": odoo, "step": "work"})
        await query.message.edit_text(TEXTS[data["lang"]]["ask_work"])

    elif cb == "photo_yes":
        data["step"] = "uploading"
        await query.message.edit_text(TEXTS[data["lang"]]["send_photo"])

    elif cb in ["photo_no", "finish"]:
        await save_report(update, context, data)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id)
    if not data: return
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
    
    caption = f"👷‍♂️ Worker: {data.get('name')}\n📍 City: {data.get('city_key')}\n🏗 Project: {data.get('project_en')}\n📝 Work: {data.get('work')}\n⚠️ Issues: {data.get('issues')}"
    
    try:
        if data.get("photos"):
            for p_id in data["photos"]:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=p_id, caption=caption)
        else:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=caption)
        
        if log_sheet:
            log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), data.get('name'), "", user_id, lang, data.get('city_key'), data.get('project_en'), data.get('odoo'), data.get('work'), "", data.get('issues'), "", "Sent"])
        
        msg = TEXTS[lang]["done"]
        if update.callback_query: await update.callback_query.message.edit_text(msg)
        else: await update.message.reply_text(msg)
    except Exception as e: print(f"Error: {e}")
    user_data[user_id] = {"photos": []}

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
