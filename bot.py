import asyncio
import logging
import os
from datetime import timedelta
from typing import Dict, List, Tuple

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
    InputMediaAnimation,
)

# ─────────────────────── env ───────────────────────
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
if not API_TOKEN or not ADMIN_CHAT_ID:
    raise RuntimeError("Заполни BOT_TOKEN и ADMIN_CHAT_ID в .env")

# ───────────────────── logging ─────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot_mentalburo")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# admin_id -> user_id (кому админ сейчас отвечает)
reply_targets: Dict[int, int] = {}

# Копим части альбомов и шлём одним блоком
album_buffer: Dict[Tuple[int, str], List] = {}
album_timer_running: Dict[Tuple[int, str], bool] = {}
ALBUM_FLUSH_DELAY = 1.4  # сек

# ────────────────── UI ───────────────────
def reply_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить", callback_data=f"reply:{user_id}")],
            [InlineKeyboardButton(text="Отмена", callback_data="cancel")],
        ]
    )

def admin_caption(prefix: str, text: str) -> str:
    return f"{prefix}\n\n{text}" if text else prefix

# ─────────────────── Команды ─────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! Здесь можно анонимно задать вопрос как всей нашей команде, так и отдельным специалистам 🤝\n\n"
        "Просто отправьте сообщение, и мы сделаем всё возможное (в рамках наших СДВГ и плотных графиков работы и обучения), "
        "чтобы ответить ✍️ Ответы мы будем стараться выкладывать в канал, но также можем ответить в самом боте."
    )

@dp.message(Command("here"))
async def cmd_here(message: types.Message):
    await message.answer(f"chat id: {message.chat.id}")

# ───────────── Альбомы (media_group) ─────────────
@dp.message(F.media_group_id)
async def on_album_part(message: types.Message):
    await message.answer("Сообщение получено, спасибо! 🌄")

    key = (message.chat.id, str(message.media_group_id))
    media_list = album_buffer.setdefault(key, [])

    caption = message.caption or ""
    if message.photo:
        media_list.append(InputMediaPhoto(media=message.photo[-1].file_id, caption=caption if not media_list else None))
    elif message.video:
        media_list.append(InputMediaVideo(media=message.video.file_id, caption=caption if not media_list else None))
    elif message.document:
        media_list.append(InputMediaDocument(media=message.document.file_id, caption=caption if not media_list else None))
    elif message.audio:
        media_list.append(InputMediaAudio(media=message.audio.file_id, caption=caption if not media_list else None))
    elif message.animation:
        media_list.append(InputMediaAnimation(media=message.animation.file_id, caption=caption if not media_list else None))

    if not album_timer_running.get(key):
        album_timer_running[key] = True
        asyncio.create_task(flush_album_later(key, message))

async def flush_album_later(key: Tuple[int, str], any_message: types.Message):
    await asyncio.sleep(ALBUM_FLUSH_DELAY)

    media = album_buffer.pop(key, [])
    album_timer_running.pop(key, None)
    if not media:
        return

    ts = (any_message.date + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    header = f"📩 Новое анонимное сообщение (альбом)\n🕓 {ts}"
    user_id = any_message.from_user.id

    if not getattr(media[0], "caption", None):
        media[0].caption = "📎 Пользователь отправил альбом"

    try:
        await bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=media)
        await bot.send_message(
            ADMIN_CHAT_ID,
            admin_caption(header, media[0].caption or "📎 Пользователь отправил альбом"),
            reply_markup=reply_keyboard(user_id),
        )
    except Exception as e:
        log.exception(f"Ошибка при отправке альбома в админ-чат: {e}")

# ───────────── Одиночные сообщения (copy_message) ─────────────
@dp.message()
async def on_any_message(message: types.Message):
    # подтверждение пользователю
    await message.answer("Сообщение получено, спасибо! 🌄")

    # 1) Копируем ОРИГИНАЛЬНОЕ сообщение как есть (любой тип вложений)
    try:
        await bot.copy_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
    except Exception as e:
        log.exception(f"copy_message провалился: {e}")

    # 2) Отдельно отправляем заголовок + кнопку «Ответить»
    ts = (message.date + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    header = f"📩 Новое анонимное сообщение\n🕓 {ts}"
    user_id = message.from_user.id
    text = message.text or message.caption or "📎 Пользователь отправил файл"

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            admin_caption(header, text),
            reply_markup=reply_keyboard(user_id),
        )
    except Exception as e:
        log.exception(f"Ошибка при отправке сообщения в админ-чат: {e}")

# ───────────── Кнопки и ответы администратора ─────────────
@dp.callback_query(F.data.startswith("reply:"))
async def cb_reply(callback: types.CallbackQuery):
    if callback.message.chat.id != ADMIN_CHAT_ID:
        await callback.answer("Недоступно здесь.", show_alert=True)
        return

    target_user_id = int(callback.data.split(":", 1)[1])
    reply_targets[callback.from_user.id] = target_user_id
    await callback.message.reply("Напиши ответ пользователю ниже одним сообщением. «Отмена» — чтобы выйти.")
    await callback.answer("Режим ответа включён.")

@dp.callback_query(F.data == "cancel")
async def cb_cancel(callback: types.CallbackQuery):
    reply_targets.pop(callback.from_user.id, None)
    await callback.answer("Отмена.")

@dp.message(F.chat.id == ADMIN_CHAT_ID)
async def on_admin_chat_message(message: types.Message):
    admin_id = message.from_user.id
    target_user = reply_targets.get(admin_id)
    if not target_user:
        return

    sent_ok = False
    try:
        if message.text and not message.caption:
            await bot.send_message(target_user, f"Ответ от админа:\n\n{message.text}")
            sent_ok = True
        elif message.photo:
            await bot.send_photo(target_user, message.photo[-1].file_id,
                                 caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.video:
            await bot.send_video(target_user, message.video.file_id,
                                 caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.document:
            await bot.send_document(target_user, message.document.file_id,
                                    caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.audio:
            await bot.send_audio(target_user, message.audio.file_id,
                                 caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.voice:
            await bot.send_voice(target_user, message.voice.file_id,
                                 caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.animation:
            await bot.send_animation(target_user, message.animation.file_id,
                                     caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.sticker:
            await bot.send_sticker(target_user, message.sticker.file_id)
            sent_ok = True
        elif message.video_note:
            await bot.send_video_note(target_user, message.video_note.file_id)
            sent_ok = True
    except Exception as e:
        log.exception(f"Не удалось отправить ответ пользователю {target_user}: {e}")
        sent_ok = False

    if sent_ok:
        await message.reply("✅ Отправлено пользователю.")
        reply_targets.pop(admin_id, None)
    else:
        await message.reply("❌ Ошибка при отправке. Попробуй ещё раз.")

# ─────────────────────── запуск ───────────────────────
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
