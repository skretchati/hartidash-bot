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
# Токен берется из переменных окружения (настройки Railway)
BOT_TOKEN = os.environ.get("8430939712:AAHgNtELNl2Tv3slSt9vomhn_kYF26fDKno")
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

# ===================== КЛАСС ДЛЯ ХРАНЕНИЯ ДАННЫХ ПОЛЬЗОВАТЕЛЕЙ =====================
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
        """Возвращает предпочтение пользователя"""
        user_data = self.users.get(str(user_id))
        if user_data and 'preference' in user_data:
            return user_data['preference']
        return 'video'
    
    def set_user_preference(self, user_id: int, preference: str):
        """Устанавливает предпочтение пользователя"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {}
        self.users[user_id_str]['preference'] = preference
        self.users[user_id_str]['last_seen'] = datetime.now().isoformat()
        self.save_data()
    
    def get_user_stats(self, user_id: int) -> Dict:
        """Возвращает статистику пользователя"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {
                'downloads': 0,
                'qr_codes': 0,
                'first_seen': datetime.now().isoformat()
            }
        return self.users[user_id_str]
    
    def increment_downloads(self, user_id: int):
        """Увеличивает счетчик скачиваний"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {'downloads': 0, 'qr_codes': 0}
        if 'downloads' not in self.users[user_id_str]:
            self.users[user_id_str]['downloads'] = 0
        self.users[user_id_str]['downloads'] += 1
        self.save_data()
    
    def increment_qr(self, user_id: int):
        """Увеличивает счетчик QR-кодов"""
        user_id_str = str(user_id)
        if user_id_str not in self.users:
            self.users[user_id_str] = {'downloads': 0, 'qr_codes': 0}
        if 'qr_codes' not in self.users[user_id_str]:
            self.users[user_id_str]['qr_codes'] = 0
        self.users[user_id_str]['qr_codes'] += 1
        self.save_data()

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
async def download_media(url: str, download_type: str = 'video'):
    """
    Скачивает медиа с платформы
    download_type: 'video', 'audio', 'all'
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_path = os.path.join(TEMP_DIR, f"hartidash_{timestamp}")
        os.makedirs(base_path, exist_ok=True)
        
        result_files = {
            'video': None,
            'audio': None,
            'thumbnail': None,
            'title': None,
            'filesize': 0
        }
        
        # Базовые настройки
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'outtmpl': os.path.join(base_path, '%(title)s.%(ext)s'),
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        # Настройки в зависимости от типа загрузки
        if download_type == 'video':
            ydl_opts['format'] = 'best[ext=mp4]/best'
        elif download_type == 'audio':
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        elif download_type == 'all':
            ydl_opts['format'] = 'best[ext=mp4]/best'
            ydl_opts['writethumbnail'] = True
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        
        # Специальные настройки для TikTok
        if 'tiktok.com' in url:
            ydl_opts['format'] = 'best[ext=mp4]/best'
        
        # Скачиваем
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Получаем информацию
            info = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ydl.extract_info(url, download=True)
            )
            
            result_files['title'] = info.get('title', 'video')
            
            # Ищем все скачанные файлы
            for file in os.listdir(base_path):
                file_path = os.path.join(base_path, file)
                if file.endswith('.mp4'):
                    result_files['video'] = file_path
                    result_files['filesize'] += os.path.getsize(file_path)
                elif file.endswith('.mp3'):
                    result_files['audio'] = file_path
                    result_files['filesize'] += os.path.getsize(file_path)
                elif file.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    result_files['thumbnail'] = file_path
                    result_files['filesize'] += os.path.getsize(file_path)
        
        return result_files, base_path
        
    except Exception as e:
        logger.error(f"Ошибка при скачивании: {e}")
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
    if preference != 'qr':
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
        f"📱 Создано QR-кодов: *{stats.get('qr_codes', 0)}*\n"
        f"📅 Впервые в боте: *{stats.get('first_seen', 'только что')[:10]}*\n\n"
        f"⚡ Продолжай в том же духе!"
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
    
    if is_valid_url(text):
        preference = user_storage.get_user_preference(user_id)
        icons = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
        platform = get_platform_name(text)
        
        status_msg = await update.message.reply_text(
            f"{icons[preference]} *Скачиваю {preference} с {platform}...*",
            parse_mode='Markdown'
        )
        
        result, temp_dir = await download_media(text, preference)
        
        if result:
            sent_count = 0
            user_storage.increment_downloads(user_id)
            
            if preference == 'video' and result['video']:
                file_size = format_size(os.path.getsize(result['video']))
                with open(result['video'], 'rb') as video:
                    await update.message.reply_video(
                        video=video,
                        caption=f"✅ *Видео готово!*\n📱 {platform} | 📦 {file_size}",
                        reply_markup=get_back_button(),
                        parse_mode='Markdown',
                        supports_streaming=True
                    )
                sent_count += 1
            
            elif preference == 'audio' and result['audio']:
                file_size = format_size(os.path.getsize(result['audio']))
                with open(result['audio'], 'rb') as audio:
                    await update.message.reply_audio(
                        audio=audio,
                        title=clean_filename(result['title'][:50]),
                        performer=platform,
                        caption=f"✅ *Аудио готово!*\n📱 {platform} | 📦 {file_size}",
                        reply_markup=get_back_button(),
                        parse_mode='Markdown'
                    )
                sent_count += 1
            
            elif preference == 'all':
                if result['video']:
                    file_size = format_size(os.path.getsize(result['video']))
                    with open(result['video'], 'rb') as video:
                        await update.message.reply_video(
                            video=video,
                            caption=f"🎥 *Видео*\n📱 {platform} | 📦 {file_size}",
                            reply_markup=None,
                            parse_mode='Markdown'
                        )
                    sent_count += 1
                
                if result['audio']:
                    file_size = format_size(os.path.getsize(result['audio']))
                    with open(result['audio'], 'rb') as audio:
                        await update.message.reply_audio(
                            audio=audio,
                            title=clean_filename(result['title'][:50]),
                            performer=platform,
                            caption=f"🎵 *Аудио*\n📦 {file_size}",
                            reply_markup=None,
                            parse_mode='Markdown'
                        )
                    sent_count += 1
                
                if result['thumbnail']:
                    with open(result['thumbnail'], 'rb') as thumb:
                        await update.message.reply_photo(
                            photo=thumb,
                            caption=f"📸 *Обложка*",
                            reply_markup=get_back_button(),
                            parse_mode='Markdown'
                        )
                    sent_count += 1
            
            await status_msg.delete()
            
            if sent_count == 0:
                await update.message.reply_text(
                    "❌ *Не удалось найти файлы для отправки*",
                    reply_markup=get_back_button(),
                    parse_mode='Markdown'
                )
            
            shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            await status_msg.edit_text(
                "❌ *Не удалось скачать файлы*\n"
                "Возможно, ссылка недействительна или видео защищено",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
    
    elif text.lower().startswith('/qr'):
        qr_text = text[3:].strip()
        if qr_text:
            await qr_command(update, context)
        else:
            await update.message.reply_text(
                "❓ *Отправь текст после /qr*",
                reply_markup=get_back_button(),
                parse_mode='Markdown'
            )
    
    else:
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
    
    elif data == "noop":
        pass
    
    elif data.startswith("set_"):
        preference = data[4:]
        user_storage.set_user_preference(user_id, preference)
        icons = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
        
        await query.edit_message_text(
            f"✅ *Формат изменен на {icons[preference]} {preference.upper()}*\n\n"
            f"Теперь все ссылки буду скачивать в этом формате!\n\n"
            f"👉 Отправляй ссылку и смотри результат",
            reply_markup=get_main_menu(user_id),
            parse_mode='Markdown'
        )
    
    elif data == "menu_qr":
        await query.edit_message_text(
            "📱 *Создание QR-кода*\n\n"
            "Используй команду:\n"
            "`/qr текст или ссылка`\n\n"
            "Или просто отправь текст, и я предложу сделать QR-код\n\n"
            "*Примеры:*\n"
            "• `/qr https://telegram.org`\n"
            "• `/qr Привет, мир!`\n"
            "• `/qr +7 (999) 123-45-67`",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
    
    elif data == "menu_stats":
        stats = user_storage.get_user_stats(user_id)
        await query.edit_message_text(
            f"📊 *Твоя статистика в HartiDash*\n\n"
            f"🎥 Скачано: *{stats.get('downloads', 0)}*\n"
            f"📱 QR-кодов: *{stats.get('qr_codes', 0)}*\n"
            f"📅 В боте с: *{stats.get('first_seen', 'только что')[:10]}*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
    
    elif data == "menu_help":
        help_text = (
            "📖 *HartiDash — Помощь*\n\n"
            "*🎥 Форматы:* Видео / Аудио / Всё\n"
            "*📱 QR:* /qr текст\n"
            "*🌐 Поддерживаемые сайты:* TikTok, YouTube, Instagram, Facebook и др.\n\n"
            "Выбери формат в меню и просто кидай ссылки!"
        )
        await query.edit_message_text(
            help_text,
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
    
    elif data.startswith("qr_"):
        qr_text = data[3:]
        await query.edit_message_text("⚡ *Создаю QR-код...*", parse_mode='Markdown')
        
        qr_path = create_qr_code(qr_text)
        if qr_path:
            with open(qr_path, 'rb') as photo:
                await query.message.reply_photo(
                    photo=photo,
                    caption=f"✅ *QR-код готов!*\n\n📝 `{qr_text[:50]}{'...' if len(qr_text) > 50 else ''}`",
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
    print("⚡ Запуск HartiDash на Railway...")
    
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