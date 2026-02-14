#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import tempfile
from urllib.parse import urlparse
import asyncio
from datetime import datetime, timedelta
import shutil
import json
import time
from threading import Timer
import requests

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
import yadisk
# ВРЕМЕННАЯ ОТЛАДКА - удалить потом
import os
print("=== ОТЛАДКА ФАЙЛОВ ===")
print("Текущая папка:", os.getcwd())
print("Файлы в папке:", os.listdir('.'))
if os.path.exists('cookies.txt'):
    print("✅ cookies.txt НАЙДЕН!")
    print("Размер:", os.path.getsize('cookies.txt'), "байт")
else:
    print("❌ cookies.txt НЕ НАЙДЕН!")
print("=====================")
# ===================== НАСТРОЙКА ЛОГИРОВАНИЯ =====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
logger.info("⚡ Запуск HartiDash...")

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN не найден!")
    raise ValueError("❌ BOT_TOKEN не найден! Добавьте его в переменные окружения Railway.")

# Yandex.Disk настройки
YANDEX_DISK_TOKEN = os.environ.get("YANDEX_DISK_TOKEN")
YANDEX_DISK_CLIENT = None

if YANDEX_DISK_TOKEN:
    try:
        YANDEX_DISK_CLIENT = yadisk.Client(token=YANDEX_DISK_TOKEN)
        # Проверяем, работает ли токен
        if YANDEX_DISK_CLIENT.check_token():
            logger.info("✅ Yandex.Disk клиент успешно создан и токен валиден")
        else:
            logger.error("❌ Токен Yandex.Disk невалиден")
            YANDEX_DISK_CLIENT = None
    except Exception as e:
        logger.error(f"❌ Ошибка создания Yandex.Disk клиента: {e}")
        YANDEX_DISK_CLIENT = None
else:
    logger.info("⚠️ Yandex.Disk не настроен (переменная YANDEX_DISK_TOKEN отсутствует)")

# Временная директория
TEMP_DIR = tempfile.gettempdir()
MAX_TELEGRAM_SIZE = 50 * 1024 * 1024  # 50 МБ - лимит Telegram
MAX_YANDEX_SIZE = 100 * 1024 * 1024  # 100 МБ - ограничение API Яндекс.Диска для одного файла (можно увеличить)

# Файл для хранения информации о загруженных файлах
FILES_DB = os.path.join(TEMP_DIR, 'yandex_files.json')

logger.info(f"📁 Временная директория: {TEMP_DIR}")
logger.info(f"📦 Максимальный размер для Telegram: {MAX_TELEGRAM_SIZE / 1024 / 1024} МБ")

