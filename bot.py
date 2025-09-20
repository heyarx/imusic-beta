import os
import json
import asyncio
from pathlib import Path
import requests
import yt_dlp
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, APIC
import datetime
import logging
from contextlib import asynccontextmanager
import subprocess
import sys

from fastapi import FastAPI, Request
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes,
    MessageHandler, filters
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://imusic-beta.onrender.com/webhook")

DOWNLOAD_DIR = Path("downloads")
CACHE_DIR = Path("cache")
USERS_FILE = Path("users.json")
DOWNLOAD_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
if not USERS_FILE.exists():
    USERS_FILE.write_text(json.dumps({}))

# ---------------- LANGUAGES ----------------
LANGUAGES = {
    "en": "English", "hi": "Hindi", "es": "Spanish", "fr": "French", "de": "German",
    "pt": "Portuguese", "ru": "Russian", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "ar": "Arabic", "bn": "Bengali", "tr": "Turkish", "it": "Italian",
    "nl": "Dutch", "sv": "Swedish", "pl": "Polish", "vi": "Vietnamese", "th": "Thai",
    "id": "Indonesian", "ta": "Tamil"
}

# ---------------- USER STATE ----------------
def load_users(): 
    return json.loads(USERS_FILE.read_text() or "{}")

def save_users(data): 
    USERS_FILE.write_text(json.dumps(data, indent=2))

def register_user(user_id, first_name=None):
    users = load_users()
    if str(user_id) not in users:
        users[str(user_id)] = {"first_name": first_name or "", "language": "en", "ready": True}
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

# ---------------- COOKIE ROTATION ----------------
COOKIE_STATE = Path("cookie_state.json")
COOKIES_FOLDER = Path("cookies")
if not COOKIE_STATE.exists():
    COOKIE_STATE.write_text(json.dumps({"index": 0}))

def get_current_cookie():
    cookies = sorted(COOKIES_FOLDER.glob("cookies*.txt"))
    if not cookies:
        return None
    state = json.loads(COOKIE_STATE.read_text())
    idx = state.get("index", 0) % len(cookies)
    return cookies[idx]

def rotate_cookie():
    cookies = sorted(COOKIES_FOLDER.glob("cookies*.txt"))
    if not cookies: 
        return None
    state = json.loads(COOKIE_STATE.read_text())
    idx = (state.get("index", 0) + 1) % len(cookies)
    COOKIE_STATE.write_text(json.dumps({"index": idx}))
    logger.info(f"Rotated cookie to {cookies[idx].name}")
    return cookies[idx]

# ---------------- KEYBOARDS ----------------
def language_keyboard():
    kb, row = [], []
    for idx, (code, name) in enumerate(LANGUAGES.items(),1):
        row.append(InlineKeyboardButton(name, callback_data=f"lang_{code}"))
        if idx % 3 == 0: 
            kb.append(row)
            row=[]
    if row: kb.append(row)
    return InlineKeyboardMarkup(kb)

def post_download_keyboard():
    kb = [
        [InlineKeyboardButton("🎵 Another song", callback_data="action_download")],
        [InlineKeyboardButton("🌐 Change language", callback_data="action_language")],
        [InlineKeyboardButton("ℹ️ About Bot", callback_data="action_about")]
    ]
    return InlineKeyboardMarkup(kb)

# ---------------- APPLICATION ----------------
application = Application.builder().token(BOT_TOKEN).build()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await application.initialize()
    await application.start()

    async def set_commands(app):
        commands = [
            BotCommand("start", "Start the bot & choose language"),
            BotCommand("help", "Show instructions"),
            BotCommand("about", "Info about iMusic Beta")
        ]
        await app.bot.set_my_commands(commands)
    asyncio.create_task(set_commands(application))
    asyncio.create_task(song_reminder())

    try:
        await application.bot.set_webhook(WEBHOOK_URL)
        print(f"✅ Webhook set to {WEBHOOK_URL}")
    except Exception as e:
        print(f"❌ Failed to set webhook: {e}")

    yield
    await application.stop()
    await application.shutdown()

app = FastAPI(lifespan=lifespan)

