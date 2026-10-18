import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters
from pymongo import MongoClient

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Fetch credentials from Render Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
MAIN_CHANNEL_ID = int(os.getenv("MAIN_CHANNEL_ID", "0"))

# Parse multiple source channel IDs
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

        # Copy the exact post (poster/video/file + caption) to the Main Channel
        await context.bot.copy_message(
            chat_id=MAIN_CHANNEL_ID,
            from_chat_id=chat_id,
            message_id=message_id
        )
        
        logging.info(f"Successfully copied post with poster from channel {chat_id}, message ID: {message_id}")

if __name__ == '__main__':
    if not BOT_TOKEN or not MONGO_URI or not SOURCE_CHANNEL_IDS:
        raise ValueError("Missing critical environment variables.")

    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Listen to updates from all source channels
    channel_handler = MessageHandler(filters.Chat(chat_id=SOURCE_CHANNEL_IDS), handle_new_anime)
    application.add_handler(channel_handler)

    print(f"Poster Auto-Poster Bot is running, monitoring {len(SOURCE_CHANNEL_IDS)} channels...")
    application.run_polling()
