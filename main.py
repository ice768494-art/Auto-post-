import os
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters,
)

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("anime_auto_poster")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "").strip()
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MAIN_CHANNEL_ID_RAW = os.getenv("MAIN_CHANNEL_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN environment variable")
if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI environment variable")
if not MAIN_CHANNEL_ID_RAW:
    raise RuntimeError("Missing MAIN_CHANNEL_ID environment variable")

try:
    MAIN_CHANNEL_ID = int(MAIN_CHANNEL_ID_RAW)
except ValueError as exc:
    raise RuntimeError("MAIN_CHANNEL_ID must be a numeric Telegram channel ID") from exc

ADMIN_IDS = set()
for item in ADMIN_IDS_RAW.split(","):
    item = item.strip()
    if item:
        try:
            ADMIN_IDS.add(int(item))
        except ValueError:
            log.warning("Ignoring invalid ADMIN_ID entry: %s", item)

mongo_client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
db = mongo_client["anime_auto_poster"]
anime_collection = db["anime_sources"]
processed_collection = db["processed_posts"]
processed_collection.create_index(
    [("source_chat_id", ASCENDING), ("source_message_id", ASCENDING)],
    unique=True,
)
anime_collection.create_index([("source_chat_id", ASCENDING)], unique=True)

# Conversation states
ANIME_NAME, SOURCE_CHANNEL, JOIN_URL, POSTER_URL = range(4)


def is_admin(user_id: int) -> bool:
    # If ADMIN_ID is omitted, commands are unavailable to everyone for safety.
    return user_id in ADMIN_IDS


async def admin_only(update: Update) -> bool:
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ This command is for bot admins only.")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎬 Anime Auto Poster Bot is running!\\n\\n"
        "Admin commands:\\n"
        "/addanime - Register a source channel\\n"
        "/listanime - List registered channels\\n"
        "/removeanime - Remove a source channel\\n"
        "/cancel - Cancel the current setup\\n\\n"
        "When the bot is an admin in a registered source channel, new photo/video/document "
        "posts are automatically forwarded as an episode post to the main channel."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def addanime_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return ConversationHandler.END
    await update.effective_message.reply_text("Send the anime name:")
    return ANIME_NAME


async def addanime_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["anime_name"] = update.effective_message.text.strip()
    await update.effective_message.reply_text(
        "Send the source channel ID (usually starts with -100). "
        "Make the bot an administrator in that channel first."
    )
    return SOURCE_CHANNEL


async def addanime_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["source_chat_id"] = int(update.effective_message.text.strip())
    except ValueError:
        await update.effective_message.reply_text("That channel ID is not numeric. Send the ID again:")
        return SOURCE_CHANNEL
    await update.effective_message.reply_text(
        "Send the channel's public join URL (for example https://t.me/yourchannel). "
        "For a private channel, send its invite URL."
    )
    return JOIN_URL


async def addanime_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_url = update.effective_message.text.strip()
    if not join_url.startswith(("https://t.me/", "http://t.me/")):
        await update.effective_message.reply_text("Please send a Telegram URL beginning with https://t.me/")
        return JOIN_URL
    context.user_data["join_url"] = join_url
    await update.effective_message.reply_text(
        "Send a direct poster image URL, or type SKIP to use the episode's media as the post image."
    )
    return POSTER_URL


