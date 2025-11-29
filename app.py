import os
import csv
import logging
from typing import Dict, Any, List
import requests
import io
import base64
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, 
    ApplicationBuilder, ConversationHandler
)
from dotenv import load_dotenv
from fastapi import FastAPI # Новый импорт для веб-сервера
import uvicorn # Новый импорт для запуска сервера

load_dotenv() 

# --- КОНСТАНТЫ СОСТОЯНИЙ ДЛЯ ConversationHandler ---
GETTING_ID, GETTING_ABSENCES = range(2)

# --- КОНСТАНТЫ КЛАВИАТУРЫ ---
USER_ID_KEY = 'registered_id'
BTN_CHECK_PASSES = '📊 Посмотреть количество пропусков'
BTN_CHANGE_ID = '✏️ Сменить номер'

# --- ФУНКЦИИ КЛАВИАТУРЫ ---
def get_main_keyboard():
    keyboard = [[BTN_CHECK_PASSES], [BTN_CHANGE_ID]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def remove_keyboard():
    return ReplyKeyboardRemove()

# --- ПАРАМЕТРЫ GITHUB (Убедитесь, что они установлены в переменных окружения Render) ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# Ожидаемый формат: USERNAME/REPO_NAME/BRANCH_NAME/разраб.csv
REPO_DETAILS_FULL = os.getenv("GIT_REPO_DETAILS")
CSV_URL = os.getenv("CSV_URL") 
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 1234567890)) 

# --- ПАРАМЕТРЫ WEBHOOK (Для Render) ---
WEBHOOK_PATH = "/telegram" 
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
# Порт 10000 используется по умолчанию на Render
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
STUDENT_DATA: Dict[str, Dict[str, Any]] = {} 


# --- ФУНКЦИИ ЗАГРУЗКИ / ПАРСИНГА ДАННЫХ ---
def parse_csv_data(csv_content: str) -> bool:
    """Парсит содержимое CSV-файла (строка) и заполняет STUDENT_DATA."""
    global STUDENT_DATA
    STUDENT_DATA = {}
    csvfile = io.StringIO(csv_content)
    
    try:
        content_snippet = csv_content[:1024]
        delimiter_char = '|' if '|' in content_snippet and ';' not in content_snippet else ';'
        
        reader = csv.DictReader(csvfile, delimiter=delimiter_char)
        
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items()}
            student_id = row.get('ID номер')
            
            if student_id:
                absences_str = row.get('Количество пропусков', '0')
                try:
                    absences = int(absences_str)
                except ValueError:
                    absences = 0

                STUDENT_DATA[student_id] = {
                    'ФИО': row.get('ФИО', 'Неизвестно'),
                    'Количество пропусков': absences
                }
        
        logger.info(f"✅ Данные успешно загружены. Загружено {len(STUDENT_DATA)} записей.")
        return True
    
    except Exception as e:
        logger.error(f"❌ Ошибка при парсинге CSV-данных: {e}")
        return False


def load_data_from_git() -> bool:
    """Загружает данные, скачивая файл с GitHub по прямому URL."""
    if not CSV_URL:
        logger.error("❌ Переменная CSV_URL не установлена. Загрузка данных невозможна.")
        return False
    
    try:
        response = requests.get(CSV_URL)
        response.raise_for_status()
        
        return parse_csv_data(response.text)
        
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка при скачивании файла с GitHub ({CSV_URL}): {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка при загрузке данных: {e}")
        return False


# --- ФУНКЦИИ РЕДАКТИРОВАНИЯ ДАННЫХ В GIT ---
def update_github_file(new_csv_content: str, commit_message: str) -> bool:
    """Обновляет файл разраб.csv на GitHub через API."""
    if not GITHUB_TOKEN or not REPO_DETAILS_FULL:
        logger.error("❌ Отсутствуют GITHUB_TOKEN или GIT_REPO_DETAILS.")
        return False
        
    try:
        user, repo, branch, filepath = REPO_DETAILS_FULL.split('/', 3)
    except ValueError:
        logger.error(f"❌ Неверный формат GIT_REPO_DETAILS: {REPO_DETAILS_FULL}")
        return False

    # 1. Получаем SHA текущего файла
    contents_url = f"https://api.github.com/repos/{user}/{repo}/contents/{filepath}?ref={branch}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    try:
        response = requests.get(contents_url, headers=headers)
        response.raise_for_status()
        current_file_data = response.json()
        current_sha = current_file_data['sha']
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка получения SHA файла: {e}")
        return False

    # 2. Подготавливаем данные для нового коммита
    encoded_content = base64.b64encode(new_csv_content.encode('utf-8')).decode('utf-8')
    
    payload = {
        "message": commit_message,
        "content": encoded_content,
        "sha": current_sha,
        "branch": branch
    }

    # 3. Отправляем новый контент
    try:
        response = requests.put(contents_url, headers=headers, json=payload)
        response.raise_for_status()
        logger.info(f"✅ Файл {filepath} успешно обновлен на ветке {branch}. Коммит: {response.json()['commit']['sha']}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Ошибка коммита на GitHub: {e}")
        return False


def convert_data_to_csv_string() -> str:
    """Преобразует текущий STUDENT_DATA в строку CSV."""
    if not STUDENT_DATA:
        return "ID номер;ФИО;Количество пропусков\n"
        
    fieldnames = ['ID номер', 'ФИО', 'Количество пропусков']
    output = io.StringIO()
    # Используем DictWriter для записи в CSV
    writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=';')
    
    writer.writeheader()
    for student_id, data in STUDENT_DATA.items():
        row = {
            'ID номер': student_id,
            'ФИО': data.get('ФИО', 'Неизвестно'),
            'Количество пропусков': data.get('Количество пропусков', 0)
        }
        writer.writerow(row)
        
    return output.getvalue()