# ---------------- REMINDER ----------------
async def song_reminder():
    while True:
        await asyncio.sleep(1800)
        for uid, data in load_users().items():
            try:
                msg = await application.bot.send_message(
                    chat_id=int(uid),
                    text=f"🎶 Hey {data.get('first_name','')}! What song would you like to listen to now?"
                )
                await asyncio.sleep(300)
                try: await msg.delete()
                except: pass
            except Exception as e:
                logger.error(f"Reminder failed for {uid}: {e}")

# ---------------- HANDLERS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user.id, user.first_name)
    now = datetime.datetime.now().hour
    greeting = "🌅 Good morning" if 5 <= now < 12 else "🌞 Good afternoon" if 12 <= now < 17 else "🌆 Good evening" if 17 <= now < 21 else "🌙 Good night"
    msg = await update.message.reply_text(
        f"{greeting}, *{user.first_name}*!\nSelect your language:",
        reply_markup=language_keyboard(), parse_mode="Markdown"
    )
    await asyncio.sleep(3)
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
        await query.edit_message_text(
            f"✅ Language set to *{LANGUAGES.get(code,'English')}*.\n🎵 Please send the song name you want to listen to:",
            parse_mode="Markdown"
        )
    elif data == "action_download":
        await query.message.reply_text("🎵 Send the song name you want:")
    elif data == "action_language":
        await query.message.reply_text("🌐 Select language:", reply_markup=language_keyboard())
    elif data == "action_about":
        await query.message.reply_text("🎶 iMusic Beta v1.0\nCreated by @hey_arnab02")

# ---------------- SONG SEARCH ----------------
async def search_song(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    register_user(user.id, user.first_name)
    pref = get_user_pref(user.id)
    song_name = update.message.text.strip()
    status_msg = await update.message.reply_text("Downloading 🎵")

    cookie_file = get_current_cookie()
    attempt = 0

    while attempt < 3:
        try:
            ydl_opts = {
                "format":"bestaudio/best",
                "outtmpl": str(CACHE_DIR / "%(title)s.%(ext)s"),
                "noplaylist": True,
                "cookiefile": str(cookie_file) if cookie_file else None,
                "postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}],
                "quiet": True, "no_warnings": True
            }
            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    return ydl.extract_info(f"ytsearch:{song_name}", download=True)

            info = await asyncio.to_thread(download)
            entry = info["entries"][0]
            break
        except yt_dlp.DownloadError as e:
            if "Signature extraction failed" in str(e):
                try:
                    await status_msg.edit_text("⚠️ yt-dlp outdated, updating...")
                    subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
                    await status_msg.edit_text("✅ yt-dlp updated, retrying download...")
                    continue
                except Exception as update_error:
                    await status_msg.edit_text(f"❌ Could not update yt-dlp: {update_error}")
                    return
            attempt += 1
            cookie_file = rotate_cookie()
        except Exception as e:
            attempt += 1
            cookie_file = rotate_cookie()
            logger.error(f"Download error attempt {attempt}: {e}")
    else:
        await status_msg.edit_text("❌ Could not fetch the song after rotating cookies.")
        return

    file_path = CACHE_DIR / f"{entry['title']}.mp3"
    if not file_path.exists():
        await status_msg.edit_text("❌ Download failed.")
        return

    try:
        audio = EasyID3(str(file_path))
        audio["title"] = entry.get("title", song_name)
        audio.save()
        thumbnail = entry.get("thumbnail")
        if thumbnail:
            img_data = requests.get(thumbnail).content
            audio_id3 = ID3(str(file_path))
            audio_id3['APIC'] = APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=img_data)
            audio_id3.save()
    except: 
        pass

    await application.bot.send_audio(
        chat_id=chat_id,
        audio=open(file_path,"rb"),
        title=entry.get("title", song_name),
        performer=entry.get("uploader", "Unknown"),
        reply_markup=post_download_keyboard()
    )
    try: await status_msg.delete()
    except: pass

# ---------------- WEBHOOK ----------------
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        return {"ok": True}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"ok": False, "error": str(e)}

@app.get("/")
async def root(): 
    state = json.loads(COOKIE_STATE.read_text())
    return {"status":"iMusic Beta running 🎵", "cookie_index": state.get("index",0)}

# ---------------- ADD HANDLERS ----------------
application.add_handler(CommandHandler("start", start))
application.add_handler(CallbackQueryHandler(callback_handler))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, search_song))
