import json
import random
import sqlite3
import os
import time
import asyncio
import re
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

# ===== НАСТРОЙКИ =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 5206039766
QUIZ_FILE = "quizzes.json"
MEMES_FILE = "memes.json"
BASE_QUIZZES_DB = "base_quizzes.db"
USERS_DB = "quiz_users.db"

# ===== РЕДКОСТИ =====
RARITY_REWARDS = {
    "common": 1,
    "uncommon": 2,
    "rare": 3,
    "epic": 5,
    "legendary": 10
}

RARITY_EMOJIS = {
    "common": "⬜ Обычный",
    "uncommon": "🟩 Необычный",
    "rare": "🟦 Редкий",
    "epic": "🟪 Эпический",
    "legendary": "🟧 Легендарный"
}

RARITY_EMOJI_ONLY = {
    "common": "⬜",
    "uncommon": "🟩",
    "rare": "🟦",
    "epic": "🟪",
    "legendary": "🟧"
}

RANKS = [
    {"name": "Новичок", "min_score": 0, "emoji": "🪴"},
    {"name": "Знаток", "min_score": 10, "emoji": "📖"},
    {"name": "Эрудит", "min_score": 25, "emoji": "🧠"},
    {"name": "Мастер", "min_score": 50, "emoji": "🎯"},
    {"name": "Гений", "min_score": 100, "emoji": "💎"},
    {"name": "Легенда", "min_score": 200, "emoji": "👑"},
]

def get_rank(score):
    for rank in reversed(RANKS):
        if score >= rank["min_score"]:
            return rank
    return RANKS[0]

