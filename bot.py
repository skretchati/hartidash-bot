#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import tempfile
from urllib.parse import urlparse
import asyncio
from datetime import datetime
import shutil
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
import yt_dlp
import qrcode

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Добавьте его в переменные окружения Railway.")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Временная директория
TEMP_DIR = tempfile.gettempdir()

# ===================== ХРАНЕНИЕ ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ =====================
class UserData:
    def __init__(self):
        self.users = {}
        self.data_file = os.path.join(TEMP_DIR, 'users.json')
        self.load_data()
    
    def load_data(self):
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r') as f:
                    self.users = json.load(f)
        except:
            self.users = {}
    
    def save_data(self):
        try:
            with open(self.data_file, 'w') as f:
                json.dump(self.users, f)
        except:
            pass
    
    def get_preference(self, user_id):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 0, 'qr': 0}
        return self.users[user_id].get('pref', 'video')
    
    def set_preference(self, user_id, pref):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': pref, 'downloads': 0, 'qr': 0}
        else:
            self.users[user_id]['pref'] = pref
        self.save_data()
    
    def add_download(self, user_id):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 1, 'qr': 0}
        else:
            self.users[user_id]['downloads'] = self.users[user_id].get('downloads', 0) + 1
        self.save_data()
    
    def add_qr(self, user_id):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 0, 'qr': 1}
        else:
            self.users[user_id]['qr'] = self.users[user_id].get('qr', 0) + 1
        self.save_data()
    
    def get_stats(self, user_id):
        user_id = str(user_id)
        if user_id in self.users:
            return self.users[user_id]
        return {'downloads': 0, 'qr': 0}

# Создаем глобальный экземпляр
user_data = UserData()

# ===================== ФУНКЦИИ СКАЧИВАНИЯ =====================
async def download_video(url, mode='video'):
    """
    Скачивает видео/аудио с любой платформы
    mode: 'video', 'audio', 'all'
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(TEMP_DIR, f"harti_{timestamp}")
        os.makedirs(out_path, exist_ok=True)
        
        files = []
        logger.info(f"Скачиваю {mode} с {url}")
        
        if mode == 'video':
            # Только видео
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'outtmpl': os.path.join(out_path, '%(title)s.%(ext)s'),
                'format': 'best[ext=mp4]/best',
                'nocheckcertificate': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
        
        elif mode == 'audio':
            # Только аудио
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'outtmpl': os.path.join(out_path, '%(title)s.%(ext)s'),
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'nocheckcertificate': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
        
        elif mode == 'all':
            # Сначала видео
            video_opts = {
                'quiet': True,
                'no_warnings': True,
                'outtmpl': os.path.join(out_path, 'video.%(ext)s'),
                'format': 'best[ext=mp4]/best',
                'nocheckcertificate': True,
            }
            
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
            
            # Потом аудио
            audio_opts = {
                'quiet': True,
                'no_warnings': True,
                'outtmpl': os.path.join(out_path, 'audio.%(ext)s'),
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'nocheckcertificate': True,
            }
            
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
            
            # Пробуем скачать обложку
            try:
                thumb_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'outtmpl': os.path.join(out_path, 'thumbnail.%(ext)s'),
                    'format': 'best',
                    'writethumbnail': True,
                    'skip_download': True,
                    'nocheckcertificate': True,
                }
                
                with yt_dlp.YoutubeDL(thumb_opts) as ydl:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: ydl.download([url])
                    )
            except Exception as e:
                logger.info(f"Обложка не скачалась: {e}")
        
        # Собираем все скачанные файлы
        if os.path.exists(out_path):
            for f in os.listdir(out_path):
                file_path = os.path.join(out_path, f)
                files.append(file_path)
                logger.info(f"Скачан файл: {f}")
        
        return files, out_path
        
    except Exception as e:
        logger.error(f"ОШИБКА СКАЧИВАНИЯ: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None, None

# ===================== ФУНКЦИЯ СОЗДАНИЯ QR-КОДА =====================
def make_qr(text):
    """Создает QR-код из текста"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(TEMP_DIR, f"qr_{timestamp}.png")
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(text)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(path)
        
        return path
    except Exception as e:
        logger.error(f"Ошибка создания QR: {e}")
        return None

