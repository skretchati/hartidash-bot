FROM python:3.11-slim

WORKDIR /app

# Устанавливаем ffmpeg и системные зависимости
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Копируем файлы
COPY requirements.txt .
COPY bot.py .
COPY Procfile* ./

# Устанавливаем Python-зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Запускаем бота
CMD ["python", "bot.py"]
