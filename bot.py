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
last_messages = {}
active_chats = set()

# --- Load / Save user languages ---
if os.path.exists(USER_LANG_FILE):
    with open(USER_LANG_FILE, "r") as f:
        user_languages = json.load(f)
else:
    user_languages = {}

def save_user_languages():
    with open(USER_LANG_FILE, "w") as f:
        json.dump(user_languages, f)

# --- Helper to delete last bot message ---
async def delete_last(chat_id, context: ContextTypes.DEFAULT_TYPE):
    if chat_id in last_messages:
        try:
            await context.bot.delete_message(chat_id, last_messages[chat_id])
        except:
            pass

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
    await delete_last(chat_id, context)
    
    if str(chat_id) in user_languages:
        msg = await update.message.reply_text("👋 Welcome back! Send a song name to get started 🎵")
    else:
        msg = await update.message.reply_text(
            "👋 Welcome! Choose your language:",
            reply_markup=InlineKeyboardMarkup(LANG_BUTTONS)
        )
    last_messages[chat_id] = msg.message_id

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await delete_last(chat_id, context)
    msg = await update.message.reply_text(
        "ℹ️ *Help*\nSend a song name/artist to get it instantly 🎧",
        parse_mode="Markdown"
    )
    last_messages[chat_id] = msg.message_id

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await delete_last(chat_id, context)
    msg = await update.message.reply_text(
        "🎵 *iMusic Beta Bot*\nCreated by @hey_arnab02",
        parse_mode="Markdown"
    )
    last_messages[chat_id] = msg.message_id

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
    await delete_last(chat_id, context)
    msg = await update.message.reply_text(
        "🌐 Select your new language:",
        reply_markup=InlineKeyboardMarkup(LANG_BUTTONS)
    )
    last_messages[chat_id] = msg.message_id

# --- Song handler ---
async def song_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    await delete_last(chat_id, context)

    query_text = update.message.text

    # Typing + downloading message
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    dl_msg = await update.message.reply_text("⬇️ Downloading your song… 🎶")
    last_messages[chat_id] = dl_msg.message_id

    if not os.path.exists(COOKIES_FILE):
        await context.bot.delete_message(chat_id=chat_id, message_id=dl_msg.message_id)
        msg = await update.message.reply_text("⚠️ Bot Under Maintenance")
        last_messages[chat_id] = msg.message_id
        return

    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': 'song.%(ext)s',
            'cookiefile': COOKIES_FILE,
            'noplaylist': True,
            'quiet': True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{query_text}", download=True)['entries'][0]
            file_name = ydl.prepare_filename(info)

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

        await context.bot.delete_message(chat_id=chat_id, message_id=dl_msg.message_id)

        caption = f"🎶 *{title}*\n👤 {artist}\n💿 {album}"
        await context.bot.send_audio(chat_id=chat_id, audio=open(file_name, 'rb'), caption=caption, parse_mode="Markdown")
        enjoy_msg = await context.bot.send_message(chat_id=chat_id, text="🎧 Enjoy your song!")
        last_messages[chat_id] = enjoy_msg.message_id

        os.remove(file_name)

    except Exception as e:
        await context.bot.delete_message(chat_id=chat_id, message_id=dl_msg.message_id)
        msg = await update.message.reply_text("⚠️ Bot Under Maintenance")
        last_messages[chat_id] = msg.message_id

# --- 30-min periodic reminder ---
async def periodic_reminder():
    while True:
        await asyncio.sleep(1800)
        for chat_id in list(active_chats):
            try:
                await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(1)
                msg = await application.bot.send_message(chat_id=chat_id, text="🎵 Which song would you like to listen?")
                last_messages[chat_id] = msg.message_id
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
