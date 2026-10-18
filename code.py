import os
import re
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ==================== LOGGING ====================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("anime_auto_poster")


# ==================== ENVIRONMENT ====================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS_RAW = os.getenv("ADMIN_ID", "").strip()
MONGO_URI = os.getenv("MONGO_URI", "").strip()
MAIN_CHANNEL_RAW = os.getenv("MAIN_CHANNEL_ID", "").strip()

if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN")

if not MONGO_URI:
    raise RuntimeError("Missing MONGO_URI")

if not MAIN_CHANNEL_RAW:
    raise RuntimeError("Missing MAIN_CHANNEL_ID")

try:
    MAIN_CHANNEL_ID = int(MAIN_CHANNEL_RAW)
except ValueError as exc:
    raise RuntimeError("MAIN_CHANNEL_ID must be numeric") from exc


ADMIN_IDS = set()

for item in ADMIN_IDS_RAW.split(","):
    item = item.strip()

    if item:
        try:
            ADMIN_IDS.add(int(item))
        except ValueError:
            log.warning("Invalid admin ID ignored: %s", item)


# ==================== MONGODB ====================

mongo_client = MongoClient(
    MONGO_URI,
    serverSelectionTimeoutMS=10000,
)

db = mongo_client["anime_auto_poster"]

anime_collection = db["anime_sources"]
processed_collection = db["processed_posts"]

anime_collection.create_index(
    [("source_chat_id", ASCENDING)],
    unique=True,
)

processed_collection.create_index(
    [
        ("source_chat_id", ASCENDING),
        ("source_message_id", ASCENDING),
    ],
    unique=True,
)


# ==================== CONVERSATION STATES ====================

ANIME_NAME, SOURCE_CHANNEL, JOIN_URL, POSTER_URL = range(4)


# ==================== HELPERS ====================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def admin_only(update: Update) -> bool:
    user = update.effective_user

    if not user or not is_admin(user.id):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ This command is for bot admins only."
            )
        return False

    return True


def message_link(chat, message_id: int) -> str:
    username = getattr(chat, "username", None)

    if username:
        return f"https://t.me/{username}/{message_id}"

    chat_id = str(chat.id)

    if chat_id.startswith("-100"):
        internal_id = chat_id[4:]
        return f"https://t.me/c/{internal_id}/{message_id}"

    return "https://t.me/"


def get_post_text(message) -> str:
    return message.text or message.caption or ""


def make_keyboard(text: str, config: dict, watch_url: str):
    buttons = []

    # Extract quality links from the source post.
    pattern = re.compile(
        r"(480p|720p|1080p|1440p|2160p)"
        r"\s*[-:|]\s*"
        r"(https?://[^\s<>]+)",
        re.IGNORECASE,
    )

    found = {}

    for match in pattern.finditer(text):
        quality = match.group(1).lower()
        url = match.group(2).rstrip(".,)")

        found[quality] = url

    qualities = ["480p", "720p", "1080p", "1440p", "2160p"]

    quality_buttons = []

    for quality in qualities:
        if quality in found:
            quality_buttons.append(
                InlineKeyboardButton(
                    f"▶️ {quality}",
                    url=found[quality],
                )
            )

    # Arrange quality buttons in one row.
    if quality_buttons:
        buttons.append(quality_buttons)
    else:
        buttons.append([
            InlineKeyboardButton(
                "▶️ WATCH EPISODE",
                url=watch_url,
            )
        ])

    join_url = config.get("join_url", "")

    if join_url:
        buttons.append([
            InlineKeyboardButton(
                "📢 JOIN CHANNEL",
                url=join_url,
            )
        ])

    return InlineKeyboardMarkup(buttons)


# ==================== COMMANDS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎬 Anime Auto Poster Bot is running!\n\n"
        "Admin commands:\n"
        "/addanime - Register a source channel\n"
        "/listanime - List registered channels\n"
        "/removeanime <channel_id> - Remove a source channel\n"
        "/cancel - Cancel setup\n\n"
        "New posts in registered source channels are published automatically."
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
        "Send the source channel ID, usually starting with -100:"
    )

    return SOURCE_CHANNEL


async def addanime_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["source_chat_id"] = int(
            update.effective_message.text.strip()
        )
    except ValueError:
        await update.effective_message.reply_text(
            "Invalid channel ID. Send the numeric ID again:"
        )
        return SOURCE_CHANNEL

    await update.effective_message.reply_text(
        "Send the source channel's Telegram URL or invite link:"
    )

    return JOIN_URL


