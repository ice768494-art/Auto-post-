import os
import logging
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from pymongo import MongoClient

# ================= CONFIGURATION =================

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
MONGO_URI = os.environ["MONGO_URI"]
MAIN_CHANNEL_ID = int(os.environ["MAIN_CHANNEL_ID"])

# ==================================================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mongo = MongoClient(MONGO_URI)
db = mongo["anime_auto_poster"]
anime_collection = db["anime"]

# Temporary admin conversation states
admin_states = {}


def is_admin(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == ADMIN_ID
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.effective_message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    await update.effective_message.reply_text(
        "🎬 Anime Auto Poster Bot\n\n"
        "/addanime - Add an anime\n"
        "/removeanime - Remove an anime\n"
        "/listanime - List anime\n"
        "/setmain - Set Main Channel\n"
        "/setposter - Change poster\n"
        "/cancel - Cancel current operation\n"
        "/help - Show commands"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    await update.effective_message.reply_text(
        "Commands:\n"
        "/addanime - Register anime\n"
        "/removeanime - Remove anime\n"
        "/listanime - List anime\n"
        "/setmain - Set Main Channel\n"
        "/setposter - Update poster\n"
        "/cancel - Cancel operation"
    )


async def addanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    admin_states[ADMIN_ID] = {"action": "add"}
    await update.effective_message.reply_text(
        "Send the anime name."
    )


async def removeanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    admin_states[ADMIN_ID] = {"action": "remove"}
    await update.effective_message.reply_text(
        "Send the exact anime name to remove."
    )


async def setposter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    admin_states[ADMIN_ID] = {"action": "poster"}
    await update.effective_message.reply_text(
        "Send the anime name whose poster you want to change."
    )


async def setmain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global MAIN_CHANNEL_ID

    if not is_admin(update):
        return

    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /setmain -1001234567890"
        )
        return

    try:
        new_channel_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "Invalid channel ID."
        )
        return

    global main_channel_id
    main_channel_id = new_channel_id

    await update.effective_message.reply_text(
        f"✅ Main Channel ID updated to {new_channel_id}.\n"
        "Note: Also update MAIN_CHANNEL_ID in Render's environment "
        "variables to keep this setting after a restart."
    )


async def listanime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    items = list(anime_collection.find({}, {"_id": 0, "name": 1, "channel_id": 1}))

    if not items:
        await update.effective_message.reply_text(
            "No anime registered yet. Use /addanime."
        )
        return

    lines = ["🎬 Registered Anime:\n"]
    for item in items:
        lines.append(
            f"• {item['name']} — {item['channel_id']}"
        )

    await update.effective_message.reply_text("\n".join(lines))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return

    admin_states.pop(ADMIN_ID, None)
    await update.effective_message.reply_text(
        "❌ Operation cancelled."
    )


async def handle_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not is_admin(update):
        return

    state = admin_states.get(ADMIN_ID)
    if not state:
        return

    message = update.effective_message
    action = state["action"]

    if action == "add":
        state["name"] = message.text.strip()
        state["action"] = "channel_id"
        await message.reply_text(
            "Send the source channel ID (example: -1001234567890).\n"
            "Make sure the bot is an administrator in that channel."
        )

    elif action == "channel_id":
        try:
            state["channel_id"] = int(message.text.strip())
        except (ValueError, AttributeError):
            await message.reply_text("Invalid channel ID. Try again.")
            return

        state["action"] = "watch_url"
        await message.reply_text(
            "Send the custom Watch Now URL."
        )

    elif action == "watch_url":
        url = message.text.strip()

        if not url.startswith(("https://", "http://")):
            await message.reply_text(
                "Please send a valid URL starting with https:// or http://."
            )
            return

        state["watch_url"] = url
        state["action"] = "join_url"
        await message.reply_text(
            "Send the Join Channel URL."
        )

    elif action == "join_url":
        url = message.text.strip()

        if not url.startswith(("https://", "http://")):
            await message.reply_text(
                "Please send a valid URL starting with https:// or http://."
            )
            return

        state["join_url"] = url
        state["action"] = "poster"
        await message.reply_text(
            "Now send the poster as a photo."
        )

    elif action == "poster":
        if not message.photo:
            await message.reply_text(
                "Please send the poster as a Telegram photo."
            )
            return

        poster_file_id = message.photo[-1].file_id

        if action == "poster" and "name" not in state:
            anime_name = state.get("anime_name")
            if not anime_name:
                await message.reply_text(
                    "Anime name is missing. Use /addanime to register a new anime."
                )
                admin_states.pop(ADMIN_ID, None)
                return
        else:
            anime_name = state["name"]

        document = {
            "name": anime_name,
            "channel_id": state["channel_id"],
            "watch_url": state["watch_url"],
            "join_url": state["join_url"],
            "poster_file_id": poster_file_id,
        }

        anime_collection.update_one(
            {"channel_id": state["channel_id"]},
            {"$set": document},
            upsert=True,
        )

        admin_states.pop(ADMIN_ID, None)

        await message.reply_text(
            f"✅ {anime_name} registered successfully!\n"
            "The bot will announce new media posts from this channel."
        )

    elif action == "remove":
        name = message.text.strip()
        result = anime_collection.delete_one({"name": name})
        admin_states.pop(ADMIN_ID, None)

        if result.deleted_count:
            await message.reply_text(f"✅ Removed {name}.")
        else:
            await message.reply_text("Anime not found.")

    elif action == "poster":
        state["anime_name"] = message.text.strip()
        state["action"] = "new_poster"
        await message.reply_text("Send the replacement poster photo.")

    elif action == "new_poster":
        if not message.photo:
            await message.reply_text("Please send a photo.")
            return

        anime_collection.update_one(
            {"name": state["anime_name"]},
            {"$set": {"poster_file_id": message.photo[-1].file_id}},
        )
        admin_states.pop(ADMIN_ID, None)
        await message.reply_text("✅ Poster updated.")


async def handle_channel_post(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    post = update.channel_post
    if not post:
        return

    # Only photos, videos, and documents trigger announcements.
    if not (post.photo or post.video or post.document):
        return

    anime = anime_collection.find_one(
        {"channel_id": post.chat.id}
    )

    if not anime:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📺 WATCH NOW",
                url=anime["watch_url"],
            )
        ],
        [
            InlineKeyboardButton(
                "📢 JOIN CHANNEL",
                url=anime["join_url"],
            )
        ],
    ])

    try:
        await context.bot.send_photo(
            chat_id=MAIN_CHANNEL_ID,
            photo=anime["poster_file_id"],
            reply_markup=keyboard,
        )
        logger.info("Announcement sent for %s", anime["name"])

    except Exception:
        logger.exception("Failed to send announcement")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("addanime", addanime))
    app.add_handler(CommandHandler("removeanime", removeanime))
    app.add_handler(CommandHandler("listanime", listanime))
    app.add_handler(CommandHandler("setmain", setmain))
    app.add_handler(CommandHandler("setposter", setposter))
    app.add_handler(CommandHandler("cancel", cancel))

    # Admin setup conversations.
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_admin_message,
        )
    )

    # Telegram channel posts.
    app.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POST,
            handle_channel_post,
        )
    )

    logger.info("Anime Auto Poster Bot started.")
    app.run_polling(
        allowed_updates=["message", "channel_post"]
    )


if __name__ == "__main__":
    main()