# ===================== ХРАНЕНИЕ ДАННЫХ О ФАЙЛАХ =====================
class FileManager:
    """Класс для управления файлами с автоудалением через 12 часов"""
    
    def __init__(self):
        self.files = {}
        self.load_files()
        self.start_cleanup_scheduler()
    
    def load_files(self):
        """Загружает информацию о файлах из JSON"""
        try:
            if os.path.exists(FILES_DB):
                with open(FILES_DB, 'r') as f:
                    self.files = json.load(f)
                logger.info(f"📂 Загружено {len(self.files)} файлов из базы")
        except Exception as e:
            logger.error(f"Ошибка загрузки базы файлов: {e}")
            self.files = {}
    
    def save_files(self):
        """Сохраняет информацию о файлах в JSON"""
        try:
            with open(FILES_DB, 'w') as f:
                json.dump(self.files, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения базы файлов: {e}")
    
    def add_file(self, file_path, yandex_path, public_url, user_id, chat_id):
        """Добавляет файл в базу с временем загрузки"""
        file_id = os.path.basename(file_path)
        upload_time = datetime.now().isoformat()
        delete_time = (datetime.now() + timedelta(hours=12)).isoformat()
        
        self.files[file_id] = {
            'file_id': file_id,
            'local_path': file_path,
            'yandex_path': yandex_path,
            'public_url': public_url,
            'user_id': str(user_id),
            'chat_id': str(chat_id),
            'upload_time': upload_time,
            'delete_time': delete_time,
            'deleted': False
        }
        self.save_files()
        logger.info(f"✅ Файл {file_id} будет удален через 12 часов")
        return delete_time
    
    def get_files_to_delete(self):
        """Возвращает список файлов, которые нужно удалить"""
        now = datetime.now()
        to_delete = []
        
        for file_id, file_info in self.files.items():
            if file_info.get('deleted', False):
                continue
            
            delete_time = datetime.fromisoformat(file_info['delete_time'])
            if now >= delete_time:
                to_delete.append((file_id, file_info))
        
        return to_delete
    
    def mark_as_deleted(self, file_id):
        """Отмечает файл как удаленный"""
        if file_id in self.files:
            self.files[file_id]['deleted'] = True
            self.save_files()
    
    def start_cleanup_scheduler(self):
        """Запускает планировщик очистки"""
        self.check_and_delete_files()
        Timer(1800, self.start_cleanup_scheduler).start()
    
    def check_and_delete_files(self):
        """Проверяет и удаляет файлы, время которых истекло"""
        if not YANDEX_DISK_CLIENT:
            return
        
        to_delete = self.get_files_to_delete()
        if not to_delete:
            return
        
        logger.info(f"🔄 Начинаю очистку {len(to_delete)} файлов...")
        
        for file_id, file_info in to_delete:
            try:
                with YANDEX_DISK_CLIENT:
                    # Удаляем файл с Яндекс.Диска
                    if YANDEX_DISK_CLIENT.exists(file_info['yandex_path']):
                        YANDEX_DISK_CLIENT.remove(file_info['yandex_path'], permanently=True)
                        logger.info(f"✅ Удален файл с Яндекс.Диска: {file_info['yandex_path']}")
                    
                    # Удаляем локальный файл, если он ещё существует
                    if os.path.exists(file_info['local_path']):
                        os.remove(file_info['local_path'])
                    
                    self.mark_as_deleted(file_id)
                
            except Exception as e:
                logger.error(f"❌ Ошибка удаления файла {file_id}: {e}")
        
        logger.info("✅ Очистка завершена")

# Создаем глобальный экземпляр
file_manager = FileManager()

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
            self.users[user_id] = {'pref': 'video', 'downloads': 0, 'qr': 0, 'cloud_uploads': 0}
        return self.users[user_id].get('pref', 'video')
    
    def set_preference(self, user_id, pref):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': pref, 'downloads': 0, 'qr': 0, 'cloud_uploads': 0}
        else:
            self.users[user_id]['pref'] = pref
        self.save_data()
    
    def add_download(self, user_id, via_cloud=False):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 1, 'qr': 0, 'cloud_uploads': 1 if via_cloud else 0}
        else:
            self.users[user_id]['downloads'] = self.users[user_id].get('downloads', 0) + 1
            if via_cloud:
                self.users[user_id]['cloud_uploads'] = self.users[user_id].get('cloud_uploads', 0) + 1
        self.save_data()
    
    def add_qr(self, user_id):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 0, 'qr': 1, 'cloud_uploads': 0}
        else:
            self.users[user_id]['qr'] = self.users[user_id].get('qr', 0) + 1
        self.save_data()
    
    def get_stats(self, user_id):
        user_id = str(user_id)
        if user_id in self.users:
            return self.users[user_id]
        return {'downloads': 0, 'qr': 0, 'cloud_uploads': 0}

# Создаем глобальный экземпляр
user_data = UserData()