async def addanime_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    join_url = update.effective_message.text.strip()

    if not join_url.startswith(("https://t.me/", "http://t.me/")):
        await update.effective_message.reply_text(
            "Please send a Telegram URL beginning with https://t.me/"
        )
        return JOIN_URL

    context.user_data["join_url"] = join_url

    await update.effective_message.reply_text(
        "Send a direct poster image URL, or type SKIP:"
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
        "✅ Source channel registered!\n\n"
        f"🎬 Anime: {record['anime_name']}\n"
        f"🆔 Source ID: {record['source_chat_id']}\n\n"
        "New supported posts will be published automatically."
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()

    await update.effective_message.reply_text("Setup cancelled.")

    return ConversationHandler.END


async def listanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    records = list(anime_collection.find().sort("anime_name", ASCENDING))

    if not records:
        await update.effective_message.reply_text(
            "No source channels registered. Use /addanime."
        )
        return

    lines = ["📚 Registered anime sources:"]

    for record in records:
        lines.append(
            f"• {record.get('anime_name', 'Unknown')} — "
            f"{record.get('source_chat_id')}"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def removeanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await admin_only(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /removeanime <source_channel_id>"
        )
        return

    try:
        channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "Channel ID must be numeric."
        )
        return

    result = anime_collection.delete_one(
        {"source_chat_id": channel_id}
    )

    if result.deleted_count:
        await update.effective_message.reply_text(
            "✅ Source channel removed."
        )
    else:
        await update.effective_message.reply_text(
            "No registered source channel found."
        )


# ==================== AUTO POST ====================

async def handle_channel_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.channel_post

    if not message:
        return

    source_id = message.chat.id

    log.info(
        "Received channel post: chat=%s message=%s",
        source_id,
        message.message_id,
    )

    config = anime_collection.find_one(
        {"source_chat_id": source_id}
    )

    if not config:
        log.warning(
            "Channel %s is not registered. Use /addanime.",
            source_id,
        )
        return

    # Support text, photos, videos, animations, documents and audio.
    if not (
        message.text
        or message.caption
        or message.photo
        or message.video
        or message.animation
        or message.document
        or message.audio
        or message.voice
    ):
        log.info("Unsupported empty/media post ignored.")
        return

    key = {
        "source_chat_id": source_id,
        "source_message_id": message.message_id,
    }

    try:
        processed_collection.insert_one({
            **key,
            "status": "processing",
        })
    except DuplicateKeyError:
        log.info(
            "Duplicate post ignored: %s/%s",
            source_id,
            message.message_id,
        )
        return

    try:
        original_text = get_post_text(message)
        watch_url = message_link(
            message.chat,
            message.message_id,
        )

        keyboard = make_keyboard(
            original_text,
            config,
            watch_url,
        )

        anime_name = config.get("anime_name", "Anime")

        # Keep the original post content and add the anime name
        # only if the original post doesn't already contain it.
        if message.text:
            text = message.text

            if anime_name.lower() not in text.lower():
                text = f"🎬 {anime_name}\n\n{text}"

            await context.bot.send_message(
                chat_id=MAIN_CHANNEL_ID,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )

        elif message.photo:
            await context.bot.send_photo(
                chat_id=MAIN_CHANNEL_ID,
                photo=message.photo[-1].file_id,
                caption=message.caption or f"🎬 {anime_name}",
                reply_markup=keyboard,
            )

        elif message.video:
            await context.bot.send_video(
                chat_id=MAIN_CHANNEL_ID,
                video=message.video.file_id,
                caption=message.caption or f"🎬 {anime_name}",
                reply_markup=keyboard,
            )

        elif message.animation:
            await context.bot.send_animation(
                chat_id=MAIN_CHANNEL_ID,
                animation=message.animation.file_id,
                caption=message.caption or f"🎬 {anime_name}",
                reply_markup=keyboard,
            )

        elif message.document:
            await context.bot.send_document(
                chat_id=MAIN_CHANNEL_ID,
                document=message.document.file_id,
                caption=message.caption or f"🎬 {anime_name}",
                reply_markup=keyboard,
            )

        elif message.audio:
            await context.bot.send_audio(
                chat_id=MAIN_CHANNEL_ID,
                audio=message.audio.file_id,
                caption=message.caption or f"🎬 {anime_name}",
                reply_markup=keyboard,
            )

        elif message.voice:
            await context.bot.send_voice(
                chat_id=MAIN_CHANNEL_ID,
                voice=message.voice.file_id,
                caption=message.caption or f"🎬 {anime_name}",
                reply_markup=keyboard,
            )

        processed_collection.update_one(
            key,
            {"$set": {"status": "published"}},
        )

        log.info(
            "Successfully published post %s/%s",
            source_id,
            message.message_id,
        )

    except Exception:
        processed_collection.delete_one(key)

        log.exception(
            "Failed to publish post %s/%s",
            source_id,
            message.message_id,
        )


# ==================== RENDER HEALTH SERVER ====================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        body = b"OK"

        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    server = HTTPServer(
        ("0.0.0.0", port),
        HealthHandler,
    )

    thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )

    thread.start()

    log.info("Health server listening on port %s", port)


# ==================== MAIN ====================

def main():
    mongo_client.admin.command("ping")

    start_health_server()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    conversation = ConversationHandler(
        entry_points=[
            CommandHandler("addanime", addanime_start)
        ],
        states={
            ANIME_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    addanime_name,
                )
            ],
            SOURCE_CHANNEL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    addanime_source,
                )
            ],
            JOIN_URL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    addanime_join,
                )
            ],
            POSTER_URL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    addanime_poster,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )

    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("help", help_command)
    )

    application.add_handler(conversation)

    application.add_handler(
        CommandHandler("listanime", listanime)
    )

    application.add_handler(
        CommandHandler("removeanime", removeanime)
    )

    application.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POST,
            handle_channel_post,
        )
    )
    async def debug_channel_post(update, context):
    message = update.channel_post

    if message:
        log.warning(
            "DEBUG: Received channel post from chat_id=%s, message_id=%s",
            message.chat.id,
            message.message_id,
        )
    else:
        log.warning("DEBUG: Update received without channel_post")


application.add_handler(
    MessageHandler(
        filters.UpdateType.CHANNEL_POST,
        debug_channel_post,
    ),
    group=1,
                 )

    log.info("Anime Auto Poster Bot starting...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
