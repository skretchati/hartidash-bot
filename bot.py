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
from typing import Dict, Optional

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
    """Класс для хранения данных пользователей"""
    
    def __init__(self):
        self.users: Dict[int, Dict] = {}
        self.data_file = os.path.join(TEMP_DIR, 'hartidash_users.json')
        self.load_data()
    
    def load_data(self):
        """Загружает данные пользователей из файла"""
        try:
            if os.path.exists(self.data_file):
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    self.users = json.load(f)
                self.users = {int(k): v for k, v in self.users.items()}
                logger.info(f"📊 Загружены данные для {len(self.users)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка загрузки данных: {e}")
            self.users = {}
    
    def save_data(self):
        """Сохраняет данные пользователей в файл"""
        try:
            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(self.users, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    def get_user_preference(self, user_id: int) -> str:
        """Возвращает предпочтение пользователя (по умолчанию 'video')"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {'preference': 'video', 'downloads': 0, 'qr_codes': 0}
        return self.users[user_id_str].get('preference', 'video')
    
    def set_user_preference(self, user_id: int, preference: str):
        """Устанавливает предпочтение пользователя"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {'preference': preference, 'downloads': 0, 'qr_codes': 0}
        else:
            self.users[user_id_str]['preference'] = preference
        self.users[user_id_str]['last_seen'] = datetime.now().isoformat()
        self.save_data()
    
    def increment_downloads(self, user_id: int):
        """Увеличивает счетчик скачиваний"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {'preference': 'video', 'downloads': 1, 'qr_codes': 0}
        else:
            self.users[user_id_str]['downloads'] = self.users[user_id_str].get('downloads', 0) + 1
        self.save_data()
    
    def increment_qr(self, user_id: int):
        """Увеличивает счетчик QR-кодов"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {'preference': 'video', 'downloads': 0, 'qr_codes': 1}
        else:
            self.users[user_id_str]['qr_codes'] = self.users[user_id_str].get('qr_codes', 0) + 1
        self.save_data()
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Возвращает статистику пользователя"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            return {'downloads': 0, 'qr_codes': 0}
        return {
            'downloads': self.users[user_id_str].get('downloads', 0),
            'qr_codes': self.users[user_id_str].get('qr_codes', 0)
        }

# Создаем глобальный экземпляр хранилища пользователей
user_storage = UserData()

# ===================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====================
def is_valid_url(url: str) -> bool:
    """Проверяет, является ли строка валидным URL"""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except:
        return False

def get_platform_name(url: str) -> str:
    """Определяет платформу по URL"""
    url_lower = url.lower()
    if 'tiktok.com' in url_lower:
        return 'TikTok'
    elif 'instagram.com' in url_lower:
        return 'Instagram'
    elif 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'YouTube'
    elif 'facebook.com' in url_lower or 'fb.watch' in url_lower:
        return 'Facebook'
    elif 'twitter.com' in url_lower or 'x.com' in url_lower:
        return 'Twitter/X'
    elif 'reddit.com' in url_lower:
        return 'Reddit'
    elif 'pinterest.com' in url_lower:
        return 'Pinterest'
    else:
        return 'видеоплатформы'

def clean_filename(filename: str) -> str:
    """Очищает имя файла от недопустимых символов"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    return filename

def format_size(size_bytes: int) -> str:
    """Форматирует размер файла"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

# ===================== ФУНКЦИИ СКАЧИВАНИЯ =====================
async def download_video(url: str, download_type: str = 'video'):
    """
    Скачивает видео с YouTube, TikTok и других платформ
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_path = os.path.join(TEMP_DIR, f"hartidash_{timestamp}")
        os.makedirs(base_path, exist_ok=True)
        
        logger.info(f"Скачиваю {download_type} с {url}")
        
        # Базовые настройки для yt-dlp
        ydl_opts = {
            'outtmpl': os.path.join(base_path, '%(title)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }
        
        # Настройки в зависимости от типа загрузки
        if download_type == 'video':
            ydl_opts['format'] = 'best[height<=720]'  # Ограничим до 720p для скорости
        elif download_type == 'audio':
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif download_type == 'all':
            ydl_opts['format'] = 'best[height<=720]'
            ydl_opts['writethumbnail'] = True
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        # Скачиваем
        loop = asyncio.get_event_loop()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await loop.run_in_executor(None, lambda: ydl.download([url]))
        
        # Собираем все скачанные файлы
        downloaded_files = []
        for file in os.listdir(base_path):
            file_path = os.path.join(base_path, file)
            downloaded_files.append(file_path)
            logger.info(f"Скачан файл: {file}")
        
        return downloaded_files, base_path
        
    except Exception as e:
        logger.error(f"Ошибка при скачивании: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None, None

# ===================== ФУНКЦИЯ СОЗДАНИЯ QR-КОДА =====================
def create_qr_code(data: str):
    """Создает QR-код из данных"""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        qr_path = os.path.join(TEMP_DIR, f"hartidash_qr_{timestamp}.png")
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_path)
        
        return qr_path
        
    except Exception as e:
        logger.error(f"Ошибка создания QR-кода: {e}")
        return None

# ===================== ФУНКЦИИ ДЛЯ СОЗДАНИЯ МЕНЮ =====================
def get_main_menu(user_id: int) -> InlineKeyboardMarkup:
    """Создает главное меню с учетом предпочтений пользователя"""
    preference = user_storage.get_user_preference(user_id)
    
    # Эмодзи для разных типов
    icons = {
        'video': '🎥',
        'audio': '🎵',
        'all': '📦'
    }
    
    keyboard = [
        [
            InlineKeyboardButton(f"{icons['video']} Видео" + (" ✅" if preference == 'video' else ""), callback_data="set_video"),
            InlineKeyboardButton(f"{icons['audio']} Аудио" + (" ✅" if preference == 'audio' else ""), callback_data="set_audio"),
            InlineKeyboardButton(f"{icons['all']} Всё" + (" ✅" if preference == 'all' else ""), callback_data="set_all")
        ],
        [
            InlineKeyboardButton("📱 QR-код", callback_data="menu_qr"),
            InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")
        ],
        [
            InlineKeyboardButton("❓ Помощь", callback_data="menu_help")
        ]
    ]
    
    # Добавляем подсказку о текущем выборе
    keyboard.insert(0, [InlineKeyboardButton(
        f"⚡ Твой выбор: {icons[preference]} {preference.upper()}", 
        callback_data="noop"
    )])
    
    return InlineKeyboardMarkup(keyboard)

def get_back_button() -> InlineKeyboardMarkup:
    """Создает кнопку возврата в меню"""
    keyboard = [[InlineKeyboardButton("◀️ В меню", callback_data="back_to_menu")]]
    return InlineKeyboardMarkup(keyboard)

# ===================== ОБРАБОТЧИКИ КОМАНД =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    preference = user_storage.get_user_preference(user_id)
    
    icons = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
    
    welcome_text = (
        f"⚡ *HartiDash — твой быстрый загрузчик!*\n\n"
        f"👋 Привет, {update.effective_user.first_name}!\n\n"
        f"✨ *Текущий формат:* {icons[preference]} {preference.upper()}\n\n"
        f"📌 *Как пользоваться:*\n"
        f"• Просто отправь ссылку на видео\n"
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
        "*🎥 Форматы скачивания:*\n"
        "• 🎥 Видео — MP4 без водяного знака\n"
        "• 🎵 Аудио — MP3 из любого видео\n"
        "• 📦 Всё — видео + аудио + обложка\n\n"
        "*📱 QR-коды:*\n"
        "• /qr текст — создать QR-код\n"
        "• Или просто отправь текст\n\n"
        "*💡 Где работает:*\n"
        "✅ TikTok\n"
        "✅ YouTube (включая Shorts)\n"
        "✅ Instagram Reels\n"
        "✅ Facebook\n"
        "✅ Twitter/X\n"
        "✅ Reddit\n\n"
        "*⚡ Советы:*\n"
        "• Выбери формат в меню — я запомню\n"
        "• Просто кидай ссылки после выбора\n"
        "• В статистике видно сколько скачал"
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
    
    qr_text = ' '.join(context.args)
    status_msg = await update.message.reply_text("⚡ *Создаю QR-код...*", parse_mode='Markdown')
    
    qr_path = create_qr_code(qr_text)
    
    if qr_path:
        with open(qr_path, 'rb') as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=f"✅ *QR-код готов!*\n\n📝 `{qr_text[:50]}{'...' if len(qr_text) > 50 else ''}`",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
        os.unlink(qr_path)
        await status_msg.delete()
        user_storage.increment_qr(user_id)
    else:
        await status_msg.edit_text(
            "❌ *Не удалось создать QR-код*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает статистику пользователя"""
    user_id = update.effective_user.id
    stats = user_storage.get_user_stats(user_id)
    
    stats_text = (
        f"📊 *Твоя статистика в HartiDash*\n\n"
        f"🎥 Скачано видео/аудио: *{stats.get('downloads', 0)}*\n"
        f"📱 Создано QR-кодов: *{stats.get('qr_codes', 0)}*\n\n"
        f"⚡ Продолжай в том же духе!"
    )
    
    await update.message.reply_text(
        stats_text,
        reply_markup=get_back_button(),
        parse_mode='Markdown'
    )

# ===================== ОБНОВЛЕННЫЙ ОБРАБОТЧИК СООБЩЕНИЙ =====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик сообщений - УПРОЩЕННАЯ ВЕРСИЯ"""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Самая простая проверка на ссылку
    if ('http' in text or 'www.' in text or '.com' in text or '.ru' in text or 
        'tiktok' in text.lower() or 'youtu' in text.lower() or 'instagram' in text.lower()):
        
        pref = user_storage.get_user_preference(user_id)
        
        # Отправляем статус
        status_msg = await update.message.reply_text(f"⏬ Скачиваю {pref}... Это может занять несколько секунд")
        
        try:
            # Скачиваем видео
            files, temp_dir = await download_video(text, pref)
            
            if files and len(files) > 0:
                user_storage.increment_downloads(user_id)
                sent_count = 0
                
                # Отправляем все скачанные файлы
                for file_path in files:
                    if file_path.endswith('.mp4'):
                        with open(file_path, 'rb') as video:
                            await update.message.reply_video(video, supports_streaming=True)
                        sent_count += 1
                    elif file_path.endswith('.mp3'):
                        with open(file_path, 'rb') as audio:
                            await update.message.reply_audio(audio)
                        sent_count += 1
                    elif file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                        with open(file_path, 'rb') as photo:
                            await update.message.reply_photo(photo)
                        sent_count += 1
                
                # Удаляем статус
                await status_msg.delete()
                
                if sent_count == 0:
                    await update.message.reply_text("❌ Не удалось найти видео")
                
                # Очищаем временную папку
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    
            else:
                await status_msg.edit_text("❌ Не удалось скачать файлы")
                
        except Exception as e:
            await status_msg.edit_text(f"❌ Ошибка: {str(e)[:100]}")
            logger.error(f"Ошибка обработки: {e}")
            
    else:
        # Если не ссылка - предлагаем QR
        keyboard = [[InlineKeyboardButton("📱 Сделать QR", callback_data=f"qr_{text}")]]
        await update.message.reply_text(
            "Создать QR-код из этого текста?",
            reply_markup=InlineKeyboardMarkup(keyboard)
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
    
    elif data == "noop":
        pass
    
    elif data.startswith("set_"):
        preference = data[4:]
        user_storage.set_user_preference(user_id, preference)
        icons = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
        
        await query.edit_message_text(
            f"✅ *Формат изменен на {icons[preference]} {preference.upper()}*",
            reply_markup=get_main_menu(user_id),
            parse_mode='Markdown'
        )
    
    elif data == "menu_qr":
        await query.edit_message_text(
            "📱 *Создание QR-кода*\n\n"
            "Используй команду: `/qr текст`\n\n"
            "Например: `/qr https://telegram.org`",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
    
    elif data == "menu_stats":
        stats = user_storage.get_user_stats(user_id)
        await query.edit_message_text(
            f"📊 *Твоя статистика*\n\n"
            f"🎥 Скачано: {stats.get('downloads', 0)}\n"
            f"📱 QR-кодов: {stats.get('qr_codes', 0)}",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
    
    elif data == "menu_help":
        await help_command(update, context)
    
    elif data.startswith("qr_"):
        qr_text = data[3:]
        await query.edit_message_text("⚡ *Создаю QR-код...*", parse_mode='Markdown')
        
        qr_path = create_qr_code(qr_text)
        if qr_path:
            with open(qr_path, 'rb') as photo:
                await query.message.reply_photo(
                    photo=photo,
                    caption=f"✅ *QR-код готов!*",
                    reply_markup=get_back_button(),
                    parse_mode='Markdown'
                )
            os.unlink(qr_path)
            await query.delete_message()
            user_storage.increment_qr(user_id)
        else:
            await query.edit_message_text(
                "❌ *Ошибка создания QR-кода*",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
    
    elif data == "cancel":
        await query.edit_message_text(
            "❌ *Действие отменено*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )

# ===================== ЗАПУСК БОТА =====================
def main():
    """Запуск бота"""
    print("⚡ Запуск HartiDash...")
    
    # Создаем приложение
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("qr", qr_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Для Railway используем вебхуки
    port = int(os.environ.get('PORT', 8080))
    railway_url = os.environ.get('RAILWAY_STATIC_URL', None)
    
    if railway_url:
        # Режим вебхуков для Railway
        webhook_url = f"https://{railway_url}/webhook"
        print(f"🌐 Устанавливаем вебхук на {webhook_url}")
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url
        )
    else:
        # Режим polling для локальной разработки
        print("🔄 Запуск в режиме polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
