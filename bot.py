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

# Google Drive импорты
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ===================== НАСТРОЙКИ =====================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN не найден! Добавьте его в переменные окружения Railway.")

# Google Drive настройки
GOOGLE_DRIVE_CREDENTIALS = os.environ.get("GOOGLE_DRIVE_CREDENTIALS")
if GOOGLE_DRIVE_CREDENTIALS:
    try:
        # Убираем лишние кавычки, если они есть
        clean_creds = GOOGLE_DRIVE_CREDENTIALS.strip()
        if clean_creds.startswith('"') and clean_creds.endswith('"'):
            clean_creds = clean_creds[1:-1]
        DRIVE_CREDENTIALS = json.loads(clean_creds)
        logger.info("✅ Google Drive credentials загружены")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки Drive credentials: {e}")
        DRIVE_CREDENTIALS = None
else:
    DRIVE_CREDENTIALS = None
    logger.info("⚠️ Google Drive не настроен")

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Временная директория
TEMP_DIR = tempfile.gettempdir()
MAX_TELEGRAM_SIZE = 50 * 1024 * 1024  # 50 МБ - лимит Telegram

# Файл для хранения информации о загруженных в Drive файлах
DRIVE_FILES_DB = os.path.join(TEMP_DIR, 'drive_files.json')

# ===================== ХРАНЕНИЕ ДАННЫХ О ФАЙЛАХ DRIVE =====================
class DriveFileManager:
    """Класс для управления файлами в Google Drive с автоудалением через 12 часов"""
    
    def __init__(self):
        self.files = {}
        self.load_files()
        # Запускаем фоновый процесс проверки каждые 30 минут
        self.start_cleanup_scheduler()
    
    def load_files(self):
        """Загружает информацию о файлах из JSON"""
        try:
            if os.path.exists(DRIVE_FILES_DB):
                with open(DRIVE_FILES_DB, 'r') as f:
                    self.files = json.load(f)
                logger.info(f"📂 Загружено {len(self.files)} файлов из базы Drive")
        except Exception as e:
            logger.error(f"Ошибка загрузки базы Drive файлов: {e}")
            self.files = {}
    
    def save_files(self):
        """Сохраняет информацию о файлах в JSON"""
        try:
            with open(DRIVE_FILES_DB, 'w') as f:
                json.dump(self.files, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения базы Drive файлов: {e}")
    
    def add_file(self, file_id, file_name, user_id, chat_id):
        """Добавляет файл в базу с временем загрузки"""
        upload_time = datetime.now().isoformat()
        delete_time = (datetime.now() + timedelta(hours=12)).isoformat()
        
        self.files[file_id] = {
            'file_id': file_id,
            'file_name': file_name,
            'user_id': str(user_id),
            'chat_id': str(chat_id),
            'upload_time': upload_time,
            'delete_time': delete_time,
            'deleted': False
        }
        self.save_files()
        logger.info(f"✅ Файл {file_name} (ID: {file_id}) будет удален через 12 часов")
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
        """Запускает планировщик очистки (проверка каждые 30 минут)"""
        self.check_and_delete_files()
        # Запускаем следующий цикл через 30 минут
        Timer(1800, self.start_cleanup_scheduler).start()  # 1800 секунд = 30 минут
    
    def check_and_delete_files(self):
        """Проверяет и удаляет файлы, время которых истекло"""
        if not DRIVE_CREDENTIALS:
            logger.info("⏭️ Drive не настроен, пропускаем очистку")
            return
        
        to_delete = self.get_files_to_delete()
        if not to_delete:
            logger.info("⏭️ Нет файлов для удаления")
            return
        
        logger.info(f"🔄 Начинаю очистку {len(to_delete)} файлов...")
        
        service = get_drive_service()
        if not service:
            logger.error("❌ Не удалось получить Drive сервис для очистки")
            return
        
        for file_id, file_info in to_delete:
            try:
                # Пытаемся удалить файл [citation:1][citation:7]
                service.files().delete(fileId=file_id).execute()
                self.mark_as_deleted(file_id)
                logger.info(f"✅ Удален файл {file_info['file_name']} (ID: {file_id})")
                
                # Можно отправить уведомление пользователю (опционально)
                # await notify_user(file_info)
                
            except HttpError as e:
                if e.resp.status == 404:
                    # Файл уже не существует
                    self.mark_as_deleted(file_id)
                    logger.info(f"✅ Файл {file_id} уже не существует, помечен как удаленный")
                else:
                    logger.error(f"❌ Ошибка удаления файла {file_id}: {e}")
            except Exception as e:
                logger.error(f"❌ Неизвестная ошибка при удалении {file_id}: {e}")
        
        logger.info("✅ Очистка завершена")

# Создаем глобальный экземпляр менеджера файлов
drive_manager = DriveFileManager()

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
            self.users[user_id] = {'pref': 'video', 'downloads': 0, 'qr': 0, 'drive_uploads': 0}
        return self.users[user_id].get('pref', 'video')
    
    def set_preference(self, user_id, pref):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': pref, 'downloads': 0, 'qr': 0, 'drive_uploads': 0}
        else:
            self.users[user_id]['pref'] = pref
        self.save_data()
    
    def add_download(self, user_id, via_drive=False):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 1, 'qr': 0, 'drive_uploads': 1 if via_drive else 0}
        else:
            self.users[user_id]['downloads'] = self.users[user_id].get('downloads', 0) + 1
            if via_drive:
                self.users[user_id]['drive_uploads'] = self.users[user_id].get('drive_uploads', 0) + 1
        self.save_data()
    
    def add_qr(self, user_id):
        user_id = str(user_id)
        if user_id not in self.users:
            self.users[user_id] = {'pref': 'video', 'downloads': 0, 'qr': 1, 'drive_uploads': 0}
        else:
            self.users[user_id]['qr'] = self.users[user_id].get('qr', 0) + 1
        self.save_data()
    
    def get_stats(self, user_id):
        user_id = str(user_id)
        if user_id in self.users:
            return self.users[user_id]
        return {'downloads': 0, 'qr': 0, 'drive_uploads': 0}