# ===================== ФУНКЦИИ ДЛЯ МЕНЮ =====================
def get_main_menu(user_id):
    """Создает главное меню"""
    pref = user_data.get_preference(user_id)
    
    # Кнопки с текстом и эмодзи для форматов
    video_text = "🎥 Видео"
    audio_text = "🎵 Аудио" 
    all_text = "📦 Всё"
    
    # Добавляем галочку к текущему выбору
    if pref == 'video':
        video_text = "🎥 Видео ✅"
    elif pref == 'audio':
        audio_text = "🎵 Аудио ✅"
    elif pref == 'all':
        all_text = "📦 Всё ✅"
    
    # Создаем клавиатуру с кнопками
    keyboard = [
        # Первая строка - форматы
        [
            InlineKeyboardButton(video_text, callback_data="set_video"),
            InlineKeyboardButton(audio_text, callback_data="set_audio"),
            InlineKeyboardButton(all_text, callback_data="set_all")
        ],
        # Вторая строка - QR и статистика
        [
            InlineKeyboardButton("📱 QR-код", callback_data="menu_qr"),
            InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")
        ],
        # Третья строка - помощь
        [
            InlineKeyboardButton("❓ Помощь", callback_data="menu_help")
        ]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def get_back_button():
    """Создает кнопку возврата в меню"""
    keyboard = [[InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]]
    return InlineKeyboardMarkup(keyboard)

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    welcome_text = (
        f"⚡ *HartiDash — твой быстрый загрузчик!*\n\n"
        f"👋 Привет, {first_name}!\n\n"
        f"📌 *Как пользоваться:*\n"
        f"• Отправь ссылку на видео\n"
        f"• Я скачаю в выбранном формате\n"
        f"• /qr текст — создать QR-код\n"
        f"• Меню — изменить формат\n\n"
        f"🚀 *Погнали!*"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=get_main_menu(user_id),
        parse_mode='Markdown'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📖 *HartiDash — Помощь*\n\n"
        "*🎥 Форматы:*\n"
        "• Видео — скачать только видео (MP4)\n"
        "• Аудио — скачать только аудио (MP3)\n"
        "• Всё — скачать видео + аудио + обложку\n\n"
        "*📱 QR-коды:*\n"
        "• /qr текст — создать QR-код\n\n"
        "*🌐 Поддерживаемые сайты:*\n"
        "✅ TikTok, YouTube, Instagram, Facebook, Twitter/X\n\n"
        "*📊 Статистика:*\n"
        "• /stats — посмотреть свою статистику"
    )
    
    await update.message.reply_text(
        help_text,
        reply_markup=get_back_button(),
        parse_mode='Markdown'
    )

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /qr"""
    user_id = update.effective_user.id
    
    if not context.args:
        await update.message.reply_text(
            "❓ *Как использовать:*\n"
            "Отправь: `/qr текст или ссылка`\n"
            "Например: `/qr https://telegram.org`",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return
    
    text = ' '.join(context.args)
    status_msg = await update.message.reply_text("🔄 *Создаю QR-код...*", parse_mode='Markdown')
    
    path = make_qr(text)
    
    if path:
        with open(path, 'rb') as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=f"✅ *QR-код готов!*",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
        os.unlink(path)
        await status_msg.delete()
        user_data.add_qr(user_id)
    else:
        await status_msg.edit_text(
            "❌ *Не удалось создать QR-код*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику пользователя"""
    user_id = update.effective_user.id
    stats = user_data.get_stats(user_id)
    
    stats_text = (
        f"📊 *Твоя статистика в HartiDash*\n\n"
        f"🎥 Скачано: *{stats.get('downloads', 0)}*\n"
        f"📱 QR-кодов: *{stats.get('qr', 0)}*"
    )
    
    await update.message.reply_text(
        stats_text,
        reply_markup=get_back_button(),
        parse_mode='Markdown'
    )

# ===================== ОБРАБОТЧИК СООБЩЕНИЙ =====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик всех сообщений"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # Проверяем, похоже ли на ссылку
    if any(x in text.lower() for x in ['.com', '.ru', 'http', 'www', 'youtu', 'tiktok', 'instagram']):
        # Получаем предпочтение пользователя
        pref = user_data.get_preference(user_id)
        
        # Эмодзи для разных режимов
        emoji = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
        
        status_msg = await update.message.reply_text(
            f"{emoji[pref]} *Скачиваю...*",
            parse_mode='Markdown'
        )
        
        # Скачиваем
        files, temp_dir = await download_video(text, pref)
        
        if files and len(files) > 0:
            user_data.add_download(user_id)
            sent_count = 0
            
            # Отправляем все файлы
            for file_path in files:
                try:
                    if file_path.endswith('.mp4'):
                        with open(file_path, 'rb') as f:
                            await update.message.reply_video(f)
                        sent_count += 1
                    elif file_path.endswith('.mp3'):
                        with open(file_path, 'rb') as f:
                            await update.message.reply_audio(f)
                        sent_count += 1
                    elif file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        with open(file_path, 'rb') as f:
                            await update.message.reply_photo(f)
                        sent_count += 1
                except Exception as e:
                    logger.error(f"Ошибка отправки файла {file_path}: {e}")
            
            await status_msg.delete()
            
            if sent_count == 0:
                await update.message.reply_text(
                    "❌ *Не удалось отправить файлы*",
                    reply_markup=get_back_button(),
                    parse_mode='Markdown'
                )
            
            # Очищаем временную папку
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            await status_msg.edit_text(
                "❌ *Не удалось скачать файлы*\n"
                "Возможно, ссылка недействительна или видео защищено",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
    
    elif text.lower().startswith('/qr'):
        # Обработка QR через команду в сообщении
        qr_text = text[3:].strip()
        if qr_text:
            context.args = [qr_text]
            await qr_command(update, context)
        else:
            await update.message.reply_text(
                "❓ *Отправь текст после /qr*",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
    
    else:
        # Если не ссылка, предлагаем создать QR-код
        keyboard = [
            [
                InlineKeyboardButton("📱 Создать QR-код", callback_data=f"qr_{text}"),
                InlineKeyboardButton("❌ Отмена", callback_data="cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"🤔 *Это не похоже на ссылку*\n\n"
            f"Создать QR-код из этого текста?\n\n"
            f"`{text[:100]}{'...' if len(text) > 100 else ''}`",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# ===================== ОБРАБОТЧИК КНОПОК =====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "back_to_menu":
        await query.edit_message_text(
            "⚡ *Главное меню HartiDash*",
            reply_markup=get_main_menu(user_id),
            parse_mode='Markdown'
        )
        return
    
    elif data.startswith("set_"):
        pref = data[4:]  # video, audio, all
        user_data.set_preference(user_id, pref)
        
        # Названия для сообщения
        names = {'video': '🎥 VIDEO', 'audio': '🎵 AUDIO', 'all': '📦 ALL'}
        
        await query.edit_message_text(
            f"✅ *Формат изменен на {names[pref]}*\n\n"
            f"Теперь все ссылки буду скачивать в этом формате!\n\n"
            f"👉 Отправляй ссылку",
            reply_markup=get_main_menu(user_id),
            parse_mode='Markdown'
        )
        return
    
    elif data == "menu_qr":
        await query.edit_message_text(
            "📱 *Создание QR-кода*\n\n"
            "Используй команду:\n"
            "`/qr текст или ссылка`\n\n"
            "*Примеры:*\n"
            "• `/qr https://telegram.org`\n"
            "• `/qr Привет, мир!`",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return
    
    elif data == "menu_stats":
        stats = user_data.get_stats(user_id)
        await query.edit_message_text(
            f"📊 *Твоя статистика*\n\n"
            f"🎥 Скачано: *{stats.get('downloads', 0)}*\n"
            f"📱 QR-кодов: *{stats.get('qr', 0)}*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return
    
    elif data == "menu_help":
        help_text = (
            "📖 *HartiDash — Помощь*\n\n"
            "*🎥 Форматы:*\n"
            "• Видео — скачать только видео (MP4)\n"
            "• Аудио — скачать только аудио (MP3)\n"
            "• Всё — скачать видео + аудио + обложку\n\n"
            "*📱 QR-коды:*\n"
            "• /qr текст — создать QR-код\n\n"
            "*🌐 Поддерживаемые сайты:*\n"
            "✅ TikTok, YouTube, Instagram, Facebook, Twitter/X\n\n"
            "*📊 Статистика:*\n"
            "• /stats — посмотреть свою статистику"
        )
        await query.edit_message_text(
            help_text,
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return
    
    elif data.startswith("qr_"):
        qr_text = data[3:]
        await query.edit_message_text("🔄 *Создаю QR-код...*", parse_mode='Markdown')
        
        path = make_qr(qr_text)
        if path:
            with open(path, 'rb') as photo:
                await query.message.reply_photo(
                    photo=photo,
                    caption=f"✅ *QR-код готов!*",
                    reply_markup=get_back_button(),
                    parse_mode='Markdown'
                )
            os.unlink(path)
            await query.delete_message()
            user_data.add_qr(user_id)
        else:
            await query.edit_message_text(
                "❌ *Ошибка создания QR-кода*",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
        return
    
    elif data == "cancel":
        await query.edit_message_text(
            "❌ *Действие отменено*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return

# ===================== ЗАПУСК БОТА =====================
def main():
    """Запуск бота"""
    print("⚡ Запуск HartiDash...")
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("qr", qr_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Определяем режим запуска
    port = int(os.environ.get('PORT', 8080))
    railway_url = os.environ.get('RAILWAY_STATIC_URL')
    
    if railway_url:
        # Режим вебхуков для Railway
        webhook_url = f"https://{railway_url}/webhook"
        print(f"🌐 Устанавливаем вебхук на {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url
        )
    else:
        # Режим polling для локальной разработки
        print("🔄 Запуск в режиме polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
