ANIME AUTO POSTER BOT — RENDER WEB SERVICE

Files:
- main.py: bot code with an HTTP health endpoint for Render.
- requirements.txt: Python dependencies.
- render.yaml: optional Render Blueprint configuration.

Render setup:
1. Upload these files to the root of your GitHub repository.
2. In Render, create a Web Service connected to that repository.
3. Build command: pip install -r requirements.txt
4. Start command: python main.py
5. Add these environment variables in Render:
   BOT_TOKEN = your Telegram bot token
   ADMIN_ID = your numeric Telegram user ID
   MONGO_URI = your MongoDB connection string
   MAIN_CHANNEL_ID = your main channel ID, usually -100...
6. Deploy the service.

Telegram setup:
- Add the bot as an administrator in every source channel.
- Add the bot as an administrator in the main channel with permission to post.
- Use /addanime in a private chat with the bot to register each anime.
- Keep only one running instance of this polling bot to avoid conflicts.

Important:
- The MongoDB database must be reachable from Render. Check MongoDB Atlas network access.
- Private source-channel links only work for people who already have access to that channel.
- No program can guarantee zero runtime errors if tokens, IDs, database access, or Telegram permissions are invalid. Check Render logs if deployment fails.