# --- ОБРАБОТЧИКИ КОМАНД ПОЛЬЗОВАТЕЛЯ ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start."""
    user_id = context.user_data.get(USER_ID_KEY)

    if user_id:
        reply_text = (
            f'С возвращением! Ваш текущий ID Номер: **{user_id}**.\n'
            'Нажмите кнопку "📊 Посмотреть количество пропусков" ниже, чтобы узнать актуальные данные.'
        )
        keyboard = get_main_keyboard()
    else:
        reply_text = (
            'Привет! 👋 Я бот для проверки пропусков в ВУЗе.\n'
            'Для начала работы, пожалуйста, **введите свой ID Номер** (номер студенческого билета).'
        )
        keyboard = remove_keyboard()

    await update.message.reply_text(reply_text, reply_markup=keyboard, parse_mode='Markdown')


async def change_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Запускает процесс смены ID Номера."""
    await update.message.reply_text(
        'Хорошо, введите, пожалуйста, новый ID Номер.',
        reply_markup=remove_keyboard()
    )
    if USER_ID_KEY in context.user_data:
        del context.user_data[USER_ID_KEY]


async def process_data_request(update: Update, context: ContextTypes.DEFAULT_TYPE, search_id: str) -> None:
    """Извлекает и форматирует данные о пропусках по ID."""
    
    if search_id in STUDENT_DATA:
        student = STUDENT_DATA[search_id]
        name = student.get('ФИО', 'Неизвестно')
        absences = student.get('Количество пропусков', 0)
            
        reply_text = (
            f"👤 **Студент:** {name}\n"
            f"🆔 **ID:** `{search_id}`\n"
            f"📚 **Количество пропусков (в часах):** {absences}"
        )
    else:
        reply_text = (
            '❌ Ошибка данных. Пожалуйста, введите свой ID Номер снова.'
        )

    await update.message.reply_text(reply_text, parse_mode='Markdown', reply_markup=get_main_keyboard())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает текстовый ввод (как ИД) или нажатие кнопки."""
    user_input = update.message.text.strip()
    search_id = None

    if user_input == BTN_CHECK_PASSES:
        search_id = context.user_data.get(USER_ID_KEY)
        if not search_id:
            return await start_command(update, context)

    elif user_input == BTN_CHANGE_ID:
        return await change_id_handler(update, context)

    else:
        search_id = user_input

        if search_id not in STUDENT_DATA:
            message = (
                f'❌ ID Номер **{search_id}** не найден в нашей базе.\n'
                'Пожалуйста, проверьте правильность ввода и попробуйте снова.'
            )
            return await update.message.reply_text(message, parse_mode='Markdown', reply_markup=remove_keyboard())

        context.user_data[USER_ID_KEY] = search_id
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
        return await process_data_request(update, context, search_id)

    if search_id:
        await process_data_request(update, context, search_id)
    else:
        await update.message.reply_text(
            '🤔 Извините, я не понимаю. Введите ваш ID Номер или нажмите /start.'
        )

# --- КОМАНДЫ АДМИНИСТРАТОРА ---

async def reload_data_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда для администратора, чтобы принудительно обновить данные из Git."""
    
    if update.effective_user.id != ADMIN_USER_ID:
        logger.warning(f"Попытка несанкционированного обновления данных от пользователя ID: {update.effective_user.id}")
        await update.message.reply_text("❌ У вас нет прав на выполнение этой команды.")
        return

    await update.message.reply_text("⏳ Начинаю загрузку актуальных данных из Git...")
    
    if load_data_from_git():
        await update.message.reply_text(
            f"✅ Данные успешно обновлены! Загружено {len(STUDENT_DATA)} записей."
        )
    else:
        await update.message.reply_text(
            "❌ Ошибка загрузки данных. Проверьте логи и переменную CSV_URL."
        )

# --- ОБРАБОТЧИКИ ДЛЯ РЕДАКТИРОВАНИЯ ДАННЫХ (ConversationHandler) ---

async def start_edit_pass_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс редактирования пропусков."""
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("❌ У вас нет прав на выполнение этой команды.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📝 **Режим редактирования пропусков**\nВведите ID Номер студента, пропуски которого нужно изменить.",
        reply_markup=remove_keyboard(),
        parse_mode='Markdown'
    )
    return GETTING_ID


