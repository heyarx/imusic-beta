import os
import asyncio
import json
import yt_dlp
from mutagen.easyid3 import EasyID3
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)

# --- ENV VARIABLES ---
TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
COOKIES_FILE = os.getenv("YT_COOKIES_FILE", "cookies.txt")
USER_LANG_FILE = "user_lang.json"

# --- FastAPI App ---
app = FastAPI()

# --- Telegram App ---
application = Application.builder().token(TOKEN).build()

# --- Globals ---
last_messages = {}       # Stores all bot message IDs per chat
active_chats = set()     # For reminders
last_song_sent = {}      # Prevent duplicate song sending

# --- Load / Save user languages ---
if os.path.exists(USER_LANG_FILE):
    with open(USER_LANG_FILE, "r") as f:
        user_languages = json.load(f)
else:
    user_languages = {}

def save_user_languages():
    with open(USER_LANG_FILE, "w") as f:
        json.dump(user_languages, f)

# --- Helper to delete all bot messages in chat except song ---
async def clear_previous_bot_messages(chat_id, context: ContextTypes.DEFAULT_TYPE, keep_messages=[]):
    """Delete all messages stored in last_messages except IDs in keep_messages."""
    for msg_id in last_messages.get(chat_id, []):
        if msg_id not in keep_messages:
            try:
                await context.bot.delete_message(chat_id, msg_id)
            except:
                pass
    last_messages[chat_id] = [msg_id for msg_id in last_messages.get(chat_id, []) if msg_id in keep_messages]

# --- Language buttons ---
LANG_BUTTONS = [
    [InlineKeyboardButton("English 🇬🇧", callback_data="lang_en"),
     InlineKeyboardButton("Bangla 🇧🇩", callback_data="lang_bn")],
    [InlineKeyboardButton("Hindi 🇮🇳", callback_data="lang_hi"),
     InlineKeyboardButton("Other 🌍", callback_data="lang_other")]
]

# --- Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    await clear_previous_bot_messages(chat_id, context)

    if str(chat_id) in user_languages:
        msg = await update.message.reply_text("👋 Welcome back! Send a song name to get started 🎵")
    else:
        msg = await update.message.reply_text(
            "👋 Welcome! Please select your language:",
            reply_markup=InlineKeyboardMarkup(LANG_BUTTONS)
        )
    last_messages.setdefault(chat_id, []).append(msg.message_id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await clear_previous_bot_messages(chat_id, context)
    msg = await update.message.reply_text(
        "ℹ️ *Help*\nSend a song name or artist, and I will provide the playable audio instantly 🎧",
        parse_mode="Markdown"
    )
    last_messages.setdefault(chat_id, []).append(msg.message_id)

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await clear_previous_bot_messages(chat_id, context)
    msg = await update.message.reply_text(
        "🎵 *iMusic Beta Bot*\nCreated by @hey_arnab02",
        parse_mode="Markdown"
    )
    last_messages.setdefault(chat_id, []).append(msg.message_id)

# --- Language selection callback ---
async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.from_user.id)
    user_languages[chat_id] = query.data
    save_user_languages()
    await query.edit_message_text(f"🌐 Language set to {query.data.split('_')[1].upper()}\n\nSend a song name to continue:")

# --- Change language command ---
async def change_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await clear_previous_bot_messages(chat_id, context)
    msg = await update.message.reply_text(
        "🌐 Select your new language:",
        reply_markup=InlineKeyboardMarkup(LANG_BUTTONS)
    )
    last_messages.setdefault(chat_id, []).append(msg.message_id)

# --- Song handler ---
async def song_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    query_text = update.message.text.strip()
    active_chats.add(chat_id)

    # Prevent duplicate song requests
    if last_song_sent.get(chat_id) == query_text:
        msg = await update.message.reply_text("⚡ You already requested this song! Please try a new one.")
        last_messages.setdefault(chat_id, []).append(msg.message_id)
        return
    last_song_sent[chat_id] = query_text

    # Delete all previous bot messages except the song
    await clear_previous_bot_messages(chat_id, context)

    # Typing + professional downloading message
    typing_msg = await update.message.reply_text("🎶 Processing your request… Please wait ⏳")
    last_messages.setdefault(chat_id, []).append(typing_msg.message_id)

    if not os.path.exists(COOKIES_FILE):
        msg = await update.message.reply_text("⚠️ Bot Under Maintenance")
        last_messages.setdefault(chat_id, []).append(msg.message_id)
        return

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'song.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'noplaylist': True,
            'quiet': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query_text}", download=True)['entries'][0]
            file_name = ydl.prepare_filename(info)
            file_name = os.path.splitext(file_name)[0] + ".mp3"

        # Metadata
        title = info.get("title", "Unknown Title")
        artist = info.get("artist") or info.get("uploader") or "Unknown Artist"
        album = info.get("album") or "Unknown Album"

        try:
            audio = EasyID3(file_name)
        except:
            audio = EasyID3()
        audio['title'] = title
        audio['artist'] = artist
        audio['album'] = album
        audio.save(file_name)

        # Delete typing message only
        await clear_previous_bot_messages(chat_id, context, keep_messages=[])

        # Send the song
        if os.path.exists(file_name):
            caption = f"🎶 *{title}*\n👤 {artist}\n💿 {album}"
            song_msg = await context.bot.send_audio(chat_id=chat_id, audio=open(file_name, 'rb'), caption=caption, parse_mode="Markdown")
            enjoy_msg = await context.bot.send_message(chat_id=chat_id, text="🎧 Enjoy your song!")
            # Keep only song + enjoy message
            last_messages[chat_id] = [song_msg.message_id, enjoy_msg.message_id]
            os.remove(file_name)
        else:
            msg = await update.message.reply_text("⚠️ Failed to process the song. Please try another.")
            last_messages[chat_id].append(msg.message_id)

    except Exception as e:
        msg = await update.message.reply_text("⚠️ Bot Under Maintenance")
        last_messages[chat_id].append(msg.message_id)

# --- 30-min periodic reminder ---
async def periodic_reminder():
    while True:
        await asyncio.sleep(1800)
        for chat_id in list(active_chats):
            try:
                await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(1)
                msg = await application.bot.send_message(chat_id=chat_id, text="🎵 Which song would you like to listen?")
                last_messages.setdefault(chat_id, []).append(msg.message_id)
            except:
                active_chats.discard(chat_id)

# --- Register handlers ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("about", about_command))
application.add_handler(CommandHandler("language", change_language))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, song_handler))
application.add_handler(CallbackQueryHandler(language_callback, pattern="lang_"))

# --- FastAPI webhook ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("ok")

@app.get("/webhook")
async def webhook_get():
    return PlainTextResponse("⚠️ This endpoint accepts POST only for Telegram updates.")

@app.get("/")
async def home():
    return {"status": "ok"}

# --- Startup event ---
@app.on_event("startup")
async def startup_event():
    await application.initialize()
    await application.start()
    await application.bot.set_webhook(WEBHOOK_URL)
    asyncio.create_task(periodic_reminder())
