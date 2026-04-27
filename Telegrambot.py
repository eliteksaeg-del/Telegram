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

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO)

# ================= CONFIG =================
TOKEN = "8468978393:AAGS2tu8Xj1O7bUOExicaWPGgFhcokLNLJo"
GROUP_CHAT_ID = "-5104938886"

# ================= KEEP ALIVE =================
def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

# ================= GOOGLE SHEETS =================
def connect_google():
    try:
        raw_json = os.environ.get("GSPREAD_JSON", "").strip()
        if not raw_json:
            print("❌ GSPREAD_JSON is EMPTY")
            return None, None

        creds_info = json.loads(raw_json)

        if "private_key" in creds_info:
            creds_info["private_key"] = creds_info["private_key"].replace("\\n", "\n")

        scope = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]

        creds = Credentials.from_service_account_info(creds_info, scopes=scope)
        client = gspread.authorize(creds)

        spreadsheet = client.open("Daily Summary")
        projects_sheet = spreadsheet.worksheet("Projects")
        log_sheet = spreadsheet.sheet1

        print("✅ Google Sheets Connected Successfully")
        return projects_sheet, log_sheet

    except Exception as e:
        print("❌ Google Sheets ERROR:", str(e))
        return None, None


# ================= LOAD PROJECTS (FIXED + DEBUG) =================
def get_projects_data():
    proj_sheet, _ = connect_google()
    if not proj_sheet:
        print("❌ No sheet connection")
        return {}

    try:
        all_values = proj_sheet.get_all_values()

        print("📊 RAW SHEET DATA:")
        for row in all_values[:5]:
            print(row)

        if len(all_values) < 2:
            print("❌ Sheet has no data")
            return {}

        headers = [h.strip() for h in all_values[0]]
        rows = all_values[1:]

        print("📌 HEADERS:", headers)

        def find_col(name):
            for i, h in enumerate(headers):
                if name.lower() in h.lower():
                    return i
            return None

        c_en = find_col("city_en")
        c_ar = find_col("city_ar")
        p_en = find_col("project_en")
        p_ar = find_col("project_ar")
        odoo = find_col("odoo")

        print("📍 Column indexes:", c_en, c_ar, p_en, p_ar, odoo)

        if c_en is None or p_en is None:
            print("❌ Required columns missing in sheet")
            return {}

        data = {}

        for r in rows:
            if len(r) <= max(c_en, p_en):
                continue

            city = r[c_en].strip()
            if not city:
                continue

            if city not in data:
                data[city] = {
                    "en": city,
                    "ar": r[c_ar] if c_ar is not None and c_ar < len(r) else city,
                    "projects": []
                }

            data[city]["projects"].append({
                "en": r[p_en] if p_en < len(r) else "",
                "ar": r[p_ar] if p_ar is not None and p_ar < len(r) else "",
                "odoo": r[odoo] if odoo is not None and odoo < len(r) else ""
            })

        print("✅ Parsed projects:", data.keys())
        return data

    except Exception as e:
        print("❌ PARSE ERROR:", str(e))
        return {}

# ================= MEMORY =================
user_data = {}

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_data[user_id] = {"photos": []}

    keyboard = [
        [
            InlineKeyboardButton("English", callback_data="lang_en"),
            InlineKeyboardButton("عربي", callback_data="lang_ar")
        ]
    ]

    await update.message.reply_text(
        "Choose Language:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ================= MESSAGE =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    data = user_data.get(user_id)

    if not data:
        return

    # NAME STEP
    if data.get("step") == "name":
        data["name"] = update.message.text
        data["step"] = "city"

        projects = get_projects_data()

        if not projects:
            await update.message.reply_text("❌ No projects found (Check Sheet or logs)")
            return

        buttons = [
            [InlineKeyboardButton(v["en"], callback_data=f"city|{k}")]
            for k, v in projects.items()
        ]

        await update.message.reply_text(
            "Select City:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

# ================= BUTTONS =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = user_data.setdefault(user_id, {"photos": []})

    cb = query.data

    if cb.startswith("lang_"):
        data["step"] = "name"
        await query.message.edit_text("Enter your name:")

# ================= MAIN =================
def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO, handle_message))

    print("🚀 BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