async def get_student_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает ID студента и запрашивает новое количество пропусков."""
    student_id = update.message.text.strip()
    
    if student_id not in STUDENT_DATA:
        await update.message.reply_text(
            f"❌ ID Номер **{student_id}** не найден в базе. Попробуйте снова или нажмите /cancel.",
            parse_mode='Markdown'
        )
        return GETTING_ID

    context.user_data['temp_edit_id'] = student_id
    current_absences = STUDENT_DATA[student_id].get('Количество пропусков', 0)
    student_name = STUDENT_DATA[student_id].get('ФИО', 'Неизвестно')
    
    await update.message.reply_text(
        f"✅ ID Номер **{student_id}** ({student_name}) найден.\n"
        f"Текущее количество пропусков: **{current_absences}**.\n"
        "Введите **новое** количество пропусков (целое число):",
        parse_mode='Markdown'
    )
    return GETTING_ABSENCES


async def get_absences_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает новое количество пропусков, обновляет данные локально и на GitHub."""
    new_absences_str = update.message.text.strip()
    
    try:
        new_absences = int(new_absences_str)
        if new_absences < 0:
             raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введите, пожалуйста, только целое положительное число (например, 15). Попробуйте снова или /cancel."
        )
        return GETTING_ABSENCES

    student_id = context.user_data.pop('temp_edit_id')
    student_name = STUDENT_DATA[student_id].get('ФИО', 'Неизвестно')
    
    # 1. Обновление локальных данных
    STUDENT_DATA[student_id]['Количество пропусков'] = new_absences
    
    # 2. Формирование нового CSV
    new_csv_content = convert_data_to_csv_string()
    
    # 3. Коммит на GitHub
    commit_message = f"🤖 Обновление пропусков: {student_name} ({student_id}) -> {new_absences}"
    
    await update.message.reply_text("⏳ Данные обновлены локально. Отправляю коммит на GitHub...")
    
    if update_github_file(new_csv_content, commit_message):
        final_message = (
            f"🎉 Успешно!\n"
            f"Пропуски для **{student_name}** (`{student_id}`) установлены на **{new_absences}**.\n"
            "Изменение зафиксировано на GitHub."
        )
    else:
        final_message = (
            "⚠️ **Критическая ошибка коммита!**\n"
            "Локальные данные обновлены, но коммит на GitHub не удался. Проверьте токен и логи."
        )

    await update.message.reply_text(final_message, parse_mode='Markdown', reply_markup=get_main_keyboard())
    return ConversationHandler.END


async def cancel_edit_pass(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отменяет процесс редактирования."""
    if 'temp_edit_id' in context.user_data:
        del context.user_data['temp_edit_id']
        
    await update.message.reply_text(
        'Операция редактирования отменена.', 
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END


# --- ГЛОБАЛЬНЫЙ ЭКЗЕМПЛЯР FASTAPI (для Uvicorn) ---
fastapi_app = FastAPI()

# Health Check Endpoint для Uptime Robot
@fastapi_app.get("/")
def health_check():
    """Возвращает HTTP 200 OK для мониторинга Uptime Robot."""
    return {"status": "ok", "app": "Telegram Bot Webhook"}


# --- ГЛАВНАЯ ФУНКЦИЯ ---
def init_application() -> Application:
    """Инициализирует и настраивает PTB Application."""
    
    # 1. Первая загрузка данных при старте сервиса
    load_data_from_git()
    
    # 2. Получение токена
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("❌ Токен бота не найден. Установите переменную окружения TELEGRAM_BOT_TOKEN.")
        raise ValueError("TELEGRAM_BOT_TOKEN не установлен")
        
    # 3. Создание приложения PTB
    application = ApplicationBuilder() \
        .token(token) \
        .build()

    # 4. Добавление обработчиков
    edit_pass_handler = ConversationHandler(
        entry_points=[CommandHandler("edit_pass", start_edit_pass_command)],
        states={
            GETTING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_student_id)],
            GETTING_ABSENCES: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_absences_count)],
        },
        fallbacks=[CommandHandler('cancel', cancel_edit_pass)],
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("reload_data", reload_data_command)) 
    application.add_handler(edit_pass_handler) 
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 5. Настройка WebHook в PTB
    application.run_webhook(
        listen=LISTEN_HOST,
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=f"{WEBHOOK_URL}{WEBHOOK_PATH}" if WEBHOOK_URL else "Неизвестно",
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
        webhook_server=fastapi_app # Интегрируем PTB в наш FastAPI инстанс
    )
    
    logger.info(f"🚀 Бот настроен. Сервер будет запущен Uvicorn'ом на {LISTEN_HOST}:{PORT}")
    return application

# Инициализация приложения при старте Uvicorn
try:
    init_application()
except ValueError:
    logger.error("Критическая ошибка инициализации: не удалось запустить приложение из-за отсутствия токена.")

# Uvicorn будет использовать глобальную переменную fastapi_app для запуска
# Запуск выполняется командой `uvicorn app:fastapi_app --host 0.0.0.0 --port 10000`
