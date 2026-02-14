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

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TEMP_DIR = tempfile.gettempdir()

# ===================== ХРАНЕНИЕ ДАННЫХ =====================
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

user_data = UserData()

# ===================== СКАЧИВАНИЕ =====================
async def download_video(url, mode='video'):
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(TEMP_DIR, f"harti_{timestamp}")
        os.makedirs(out_path, exist_ok=True)
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': os.path.join(out_path, '%(title)s.%(ext)s'),
            'format': 'best[ext=mp4]/best' if mode == 'video' else 'bestaudio/best',
        }
        
        if mode == 'audio':
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }]
        
        if mode == 'all':
            ydl_opts['format'] = 'best[ext=mp4]/best'
            ydl_opts['writethumbnail'] = True
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.download([url]))
        
        files = []
        for f in os.listdir(out_path):
            files.append(os.path.join(out_path, f))
        
        return files, out_path
    except Exception as e:
        logger.error(f"Error: {e}")
        return None, None

# ===================== QR-КОД =====================
def make_qr(text):
    try:
        path = os.path.join(TEMP_DIR, f"qr_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
        qr = qrcode.make(text)
        qr.save(path)
        return path
    except:
        return None

# ===================== МЕНЮ =====================
def get_menu(user_id):
    pref = user_data.get_preference(user_id)
    icons = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
    
    keyboard = [
        [
            InlineKeyboardButton(f"{icons['video']} Видео" + (" ✅" if pref == 'video' else ""), callback_data="set_video"),
            InlineKeyboardButton(f"{icons['audio']} Аудио" + (" ✅" if pref == 'audio' else ""), callback_data="set_audio"),
            InlineKeyboardButton(f"{icons['all']} Всё" + (" ✅" if pref == 'all' else ""), callback_data="set_all")
        ],
        [
            InlineKeyboardButton("📱 QR-код", callback_data="menu_qr"),
            InlineKeyboardButton("📊 Статистика", callback_data="menu_stats")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ===================== КОМАНДЫ =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        f"⚡ *HartiDash*\n\nПривет, {update.effective_user.first_name}!\n\n"
        f"📌 Отправь ссылку — я скачаю в выбранном формате\n"
        f"📱 /qr текст — создать QR-код",
        reply_markup=get_menu(user_id),
        parse_mode='Markdown'
    )

async def qr_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❓ Отправь: /qr текст")
        return
    
    text = ' '.join(context.args)
    msg = await update.message.reply_text("🔄 Создаю QR-код...")
    
    path = make_qr(text)
    if path:
        with open(path, 'rb') as f:
            await update.message.reply_photo(f)
        os.unlink(path)
        await msg.delete()
        user_data.add_qr(update.effective_user.id)
    else:
        await msg.edit_text("❌ Ошибка")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    if user_id in user_data.users:
        d = user_data.users[user_id].get('downloads', 0)
        q = user_data.users[user_id].get('qr', 0)
        await update.message.reply_text(f"📊 Статистика:\n🎥 Скачиваний: {d}\n📱 QR-кодов: {q}")
    else:
        await update.message.reply_text("📊 У вас пока нет активности")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id
    
    # Проверяем ссылку
    if any(x in text for x in ['.com', '.ru', 'http', 'www', 'youtu', 'tiktok']):
        pref = user_data.get_preference(user_id)
        icons = {'video': '🎥', 'audio': '🎵', 'all': '📦'}
        
        msg = await update.message.reply_text(f"{icons[pref]} Скачиваю...")
        
        files, temp_dir = await download_video(text, pref)
        
        if files:
            user_data.add_download(user_id)
            sent = 0
            
            for f in files:
                if f.endswith('.mp4'):
                    with open(f, 'rb') as v:
                        await update.message.reply_video(v)
                    sent += 1
                elif f.endswith('.mp3'):
                    with open(f, 'rb') as a:
                        await update.message.reply_audio(a)
                    sent += 1
                elif f.endswith(('.jpg', '.png')):
                    with open(f, 'rb') as p:
                        await update.message.reply_photo(p)
                    sent += 1
            
            await msg.delete()
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
        else:
            await msg.edit_text("❌ Не удалось скачать")
    else:
        # Предлагаем QR
        keyboard = [[InlineKeyboardButton("📱 Сделать QR", callback_data=f"qr_{text}")]]
        await update.message.reply_text(
            "Создать QR-код?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    
    if data.startswith('set_'):
        pref = data[4:]
        user_data.set_preference(user_id, pref)
        await query.edit_message_text(f"✅ Формат изменен", reply_markup=get_menu(user_id))
    elif data == 'menu_qr':
        await query.edit_message_text("📱 Отправь /qr текст")
    elif data == 'menu_stats':
        await stats(update, context)
    elif data.startswith('qr_'):
        text = data[3:]
        path = make_qr(text)
        if path:
            with open(path, 'rb') as f:
                await query.message.reply_photo(f)
            os.unlink(path)
            user_data.add_qr(user_id)
        await query.delete_message()

# ===================== ЗАПУСК =====================
def main():
    print("⚡ Запуск HartiDash...")
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("qr", qr_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    port = int(os.environ.get('PORT', 8080))
    url = os.environ.get('RAILWAY_STATIC_URL')
    
    if url:
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="webhook",
            webhook_url=f"https://{url}/webhook"
        )
    else:
        app.run_polling()

if __name__ == '__main__':
    main()
