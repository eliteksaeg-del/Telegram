import os
import json
import gspread
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
GROUP_CHAT_ID = "-5104938886" # ايدي الجروب اللي هتوصل عليه الصور

# جلب بيانات جوجل (للشيت فقط)
# بما إننا لسه بنستخدم الشيت، استخدم طريقة المفتاح المباشر عشان نتخطى الـ Signature Error
google_creds_json = os.environ.get("GSPREAD_JSON")

# ================= GOOGLE SHEETS SETUP =================
try:
    creds_info = json.loads(google_creds_json)
    if "private_key" in creds_info:
        creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")
    
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(creds_info, scopes=scope)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open("Daily Summary")
    PROJECTS_SHEET = spreadsheet.worksheet("Projects")
    LOG_SHEET = spreadsheet.sheet1
    print("✅ Connected to Google Sheets")
except Exception as e:
    print(f"❌ Sheets Connection Error: {e}")

# ================= MEMORY & PROJECTS =================
user_data = {}

def load_projects():
    try:
        rows = PROJECTS_SHEET.get_all_records()
        data = {}
        for r in rows:
            city_en = r["City_EN"]
            if city_en not in data:
                data[city_en] = {"en": r["City_EN"], "ar": r["City_AR"], "projects": []}
            data[city_en]["projects"].append({"en": r["Project_EN"], "ar": r["Project_AR"], "odoo": r["Odoo"]})
        return data
    except: return {}

projects = load_projects()

# ================= HELPERS =================
def to_en(text):
    try: return GoogleTranslator(source='auto', target='en').translate(text)
    except: return text

# ================= START & HANDLERS =================
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
        data["city"] = city
        data["step"] = "project"
        lang = "ar" if data["language"] == "عربي" else "en"
        buttons = [[InlineKeyboardButton(p[lang], callback_data=f"project|{city}|{p['en']}|{p['odoo']}")] for p in projects[city]["projects"]]
        await query.message.reply_text("Select Project / اختر المشروع:", reply_markup=InlineKeyboardMarkup(buttons))

    elif cb.startswith("project|"):
        _, city, project_en, odoo = cb.split("|")
        data.update({"city": city, "project": project_en, "odoo": odoo, "step": "work"})
        await query.message.reply_text("Write work done / اكتب الشغل بالتفصيل:")

    elif cb == "photo_more":
        data["step"] = "photo_upload"
        await query.message.reply_text("📸 Send photo / ارسل الصورة:")

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
        await update.message.reply_text("Select City / اختر المدينة:", reply_markup=InlineKeyboardMarkup(buttons))

    elif data.get("step") == "work":
        data.update({"work": update.message.text, "step": "issues"})
        await update.message.reply_text("Issues faced / المشاكل التي واجهتك:")

    elif data.get("step") == "issues":
        data.update({"issues": update.message.text, "step": "photo_choice"})
        keyboard = [[InlineKeyboardButton("Yes 📸", callback_data="photo_more"), InlineKeyboardButton("No ❌", callback_data="photo_done")]]
        await update.message.reply_text("Upload photos? / هل تريد رفع صور؟", reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.get("step") == "photo_upload":
        if update.message.photo:
            # هنخزن الـ file_id بتاع الصورة عشان نبعتها للجروب بعدين
            data["photos"].append(update.message.photo[-1].file_id)
            keyboard = [[InlineKeyboardButton("➕ Add more", callback_data="photo_more"), InlineKeyboardButton("✅ Finish", callback_data="photo_done")]]
            await update.message.reply_text("✅ Photo received / تم استلام الصورة", reply_markup=InlineKeyboardMarkup(keyboard))

# ================= SAVE & FORWARD =================
async def save_report(user_id, context, query):
    data = user_data[user_id]
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # 1. إرسال للجروب (الأرشيف)
    caption = (
        f"👷‍♂️ *Worker:* {data.get('name')}\n"
        f"🏗 *Project:* {data.get('project')}\n"
        f"📍 *City:* {data.get('city')}\n"
        f"📅 *Date:* {date_str}\n"
        f"📝 *Work:* {data.get('work')}\n"
        f"⚠️ *Issues:* {data.get('issues')}"
    )

    if data["photos"]:
        for photo_id in data["photos"]:
            await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=photo_id, caption=caption, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"🔔 *Report without photos:*\n{caption}", parse_mode="Markdown")

    # 2. الحفظ في جوجل شيت
    try:
        row = [date_str, data.get('name'), data.get('username'), user_id, data.get('language'), 
               data.get('city'), data.get('project'), data.get('odoo'), data.get('work'), 
               to_en(data.get('work')), data.get('issues'), to_en(data.get('issues')), "Sent to Telegram Group"]
        LOG_SHEET.append_row(row)
        await query.message.reply_text("✅ Saved & Archive updated!")
    except Exception as e:
        await query.message.reply_text(f"⚠️ Saved to Group but Sheet Error: {e}")

    user_data[user_id] = {"step": "language", "photos": []}

# ================= RUN =================
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))
app.run_polling()
