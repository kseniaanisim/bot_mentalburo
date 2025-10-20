import logging
import os
from datetime import timedelta
from typing import Dict, Tuple

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Загрузка токена и ID из .env
load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not API_TOKEN or not ADMIN_CHAT_ID:
    raise RuntimeError("Заполни BOT_TOKEN и ADMIN_CHAT_ID в .env")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("anon-bot")

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Кэш для альбомов
seen_media_groups: set[Tuple[int, str]] = set()
# Сопоставление: admin_id -> user_id
reply_targets: Dict[int, int] = {}

# ─────────────────────────── Кнопки ───────────────────────────
def reply_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Ответить", callback_data=f"reply:{user_id}")],
        [InlineKeyboardButton(text="Отмена", callback_data="cancel")]
    ])

def admin_caption(prefix: str, text: str) -> str:
    return f"{prefix}\n\n{text}" if text else prefix

# ─────────────────────────── Старт ───────────────────────────
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "Привет! 👋 Здесь можно анонимно задать вопрос.\n\n"
        "Просто отправь сообщение — я передам его администратору.\n"
        "В ответ ты получишь «Сообщение получено 💌»."
    )

# ─────────────────────────── Анонимные сообщения ───────────────────────────
@dp.message(F.media_group_id)
async def on_album(message: types.Message):
    key = (message.chat.id, str(message.media_group_id))
    if key in seen_media_groups:
        return
    seen_media_groups.add(key)
    await message.answer("Сообщение получено 💌")

    ts = (message.date + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    header = f"📩 Новое анонимное сообщение (альбом)\n🕓 {ts}"
    user_id = message.from_user.id
    text = message.caption or "📎 Пользователь отправил альбом"

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            admin_caption(header, text),
            reply_markup=reply_keyboard(user_id)
        )
    except Exception as e:
        log.exception(f"Ошибка при отправке альбома в админ-чат: {e}")

@dp.message()
async def on_any_message(message: types.Message):
    await message.answer("Сообщение получено 💌")

    ts = (message.date + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
    header = f"📩 Новое анонимное сообщение\n🕓 {ts}"
    user_id = message.from_user.id
    text = message.text or message.caption or "📎 Пользователь отправил файл"

    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            admin_caption(header, text),
            reply_markup=reply_keyboard(user_id)
        )
    except Exception as e:
        log.exception(f"Ошибка при отправке сообщения в админ-чат: {e}")

# ─────────────────────────── Ответы администратора ───────────────────────────
@dp.callback_query(F.data.startswith("reply:"))
async def cb_reply(callback: types.CallbackQuery):
    if callback.message.chat.id != ADMIN_CHAT_ID:
        await callback.answer("Недоступно здесь.", show_alert=True)
        return

    target_user_id = int(callback.data.split(":", 1)[1])
    reply_targets[callback.from_user.id] = target_user_id
    await callback.message.reply("Напиши ответ пользователю ниже одним сообщением.\n«Отмена» — чтобы выйти.")
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
        elif message.voice:
            await bot.send_voice(target_user, message.voice.file_id,
                                 caption=f"Ответ от админа:\n\n{message.caption or ''}")
            sent_ok = True
        elif message.sticker:
            await bot.send_sticker(target_user, message.sticker.file_id)
            sent_ok = True
    except Exception as e:
        log.exception(f"Не удалось отправить ответ пользователю {target_user}: {e}")
        sent_ok = False

    if sent_ok:
        await message.reply("✅ Отправлено пользователю.")
        reply_targets.pop(admin_id, None)
    else:
        await message.reply("❌ Ошибка при отправке. Попробуй ещё раз.")

# ─────────────────────────── Запуск ───────────────────────────
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
