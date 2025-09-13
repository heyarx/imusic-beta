import os
import json
import asyncio
import datetime
from pathlib import Path
import requests

import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ------------------------- CONFIG -------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://imusic-beta.onrender.com/webhook")
YT_COOKIES = os.getenv("YT_COOKIES_FILE", "cookies.txt")
DOWNLOAD_DIR = Path("downloads")
USERS_FILE = Path("users.json")

LANGUAGES = {
    "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ar": "Arabic", "bn": "Bengali", "tr": "Turkish", "it": "Italian",
    "nl": "Dutch", "sv": "Swedish", "pl": "Polish", "vi": "Vietnamese", "th": "Thai",
    "id": "Indonesian", "ta": "Tamil"
}

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ------------------------- INIT -------------------------
DOWNLOAD_DIR.mkdir(exist_ok=True)
if not USERS_FILE.exists():
    USERS_FILE.write_text(json.dumps({}))

def load_users():
    try:
        return json.loads(USERS_FILE.read_text())
    except:
        return {}

def save_users(data):
    USERS_FILE.write_text(json.dumps(data, indent=2))

def register_user(user_id, first_name=None):
    users = load_users()
    str_id = str(user_id)
    if str_id not in users:
        users[str_id] = {"first_name": first_name or "", "language": "en", "ready": True}
        save_users(users)

def set_user_language(user_id, lang_code):
    users = load_users()
    u = users.get(str(user_id), {})
    u["language"] = lang_code
    u["ready"] = True
    users[str(user_id)] = u
    save_users(users)

def get_user_pref(user_id):
    return load_users().get(str(user_id), {})

# ------------------------- ANIMATIONS -------------------------
async def send_typing(chat_id, duration=5):
    try:
        for _ in range(duration):
            await application.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(1)
    except: 
        pass

async def animate_downloading(chat_id, message_id, stop_event: asyncio.Event):
    emojis = ["🎵", "🎶", "🎧", "🔄"]
    idx = 0
    while not stop_event.is_set():
        try:
            await application.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"Downloading {emojis[idx % len(emojis)]}"
            )
        except:
            pass
        idx += 1
        await asyncio.sleep(0.5)

# ------------------------- KEYBOARDS -------------------------
def language_keyboard():
    keyboard = []
    row = []
    for idx, (code, name) in enumerate(LANGUAGES.items(), 1):
        row.append(InlineKeyboardButton(name, callback_data=f"lang_{code}"))
        if idx % 3 == 0:
            keyboard.append(row)
            row = []
    if row: keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

# ------------------------- APP + PTB -------------------------
from contextlib import asynccontextmanager
application = Application.builder().token(BOT_TOKEN).build()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await application.initialize()
    await application.start()

    # Set professional bot commands
    async def set_bot_commands(app):
        commands = [
            BotCommand("start", "Start the bot and choose language"),
            BotCommand("help", "Show instructions to use iMusic Beta"),
            BotCommand("about", "Info about iMusic Beta and creator")
        ]
        await app.bot.set_my_commands(commands)
    asyncio.create_task(set_bot_commands(application))

    try:
        await application.bot.set_webhook(WEBHOOK_URL)
        print(f"✅ Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        print(f"❌ Failed to set webhook: {e}")

    yield
    await application.stop()
    await application.shutdown()

app = FastAPI(lifespan=lifespan)

# ------------------------- HANDLERS -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    now = datetime.datetime.now().hour
    greeting = "🌅 Good morning" if 5 <= now < 12 else "🌞 Good afternoon" if 12 <= now < 17 else "🌆 Good evening" if 17 <= now < 21 else "🌙 Good night"
    msg = await update.message.reply_text(
        f"{greeting}, {user.first_name}!\nPlease select your language:",
        reply_markup=language_keyboard()
    )
    await asyncio.sleep(5)
    try: await msg.delete()
    except: pass

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("lang_"):
        code = data.split("_")[1]
        set_user_language(user_id, code)

        # Directly ask for song name
        msg = await query.edit_message_text(
            f"Language set to {LANGUAGES[code]}.\n🎵 Now, please send the song name you want to listen to:"
        )
        context.user_data["prompt_msg_id"] = msg.message_id

# ------------------------- HELP & ABOUT -------------------------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🎵 **iMusic Beta Help**\n"
        "1. /start - Start the bot & select language\n"
        "2. Send a song name\n"
        "3. Receive your song with album art & metadata\n"
        "Enjoy your music! 🎧"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    about_text = (
        "🎶 iMusic Beta v1.0\n"
        "Created by: @hey_arnab02\n"
        "Download songs with album art & metadata easily!"
    )
    await update.message.reply_text(about_text)

application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("about", about_command))

