ANIME AUTO POSTER BOT — RENDER WEB SERVICE

Files:
- code.py: bot code
- requirements.txt: Python dependencies
- render.yaml: Render configuration

Render environment variables:
BOT_TOKEN       Telegram bot token from BotFather
ADMIN_ID        Your Telegram numeric user ID (multiple IDs can be comma-separated)
MONGO_URI       MongoDB connection URI
MAIN_CHANNEL_ID Numeric ID of the destination/main channel (usually -100...)

Setup:
1. Upload these files to a GitHub repository.
2. Create a Render Web Service from that repository.
3. Add the four environment variables above.
4. Deploy.
5. Add the bot as administrator to the main channel with permission to post.
6. Add the bot as administrator to each source channel.
7. In a private chat with the bot, run /addanime and follow the prompts.

Commands:
 /start
 /help
 /addanime
 /listanime
 /removeanime <source_channel_id>
 /cancel

Supported source channel posts: photos, videos, animations, and documents.
The bot posts a new message in the main channel with WATCH EPISODE and JOIN CHANNEL buttons.

Notes:
- The source channel must be registered using /addanime.
- Telegram channel IDs are numeric; private/supergroup channel IDs commonly begin with -100.
- Use a direct image URL for a custom poster, or type SKIP.
- Keep BOT_TOKEN and MONGO_URI private.
