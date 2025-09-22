import os
import asyncio
from pathlib import Path
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# === Environment variables ===
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://imusic-beta.onrender.com/webhook")
COOKIES_FILE = os.getenv("YT_COOKIES_FILE", "cookies.txt")

app = FastAPI()
application = Application.builder().token(TOKEN).build()

# store last bot message id per chat for deleting old messages
last_message_ids = {}

async def delete_last(chat_id, context: ContextTypes.DEFAULT_TYPE):
    if chat_id in last_message_ids:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_message_ids[chat_id])
        except:
            pass

# === Commands ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user.first_name
    await delete_last(update.effective_chat.id, context)
    msg = await update.message.reply_text(
        f"👋 Welcome *{user}*!\n\n🎵 Which song would you like to listen to?",
        parse_mode="Markdown"
    )
    last_message_ids[update.effective_chat.id] = msg.message_id

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_last(update.effective_chat.id, context)
    msg = await update.message.reply_text(
        "ℹ️ *Help*\n\nSend a song name or artist to get the track instantly.",
        parse_mode="Markdown"
    )
    last_message_ids[update.effective_chat.id] = msg.message_id

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_last(update.effective_chat.id, context)
    msg = await update.message.reply_text(
        "🎵 *iMusic Beta Bot*\n\nCreated by @hey_arnab02",
        parse_mode="Markdown"
    )
    last_message_ids[update.effective_chat.id] = msg.message_id

# === Song handler ===
async def song_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.message.text
    chat_id = update.effective_chat.id

    # Delete previous bot message
    await delete_last(chat_id, context)

    # Show typing action
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    # Temporary “downloading” message
    dl_msg = await update.message.reply_text("⬇️ Downloading your song… Please wait 🎶")
    last_message_ids[chat_id] = dl_msg.message_id

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'song.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'noplaylist': True,
            'quiet': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query}", download=True)['entries'][0]
            file_name = ydl.prepare_filename(info)

        # Metadata
        title = info.get("title", "Unknown Title")
        artist = info.get("artist") or info.get("uploader") or "Unknown Artist"
        album = info.get("album") or "Unknown Album"

        # Tag metadata
        try:
            audio = EasyID3(file_name)
        except:
            audio = EasyID3()
        audio['title'] = title
        audio['artist'] = artist
        audio['album'] = album
        audio.save(file_name)

        # Delete “downloading” message
        await context.bot.delete_message(chat_id=chat_id, message_id=dl_msg.message_id)

        # Send audio
        caption = f"🎶 *{title}*\n👤 {artist}\n💿 {album}"
        sent = await context.bot.send_audio(
            chat_id=chat_id,
            audio=open(file_name, 'rb'),
            caption=caption,
            parse_mode="Markdown"
        )

        # After sending, show enjoy message
        enjoy_msg = await context.bot.send_message(chat_id=chat_id, text="Enjoy your song 🎧")
        last_message_ids[chat_id] = enjoy_msg.message_id

        os.remove(file_name)
    except Exception as e:
        # Show under maintenance if cookies fail or any error
        await context.bot.delete_message(chat_id=chat_id, message_id=dl_msg.message_id)
        msg = await context.bot.send_message(chat_id=chat_id, text="⚠️ Bot Under Maintenance")
        last_message_ids[chat_id] = msg.message_id

# === Register handlers ===
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("about", about_command))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, song_handler))

# === FastAPI webhook ===
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.get("/")
async def home():
    return {"status": "ok"}

# === Startup to set webhook ===
@app.on_event("startup")
async def startup_event():
    await application.bot.set_webhook(WEBHOOK_URL)