# ===================== YANDEX.DISK ФУНКЦИИ =====================
async def upload_to_yandex(file_path, filename=None, user_id=None, chat_id=None):
    """
    Загружает файл на Yandex.Disk и возвращает публичную ссылку
    """
    try:
        if not YANDEX_DISK_CLIENT:
            logger.error("❌ Yandex.Disk клиент не доступен")
            return None, None
        
        # Генерируем имя для файла на Яндекс.Диске
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_filename = filename or os.path.basename(file_path)
        # Очищаем имя файла от недопустимых символов
        safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in " ._-()").strip()
        yandex_path = f"/HartiDash/{timestamp}_{safe_filename}"
        
        logger.info(f"📤 Загружаю на Yandex.Disk: {yandex_path}")
        
        # Используем контекстный менеджер для работы с клиентом
        with YANDEX_DISK_CLIENT:
            # Создаём папку HartiDash, если её нет
            if not YANDEX_DISK_CLIENT.exists("/HartiDash"):
                YANDEX_DISK_CLIENT.mkdir("/HartiDash")
                logger.info("📁 Создана папка /HartiDash на Яндекс.Диске")
            
            # Загружаем файл [citation:5][citation:9]
            YANDEX_DISK_CLIENT.upload(file_path, yandex_path)
            logger.info(f"✅ Файл загружен на Yandex.Disk")
            
            # Делаем файл публичным и получаем ссылку [citation:5]
            publication = YANDEX_DISK_CLIENT.publish(yandex_path)
            # Получаем публичную ссылку
            public_url = YANDEX_DISK_CLIENT.get_public_link(yandex_path)
            logger.info(f"🔗 Публичная ссылка получена")
        
        # Сохраняем информацию о файле
        delete_time = file_manager.add_file(
            file_path=file_path,
            yandex_path=yandex_path,
            public_url=public_url,
            user_id=user_id,
            chat_id=chat_id
        )
        
        delete_time_formatted = datetime.fromisoformat(delete_time).strftime("%d.%m.%Y в %H:%M")
        
        return public_url, delete_time_formatted
        
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки на Yandex.Disk: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return None, None

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
        logger.info(f"📥 Скачиваю {mode} с {url}")
        
        base_opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-us,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Sec-Fetch-Mode': 'navigate',
            },
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['hls', 'dash'],
                }
            }
        }
        
        cookies_file = os.path.join(os.path.dirname(__file__), 'cookies.txt')
        if os.path.exists(cookies_file):
            base_opts['cookiefile'] = cookies_file
            logger.info("🍪 Файл cookies найден и будет использован")
        else:
            logger.info("🍪 Файл cookies не найден, продолжаем без него")
        
        if mode == 'video':
            ydl_opts = base_opts.copy()
            ydl_opts.update({
                'outtmpl': os.path.join(out_path, '%(title)s.%(ext)s'),
                'format': 'best[ext=mp4]/best',
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
        
        elif mode == 'audio':
            ydl_opts = base_opts.copy()
            ydl_opts.update({
                'outtmpl': os.path.join(out_path, '%(title)s.%(ext)s'),
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
        
        elif mode == 'all':
            video_opts = base_opts.copy()
            video_opts.update({
                'outtmpl': os.path.join(out_path, 'video.%(ext)s'),
                'format': 'best[ext=mp4]/best',
            })
            
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
            
            audio_opts = base_opts.copy()
            audio_opts.update({
                'outtmpl': os.path.join(out_path, 'audio.%(ext)s'),
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
            
            try:
                thumb_opts = base_opts.copy()
                thumb_opts.update({
                    'outtmpl': os.path.join(out_path, 'thumbnail.%(ext)s'),
                    'writethumbnail': True,
                    'skip_download': True,
                })
                
                with yt_dlp.YoutubeDL(thumb_opts) as ydl:
                    await asyncio.get_event_loop().run_in_executor(
                        None, lambda: ydl.download([url])
                    )
            except Exception as e:
                logger.info(f"Обложка не скачалась: {e}")
        
        if os.path.exists(out_path):
            for f in os.listdir(out_path):
                file_path = os.path.join(out_path, f)
                files.append(file_path)
                logger.info(f"✅ Скачан файл: {f}")
        
        return files, out_path
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА СКАЧИВАНИЯ: {e}")
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
    
    video_text = "🎥 Видео"
    audio_text = "🎵 Аудио" 
    all_text = "📦 Всё"
    
    if pref == 'video':
        video_text = "🎥 Видео ✅"
    elif pref == 'audio':
        audio_text = "🎵 Аудио ✅"
    elif pref == 'all':
        all_text = "📦 Всё ✅"
    
    keyboard = [
        [
            InlineKeyboardButton(video_text, callback_data="set_video"),
            InlineKeyboardButton(audio_text, callback_data="set_audio"),
            InlineKeyboardButton(all_text, callback_data="set_all")
        ],
        [
            InlineKeyboardButton("📱 QR-код", callback_data="menu_qr"),
            InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")
        ],
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
    
    yandex_status = "✅ Яндекс.Диск подключен (автоудаление через 12ч)" if YANDEX_DISK_CLIENT else "⚠️ Яндекс.Диск не настроен (будут только файлы до 50 МБ)"
    cookies_status = "🍪 Cookies найдены" if os.path.exists('cookies.txt') else "⚠️ Cookies не найдены (YouTube может не работать)"
    
    welcome_text = (
        f"⚡ *HartiDash — твой быстрый загрузчик!*\n\n"
        f"👋 Привет, {first_name}!\n\n"
        f"📌 *Как пользоваться:*\n"
        f"• Отправь ссылку на видео\n"
        f"• Я скачаю в выбранном формате\n"
        f"• Файлы до 50 МБ → отправляю сразу\n"
        f"• Файлы больше 50 МБ → загружаю на Яндекс.Диск\n"
        f"• Файлы в облаке **автоматически удаляются через 12 часов**\n\n"
        f"📊 *Статус:*\n"
        f"{yandex_status}\n"
        f"{cookies_status}\n\n"
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
        "*📦 Большие файлы:*\n"
        "• Файлы >50 МБ загружаются на Яндекс.Диск\n"
        "• Файлы **автоматически удаляются через 12 часов**\n"
        "• Вы получаете прямую ссылку на скачивание\n\n"
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
        f"🎥 Скачано всего: *{stats.get('downloads', 0)}*\n"
        f"☁️ Через облако: *{stats.get('cloud_uploads', 0)}*\n"
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
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if any(x in text.lower() for x in ['.com', '.ru', 'http', 'www', 'youtu', 'tiktok', 'instagram', 'facebook', 'twitter', 'x.com']):
        pref = user_data.get_preference(user_id)
        emoji = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
        
        status_msg = await update.message.reply_text(
            f"{emoji[pref]} *Скачиваю...*",
            parse_mode='Markdown'
        )
        
        files, temp_dir = await download_video(text, pref)
        
        if files and len(files) > 0:
            user_data.add_download(user_id)
            sent_count = 0
            cloud_used = False
            
            for file_path in files:
                try:
                    if not os.path.exists(file_path):
                        logger.error(f"Файл не существует: {file_path}")
                        continue
                    
                    file_size = os.path.getsize(file_path)
                    logger.info(f"Файл: {file_path}, размер: {file_size} байт")
                    
                    if file_size <= MAX_TELEGRAM_SIZE:
                        # Маленький файл - отправляем через Telegram
                        if file_path.endswith('.mp4'):
                            with open(file_path, 'rb') as f:
                                await update.message.reply_video(
                                    f,
                                    supports_streaming=True,
                                    caption="🎥 Видео готово!"
                                )
                            logger.info(f"✅ Видео отправлено через Telegram")
                            sent_count += 1
                            
                        elif file_path.endswith('.mp3'):
                            with open(file_path, 'rb') as f:
                                await update.message.reply_audio(
                                    f,
                                    caption="🎵 Аудио готово!"
                                )
                            logger.info(f"✅ Аудио отправлено через Telegram")
                            sent_count += 1
                            
                        elif file_path.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                            with open(file_path, 'rb') as f:
                                await update.message.reply_photo(
                                    f,
                                    caption="📸 Обложка"
                                )
                            logger.info(f"✅ Фото отправлено через Telegram")
                            sent_count += 1
                    else:
                        # Большой файл - загружаем на Яндекс.Диск
                        if YANDEX_DISK_CLIENT:
                            size_mb = file_size / (1024 * 1024)
                            logger.info(f"📤 Файл большой ({size_mb:.1f} МБ), загружаю на Яндекс.Диск")
                            
                            public_url, delete_time = await upload_to_yandex(
                                file_path, 
                                user_id=user_id,
                                chat_id=chat_id
                            )
                            
                            if public_url:
                                file_type = "Видео" if file_path.endswith('.mp4') else "Аудио" if file_path.endswith('.mp3') else "Файл"
                                await update.message.reply_text(
                                    f"📦 *{file_type} большой ({size_mb:.1f} МБ)*\n\n"
                                    f"Telegram не может отправить файлы больше 50 МБ.\n"
                                    f"🔗 [Скачать с Яндекс.Диска]({public_url})\n\n"
                                    f"⏰ *Файл будет автоматически удален через 12 часов* (до {delete_time})",
                                    parse_mode='Markdown',
                                    disable_web_page_preview=True
                                )
                                logger.info(f"✅ Ссылка на Яндекс.Диск отправлена, удаление в {delete_time}")
                                sent_count += 1
                                cloud_used = True
                            else:
                                await update.message.reply_text(
                                    f"❌ *Не удалось загрузить файл на Яндекс.Диск*\n"
                                    f"Размер: {size_mb:.1f} МБ",
                                    parse_mode='Markdown'
                                )
                        else:
                            size_mb = file_size / (1024 * 1024)
                            await update.message.reply_text(
                                f"⚠️ *Файл слишком большой для Telegram ({size_mb:.1f} МБ)*\n\n"
                                f"Яндекс.Диск не настроен. Добавьте токен для загрузки больших файлов.",
                                parse_mode='Markdown'
                            )
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки файла {file_path}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    await update.message.reply_text(f"❌ Ошибка при отправке: {str(e)[:100]}")
            
            await status_msg.delete()
            
            if cloud_used:
                user_data.add_download(user_id, via_cloud=True)
            
            if sent_count == 0:
                await update.message.reply_text(
                    "❌ *Не удалось отправить файлы*\n"
                    "Проверьте логи для подробностей",
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
        pref = data[4:]
        user_data.set_preference(user_id, pref)
        names = {'video': '🎥 VIDEO', 'audio': '🎵 AUDIO', 'all': '📦 ALL'}
        
        await query.edit_message_text(
            f"✅ *Формат изменен на {names[pref]}*\n\n"
            f"👉 Отправляй ссылку",
            reply_markup=get_main_menu(user_id),
            parse_mode='Markdown'
        )
        return
    
    elif data == "menu_qr":
        await query.edit_message_text(
            "📱 *Создание QR-кода*\n\n"
            "Используй команду:\n"
            "`/qr текст или ссылка`",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return
    
    elif data == "menu_stats":
        stats = user_data.get_stats(user_id)
        await query.edit_message_text(
            f"📊 *Твоя статистика*\n\n"
            f"🎥 Скачано: *{stats.get('downloads', 0)}*\n"
            f"☁️ Через облако: *{stats.get('cloud_uploads', 0)}*\n"
            f"📱 QR-кодов: *{stats.get('qr', 0)}*",
            reply_markup=get_back_button(),
            parse_mode='Markdown'
        )
        return
    
    elif data == "menu_help":
        help_text = (
            "📖 *HartiDash — Помощь*\n\n"
            "*🎥 Форматы:* Видео / Аудио / Всё\n"
            "*📱 QR:* /qr текст\n"
            "*📦 Большие файлы:* загружаются на Яндекс.Диск и удаляются через 12ч"
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
    print("⚡ Запуск HartiDash с Яндекс.Диском...")
    
    if os.path.exists('cookies.txt'):
        print("🍪 Файл cookies.txt найден")
    else:
        print("⚠️ Файл cookies.txt не найден. YouTube может работать нестабильно")
    
    if YANDEX_DISK_CLIENT:
        print("✅ Яндекс.Диск настроен, автоудаление через 12ч активно")
    else:
        print("⚠️ Яндекс.Диск не настроен. Большие файлы не будут загружаться")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("qr", qr_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    port = int(os.environ.get('PORT', 8080))
    railway_url = os.environ.get('RAILWAY_STATIC_URL')
    
    if railway_url:
        webhook_url = f"https://{railway_url}/webhook"
        print(f"🌐 Устанавливаем вебхук на {webhook_url}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=webhook_url
        )
    else:
        print("🔄 Запуск в режиме polling...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
