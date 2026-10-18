import os
import logging
from threading import Thread
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CommandHandler
from pymongo import MongoClient

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Initialize Flask web server for Render Web Service
app = Flask(__name__)

@app.route('/')
def index():
    return "Preset Anime Auto-Poster is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Fetch credentials from Render Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MAIN_CHANNEL_ID = int(os.getenv("MAIN_CHANNEL_ID", "0"))

# Initialize MongoDB Connection
client = MongoClient(MONGO_URI)
db = client["anime_bot_database"]
config_collection = db["channel_presets"]
uploads_collection = db["anime_uploads"]

# 1. Admin Command to set/predefine a channel's poster and watch link
async def set_anime_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message and message.photo and message.caption and message.caption.startswith("/setanime"):
        parts = message.caption.replace("/setanime", "").split("|")
        
        if len(parts) < 3:
            await message.reply_text(
                "❌ **Invalid Format!**\n\n"
                "Use this format with your poster image:\n"
                "`/setanime Channel_ID | Anime Title | Watch_Link`",
                parse_mode="Markdown"
            )
            return

        channel_id = int(parts[0].strip())
        title = parts[1].strip()
        watch_link = parts[2].strip()
        poster_file_id = message.photo[-1].file_id

        # Save or update the preset in MongoDB
        config_collection.update_one(
            {"channel_id": channel_id},
            {
                "$set": {
                    "title": title,
                    "watch_link": watch_link,
                    "poster_file_id": poster_file_id
                }
            },
            upsert=True
        )

        await message.reply_text(f"✅ Preset saved successfully for Channel ID: `{channel_id}`!", parse_mode="Markdown")

# 2. Auto-detect new uploads in any registered anime source channel
async def handle_anime_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if message:
        chat_id = message.chat.id
        message_id = message.message_id

        # Look up if this channel has a predefined preset in MongoDB
        preset = config_collection.find_one({"channel_id": chat_id})
        if not preset:
            return  # Ignore channels that haven't been configured yet

        # Prevent duplicate posts for the same file/message ID
        if uploads_collection.find_one({"chat_id": chat_id, "message_id": message_id}):
            return

        # Save record to MongoDB
        uploads_collection.insert_one({"chat_id": chat_id, "message_id": message_id})

        # Grab preset details
        title = preset["title"]
        watch_link = preset["watch_link"]
        poster_file_id = preset["poster_file_id"]

        # Create inline watch button
        keyboard = [[InlineKeyboardButton("▶️ Watch Now", url=watch_link)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Format announcement caption
        broadcast_caption = (
            f"🔥 **NEW EPISODE ADDED!** 🔥\n\n"
            f"📌 **{title}**\n\n"
            f"👉 Click below to watch!"
        )

        # Broadcast preset poster + button to Main Channel
        await context.bot.send_photo(
            chat_id=MAIN_CHANNEL_ID,
            photo=poster_file_id,
            caption=broadcast_caption,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

        logging.info(f"Auto-posted update for {title} from channel {chat_id}")

def main():
    if not BOT_TOKEN or not MONGO_URI or not MAIN_CHANNEL_ID:
        raise ValueError("Missing critical environment variables.")

    # Start Flask web server in a background thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram Bot application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handler to set presets in private chat
    application.add_handler(MessageHandler(filters.PHOTO & filters.CaptionRegex(r"^/setanime"), set_anime_preset))
    
    # Handler to listen to channel uploads
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL, handle_anime_upload))

    print("Preset Auto-Poster Bot is running...")
    application.run_polling()

if __name__ == '__main__':
    main()