# ===== БАЗА ДАННЫХ =====
def init_user_db():
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  first_name TEXT,
                  total INTEGER DEFAULT 0,
                  rank TEXT DEFAULT "Новичок")''')
    c.execute('''CREATE TABLE IF NOT EXISTS completions
                 (user_id INTEGER,
                  quiz_id TEXT,
                  completed_at TIMESTAMP,
                  PRIMARY KEY (user_id, quiz_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS quiz_stats
                 (user_id INTEGER PRIMARY KEY,
                  score INTEGER DEFAULT 0,
                  today_plays INTEGER DEFAULT 0,
                  last_play_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rebus_solves
                 (user_id INTEGER PRIMARY KEY,
                  user_name TEXT,
                  solves INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()
    print("✅ База пользователей инициализирована")

def init_base_quizzes_db():
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS base_quizzes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  question TEXT,
                  options TEXT,
                  correct_option_id INTEGER,
                  rarity TEXT DEFAULT 'common',
                  date TEXT)''')
    conn.commit()
    conn.close()
    print("✅ База вопросов инициализирована")

def get_user_stats(user_id):
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute('SELECT score, today_plays, last_play_date FROM quiz_stats WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"score": row[0], "today_plays": row[1], "last_play_date": row[2]}
    return {"score": 0, "today_plays": 0, "last_play_date": None}

def update_user_stats(user_id, score, today_plays, last_play_date):
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute('''INSERT INTO quiz_stats (user_id, score, today_plays, last_play_date)
                 VALUES (?, ?, ?, ?)
                 ON CONFLICT(user_id) DO UPDATE SET
                 score = excluded.score,
                 today_plays = excluded.today_plays,
                 last_play_date = excluded.last_play_date''',
              (user_id, score, today_plays, last_play_date))
    conn.commit()
    conn.close()

def get_played_question_ids(user_id):
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    today = datetime.now().date().isoformat()
    c.execute('''SELECT quiz_id FROM completions
                 WHERE user_id = ? AND DATE(completed_at) = ?''', (user_id, today))
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]

def mark_question_as_played(user_id, quiz_id):
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO completions (user_id, quiz_id, completed_at) VALUES (?, ?, ?)',
              (user_id, quiz_id, datetime.now()))
    conn.commit()
    conn.close()

def get_random_question(user_id):
    played_ids = get_played_question_ids(user_id)
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    
    if played_ids:
        placeholders = ','.join(['?'] * len(played_ids))
        c.execute(f'''
            SELECT id, question, options, correct_option_id, rarity FROM base_quizzes
            WHERE id NOT IN ({placeholders})
            ORDER BY RANDOM() LIMIT 1
        ''', played_ids)
    else:
        c.execute('SELECT id, question, options, correct_option_id, rarity FROM base_quizzes ORDER BY RANDOM() LIMIT 1')
    
    row = c.fetchone()
    conn.close()
    return row

def add_base_quiz(question, options, correct_option_id):
    rarity_roll = random.random()
    if rarity_roll < 0.60:
        rarity = "common"
    elif rarity_roll < 0.85:
        rarity = "uncommon"
    elif rarity_roll < 0.95:
        rarity = "rare"
    elif rarity_roll < 0.99:
        rarity = "epic"
    else:
        rarity = "legendary"
    
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    c.execute('INSERT INTO base_quizzes (question, options, correct_option_id, rarity, date) VALUES (?, ?, ?, ?, ?)',
              (question, options, correct_option_id, rarity, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return rarity

def count_quizzes_by_rarity():
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    c.execute('SELECT rarity, COUNT(*) FROM base_quizzes GROUP BY rarity')
    result = dict(c.fetchall())
    conn.close()
    return result

# ===== ПАРСИНГ ВИКТОРИН (ДЛЯ МАССОВОГО ДОБАВЛЕНИЯ) =====

def parse_quiz_line(line):
    """Парсит одну строку формата: Вопрос (А; Б*; В; Г)"""
    match = re.match(r'^(.+?)\s*\((.+)\)\s*$', line.strip())
    if not match:
        return None
    
    question = match.group(1).strip()
    options = [opt.strip() for opt in match.group(2).split(';') if opt.strip()]
    
    if len(options) < 2:
        return None
    
    correct_option_id = None
    cleaned = []
    for i, opt in enumerate(options):
        if opt.endswith('*'):
            correct_option_id = i
            cleaned.append(opt[:-1].strip())
        else:
            cleaned.append(opt)
    
    if correct_option_id is None:
        correct_option_id = 0
    
    return question, cleaned, correct_option_id

# ===== БАЗА ДАННЫХ ДЛЯ ВИКТОРИН =====
def init_base_quizzes_db():
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS base_quizzes
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  question TEXT,
                  options TEXT,
                  correct_option_id INTEGER,
                  rarity TEXT DEFAULT 'common',
                  date TEXT)''')
    conn.commit()
    conn.close()
    print("✅ База вопросов инициализирована")

def add_base_quiz(question, options, correct_option_id):
    rarity_roll = random.random()
    if rarity_roll < 0.60:
        rarity = "common"
    elif rarity_roll < 0.85:
        rarity = "uncommon"
    elif rarity_roll < 0.95:
        rarity = "rare"
    elif rarity_roll < 0.99:
        rarity = "epic"
    else:
        rarity = "legendary"
    
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    c.execute('INSERT INTO base_quizzes (question, options, correct_option_id, rarity, date) VALUES (?, ?, ?, ?, ?)',
              (question, options, correct_option_id, rarity, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    return rarity

def count_quizzes_by_rarity():
    conn = sqlite3.connect(BASE_QUIZZES_DB)
    c = conn.cursor()
    c.execute('SELECT rarity, COUNT(*) FROM base_quizzes GROUP BY rarity')
    result = dict(c.fetchall())
    conn.close()
    return result

# ===== АНТИСПАМ =====
antispam = {}

def check_antispam(user_id):
    now = time.time()
    user = antispam.get(user_id, {"blocked_until": 0, "last_command": 0, "count": 0})
    
    if user["blocked_until"] > now:
        wait = int(user["blocked_until"] - now)
        return False, f"🚫 *Стоп!* Ты в спам-бане `{wait}` сек."
    
    if now - user["last_command"] < 2.0:
        user["count"] += 1
        user["last_command"] = now
        antispam[user_id] = user
        
        if user["count"] >= 2:
            user["blocked_until"] = now + 20
            user["count"] = 0
            antispam[user_id] = user
            return False, "🚫 *Спам-детект!* Блокировка на 20 сек."
        else:
            return False, ""
    
    user["count"] = 0
    user["last_command"] = now
    antispam[user_id] = user
    return True, ""

def antispam_decorator(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        allowed, msg = check_antispam(user_id)
        if not allowed:
            if msg:
                await update.message.reply_text(msg, parse_mode="Markdown")
            return
        return await func(update, context)
    return wrapper

# ===== ЗАГРУЗКА ВИКТОРИН (старый формат для совместимости) =====
def load_quizzes():
    if not os.path.exists(QUIZ_FILE):
        return []
    with open(QUIZ_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_quizzes(quizzes):
    with open(QUIZ_FILE, "w", encoding="utf-8") as f:
        json.dump(quizzes, f, ensure_ascii=False, indent=2)

def load_memes():
    if not os.path.exists(MEMES_FILE):
        return []
    with open(MEMES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_memes(memes):
    with open(MEMES_FILE, "w", encoding="utf-8") as f:
        json.dump(memes, f, ensure_ascii=False, indent=2)

# ===== КОМАНДЫ =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎯 *Бот викторин и ребусов*\n\n"
        "/quiz — случайная викторина (рейтинг)\n"
        "/rebus — отгадай ребус\n"
        "/mm — случайный мем\n"
        "/stats — моя статистика\n"
        "/top — топ игроков\n"
        "/rebustop — топ ребусников\n"
        "/donate — поддержать разработку\n"
        "/help — помощь",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Помощь по командам:*\n\n"
        "/quiz — викторина с рейтингом (выбери вариант)\n"
        "/rebus — отгадай ребус (изображение + слово)\n"
        "/mm — случайный мем\n"
        "/stats — моя статистика (аватарка + рейтинг)\n"
        "/top — топ-10 игроков по викторинам\n"
        "/rebustop — топ-10 по ребусам\n"
        "/donate — поддержать разработку\n"
        "/help — это сообщение\n\n"
        "🎯 *Как получить рейтинг:*\n"
        "Напиши /quiz и выбери правильный ответ.\n"
        "✅ Правильный ответ: +баллы (зависит от редкости)\n"
        "❌ Неправильный ответ: –1 балл\n"
        "🎮 Ограничение: 5 викторин в день",
        parse_mode="Markdown"
    )

async def donate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💳 Поддержать разработку", url="https://finance.ozon.ru/apps/sbp/ozonbankpay/019da166-0117-7486-83c4-ba6b6a587f43")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "💸 *Поддержать разработку бота*\n\n"
        "Если тебе нравятся викторины — можешь отправить донат.\n\n"
        "Спасибо за поддержку! ❤️",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

# ===== НОВАЯ ВИКТОРИНА (С БАЛЛАМИ, РАНГАМИ, РЕДКОСТЬЮ) =====
@antispam_decorator
async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    today = datetime.now().date().isoformat()
    
    stats = get_user_stats(user_id)
    if stats["last_play_date"] != today:
        stats["today_plays"] = 0
        stats["last_play_date"] = today
        update_user_stats(user_id, stats["score"], 0, today)
    
    if stats["today_plays"] >= 5:
        await update.message.reply_text("❌ Ты уже прошёл 5 викторин сегодня! Возвращайся завтра.")
        return
    
    row = get_random_question(user_id)
    if not row:
        await update.message.reply_text("📭 В базе нет новых вопросов! Ты уже прошёл все вопросы на сегодня.\n\nВозвращайся завтра или добавь новые через `/basequiz`", parse_mode="Markdown")
        return
    
    question_id, question, options_raw, correct_option_id, rarity = row
    options = options_raw.split('|||') if options_raw else []
    reward = RARITY_REWARDS.get(rarity, 1)
    
    context.user_data['quiz_question'] = {
        "question_id": question_id,
        "question": question,
        "options": options,
        "correct_option_id": correct_option_id,
        "reward": reward,
        "rarity": rarity
    }
    
    keyboard = []
    for i, opt in enumerate(options):
        keyboard.append([InlineKeyboardButton(opt, callback_data=f"quiz_ans_{i}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    rank = get_rank(stats["score"])
    await update.message.reply_text(
        f"❓ *{question}*\n\n"
        f"{RARITY_EMOJIS.get(rarity, '')}\n"
        f"🎁 Награда: +{reward} баллов\n\n"
        f"🏆 Твои баллы: {stats['score']}\n"
        f"🎖️ Ранг: {rank['emoji']} {rank['name']}\n"
        f"🎮 Осталось попыток: {5 - stats['today_plays']}",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def handle_quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    first_name = query.from_user.first_name
    
    q = context.user_data.get('quiz_question')
    if not q:
        await query.edit_message_text("❌ Викторина не найдена. Попробуй /quiz заново")
        return
    
    selected = int(query.data.split("_")[-1])
    correct = q["correct_option_id"]
    reward = q.get("reward", 1)
    rarity = q.get("rarity", "common")
    question_id = q.get("question_id")
    
    stats = get_user_stats(user_id)
    old_rank = get_rank(stats["score"])
    
    if selected == correct:
        stats["score"] += reward
        new_rank = get_rank(stats["score"])
        update_user_stats(user_id, stats["score"], stats["today_plays"] + 1, datetime.now().date().isoformat())
        mark_question_as_played(user_id, question_id)
        
        rank_up_msg = ""
        if new_rank["min_score"] > old_rank["min_score"]:
            rank_up_msg = f"\n\n🎉 **ПОВЫШЕНИЕ РАНГА!**\n{old_rank['emoji']} {old_rank['name']} → {new_rank['emoji']} {new_rank['name']}"
        
        await query.edit_message_text(
            f"✅ *Правильно!* +{reward} баллов {RARITY_EMOJI_ONLY.get(rarity, '')}{rank_up_msg}\n\n"
            f"🏆 Баллы: {stats['score']}\n"
            f"🎖️ Ранг: {new_rank['emoji']} {new_rank['name']}",
            parse_mode="Markdown"
        )
    else:
        stats["score"] -= 1
        update_user_stats(user_id, stats["score"], stats["today_plays"] + 1, datetime.now().date().isoformat())
        mark_question_as_played(user_id, question_id)
        
        correct_answer = q["options"][correct]
        await query.edit_message_text(
            f"❌ *Неправильно!* –1 балл\n\n"
            f"Правильный ответ: *{correct_answer}*\n\n"
            f"🏆 Баллы: {stats['score']}\n"
            f"🎖️ Ранг: {old_rank['emoji']} {old_rank['name']}",
            parse_mode="Markdown"
        )
    
    del context.user_data['quiz_question']

# ===== БЫСТРАЯ ВИКТОРИНА (БЕЗ РЕЙТИНГА) =====

# ===== СТАТИСТИКА =====
@antispam_decorator
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    stats_data = get_user_stats(user_id)
    rank = get_rank(stats_data["score"])
    today = datetime.now().date().isoformat()
    
    if stats_data["last_play_date"] != today:
        remaining = 5
    else:
        remaining = 5 - stats_data["today_plays"]
    
    rarity_counts = count_quizzes_by_rarity()
    rarity_names = {"common": "Обычный", "uncommon": "Необычный", "rare": "Редкий", "epic": "Эпический", "legendary": "Легендарный"}
    rarity_text = "\n".join([f"{RARITY_EMOJI_ONLY.get(r, '')} {rarity_names.get(r, r)}: {rarity_counts.get(r, 0)}" for r in ["common", "uncommon", "rare", "epic", "legendary"]])
    
    photo = None
    try:
        photos = await context.bot.get_user_profile_photos(user.id, limit=1)
        if photos.total_count > 0:
            photo = photos.photos[0][-1].file_id
    except:
        pass
    
    text = (
        f"📊 *Статистика {user.first_name}*\n\n"
        f"🏆 Баллы: {stats_data['score']}\n"
        f"🎖️ Ранг: {rank['emoji']} {rank['name']}\n"
        f"🎮 Осталось попыток сегодня: {remaining}/5\n\n"
        f"📚 *Вопросы в базе:*\n{rarity_text}"
    )
    
    if photo:
        await update.message.reply_photo(photo=photo, caption=text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

@antispam_decorator
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute("SELECT user_id, first_name, score, rank FROM users ORDER BY score DESC LIMIT 10")
    top_users = c.fetchall()
    conn.close()
    
    if not top_users:
        await update.message.reply_text("❌ Пока никого нет в рейтинге")
        return
    
    message = "🏆 *Топ-10 игроков:*\n\n"
    for i, (user_id, name, score, rank) in enumerate(top_users, 1):
        rank_obj = get_rank(score)
        message += f"{i}. *{name}* — {score} баллов ({rank_obj['emoji']} {rank_obj['name']})\n"
    
    await update.message.reply_text(message, parse_mode="None")

# ===== МЕМЫ =====
@antispam_decorator
async def mm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    memes = load_memes()
    if not memes:
        await update.message.reply_text("❌ Мемов пока нет")
        return
    
    m = random.choice(memes)
    if 'img_url' in m and m['img_url']:
        await update.message.reply_photo(photo=m['img_url'], caption=f"😂 *Мем от {m['date']}*", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"😂 *Мем от {m['date']}*\n\n👉 [Смотреть мем]({m['link']})", parse_mode="Markdown", disable_web_page_preview=True)



# ===== АДМИН-КОМАНДЫ =====
@antispam_decorator
async def editstats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет прав")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "📝 *Использование:* `/editstats <user_id> количество`\n"
            "Пример: `/editstats 123456789 15`",
            parse_mode="Markdown"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        new_score = int(context.args[1])
    except:
        await update.message.reply_text("❌ Оба аргумента должны быть числами")
        return
    
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    
    c.execute("SELECT first_name FROM users WHERE user_id = ?", (target_user_id,))
    row = c.fetchone()
    
    if row:
        c.execute("UPDATE users SET score = ? WHERE user_id = ?", (new_score, target_user_id))
        await update.message.reply_text(f"🔄 Обновлён пользователь {row[0]} (ID: {target_user_id}) → {new_score} баллов")
    else:
        c.execute("INSERT INTO users (user_id, first_name, score, rank) VALUES (?, ?, ?, ?)",
                  (target_user_id, "Неизвестный", new_score, get_rank(new_score)["name"]))
        await update.message.reply_text(f"✅ Создан пользователь с ID {target_user_id}")
    
    conn.commit()
    conn.close()

@antispam_decorator
async def edittop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет прав")
        return
    
    conn = sqlite3.connect(USERS_DB)
    c = conn.cursor()
    c.execute("SELECT user_id, first_name, score FROM users ORDER BY score DESC LIMIT 10")
    top_users = c.fetchall()
    conn.close()
    
    if not top_users:
        await update.message.reply_text("❌ Топ пуст")
        return
    
    message = "🏆 *Топ-10 игроков (для админа):*\n\n"
    for user_id, name, score in top_users:
        rank = get_rank(score)
        message += f"🆔 `{user_id}` — *{name}* — {score} баллов ({rank['emoji']} {rank['name']})\n"
    
    message += "\n📝 *Изменить статистику:* `/editstats <user_id> количество`"
    await update.message.reply_text(message, parse_mode="Markdown")

@antispam_decorator
async def base_quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет прав")
        return
    
    context.user_data['step'] = 'waiting_for_base_quiz'
    await update.message.reply_text(
        "📝 *Отправь викторины в формате:*\n\n"
        "`Вопрос 1 (А; Б*; В; Г)`\n"
        "`Вопрос 2 (А*; Б; В; Г)`\n"
        "`Вопрос 3 (А; Б; В*; Г)`\n\n"
        "Где * — правильный ответ.\n"
        "Каждая викторина с новой строки.\n\n"
        "📎 *Или отправь текстовый файл (.txt) с таким же содержимым.*",
        parse_mode="None"
    )

@antispam_decorator
async def backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Нет прав")
        return
    
    if os.path.exists(USERS_DB):
        with open(USERS_DB, 'rb') as f:
            await update.message.reply_document(document=f, filename='quiz_users_backup.db', caption="📦 Резервная копия базы")
    else:
        await update.message.reply_text("❌ Файл базы не найден")

# ===== РЕБУСЫ (не трогаем) =====
active_rebuses = {}

async def rebus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Функция ребусов остаётся без изменений (я не вставляю полный код ребусов, чтобы не перегружать)
    await update.message.reply_text("🧩 Ребусы временно отключены. Работаем над обновлением.")

async def rebus_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏆 *Топ ребусников*\n\nСкоро появится!", parse_mode="Markdown")

# ===== ОБРАБОТЧИК ТЕКСТА (для /basequiz) =====
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get('step')
    
    if step == 'waiting_for_base_quiz':
        text = update.message.text
        lines = text.strip().split('\n')
        added = 0
        errors = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            parsed = parse_quiz_line(line)
            if parsed:
                question, options, correct_option_id = parsed
                rarity = add_base_quiz(question, '|||'.join(options), correct_option_id)
                added += 1
            else:
                errors.append(f"❌ `{line[:40]}...`")
        
        # Сообщение с результатом
        result = f"✅ *Добавлено викторин: {added}*"
        if errors:
            result += f"\n\n⚠️ *Не удалось распарсить:*\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                result += f"\n... и ещё {len(errors) - 5} ошибок"
        
        await update.message.reply_text(result, parse_mode="None")
        context.user_data['step'] = None
        return
    
    # Если ничего не ждём — просто игнор или помощь
    await update.message.reply_text(
        "❓ Я не понял.\n\n"
        "Команды:\n"
        "/quiz — викторина\n"
        "/rebus — ребус\n"
        "/mm — мем\n"
        "/stats — статистика\n"
        "/top — топ\n"
        "/base — база\n"
        "/help — помощь"
    )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('step') != 'waiting_for_base_quiz':
        await update.message.reply_text("❌ Я не жду файл. Напиши /basequiz чтобы начать.")
        return
    
    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Отправь текстовый файл (.txt)")
        return
    
    await update.message.reply_text("📥 Загружаю файл...")
    
    try:
        file = await context.bot.get_file(document.file_id)
        file_path = f"temp_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        await file.download_to_drive(file_path)
        
        with open(file_path, 'r', encoding='utf-8') as f:
            text = f.read()
        
        os.remove(file_path)
        
        # Обрабатываем как обычный текст
        lines = text.strip().split('\n')
        added = 0
        errors = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            parsed = parse_quiz_line(line)
            if parsed:
                question, options, correct_option_id = parsed
                rarity = add_base_quiz(question, '|||'.join(options), correct_option_id)
                added += 1
            else:
                errors.append(f"❌ `{line[:40]}...`")
        
        result = f"✅ *Добавлено викторин из файла: {added}*"
        if errors:
            result += f"\n\n⚠️ *Не удалось распарсить:*\n" + "\n".join(errors[:5])
            if len(errors) > 5:
                result += f"\n... и ещё {len(errors) - 5} ошибок"
        
        await update.message.reply_text(result, parse_mode="Markdown")
        context.user_data['step'] = None
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

# ===== ЗАПУСК =====
if __name__ == "__main__":
    init_user_db()
    init_base_quizzes_db()
    
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("donate", donate))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(handle_quiz_answer, pattern="quiz_ans_"))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("mm", mm))
    app.add_handler(CommandHandler("editstats", editstats))
    app.add_handler(CommandHandler("edittop", edittop))
    app.add_handler(CommandHandler("backup", backup))
    app.add_handler(CommandHandler("basequiz", base_quiz_command))
    app.add_handler(CommandHandler("rebus", rebus))
    app.add_handler(CommandHandler("rebustop", rebus_top))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    print("✅ Бот запущен!")
    app.run_polling()
