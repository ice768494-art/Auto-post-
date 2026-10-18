import os
import logging
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
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
    return "Anime Auto-Poster Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Fetch credentials from Render Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MAIN_CHANNEL_ID = int(os.getenv("MAIN_CHANNEL_ID", "0"))

raw_source_ids = os.getenv("SOURCE_CHANNEL_IDS", "")
SOURCE_CHANNEL_IDS = [int(cid.strip()) for cid in raw_source_ids.split(",") if cid.strip()]

# Initialize MongoDB Connection
client = MongoClient(MONGO_URI)
db = client["anime_bot_database"]
anime_collection = db["anime_uploads"]

async def handle_new_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if message and message.chat.id in SOURCE_CHANNEL_IDS:
        chat_id = message.chat.id
        message_id = message.message_id
        
        # Check MongoDB to prevent duplicate posts
        if anime_collection.find_one({"chat_id": chat_id, "message_id": message_id}):
            return

        # Save record to MongoDB
        anime_collection.insert_one({
            "chat_id": chat_id,
            "message_id": message_id
        })

        # Get original caption or text if any exist
        original_caption = message.caption or message.text or "New Episode Available!"

        # Create your custom post layout
        custom_caption = (
            f"🔥 **NEW ANIME EPISODE ADDED!** 🔥\n\n"
            f"{original_caption}\n\n"
            f"👉 *Join our channel for more updates!*"
        )

        # Send custom post based on media type
        if message.video:
            await context.bot.send_video(
                chat_id=MAIN_CHANNEL_ID,
                video=message.video.file_id,
                caption=custom_caption,
                parse_mode="Markdown"
            )
        elif message.photo:
            # message.photo is a list of sizes, [-1] is the highest quality photo
            await context.bot.send_photo(
                chat_id=MAIN_CHANNEL_ID,
                photo=message.photo[-1].file_id,
                caption=custom_caption,
                parse_mode="Markdown"
            )
        elif message.document:
            await context.bot.send_document(
                chat_id=MAIN_CHANNEL_ID,
                document=message.document.file_id,
                caption=custom_caption,
                parse_mode="Markdown"
            )
        else:
            # If it's just text
            await context.bot.send_message(
                chat_id=MAIN_CHANNEL_ID,
                text=custom_caption,
                parse_mode="Markdown"
            )
        
        logging.info(f"Successfully uploaded custom post from channel {chat_id}, message ID: {message_id}")

def main():
    if not BOT_TOKEN or not MONGO_URI or not SOURCE_CHANNEL_IDS:
        raise ValueError("Missing critical environment variables.")

    # Start Flask web server in a background thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    # Start Telegram Bot application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    channel_handler = MessageHandler(filters.Chat(chat_id=SOURCE_CHANNEL_IDS), handle_new_anime)
    application.add_handler(channel_handler)

    print("Custom Poster Bot and Web Server are running...")
    application.run_polling()

if __name__ == '__main__':
    main()