# ------------------------- SONG SEARCH & DOWNLOAD -------------------------
async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    pref = get_user_pref(user_id)

    if not pref.get("ready", False):
        msg = await update.message.reply_text("Please select a language first.")
        await asyncio.sleep(5)
        try: await msg.delete()
        except: pass
        return

    # Delete previous prompt
    prompt_msg_id = context.user_data.get("prompt_msg_id")
    if prompt_msg_id:
        try:
            await application.bot.delete_message(chat_id=chat_id, message_id=prompt_msg_id)
        except:
            pass
        context.user_data["prompt_msg_id"] = None

    # Animated downloading message
    status_msg = await update.message.reply_text("Downloading 🎵")
    stop_event = asyncio.Event()
    spinner_task = asyncio.create_task(
        animate_downloading(chat_id, status_msg.message_id, stop_event)
    )

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(DOWNLOAD_DIR / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "cookiefile": YT_COOKIES if YT_COOKIES and os.path.exists(YT_COOKIES) else None,
        "postprocessors": [{"key": "FFmpegExtractAudio","preferredcodec": "mp3","preferredquality": "192"}],
        "quiet": True,
        "no_warnings": True
    }

    try:
        await send_typing(chat_id)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch:{update.message.text.strip()}", download=True)
            if not info["entries"]:
                stop_event.set()
                await status_msg.edit_text("❌ No results found.")
                return
            entry = info["entries"][0]
            video_id = entry.get("id")
            title = entry.get("title") or update.message.text.strip()
            artist = entry.get("uploader") or ""
            album = entry.get("album") or ""
            thumbnail = entry.get("thumbnail")
            file_path = DOWNLOAD_DIR / f"{video_id}.mp3"

        # Metadata & album art
        try:
            audio = EasyID3(str(file_path))
            audio["title"] = title
            audio["artist"] = artist
            if album: audio["album"] = album
            audio.save()

            if thumbnail:
                img_data = requests.get(thumbnail).content
                audio_id3 = ID3(str(file_path))
                audio_id3['APIC'] = APIC(
                    encoding=3,
                    mime='image/jpeg',
                    type=3,
                    desc='Cover',
                    data=img_data
                )
                audio_id3.save()
        except Exception as e:
            logger.warning(f"Failed to embed album art: {e}")

        stop_event.set()
        await spinner_task

        # Send MP3
        await send_typing(chat_id)
        with open(file_path, "rb") as f:
            await application.bot.send_audio(chat_id=chat_id, audio=f, title=title, performer=artist)

        try: await status_msg.delete()
        except: pass
        enjoy_msg = await application.bot.send_message(chat_id=chat_id, text="Enjoy your music 🎧")
        await asyncio.sleep(5)
        try: enjoy_msg.delete()
        except: pass
        if file_path.exists(): file_path.unlink()

    except Exception as e:
        stop_event.set()
        logger.exception("Search song error")
        await status_msg.edit_text("❌ Could not fetch the song.")

# ------------------------- WEBHOOK -------------------------
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    asyncio.create_task(application.process_update(update))
    return {"ok": True}

@app.get("/")
async def root():
    return {"status": "iMusic Beta is running 🎵"}

@app.get("/favicon.ico")
async def favicon():
    return FileResponse("favicon.ico")

# ------------------------- ADD HANDLERS -------------------------
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(callback_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
