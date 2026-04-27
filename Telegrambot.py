import logging
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ========== CONFIG ==========
BOT_TOKEN = "8468978393:AAGS2tu8Xj1O7bUOExicaWPGgFhcokLNLJo"
SHEET_NAME = "Daily Summary"
WORKSHEET_NAME = "Sheet1"

# ========== GOOGLE SHEET SETUP ==========
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name(
    "credentials.json", scope
)

client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME).worksheet(WORKSHEET_NAME)

# ========== LOGGING ==========
logging.basicConfig(level=logging.INFO)

# ========== USER STATE ==========
user_state = {}  # {user_id: {"lang": "", "city": ""}}

# ========== START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["Arabic", "English"]]
    await update.message.reply_text(
        "Choose language / اختار اللغة:",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
    )

# ========== HANDLE TEXT ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if user_id not in user_state:
        user_state[user_id] = {}

    state = user_state[user_id]

    # -------- LANGUAGE --------
    if text in ["Arabic", "English"]:
        state["lang"] = text

        keyboard = [["Jeddah", "Riyadh", "Makkah"]]
        await update.message.reply_text(
            "Choose city:" if text == "English" else "اختار المدينة:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True)
        )
        return

    # -------- CITY --------
    if text in ["Jeddah", "Riyadh", "Makkah"]:
        state["city"] = text
        await send_projects(update, state)
        return

    await update.message.reply_text("Use /start to begin")

# ========== FETCH PROJECTS ==========
async def send_projects(update: Update, state):
    city = state.get("city")
    lang = state.get("lang", "English")

    records = sheet.get_all_records()

    filtered = [
        r for r in records if str(r.get("city", "")).lower() == city.lower()
    ]

    if not filtered:
        await update.message.reply_text("No projects found ❌")
        return

    msg = "📌 Projects:\n\n"

    for r in filtered:
        if lang == "Arabic":
            desc = r.get("description_ar", "")
        else:
            desc = r.get("description_en", "")

        msg += f"🔹 {r.get('project_name')}\n{desc}\n\n"

    await update.message.reply_text(msg)

# ========== MAIN ==========
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
