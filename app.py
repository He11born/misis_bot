import os
import csv
import logging
from typing import Dict, Any, List
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ApplicationBuilder
from telegram.error import NetworkError
from dotenv import load_dotenv

# Загружаем переменные окружения из .env (полезно для локального тестирования)
load_dotenv() 

# --- ПАРАМЕТРЫ WEBHOOK ---

# Путь, по которому Telegram будет отправлять обновления (часть URL)
WEBHOOK_PATH = "/telegram" 

# Получаем публичный URL хостинга. Render предоставит этот URL.
# Эту переменную нужно будет установить в настройках Render после первого деплоя.
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# Получаем порт, который Render автоматически задает (обычно 10000)
PORT = int(os.getenv("PORT", 10000))

# Получаем хост, на котором нужно слушать (0.0.0.0 для всех интерфейсов)
LISTEN_HOST = os.getenv("HOST", "0.0.0.0")

TELEGRAM_API_URL = "https://api.telegram.org/bot"

# --- КОНЕЦ ПАРАМЕТРОВ WEBHOOK ---

# Установка базового уровня логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Глобальная переменная для хранения данных (инициализируется в load_data)
STUDENT_DATA: Dict[int, Dict[str, Any]] = {}

def load_data(file_path: str = 'разраб.csv') -> None:
    """Загружает данные студентов из CSV-файла в глобальный словарь."""
    global STUDENT_DATA
    STUDENT_DATA = {}
    
    try:
        # ИСПОЛЬЗУЕМ КОДИРОВКУ 'utf-8-sig' ДЛЯ АВТОМАТИЧЕСКОГО УДАЛЕНИЯ BOM (\ufeff)
        with open(file_path, mode='r', encoding='utf-8-sig', newline='') as file:
            # Пытаемся определить разделитель (либо '|', либо ';')
            with open(file_path, 'r', encoding='utf-8-sig') as delimiter_file:
                content = delimiter_file.read(1024)  # Читаем только начало
                # На основании вашего файла 'разраб.csv' разделитель, вероятно, ';'
                delimiter_char = '|' if '|' in content else ';'
            
            # Сброс указателя файла перед передачей в DictReader
            file.seek(0)
            reader = csv.DictReader(file, delimiter=delimiter_char)
            
            for row in reader:
                try:
                    # Убираем лишние пробелы из заголовков, чтобы избежать ошибок
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    
                    # Ключ словаря - ID номер студента (преобразуем в int)
                    student_id_str = row.get('ID номер')
                    if not student_id_str:
                         logger.warning(f"Пропущена строка: нет 'ID номер' в строке: {row}")
                         continue
                         
                    student_id = int(student_id_str)
                    
                    # Преобразуем количество пропусков в int, используя 0 как значение по умолчанию
                    absences_str = row.get('Количество пропусков', '0')
                    try:
                        absences = int(absences_str)
                    except ValueError:
                        absences = 0

                    STUDENT_DATA[student_id] = {
                        'ФИО': row.get('ФИО', 'Неизвестно'),
                        'Количество пропусков': absences
                    }
                except KeyError as e:
                    logger.warning(f"Пропущена строка из-за отсутствия ключа: {e} в строке: {row}")
                except ValueError as e:
                    logger.warning(f"Пропущена строка из-за ошибки преобразования ID: {e} в строке: {row}")
            
        logger.info(f"✅ Данные успешно загружены. Загружено {len(STUDENT_DATA)} записей.")
        
    except FileNotFoundError:
        logger.error(f"❌ Файл {file_path} не найден.")
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении CSV-файла: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start и приветствует пользователя."""
    reply_text = (
        "🤖 Привет! Я Бот Учебного отдела. "
        "Я могу проверить количество пропусков у студентов.\n\n"
        "Чтобы получить информацию, отправьте мне:\n"
        "1. **/check** (для получения списка доступных команд)\n"
        "2. **ID номер студента** (например, `2502954`)"
    )
    await update.message.reply_text(reply_text)

async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /check и показывает доступные команды."""
    reply_text = (
        "🔍 Доступные команды:\n"
        "**/start** - начать работу и получить инструкцию.\n"
        "**/check** - увидеть это сообщение снова.\n\n"
        "Чтобы проверить пропуски, просто отправьте мне ID номер студента."
    )
    await update.message.reply_text(reply_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовые сообщения (ожидается ID студента)."""
    text = update.message.text.strip()
    
    # Пытаемся преобразовать сообщение в целое число (ID студента)
    try:
        student_id = int(text)
    except ValueError:
        if len(text) < 3:
            return 
        
        reply_text = (
            "🤔 Извините, я не понимаю. Пожалуйста, отправьте мне "
            "только **ID номер студента** (7-значное число), например, `2502954`."
        )
        await update.message.reply_text(reply_text)
        return

    # Ищем студента в загруженных данных
    if student_id in STUDENT_DATA:
        student = STUDENT_DATA[student_id]
        name = student['ФИО']
        absences = student['Количество пропусков']
        
        # Определяем статус и цвет эмодзи
        if absences >= 50:
            status = f"🔴 КРИТИЧЕСКИЙ УРОВЕНЬ"
        elif absences >= 20:
            status = f"🟠 ВЫСОКИЙ УРОВЕНЬ"
        elif absences >= 5:
            status = f"🟡 СРЕДНИЙ УРОВЕНЬ"
        else:
            status = f"🟢 НИЗКИЙ УРОВЕНЬ"
        
        reply_text = (
            f"👤 **Студент:** {name}\n"
            f"🆔 **ID:** `{student_id}`\n"
            f"📚 **Пропуски:** {absences}\n"
            f"🚨 **Статус:** {status}"
        )
    else:
        reply_text = (
            f"❌ Студент с ID номером `{student_id}` не найден в нашей базе данных. "
            "Пожалуйста, проверьте правильность ввода."
        )

    await update.message.reply_text(reply_text, parse_mode='Markdown')

def main() -> None:
    """Основная функция для запуска бота."""
    
    # 1. Загрузка данных
    load_data()
    
    # 2. Получение токена и проверка WebHook URL
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("❌ Токен бота не найден. Установите переменную окружения TELEGRAM_BOT_TOKEN.")
        return
    if not WEBHOOK_URL:
        logger.warning("⚠️ WEBHOOK_URL не установлен. Запуск может быть невозможен. Установите его в настройках Render.")
        # Не выходим, так как Render может установить его позже, но логируем предупреждение.
        
    # 3. Создание приложения
    application = ApplicationBuilder() \
        .token(token) \
        .base_url(TELEGRAM_API_URL) \
        .build()

    # 4. Добавление обработчиков
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 5. Запуск бота в режиме WebHook
    full_webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}" if WEBHOOK_URL else "Неизвестно"
    logger.info(f"🚀 Бот запущен в режиме WebHook.")
    logger.info(f"Ожидаемый URL WebHook: {full_webhook_url}, Слушаем {LISTEN_HOST}:{PORT}")
    
    try:
        # run_webhook ожидает входящих соединений и не делает исходящих запросов (кроме set_webhook)
        application.run_webhook(
            listen=LISTEN_HOST,
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=full_webhook_url,
            # Опции для стабильности
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        ) 
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске WebHook: {e}.")

if __name__ == '__main__':
    main()
