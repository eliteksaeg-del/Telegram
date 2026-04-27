import os
import json
import gspread
import asyncio
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
TOKEN = "8468978393:AAH3cp0fA9kltxy5a1kzdfj_NuJwTiVsamA"
GROUP_CHAT_ID = "-5104938886"  # ID الجروب الخاص بك

# ================= GOOGLE SHEETS SETUP =================
def connect_google():
    try:
        # جلب الـ JSON وتنظيفه من أي مسافات زائدة
        raw_json = os.environ.get("GSPREAD_JSON", "").strip()
        
        if not raw_json:
            print("❌ GSPREAD_JSON environment variable is missing or empty!")
            return None, None
            
        creds_info = json.loads(raw_json)
        
        # معالجة المفتاح الخاص لحل مشكلة التشفير
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

# ================= MEMORY & DATA =================
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
                data[city_en]["projects"].append({
                    "en": r["Project_EN"], 
                    "ar": r["Project_AR"], 
                    "odoo": r["Odoo"]
                })
        return data
    except Exception as e:
        print(f"❌ Error loading projects from sheet: {e}")
        return {}

projects = load_projects()

# ================= TRANSLATION HELPERS =================
def to_en(text):
    if not text: return ""
    try:
        return GoogleTranslator(source='auto', target='en').translate(text)
    except:
        return text

# ================= BOT HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"step": "language", "photos": []}
    
    keyboard = [
        [
            InlineKeyboardButton("English", callback_data="lang_en"),
            InlineKeyboardButton("عربي", callback_data="lang_ar")
        ]
    ]
    await update.message.reply_text(
        "Welcome! Choose Language / أهلاً بك! اختر اللغة:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

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
        data["city"] = city
        data["step"] = "project"
        lang = "ar" if data["language"] == "عربي" else "en"
        
        if city in projects:
            buttons = [[InlineKeyboardButton(p[lang], callback_data=f"project|{city}|{p['en']}|{p['odoo']}")] 
                       for p in projects[city]["projects"]]
            prompt = "Select Project:" if data["language"] == "English" else "اختار المشروع:"
            await query.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(buttons))

    elif cb.startswith("project|"):
        _, city, project_en, odoo = cb.split("|")
        data.update({"city": city, "project": project_en, "odoo": odoo, "step": "work"})
        prompt = "Write work done in details:" if data["language"] == "English" else "اكتب الشغل بالتفصيل:"
        await query.message.reply_text(prompt)

    elif cb == "photo_more":
        data["step"] = "photo_upload"
        prompt = "📸 Send photo now..." if data["language"] == "English" else "📸 ابعت الصورة دلوقتي..."
        await query.message.reply_text(prompt)

    elif cb == "photo_done":
        await save_report(user_id, context, query)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data: return
    data = user_data[user_id]

    if data.get("step") == "name":
        data.update({"name": update.message.text, "username": update.message.from_user.username, "step": "city"})
        lang = "ar" if data["language"] == "عربي" else "en"
        buttons = [[InlineKeyboardButton(projects[c][lang], callback_data=f"city|{c}")] for c in projects]
        prompt = "Select City:" if data["language"] == "English" else "اختار المدينة:"
        await update.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(buttons))

    elif data.get("step") == "work":
        data.update({"work": update.message.text, "step": "issues"})
        prompt = "Any issues today?" if data["language"] == "English" else "أي مشاكل واجهتك اليوم؟"
        await update.message.reply_text(prompt)

    elif data.get("step") == "issues":
        data.update({"issues": update.message.text, "step": "photo_choice"})
        keyboard = [[InlineKeyboardButton("Yes 📸", callback_data="photo_more"), 
                     InlineKeyboardButton("No ❌", callback_data="photo_done")]]
        prompt = "Want to upload photos?" if data["language"] == "English" else "حابب ترفع صور؟"
        await update.message.reply_text(prompt, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.get("step") == "photo_upload":
        if update.message.photo:
            data["photos"].append(update.message.photo[-1].file_id)
            keyboard = [[InlineKeyboardButton("➕ Add more", callback_data="photo_more"), 
                         InlineKeyboardButton("✅ Finish", callback_data="photo_done")]]
            await update.message.reply_text("✅ Photo received", reply_markup=InlineKeyboardMarkup(keyboard))

# ================= REPORT SUBMISSION =================
async def save_report(user_id, context, query):
    data = user_data[user_id]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # تحضير رسالة الجروب
    caption = (
        f"👷‍♂️ *Worker:* {data.get('name')}\n"
        f"🏗 *Project:* {data.get('project')}\n"
        f"📍 *City:* {data.get('city')}\n"
        f"📅 *Date:* {date_str}\n"
        f"📝 *Work:* {data.get('work')}\n"
        f"⚠️ *Issues:* {data.get('issues')}"
    )

    try:
        # 1. الإرسال لجروب التيليجرام
        if data["photos"]:
            for photo_id in data["photos"]:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo_id, caption=caption, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"🔔 *Report (No Photos):*\n{caption}", parse_mode="Markdown")

        # 2. الحفظ في Google Sheets
        if LOG_SHEET:
            row = [
                date_str, data.get('name'), data.get('username'), user_id, 
                data.get('language'), data.get('city'), data.get('project'), 
                data.get('odoo'), data.get('work'), to_en(data.get('work')), 
                data.get('issues'), to_en(data.get('issues')), "Sent to Group"
            ]
            LOG_SHEET.append_row(row)
            
        await query.message.reply_text("✅ Success! Data saved and shared with management.")
    except Exception as e:
        await query.message.reply_text(f"⚠️ Partial Success: Report sent to group but Sheets had an error: {e}")

    # إعادة تعيين بيانات المستخدم للتقرير القادم
    user_data[user_id] = {"step": "language", "photos": []}

# ================= MAIN RUN =================
if __name__ == '__main__':
    print("🤖 Bot is starting up...")
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
    
    app.run_polling()
