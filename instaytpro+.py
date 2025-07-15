import os
import uuid
import html
import logging
import asyncio
import instaloader
import yt_dlp

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    FSInputFile
)

# 🔧 Config
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SESSION_FILE = "/home/robber/.config/instaloader/session-mirjalolinsta"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# 📁 Papkalar
YT_DOWNLOAD_FOLDER = "yt_downloads"
INSTA_DOWNLOAD_FOLDER = "insta_downloads"
for folder in [YT_DOWNLOAD_FOLDER, INSTA_DOWNLOAD_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# 📸 Instaloader sessiya
insta_loader = instaloader.Instaloader(
    dirname_pattern=INSTA_DOWNLOAD_FOLDER,
    save_metadata=False,
    download_video_thumbnails=False
)
if os.path.exists(SESSION_FILE):
    insta_loader.load_session_from_file('mirjalolinsta', SESSION_FILE)
else:
    raise Exception("❌ Instagram session fayl topilmadi.")

# 🔐 Log
logging.basicConfig(level=logging.INFO)

# ========== YouTube formatlarini olish ==========
cache = {}

def get_formats(url: str):
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = []
        for f in info['formats']:
            if f.get('format_id'):
                fmt_id = f['format_id']
                ext = f.get('ext')
                height = f.get('height')
                fps = f.get('fps')
                filesize = f.get('filesize')
                note = f.get('format_note') or ''
                resolution = f"{height}p" if height else 'unknown'
                fps_info = f"{fps}fps" if fps else ''
                size = f"{round(filesize / 1024 / 1024, 2)}MB" if filesize else 'Unknown size'
                desc = f"{ext} | {resolution} {fps_info} | {size} {note}"
                formats.append((fmt_id, desc))
        return info['title'], formats

# ========== Progress update ==========
async def progress_callback(bot_message: types.Message, progress: float):
    percent = int(progress * 100)
    progress_bar = "▓" * (percent // 10) + "░" * (10 - percent // 10)
    text = f"⏳ Yuklanmoqda: [{progress_bar}] {percent}%"
    try:
        await bot_message.edit_text(text)
    except Exception:
        pass

async def finished_callback(bot_message: types.Message):
    try:
        await bot_message.edit_text("✅ Yuklash tugadi, fayl tayyor!")
    except Exception:
        pass

def yt_progress_hook(bot_message: types.Message, loop):
    def hook(d):
        if d['status'] == 'downloading':
            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded_bytes = d.get('downloaded_bytes', 0)
            if total_bytes > 0:
                progress = downloaded_bytes / total_bytes
                loop.call_soon_threadsafe(asyncio.create_task, progress_callback(bot_message, progress))
        elif d['status'] == 'finished':
            loop.call_soon_threadsafe(asyncio.create_task, finished_callback(bot_message))
    return hook

# ========== YouTube yuklab olish va yuborish ==========
async def download_and_send(chat_id: int, url: str, format_id: str, bot_message: types.Message):
    loop = asyncio.get_running_loop()

    def download():
        ydl_opts = {
            'format': format_id + "+bestaudio",
            'outtmpl': os.path.join(YT_DOWNLOAD_FOLDER, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
            'ffmpeg_location': '/home/robber/ffmpeg-7.0.2-amd64-static/ffmpeg',
            'progress_hooks': [yt_progress_hook(bot_message, loop)],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return info, ydl.prepare_filename(info)

    try:
        info, file_path = await loop.run_in_executor(None, download)
    except Exception as e:
        await bot.send_message(chat_id, f"⚠️ Yuklab olishda xatolik: {e}")
        return

    if file_path.startswith("__ERROR__::"):
        await bot.send_message(chat_id, f"⚠️ Yuklab olishda xatolik: {file_path[10:]}")
        return

    try:
        file_size = os.path.getsize(file_path)
        input_file = FSInputFile(file_path)

        if file_size > 2048 * 1024 * 1024:
            await bot.send_message(chat_id, "❌ Fayl juda katta (2048MB dan katta).")
            os.remove(file_path)
            return

        # 📄 Caption
        title = info.get('title', 'Nomaʼlum')
        uploader = info.get('uploader', 'Nomaʼlum')
        view_count = info.get('view_count', 0)
        like_count = info.get('like_count', 0)
        resolution = info.get('height', '???')
        ext = info.get('ext', '???')
        size_mb = round(file_size / 1024 / 1024, 2)
        duration = info.get('duration')
        duration_text = f"{duration//60} daq {duration%60} soniya" if duration else "???"

        caption = (
            f"🎬 <b>{html.escape(title)}</b>\n"
            f"📺 Kanal: <b>{html.escape(uploader)}</b>\n"
            f"👁 Ko‘rishlar: <b>{view_count:,}</b>\n"
            f"👍 Layklar: <b>{like_count:,}</b>\n"
            f"⏱ Davomiyligi: {duration_text}\n"
            f"📁 Format: <code>{ext}</code>\n"
            f"📏 Sifat: <b>{resolution}p</b>\n"
            f"💾 Hajmi: {size_mb} MB"
        )

        await bot.send_video(chat_id, input_file, caption=caption, parse_mode="HTML")
        os.remove(file_path)
    except Exception as e:
        await bot.send_message(chat_id, f"⚠️ Fayl yuborishda xatolik: {e}")
        if os.path.exists(file_path):
            os.remove(file_path)

# ========== Instagram yuklash ==========
def download_instagram(url):
    shortcode = None
    if "/p/" in url:
        shortcode = url.split("/p/")[1].split("/")[0]
    elif "/reel/" in url:
        shortcode = url.split("/reel/")[1].split("/")[0]
    elif "/tv/" in url:
        shortcode = url.split("/tv/")[1].split("/")[0]
    else:
        return None, "❌ Instagram link noto'g'ri yoki qo'llab-quvvatlanmaydi."

    try:
        post = instaloader.Post.from_shortcode(insta_loader.context, shortcode)
        insta_loader.download_post(post, target=INSTA_DOWNLOAD_FOLDER)
        files = os.listdir(INSTA_DOWNLOAD_FOLDER)
        media_files = [f'{INSTA_DOWNLOAD_FOLDER}/{f}' for f in files if f.endswith(('.jpg', '.mp4'))]
        return media_files, None
    except Exception as e:
        return None, f"❌ Instagram yuklashda xatolik: {e}"

# ========== Bot komandalar ==========
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Salom!\n\n"
        "Men YouTube va Instagram videolarini yuklab bera olaman.\n"
        "YouTube uchun link yuboring, keyin formatni tanlang.\n"
        "Instagram uchun post/reel/tv link yuboring.\n\n"
        "Masalan:\n"
        "YouTube: https://youtu.be/XXXXXX\n"
        "Instagram: https://www.instagram.com/p/XXXXXX/"
    )

@dp.message()
async def message_handler(message: types.Message):
    url = message.text.strip()

    # YouTube
    if "youtube.com" in url or "youtu.be" in url:
        loading_msg = await message.answer("🎬 YouTube video formatlari olinmoqda...")
        try:
            title, formats = await asyncio.to_thread(get_formats, url)
            if not formats:
                await loading_msg.edit_text("❌ Bu videoda yuklab olish uchun format topilmadi.")
                return

            unique_id = str(uuid.uuid4())
            cache[unique_id] = {'url': url}

            buttons = []
            row = []
            for i, (fmt_id, desc) in enumerate(formats, start=1):
                callback = f"yt:{unique_id}:{fmt_id}"
                row.append(InlineKeyboardButton(text=desc[:64], callback_data=callback))
                if i % 2 == 0:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            escaped_title = html.escape(title)

            await loading_msg.edit_text(
                f"🎥 <b>{escaped_title}</b> uchun format tanlang:",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            await loading_msg.edit_text(f"⚠️ YouTube uchun xatolik yuz berdi: {e}")
        return

    # Instagram
    if "instagram.com" in url:
        loading_msg = await message.answer("📥 Instagram media yuklanmoqda...")
        files, error = await asyncio.to_thread(download_instagram, url)

        if error:
            await loading_msg.edit_text(error)
            return
        if not files:
            await loading_msg.edit_text("❌ Instagram media topilmadi.")
            return

        try:
            await loading_msg.delete()
        except:
            pass

        for file in files:
            media = FSInputFile(file)
            if file.endswith('.mp4'):
                await message.answer_video(media)
            else:
                await message.answer_photo(media)

        for file in files:
            os.remove(file)
        return

    await message.answer("❌ Iltimos, haqiqiy YouTube yoki Instagram linkini yuboring.")

# ========== Callback handler ==========
@dp.callback_query(lambda c: c.data and c.data.startswith("yt:"))
async def yt_download_callback(callback_query: CallbackQuery):
    try:
        _, unique_id, format_id = callback_query.data.split(":", 2)
    except ValueError:
        await callback_query.answer("❌ Noto‘g‘ri formatda ma'lumot.", show_alert=True)
        return

    if unique_id not in cache:
        await callback_query.answer("❌ Sessiya muddati o'tgan. Iltimos, qaytadan yuboring.", show_alert=True)
        return

    url = cache[unique_id]['url']
    chat_id = callback_query.message.chat.id  # 👈 chat ID olamiz
    await callback_query.answer("⏳ Yuklab olish boshlandi...")

    bot_message = await bot.send_message(chat_id, "⏳ Yuklanmoqda: [░░░░░░░░░░] 0%")

    asyncio.create_task(
        download_and_send(chat_id, url, format_id, bot_message)
    )

    try:
        await callback_query.message.edit_text("⏳ Yuklab olish boshlandi...")
    except:
        pass

# ========== Botni ishga tushirish ==========
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())