# Создаем глобальный экземпляр
user_data = UserData()

# ===================== GOOGLE DRIVE ФУНКЦИИ =====================
def get_drive_service():
    """Создает сервис Google Drive используя service account"""
    if not DRIVE_CREDENTIALS:
        return None
    
    try:
        credentials = service_account.Credentials.from_service_account_info(
            DRIVE_CREDENTIALS,
            scopes=['https://www.googleapis.com/auth/drive.file', 'https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=credentials)
        logger.info("✅ Google Drive сервис создан")
        return service
    except Exception as e:
        logger.error(f"❌ Ошибка создания Drive сервиса: {e}")
        return None

async def upload_to_drive(file_path, filename=None, user_id=None, chat_id=None):
    """
    Загружает файл в Google Drive, сохраняет информацию и возвращает ссылку
    """
    try:
        service = get_drive_service()
        if not service:
            logger.error("❌ Drive сервис не доступен")
            return None
        
        # Подготовка метаданных файла
        file_metadata = {
            'name': filename or os.path.basename(file_path),
        }
        
        # Создаем медиа-загрузчик с поддержкой больших файлов
        media = MediaFileUpload(
            file_path,
            resumable=True,
            chunksize=1024*1024  # Загружаем по 1 МБ
        )
        
        logger.info(f"📤 Загружаю в Drive: {file_path}")
        
        # Загружаем файл
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        file_id = file.get('id')
        logger.info(f"✅ Файл загружен в Drive, ID: {file_id}")
        
        # Делаем файл доступным по ссылке
        service.permissions().create(
            fileId=file_id,
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        # Сохраняем информацию о файле для автоудаления через 12 часов
        delete_time = drive_manager.add_file(
            file_id=file_id,
            file_name=file_metadata['name'],
            user_id=user_id,
            chat_id=chat_id
        )
        
        # Форматируем время для сообщения пользователю
        delete_time_formatted = datetime.fromisoformat(delete_time).strftime("%d.%m.%Y в %H:%M")
        
        # Возвращаем ссылку и время удаления
        return f"https://drive.google.com/uc?id={file_id}", delete_time_formatted
        
    except HttpError as e:
        logger.error(f"❌ HTTP ошибка Drive: {e}")
        return None, None
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки в Drive: {e}")
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
        
        # Базовые настройки для всех режимов (решает проблему YouTube)
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
                    'player_client': ['android', 'web'],  # Используем разные клиенты
                    'skip': ['hls', 'dash'],  # Пропускаем некоторые форматы
                }
            }
        }
        
        # Пытаемся загрузить cookies если файл существует
        cookies_file = os.path.join(os.path.dirname(__file__), 'cookies.txt')
        if os.path.exists(cookies_file):
            base_opts['cookiefile'] = cookies_file
            logger.info("🍪 Файл cookies найден и будет использован")
        else:
            logger.info("🍪 Файл cookies не найден, продолжаем без него")
        
        if mode == 'video':
            # Только видео
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
            # Только аудио
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
            # Сначала видео
            video_opts = base_opts.copy()
            video_opts.update({
                'outtmpl': os.path.join(out_path, 'video.%(ext)s'),
                'format': 'best[ext=mp4]/best',
            })
            
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.download([url])
                )
            
            # Потом аудио
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
            
            # Пробуем скачать обложку
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
        
        # Собираем все скачанные файлы
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
    
    drive_status = "✅ Drive подключен (автоудаление через 12ч)" if DRIVE_CREDENTIALS else "⚠️ Drive не настроен (будут только файлы до 50 МБ)"
    cookies_status = "🍪 Cookies найдены" if os.path.exists('cookies.txt') else "⚠️ Cookies не найдены (YouTube может не работать)"
    
    welcome_text = (
        f"⚡ *HartiDash — твой быстрый загрузчик!*\n\n"
        f"👋 Привет, {first_name}!\n\n"
        f"📌 *Как пользоваться:*\n"
        f"• Отправь ссылку на видео\n"
        f"• Я скачаю в выбранном формате\n"
        f"• Файлы до 50 МБ → отправляю сразу\n"
        f"• Файлы больше 50 МБ → загружаю в Google Drive\n"
        f"• Файлы в Drive **автоматически удаляются через 12 часов**\n\n"
        f"📊 *Статус:*\n"
        f"{drive_status}\n"
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
        "• Файлы >50 МБ загружаются в Google Drive\n"
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
        f"📤 Через Google Drive: *{stats.get('drive_uploads', 0)}*\n"
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
    
    # Проверяем, похоже ли на ссылку
    if any(x in text.lower() for x in ['.com', '.ru', 'http', 'www', 'youtu', 'tiktok', 'instagram', 'facebook', 'twitter', 'x.com']):
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
            drive_used = False
            
            # Отправляем все файлы
            for file_path in files:
                try:
                    # Проверяем существование файла
                    if not os.path.exists(file_path):
                        logger.error(f"Файл не существует: {file_path}")
                        continue
                    
                    file_size = os.path.getsize(file_path)
                    logger.info(f"Файл: {file_path}, размер: {file_size} байт")
                    
                    # Проверяем размер файла
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
                        # Большой файл - загружаем в Google Drive
                        if DRIVE_CREDENTIALS:
                            size_mb = file_size / (1024 * 1024)
                            logger.info(f"📤 Файл большой ({size_mb:.1f} МБ), загружаю в Drive")
                            
                            drive_link, delete_time = await upload_to_drive(
                                file_path, 
                                user_id=user_id,
                                chat_id=chat_id
                            )
                            
                            if drive_link:
                                file_type = "Видео" if file_path.endswith('.mp4') else "Аудио" if file_path.endswith('.mp3') else "Файл"
                                await update.message.reply_text(
                                    f"📦 *{file_type} большой ({size_mb:.1f} МБ)*\n\n"
                                    f"Telegram не может отправить файлы больше 50 МБ.\n"
                                    f"🔗 [Скачать с Google Drive]({drive_link})\n\n"
                                    f"⏰ *Файл будет автоматически удален через 12 часов* (до {delete_time})",
                                    parse_mode='Markdown'
                                )
                                logger.info(f"✅ Ссылка на Drive отправлена, удаление в {delete_time}")
                                sent_count += 1
                                drive_used = True
                            else:
                                await update.message.reply_text(
                                    f"❌ *Не удалось загрузить файл в Google Drive*\n"
                                    f"Размер: {size_mb:.1f} МБ",
                                    parse_mode='Markdown'
                                )
                        else:
                            size_mb = file_size / (1024 * 1024)
                            await update.message.reply_text(
                                f"⚠️ *Файл слишком большой для Telegram ({size_mb:.1f} МБ)*\n\n"
                                f"Google Drive не настроен. Добавьте credentials для загрузки больших файлов.",
                                parse_mode='Markdown'
                            )
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки файла {file_path}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    await update.message.reply_text(f"❌ Ошибка при отправке: {str(e)[:100]}")
            
            await status_msg.delete()
            
            # Обновляем статистику с учетом Drive
            if drive_used:
                user_data.add_download(user_id, via_drive=True)
            
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
            f"📤 Через Drive: *{stats.get('drive_uploads', 0)}*\n"
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
            "*📦 Большие файлы:*\n"
            "• Файлы >50 МБ загружаются в Google Drive\n"
            "• Файлы **автоматически удаляются через 12 часов**\n"
            "• Вы получаете прямую ссылку на скачивание"
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
    print("⚡ Запуск HartiDash с Google Drive и автоудалением через 12ч...")
    
    # Проверяем наличие cookies
    if os.path.exists('cookies.txt'):
        print("🍪 Файл cookies.txt найден")
    else:
        print("⚠️ Файл cookies.txt не найден. YouTube может работать нестабильно")
    
    # Проверяем Drive
    if DRIVE_CREDENTIALS:
        print("✅ Google Drive настроен, автоудаление через 12ч активно")
    else:
        print("⚠️ Google Drive не настроен. Большие файлы не будут загружаться")
    
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