async def addanime_poster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        context.user_data.clear()
        return ConversationHandler.END

    poster = update.effective_message.text.strip()
    if poster.upper() == "SKIP":
        poster = ""

    record = {
        "anime_name": context.user_data["anime_name"],
        "source_chat_id": context.user_data["source_chat_id"],
        "join_url": context.user_data["join_url"],
        "poster_url": poster,
    }
    anime_collection.update_one(
        {"source_chat_id": record["source_chat_id"]},
        {"$set": record},
        upsert=True,
    )
    await update.effective_message.reply_text(
        "✅ Anime source saved!\\n"
        f"🎬 Name: {record['anime_name']}\\n"
        f"🆔 Source: {record['source_chat_id']}\\n"
        "New supported posts in that channel will be published to the main channel."
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled.")
    return ConversationHandler.END


async def listanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    records = list(anime_collection.find().sort("anime_name", ASCENDING))
    if not records:
        await update.effective_message.reply_text("No anime source channels registered yet. Use /addanime.")
        return
    lines = ["📚 Registered anime sources:"]
    for rec in records:
        lines.append(f"• {rec.get('anime_name', 'Unknown')} — {rec.get('source_chat_id')}")
    await update.effective_message.reply_text("\\n".join(lines))


async def removeanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /removeanime <source_channel_id>")
        return
    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("Channel ID must be numeric.")
        return
    result = anime_collection.delete_one({"source_chat_id": channel_id})
    if result.deleted_count:
        await update.effective_message.reply_text("✅ Source channel removed.")
    else:
        await update.effective_message.reply_text("No registered source channel found with that ID.")


def message_link(chat, message_id: int) -> str:
    username = getattr(chat, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    chat_id = str(chat.id)
    if chat_id.startswith("-100"):
        internal_id = chat_id[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"
    return "https://t.me/"


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        return
    if not (message.photo or message.video or message.document or message.animation):
        return

    source_id = message.chat.id
    config = anime_collection.find_one({"source_chat_id": source_id})
    if not config:
        return

    key = {"source_chat_id": source_id, "source_message_id": message.message_id}
    # Reserve the source post before publishing so duplicate updates are ignored.
    try:
        processed_collection.insert_one({**key, "status": "processing"})
    except DuplicateKeyError:
        log.info("Ignoring duplicate source post %s/%s", source_id, message.message_id)
        return

    watch_url = message_link(message.chat, message.message_id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ WATCH EPISODE", url=watch_url)],
        [InlineKeyboardButton("📢 JOIN CHANNEL", url=config["join_url"])],
    ])
    caption = f"🎬 <b>{config['anime_name']}</b>\\n\\n👇 Tap below to watch the episode."
    try:
        poster_url = config.get("poster_url", "")
        if poster_url:
            await context.bot.send_photo(
                chat_id=MAIN_CHANNEL_ID,
                photo=poster_url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        elif message.photo:
            await context.bot.send_photo(
                chat_id=MAIN_CHANNEL_ID,
                photo=message.photo[-1].file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        elif message.video:
            await context.bot.send_video(
                chat_id=MAIN_CHANNEL_ID,
                video=message.video.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        elif message.animation:
            await context.bot.send_animation(
                chat_id=MAIN_CHANNEL_ID,
                animation=message.animation.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        else:
            await context.bot.send_document(
                chat_id=MAIN_CHANNEL_ID,
                document=message.document.file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        processed_collection.update_one(key, {"$set": {"status": "published"}})
        log.info("Published post from %s/%s", source_id, message.message_id)
    except Exception:
        processed_collection.delete_one(key)
        log.exception("Failed to publish source post %s/%s", source_id, message.message_id)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", "2")
        self.end_headers()

    def log_message(self, format, *args):
        log.info("Health HTTP: " + format, *args)


def start_health_server():
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on port %s", port)


def main():
    # Check database connectivity early so deployment errors are clear.
    mongo_client.admin.command("ping")
    start_health_server()

    application = Application.builder().token(BOT_TOKEN).build()

    conversation = ConversationHandler(
        entry_points=[CommandHandler("addanime", addanime_start)],
        states={
            ANIME_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addanime_name)],
            SOURCE_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, addanime_source)],
            JOIN_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, addanime_join)],
            POSTER_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, addanime_poster)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conversation)
    application.add_handler(CommandHandler("listanime", listanime))
    application.add_handler(CommandHandler("removeanime", removeanime))
    application.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST, handle_channel_post))

    log.info("Anime Auto Poster Bot starting")
    # python-telegram-bot's run_polling manages its own asyncio event loop.
    # Do not wrap this call in asyncio.run().
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
