import os
import csv
import logging
from typing import Dict, Any, List
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ApplicationBuilder
from telegram.error import NetworkError
from dotenv import load_dotenv

# Загружаем переменные окружения из .env (полезно для локального тестирования)
load_dotenv() 

# --- КОНСТАНТЫ ДЛЯ СОСТОЯНИЯ И КЛАВИАТУР ---
USER_ID_KEY = 'registered_id'
BTN_CHECK_PASSES = '📊 Посмотреть количество пропусков'
BTN_CHANGE_ID = '✏️ Сменить номер'

# --- ФУНКЦИИ КЛАВИАТУРЫ ---
def get_main_keyboard():
    """Возвращает клавиатуру с кнопками для просмотра и смены ИД."""
    keyboard = [[BTN_CHECK_PASSES], [BTN_CHANGE_ID]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def remove_keyboard():
    """Возвращает объект для удаления текущей клавиатуры."""
    return ReplyKeyboardRemove()

# --- ПАРАМЕТРЫ WEBHOOK (Для Render) ---
WEBHOOK_PATH = "/telegram" 
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", 10000))
LISTEN_HOST = os.getenv("HOST", "0.0.0.0")
TELEGRAM_API_URL = "https://api.telegram.org/bot"

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- ДАННЫЕ СТУДЕНТОВ ---
STUDENT_DATA: Dict[str, Dict[str, Any]] = {} # Ключ теперь str, так как ID может быть строкой

def load_data(file_path: str = 'разраб.csv') -> None:
    """Загружает данные студентов из CSV-файла в глобальный словарь."""
    global STUDENT_DATA
    STUDENT_DATA = {}
    
    try:
        # ИСПОЛЬЗУЕМ КОДИРОВКУ 'utf-8-sig' ДЛЯ АВТОМАТИЧЕСКОГО УДАЛЕНИЯ BOM (\ufeff)
        with open(file_path, mode='r', encoding='utf-8-sig', newline='') as file:
            # Пытаемся определить разделитель (либо '|', либо ';')
            with open(file_path, 'r', encoding='utf-8-sig') as delimiter_file:
                content = delimiter_file.read(1024)
                # Разделитель, вероятно, ';'
                delimiter_char = '|' if '|' in content and ';' not in content else ';'
            
            # Сброс указателя файла перед передачей в DictReader
            file.seek(0)
            reader = csv.DictReader(file, delimiter=delimiter_char)
            
            for row in reader:
                try:
                    # Убираем лишние пробелы из заголовков, чтобы избежать ошибок
                    row = {k.strip(): v.strip() for k, v in row.items()}
                    
                    # Ключ словаря - ID номер студента (храним как строку для точности)
                    student_id_str = row.get('ID номер')
                    if not student_id_str:
                         continue
                         
                    # Используем ID как строку для ключа
                    student_id = student_id_str 
                    
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
            
        logger.info(f"✅ Данные успешно загружены. Загружено {len(STUDENT_DATA)} записей.")
        
    except FileNotFoundError:
        logger.error(f"❌ Файл {file_path} не найден.")
    except Exception as e:
        logger.error(f"❌ Ошибка при чтении CSV-файла: {e}")


# --- ОБРАБОТЧИКИ КОМАНД ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start, приветствует и проверяет, зарегистрирован ли ИД."""
    # ID Номер хранится в context.user_data как строка
    user_id = context.user_data.get(USER_ID_KEY)

    if user_id:
        # Если ИД уже есть
        reply_text = (
            f'С возвращением! Ваш текущий ID Номер: **{user_id}**.\n'
            'Нажмите кнопку "📊 Посмотреть количество пропусков" ниже, чтобы узнать актуальные данные.'
        )
        keyboard = get_main_keyboard()
    else:
        # Если ИД нет, просим ввести
        reply_text = (
            'Привет! 👋 Я бот для проверки пропусков в ВУЗе.\n'
            'Для начала работы, пожалуйста, **введите свой ID Номер** (например, `2502954`).'
        )
        keyboard = remove_keyboard()

    await update.message.reply_text(reply_text, reply_markup=keyboard, parse_mode='Markdown')


async def change_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускает процесс смены ID Номера."""
    await update.message.reply_text(
        'Хорошо, введите, пожалуйста, новый ID Номер.',
        reply_markup=remove_keyboard()
    )
    # Удаляем старый ИД, чтобы бот ждал новый ввод
    if USER_ID_KEY in context.user_data:
        del context.user_data[USER_ID_KEY]


async def process_data_request(update: Update, context: ContextTypes.DEFAULT_TYPE, search_id: str) -> None:
    """Извлекает и форматирует данные о пропусках по ID."""
    
    if search_id in STUDENT_DATA:
        student = STUDENT_DATA[search_id]
        name = student.get('ФИО', 'Неизвестно')
        absences = student.get('Количество пропусков', 0)
        
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
            f"🆔 **ID:** `{search_id}`\n"
            f"📚 **Количество пропусков (в часах):** {absences}\n"
            f"🚨 **Статус:** {status}"
        )
    else:
        # Эта ветка должна быть недостижима, если ID был проверен ранее
        reply_text = (
            '❌ Ошибка данных. Пожалуйста, введите свой ID Номер снова.'
        )

    await update.message.reply_text(reply_text, parse_mode='Markdown', reply_markup=get_main_keyboard())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовый ввод (как ИД) или нажатие кнопки."""
    user_input = update.message.text.strip()
    search_id = None

    # --- СЦЕНАРИЙ 1: Нажата кнопка "Посмотреть пропуски" ---
    if user_input == BTN_CHECK_PASSES:
        search_id = context.user_data.get(USER_ID_KEY)
        if not search_id:
            # Если ID не найден в памяти, просим ввести его снова
            return await start_command(update, context)

    # --- СЦЕНАРИЙ 2: Нажата кнопка "Сменить номер" ---
    elif user_input == BTN_CHANGE_ID:
        return await change_id_handler(update, context)

    # --- СЦЕНАРИЙ 3: Введен новый ИД Номер (текст) ---
    else:
        search_id = user_input

        # Проверяем, существует ли такой ИД в базе
        if search_id not in STUDENT_DATA:
            message = (
                f'❌ ID Номер **{search_id}** не найден в нашей базе.\n'
                'Пожалуйста, проверьте правильность ввода и попробуйте снова.'
            )
            return await update.message.reply_text(message, parse_mode='Markdown', reply_markup=remove_keyboard())

        # Если ИД найден, сохраняем его для пользователя
        context.user_data[USER_ID_KEY] = search_id

        # ФИО из данных
        name = STUDENT_DATA[search_id].get('ФИО', 'Студент')
        
        message = (
            f'✅ Здравствуйте, **{name}**!\n'
            f'Ваш ID Номер **{search_id}** успешно сохранен.\n'
            'Теперь вы можете просто нажать кнопку "📊 Посмотреть количество пропусков".'
        )
        await update.message.reply_text(
            message,
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        # После сохранения ИД, сразу показываем данные
        return await process_data_request(update, context, search_id)

    # --- ОБЩАЯ ЛОГИКА: Если ID был получен из user_data (СЦЕНАРИЙ 1) ---
    if search_id:
        await process_data_request(update, context, search_id)
    else:
        # Если ни один из сценариев не сработал (например, случайный текст)
        await update.message.reply_text(
            '🤔 Извините, я не понимаю. Введите ваш ID Номер или нажмите /start.'
        )


# --- ГЛАВНАЯ ФУНКЦИЯ ---

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
        
    # 3. Создание приложения
    application = ApplicationBuilder() \
        .token(token) \
        .base_url(TELEGRAM_API_URL) \
        .build()

    # 4. Добавление обработчиков
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    # Обработчик для кнопок встроен в handle_message

    # 5. Запуск бота в режиме WebHook
    full_webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}" if WEBHOOK_URL else "Неизвестно"
    logger.info(f"🚀 Бот запущен в режиме WebHook.")
    logger.info(f"Ожидаемый URL WebHook: {full_webhook_url}, Слушаем {LISTEN_HOST}:{PORT}")
    
    try:
        application.run_webhook(
            listen=LISTEN_HOST,
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=full_webhook_url,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        ) 
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске WebHook: {e}. Проверьте, что в requirements.txt указано 'python-telegram-bot[webhooks]'.")

if __name__ == '__main__':
    # Очистка имени процесса для корректной работы Render
    main()
