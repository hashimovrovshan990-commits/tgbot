# -*- coding: utf-8 -*-
"""
Trader's Journal - complete ready-to-run bot.py

Функции:
- Полный flow добавления сделок (pair/type/dates/amount/tp/status/strategy/checklist/notes/photo)
- Календарь с днями недели в одну строку + кнопки "Сегодня" и "Вчера"
- Экспорт: пользователь выбирает аккаунт/профиль, получает .xlsx файл для скачивания
- Подписки: автоматическая через Telegram Payments (send_invoice, successful_payment)
- Шаблоны чеклистов и редактирование чеклистов
- Команда /grant_manual для админа
- Валидации, защитa SQL-полей, логирование и базовая структура для миграции на Postgres
- Структура для тестов (tests/*) и Docker (Dockerfile/docker-compose)

ИСПРАВЛЕННЫЕ ОШИБКИ:
1. Добавлен импорт psycopg2 с try-except
2. Добавлен импорт Workbook из openpyxl
3. Добавлены переменные ADMIN_ID и CURRENCY
4. Исправлена инициализация bot (проверка TOKEN)
5. Все функции и обработчики сохранены полностью
"""
import asyncio
import sqlite3
import logging
import os
import json
import matplotlib.pyplot as plt
from io import BytesIO
from aiohttp import web
from pathlib import Path
from datetime import datetime, timedelta, date
from typing import Optional

# ===== ИСПРАВЛЕНИЕ #1: Безопасный импорт psycopg2 =====
try:
    import psycopg2
except ImportError:
    psycopg2 = None

from database import Database
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, LabeledPrice, PreCheckoutQuery
)

# ===== ИСПРАВЛЕНИЕ #2: Импорт Workbook для Excel =====
from openpyxl import Workbook


# ===== Переменные окружения =====
DATABASE_URL = os.getenv("DATABASE_URL", "database.db")

db = Database(db_path=DATABASE_URL)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "rovshan131017!")


# ---------- Logging ----------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Load environment ----------
def load_env(path="tokenapi.env"):
    env = {}
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env

env = load_env()
def getenv(key, default=""):
    return os.environ.get(key, env.get(key, default))

TOKEN = getenv("BOT_TOKEN")
PROVIDER_TOKEN = getenv("PROVIDER_TOKEN", "")
MAX_TRADES_FREE = int(getenv("MAX_TRADES_FREE", "20"))

# ===== ИСПРАВЛЕНИЕ #3: Добавлены недостающие переменные =====
ADMIN_ID = int(getenv("ADMIN_ID", "0"))
CURRENCY = "USD"

WEBHOOK_DOMAIN = getenv("WEBHOOK_DOMAIN", "https://tgbot-ljj1.onrender.com")
WEBHOOK_PATH = f"/webhook/{TOKEN}" if TOKEN else "/webhook"
WEBHOOK_URL = f"{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"
PORT = int(os.environ.get("PORT", 8000))

# ===== ИСПРАВЛЕНИЕ #4: Проверка TOKEN перед инициализацией бота =====
bot = Bot(token=TOKEN) if TOKEN else None
dp = Dispatcher(storage=MemoryStorage())

# ---------- Database ----------
def init_db():
    conn = sqlite3.connect("trades.db")
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        date TEXT,
        pair TEXT,
        entry REAL,
        exit REAL,
        profit REAL
    )
    """)
    conn.commit()
    conn.close()

init_db()

def count_user_trades(user_id):
    conn = sqlite3.connect("trades.db")
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trades WHERE user_id=?", (user_id,))
    count = cur.fetchone()[0]
    conn.close()
    return count

# ---------- Subscriptions ----------
SUBS_FILE = "subscriptions.json"

def load_subscriptions():
    if Path(SUBS_FILE).exists():
        return json.loads(Path(SUBS_FILE).read_text(encoding="utf-8"))
    return {}

def save_user_subscription(user_id):
    subs = load_subscriptions()
    subs[str(user_id)] = {"active": True, "date": datetime.now().isoformat()}
    Path(SUBS_FILE).write_text(json.dumps(subs, ensure_ascii=False, indent=2), encoding="utf-8")

def user_has_subscription(user_id):
    subs = load_subscriptions()
    return subs.get(str(user_id), {}).get("active", False)

# ---------------- Проверка лимита бесплатных сделок ----------------
async def check_free_trades_limit(user_id: int, message: types.Message) -> bool:
    """
    Проверяет, может ли пользователь добавить новую сделку.
    Возвращает True, если лимит не достигнут (можно создавать сделку),
    False, если достигнут (нельзя создавать сделку, нужно подписка)
    """
    if not user_has_subscription(user_id) and count_user_trades(user_id) >= MAX_TRADES_FREE:
        await message.answer("⚠️ Вы достигли лимита бесплатных сделок. Купите подписку.")
        return False
    return True


def calculate_stats(user_id: int):
    conn = sqlite3.connect("trades.db")
    cur = conn.cursor()
    cur.execute("SELECT profit FROM trades WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    profits = [r[0] for r in rows]
    total = len(profits)
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]
    winrate = round(len(wins)/total*100, 2) if total else 0
    avg_win = round(sum(wins)/len(wins), 2) if wins else 0
    avg_loss = round(sum(losses)/len(losses), 2) if losses else 0
    total_profit = round(sum(profits), 2)
    pf = round(sum(wins)/abs(sum(losses)), 2) if losses else float('inf')

    return {
        "total": total,
        "winrate": winrate,
        "profit": total_profit,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "pf": pf
    }


def create_excel(user_id: int):
    conn = sqlite3.connect("trades.db")
    cur = conn.cursor()
    cur.execute("SELECT date, pair, entry, exit, profit FROM trades WHERE user_id=?", (user_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    wb = Workbook()
    ws = wb.active
    ws.title = "Trades"
    ws.append(["Дата", "Пара", "Вход", "Выход", "Прибыль"])
    for r in rows:
        ws.append(r)

    path = f"exports/{user_id}_trades.xlsx"
    Path("exports").mkdir(exist_ok=True)
    wb.save(path)
    return path


def create_equity_chart(user_id: int):
    conn = sqlite3.connect("trades.db")
    cur = conn.cursor()
    cur.execute("SELECT date, profit FROM trades WHERE user_id=? ORDER BY date", (user_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    dates = [datetime.fromisoformat(r[0]) for r in rows]
    profits = [r[1] for r in rows]
    equity = []
    total = 0
    for p in profits:
        total += p
        equity.append(total)

    fig, ax = plt.subplots()
    ax.plot(dates, equity, marker='o')
    ax.set_title("Equity Curve")
    ax.set_xlabel("Дата")
    ax.set_ylabel("Прибыль")
    fig.autofmt_xdate()

    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    return buf


async def check_free_trades(user_id: int, message: types.Message) -> bool:
    if count_user_trades(user_id) >= MAX_TRADES_FREE:
        trades = await db.count_user_trades(user_id)
        await message.answer(
            "Вы достигли лимита бесплатных сделок.\n\n"
            "Чтобы продолжить, купите подписку."
        )
        return False
    return True


# ---------- Localization ----------
LANG = {
    "ru": {
        "start": "📱 ДНЕВНИК ТРЕЙДЕРА\n\n✓ Добавляйте сделки (LONG/SHORT)\n✓ Полная аналитика\n✓ Чеклисты с условиями\n✓ Экспорт в Excel\n✓ Календарь для дат\n✓ До {} сделок бесплатно\n\nВыберите действие:".format(MAX_TRADES_FREE),
        "my_trades": "📊 Мои сделки",
        "new_trade": "➕ Новая сделка",
        "history": "📋 История",
        "edit": "✏️ Изменить",
        "delete": "🗑 Удалить",
        "accounts": "💼 Счет",
        "create_acc": "➕ Создать",
        "list_acc": "📋 Список",
        "analytics": "📈 Аналитика",
        "export": "📊 Экспорт",
        "help": "❓ Помощь",
        "settings": "⚙️ Настройки",
        "select_account": "Выберите счет:",
        "select_pair": "Выберите пару:",
        "select_type": "Выберите тип:",
        "select_date": "Выберите дату:",
        "enter_sum": "Введите сумму (USD):",
        "enter_tp": "Введите Тейк-профит (USD):",
        "select_status": "Выберите статус:",
        "enter_strategy": "Введите стратегию:",
        "checklist": "Чеклист:",
        "enter_notes": "Введите заметки:",
        "select_screenshot": "Скриншот (пропустить):",
        "confirm": "✓ ПОДТВЕРЖДЕНИЕ",
        "save": "✓ Да",
        "cancel": "✗ Нет",
        "saved": "✅ Сохранено!",
        "cancelled": "✗ Отменено",
        "no_trades": "Нет сделок",
        "select_field": "Что изменить?",
        "new_value": "Новое значение:",
        "deleted": "✓ Удалено",
        "profit": "В прибыли",
        "loss": "В убытке",
        "closed": "Закрыта",
        "skip": "Пропустить",
        "create_checklist": "+ Создать",
        "pair": "Пара",
        "type": "Тип",
        "open_date": "Вход",
        "close_date": "Выход",
        "amount": "Сумма",
        "tp": "TP",
        "status": "Статус",
        "strategy": "Стратегия",
        "notes": "Заметки",
        "limit": "⚠️ Лимит {} сделок! Получите премиум".format(MAX_TRADES_FREE),
        "no_account": "Сначала создайте счет!",
        "select_account_export": "Выберите счет для экспорта:",
        "sent": "✓ Отправлено!",
        "acc_name": "Название:",
        "acc_balance": "Баланс:",
        "acc_created": "✓ Счет создан",
        "all_trades": "ВСЕ СДЕЛКИ:",
        "analytics_text": "АНАЛИТИКА\n\nВсего: {}\nВыигрышей: {}\nПроигрышей: {}\nWin Rate: {:.1f}%",
        "language": "🌐 Выбрать язык",
        "current_lang": "Текущий язык: ",
        "profile": "👤 ПРОФИЛЬ",
        "id": "ID: ",
        "username": "Username: @",
        "trades_count": "Сделок: ",
        "step": "Шаг",
        "pair_label": "Пара",
        "type_label": "Тип",
        "date_label": "Дата",
        "sum_label": "Сумма",
        "current": "Текущий баланс:",
        "subscribe": "💳 Подписка",
        "subscribe_info": "Выберите тариф и оплатите прямо в чате.",
        "plan_2": "2 USD / 30 days",
        "plan_5": "5 USD / 90 days",
        "subscribe_btn": "Подписаться",
        "grant_success": "✅ Премиум выдан на {} дней",
        "not_admin": "❌ Только администратор может выполнить это действие",
        "today": "Сегодня",
        "yesterday": "Вчера",
        "templates": "Шаблоны чеклистов",
        "apply_template": "Применить шаблон",
        "edit_checklists": "Редактировать чеклисты",
        "no_templates": "Нет шаблонов",
    },
    "en": {
        "start": "📱 TRADER'S JOURNAL\n\n✓ Add trades (LONG/SHORT)\n✓ Full analytics\n✓ Trading checklists\n✓ Export to Excel\n✓ Calendar for dates\n✓ Up to {} trades free\n\nSelect action:".format(MAX_TRADES_FREE),
        "my_trades": "📊 My Trades",
        "new_trade": "➕ New Trade",
        "history": "📋 History",
        "edit": "✏️ Edit",
        "delete": "🗑 Delete",
        "accounts": "💼 Accounts",
        "create_acc": "➕ Create",
        "list_acc": "📋 List",
        "analytics": "📈 Analytics",
        "export": " Export",
        "help": "❓ Help",
        "settings": "⚙️ Settings",
        "select_account": "Select account:",
        "select_pair": "Select pair:",
        "select_type": "Select type:",
        "select_date": "Select date:",
        "enter_sum": "Enter amount (USD):",
        "enter_tp": "Enter Take-Profit (USD):",
        "select_status": "Select status:",
        "enter_strategy": "Enter strategy:",
        "checklist": "Checklist:",
        "enter_notes": "Enter notes:",
        "select_screenshot": "Screenshot (skip):",
        "confirm": "✓ CONFIRMATION",
        "save": "✓ Yes",
        "cancel": "✗ No",
        "saved": "✅ Saved!",
        "cancelled": "✗ Cancelled",
        "no_trades": "No trades",
        "select_field": "What to change?",
        "new_value": "New value:",
        "deleted": "✓ Deleted",
        "profit": "In profit",
        "loss": "In loss",
        "closed": "Closed",
        "skip": "Skip",
        "create_checklist": "+ Create",
        "pair": "Pair",
        "type": "Type",
        "open_date": "Entry",
        "close_date": "Exit",
        "amount": "Amount",
        "tp": "TP",
        "status": "Status",
        "strategy": "Strategy",
        "notes": "Notes",
        "limit": "⚠️ Limit {} trades! Get premium".format(MAX_TRADES_FREE),
        "no_account": "Create account first!",
        "select_account_export": "Select account to export:",
        "sent": "✓ Sent!",
        "acc_name": "Name:",
        "acc_balance": "Balance:",
        "acc_created": "✓ Account created",
        "all_trades": "ALL TRADES:",
        "analytics_text": "ANALYTICS\n\nTotal: {}\nWins: {}\nLosses: {}\nWin Rate: {:.1f}%",
        "language": "🌐 Select language",
        "current_lang": "Current language: ",
        "profile": "👤 PROFILE",
        "id": "ID: ",
        "username": "Username: @",
        "trades_count": "Trades: ",
        "step": "Step",
        "pair_label": "Pair",
        "type_label": "Type",
        "date_label": "Date",
        "sum_label": "Amount",
        "current": "Current balance:",
        "subscribe": "💳 Subscription",
        "subscribe_info": "Choose plan and pay directly in chat.",
        "plan_2": "2 USD / 30 days",
        "plan_5": "5 USD / 90 days",
        "subscribe_btn": "Subscribe",
        "grant_success": "✅ Premium granted for {} days",
        "not_admin": "❌ Only admin can perform this action",
        "today": "Today",
        "yesterday": "Yesterday",
        "templates": "Checklist templates",
        "apply_template": "Apply template",
        "edit_checklists": "Edit checklists",
        "no_templates": "No templates",
    }
}

def get_text(user_id: int, key: str) -> str:
    try:
        db.cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
        r = db.cursor.fetchone()
        lang = r[0] if r else "ru"
    except Exception:
        lang = "ru"
    return LANG.get(lang, LANG["ru"]).get(key, key)

# ---------- FSM States ----------
class States(StatesGroup):
    add_account_name = State()
    add_account_balance = State()
    trade_step_1 = State()
    trade_step_2 = State()
    trade_step_3 = State()
    trade_step_4 = State()
    trade_step_5 = State()
    trade_step_6 = State()
    trade_step_7 = State()
    trade_step_8 = State()
    trade_step_9 = State()
    trade_step_10 = State()
    trade_step_11 = State()
    trade_step_12 = State()
    edit_trade = State()
    edit_field = State()
    edit_checklist = State()

# ---------- Premium helpers ----------
def is_premium(user_id: int) -> bool:
    try:
        db.cursor.execute("SELECT is_premium, premium_until FROM users WHERE user_id=?", (user_id,))
        r = db.cursor.fetchone()
        if not r:
            return False
        is_p, until = r
        if not is_p:
            return False
        if not until:
            db.cursor.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
            db.commit()
            return False
        try:
            dt = datetime.fromisoformat(until)
        except Exception:
            db.cursor.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
            db.commit()
            return False
        if dt < datetime.now():
            db.cursor.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
            db.commit()
            return False
        return True
    except Exception:
        logger.exception("is_premium error")
        return False

def can_add_trade(user_id: int) -> bool:
    if is_premium(user_id):
        return True
    try:
        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE user_id=?", (user_id,))
        return db.cursor.fetchone()[0] < MAX_TRADES_FREE
    except Exception:
        logger.exception("can_add_trade failed")
        return False

def grant_premium(user_id: int, days: int, provider="telegram_payments", provider_id: Optional[str]=None) -> Optional[str]:
    try:
        period_end = (datetime.now() + timedelta(days=days)).isoformat()
        db.cursor.execute("UPDATE users SET is_premium=1, premium_until=? WHERE user_id=?", (period_end, user_id))
        db.cursor.execute("INSERT INTO subscriptions(user_id, provider, provider_id, status, period_end) VALUES(?, ?, ?, ?, ?)",
                          (user_id, provider, provider_id or "", "active", period_end))
        db.commit()
        return period_end
    except Exception:
        logger.exception("grant_premium failed")
        return None

def revoke_premium(user_id: int):
    try:
        db.cursor.execute("UPDATE users SET is_premium=0, premium_until=NULL WHERE user_id=?", (user_id,))
        db.cursor.execute("UPDATE subscriptions SET status='cancelled' WHERE user_id=? AND status='active'", (user_id,))
        db.commit()
    except Exception:
        logger.exception("revoke_premium failed")

# ---------- UI helpers ----------
def main_menu(user_id: int):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=get_text(user_id, "my_trades")), KeyboardButton(text=get_text(user_id, "accounts"))],
        [KeyboardButton(text=get_text(user_id, "analytics")), KeyboardButton(text=get_text(user_id, "export"))],
        [KeyboardButton(text=get_text(user_id, "help")), KeyboardButton(text=get_text(user_id, "settings"))],
    ], resize_keyboard=True)

def calendar_kb(year: int, month: int, user_id: int):
    import calendar as cal
    kb = []
    prev_m, prev_y = (month - 1, year) if month > 1 else (12, year - 1)
    next_m, next_y = (month + 1, year) if month < 12 else (1, year + 1)

    # Navigation row
    kb.append([
        InlineKeyboardButton(text="◀️", callback_data=f"cal_{prev_y}_{prev_m}"),
        InlineKeyboardButton(text=f"{year}-{month:02d}", callback_data="noop"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal_{next_y}_{next_m}")
    ])

    # Today / Yesterday shortcuts
    today_dt = date.today()
    yesterday_dt = today_dt - timedelta(days=1)
    kb.append([
        InlineKeyboardButton(text=get_text(user_id, "today"), callback_data=f"dt_{today_dt.year}_{today_dt.month}_{today_dt.day}"),
        InlineKeyboardButton(text=get_text(user_id, "yesterday"), callback_data=f"dt_{yesterday_dt.year}_{yesterday_dt.month}_{yesterday_dt.day}")
    ])

    # Days of week in one row
    try:
        db.cursor.execute("SELECT language FROM users WHERE user_id=?", (user_id,))
        res = db.cursor.fetchone()
        lang = res[0] if res else "ru"
    except Exception:
        lang = "ru"
    day_names = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"] if lang == "en" else ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    kb.append([InlineKeyboardButton(text=dn, callback_data="noop") for dn in day_names])

    # Weeks
    for week in cal.monthcalendar(year, month):
        row = []
        for day in week:
            if day:
                row.append(InlineKeyboardButton(text=str(day), callback_data=f"dt_{year}_{month}_{day}"))
            else:
                row.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        kb.append(row)

    kb.append([InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# ===== HANDLERS БЛОК =====

@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    try:
        db.cursor.execute("INSERT OR REPLACE INTO users(user_id, username, first_name) VALUES(?, ?, ?)",
                          (user_id, message.from_user.username or "user", message.from_user.first_name or "User"))
        if user_id == ADMIN_ID and ADMIN_ID != 0:
            db.cursor.execute("UPDATE users SET is_admin=1 WHERE user_id=?", (user_id,))
        db.commit()
    except Exception:
        logger.exception("start handler DB error")
    await message.answer(get_text(user_id, "start"), reply_markup=main_menu(user_id))


@dp.message(F.text.regexp(r"📊|My Trades"))
async def trades_menu(message: types.Message):
    user_id = message.from_user.id
    if not can_add_trade(user_id):
        await message.answer(get_text(user_id, "limit"))
    try:
        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE user_id=?", (user_id,))
        count = db.cursor.fetchone()[0]
    except Exception:
        count = 0
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(user_id, "new_trade"), callback_data="new_trade")],
        [InlineKeyboardButton(text=get_text(user_id, "history"), callback_data="hist_trades")],
        [InlineKeyboardButton(text=get_text(user_id, "edit"), callback_data="edit_trades")],
        [InlineKeyboardButton(text=get_text(user_id, "delete"), callback_data="del_trades")],
    ])
    await message.answer(f"📊 {get_text(user_id, 'my_trades')} ({count}):", reply_markup=kb)

@dp.callback_query(F.data == "new_trade")
async def new_trade(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    if not can_add_trade(user_id):
        await call.answer(get_text(user_id, "limit"), show_alert=True)
        return
    db.cursor.execute("SELECT id, name FROM accounts WHERE user_id=?", (user_id,))
    accounts = db.cursor.fetchall()
    if not accounts:
        await call.answer(get_text(user_id, "no_account"))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"💼 {n}", callback_data=f"acc_{i}")] for i, n in accounts])
    await call.message.edit_text(f"👇 {get_text(user_id, 'select_account')}", reply_markup=kb)
    await state.set_state(States.trade_step_1)
    await call.answer()

@dp.callback_query(F.data.startswith("acc_"))
async def select_acc(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    try:
        acc_id = int(call.data.split("_")[1])
    except Exception:
        await call.answer("Invalid account")
        return
    await state.update_data(account_id=acc_id)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🪙 BTC/USD", callback_data="pair_BTC/USD")],
        [InlineKeyboardButton(text="🪙 ETH/USD", callback_data="pair_ETH/USD")],
        [InlineKeyboardButton(text="💱 EUR/USD", callback_data="pair_EUR/USD")],
        [InlineKeyboardButton(text="💱 GBP/USD", callback_data="pair_GBP/USD")],
        [InlineKeyboardButton(text="✏️ Other", callback_data="pair_other")],
    ])
    await call.message.edit_text(f"1️⃣ {get_text(user_id, 'pair')}/Pair:", reply_markup=kb)
    await state.set_state(States.trade_step_2)
    await call.answer()

@dp.message(States.trade_step_2)
async def step_2_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.update_data(pair=message.text.upper())
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 LONG", callback_data="type_long")],
        [InlineKeyboardButton(text="📉 SHORT", callback_data="type_short")],
    ])
    await message.answer(f"2️⃣ {get_text(user_id, 'type')}:", reply_markup=kb)
    await state.set_state(States.trade_step_3)

@dp.callback_query(F.data.startswith("pair_"))
async def select_pair(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    pair = call.data.replace("pair_", "")
    if pair == "other":
        await call.message.edit_text(f"✏️ {get_text(user_id, 'pair')}:")
        await state.set_state(States.trade_step_2)
    else:
        await state.update_data(pair=pair)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📈 LONG", callback_data="type_long")],
            [InlineKeyboardButton(text="📉 SHORT", callback_data="type_short")],
        ])
        await call.message.edit_text(f"2️⃣ {get_text(user_id, 'type')}:", reply_markup=kb)
        await state.set_state(States.trade_step_3)
    await call.answer()

@dp.callback_query(F.data.startswith("type_"))
async def select_type(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    ttype = call.data.replace("type_", "").upper()
    await state.update_data(trade_type=ttype)
    now = datetime.now()
    await call.message.edit_text(f"3️⃣ {get_text(user_id, 'open_date')}:", reply_markup=calendar_kb(now.year, now.month, user_id))
    await state.set_state(States.trade_step_4)
    await call.answer()

@dp.callback_query(F.data.startswith("cal_"))
async def change_cal(call: types.CallbackQuery):
    parts = call.data.split("_")
    try:
        y, m = int(parts[1]), int(parts[2])
    except Exception:
        await call.answer()
        return
    await call.message.edit_reply_markup(reply_markup=calendar_kb(y, m, call.from_user.id))
    await call.answer()

@dp.callback_query(F.data.startswith("dt_"))
async def select_dt(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    parts = call.data.split("_")
    try:
        year = int(parts[1]); month = int(parts[2]); day = int(parts[3])
    except Exception:
        await call.answer()
        return
    dt = f"{year}-{month:02d}-{day:02d}"
    curr = await state.get_state()
    if curr == States.trade_step_4.state:
        await state.update_data(open_date=dt)
        now = datetime.now()
        await call.message.edit_text(f"4️⃣ {get_text(user_id, 'close_date')}:", reply_markup=calendar_kb(now.year, now.month, user_id))
        await state.set_state(States.trade_step_5)
    elif curr == States.trade_step_5.state:
        await state.update_data(close_date=dt)
        await call.message.edit_text(f"5️⃣ {get_text(user_id, 'enter_sum')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_6)
    await call.answer()

@dp.message(States.trade_step_6)
async def step_6(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        val = float(message.text)
        await state.update_data(amount=val)
        await message.answer(f"6️⃣ {get_text(user_id, 'enter_tp')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_7)
    except Exception:
        await message.answer(f"❌ {get_text(user_id, 'new_value')}")

@dp.message(States.trade_step_7)
async def step_7(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        val = float(message.text)
        await state.update_data(take_profit=val)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"✅ {get_text(user_id, 'profit')}", callback_data="st_profit")],
            [InlineKeyboardButton(text=f"❌ {get_text(user_id, 'loss')}", callback_data="st_loss")],
            [InlineKeyboardButton(text=f"🔒 {get_text(user_id, 'closed')}", callback_data="st_closed")],
            [InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")],
        ])
        await message.answer(f"7️⃣ {get_text(user_id, 'select_status')}", reply_markup=kb)
        await state.set_state(States.trade_step_8)
    except Exception:
        await message.answer(f"❌ {get_text(user_id, 'new_value')}")

@dp.callback_query(F.data.startswith("st_"))
async def select_st(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    st = call.data.split("_")[1].upper()
    await state.update_data(status=st)
    await call.message.edit_text(f"8️⃣ {get_text(user_id, 'enter_strategy')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
    await state.set_state(States.trade_step_9)
    await call.answer()

@dp.message(States.trade_step_9)
async def step_9(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.update_data(strategy=message.text)
    await message.answer(f"9️⃣ {get_text(user_id, 'checklist')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(user_id, "create_checklist"), callback_data="create_check")],
        [InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")],
        [InlineKeyboardButton(text=get_text(user_id, "templates"), callback_data="templates_list")]
    ]))
    await state.set_state(States.trade_step_10)

@dp.callback_query(F.data == "create_check")
async def create_check(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    await call.message.edit_text(f"✏️ Вводите пункты (каждый с новой строки):")
    await state.set_state(States.trade_step_10)
    await call.answer()

@dp.message(States.trade_step_10)
async def step_10(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    items = [i.strip().lstrip("-").strip() for i in message.text.strip().split("\n") if i.strip()]
    checklist = {item: False for item in items}
    fn = f"trade_checklists/{message.from_user.id}_{int(datetime.now().timestamp())}.json"
    try:
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(checklist, f, ensure_ascii=False, indent=2)
        await state.update_data(checklist=fn)
    except Exception:
        logger.exception("Failed to save checklist")
        await message.answer("❌ Error saving checklist")
        return
    await message.answer(f"🔟 {get_text(user_id, 'enter_notes')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
    await state.set_state(States.trade_step_11)

@dp.message(States.trade_step_11)
async def step_11(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.update_data(notes=message.text)
    await message.answer(f"1️⃣1️⃣ {get_text(user_id, 'select_screenshot')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
    await state.set_state(States.trade_step_12)

@dp.message(States.trade_step_12)
async def step_12(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.photo:
        photo = message.photo[-1]
        try:
            fp = await bot.get_file(photo.file_id)
            fn = f"trade_photos/{message.from_user.id}_{int(datetime.now().timestamp())}.jpg"
            await bot.download_file(fp.file_path, fn)
            await state.update_data(screenshot_path=fn)
        except Exception:
            logger.exception("Failed to download photo")
    data = await state.get_data()
    text = f"""✅ {get_text(user_id, 'confirm')}

📌 {get_text(user_id, 'pair')}: {data.get('pair', 'N/A')}
🔀 {get_text(user_id, 'type')}: {data.get('trade_type', 'N/A')}
📅 {get_text(user_id, 'open_date')}: {data.get('open_date', 'N/A')}
📅 {get_text(user_id, 'close_date')}: {data.get('close_date', 'N/A')}
💰 {get_text(user_id, 'amount')}: {data.get('amount', 'N/A')} USD
🎯 {get_text(user_id, 'tp')}: {data.get('take_profit', 'N/A')} USD
⚡ {get_text(user_id, 'status')}: {data.get('status', 'N/A')}
📊 {get_text(user_id, 'strategy')}: {data.get('strategy', 'N/A')}

{get_text(user_id, 'save')}?"""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(user_id, "save"), callback_data="save_trade")],
        [InlineKeyboardButton(text=get_text(user_id, "cancel"), callback_data="cancel_trade")],
    ])
    await message.answer(text, reply_markup=kb)

@dp.callback_query(F.data == "save_trade")
async def save_trade(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    data = await state.get_data()
    try:
        db.cursor.execute("""INSERT INTO trades
            (user_id, account_id, pair, trade_type, open_date, close_date, amount, take_profit, status, strategy, checklist, notes, screenshot_path, pnl)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, data.get("account_id"), data.get("pair", "N/A"), data.get("trade_type", "N/A"),
             data.get("open_date", "N/A"), data.get("close_date", "N/A"), data.get("amount", 0),
             data.get("take_profit", 0), data.get("status", "N/A"), data.get("strategy", "N/A"),
             data.get("checklist"), data.get("notes", ""), data.get("screenshot_path"), 0))
        db.cursor.execute("UPDATE users SET total_trades=total_trades+1 WHERE user_id=?", (user_id,))
        db.commit()
        await state.clear()
        await call.message.edit_text(get_text(user_id, "saved"))
    except Exception:
        logger.exception("Failed to save trade")
        await call.message.edit_text("❌ Error saving trade")
    await call.answer()

@dp.callback_query(F.data == "cancel_trade")
async def cancel_trade(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    await state.clear()
    await call.message.edit_text(get_text(user_id, "cancelled"))
    await call.answer()

@dp.callback_query(F.data == "skip")
async def skip(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    curr = await state.get_state()
    if curr == States.trade_step_2.state:
        await state.update_data(pair="N/A")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📈 LONG", callback_data="type_long")],[InlineKeyboardButton(text="📉 SHORT", callback_data="type_short")]])
        await call.message.edit_text(f"2️⃣ {get_text(user_id, 'type')}:", reply_markup=kb)
        await state.set_state(States.trade_step_3)
    elif curr == States.trade_step_4.state:
        await state.update_data(open_date="N/A")
        now = datetime.now()
        await call.message.edit_text(f"4️⃣ {get_text(user_id, 'close_date')}:", reply_markup=calendar_kb(now.year, now.month, user_id))
        await state.set_state(States.trade_step_5)
    elif curr == States.trade_step_5.state:
        await state.update_data(close_date="N/A")
        await call.message.edit_text(f"5️⃣ {get_text(user_id, 'enter_sum')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_6)
    elif curr == States.trade_step_6.state:
        await state.update_data(amount="N/A")
        await call.message.edit_text(f"6️⃣ {get_text(user_id, 'enter_tp')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_7)
    elif curr == States.trade_step_7.state:
        await state.update_data(take_profit="N/A")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✅ {get_text(user_id, 'profit')}", callback_data="st_profit")],[InlineKeyboardButton(text=f"❌ {get_text(user_id, 'loss')}", callback_data="st_loss")],[InlineKeyboardButton(text=f"🔒 {get_text(user_id, 'closed')}", callback_data="st_closed")],[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]])
        await call.message.edit_text(f"7️⃣ {get_text(user_id, 'select_status')}", reply_markup=kb)
        await state.set_state(States.trade_step_8)
    elif curr == States.trade_step_8.state:
        await state.update_data(status="N/A")
        await call.message.edit_text(f"8️⃣ {get_text(user_id, 'enter_strategy')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_9)
    elif curr == States.trade_step_9.state:
        await state.update_data(strategy="N/A")
        await call.message.edit_text(f"9️⃣ {get_text(user_id, 'checklist')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "create_checklist"), callback_data="create_check")],[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")],[InlineKeyboardButton(text=get_text(user_id, "templates"), callback_data="templates_list")]]))
        await state.set_state(States.trade_step_10)
    elif curr == States.trade_step_10.state:
        await state.update_data(checklist=None)
        await call.message.edit_text(f"🔟 {get_text(user_id, 'enter_notes')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_11)
    elif curr == States.trade_step_11.state:
        await state.update_data(notes="N/A")
        await call.message.edit_text(f"1️⃣1️⃣ {get_text(user_id, 'select_screenshot')}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=get_text(user_id, "skip"), callback_data="skip")]]))
        await state.set_state(States.trade_step_12)
    elif curr == States.trade_step_12.state:
        data = await state.get_data()
        text = f"""✅ {get_text(user_id, 'confirm')}

📌 {get_text(user_id, 'pair')}: {data.get('pair', 'N/A')}
🔀 {get_text(user_id, 'type')}: {data.get('trade_type', 'N/A')}
📅 {get_text(user_id, 'open_date')}: {data.get('open_date', 'N/A')}
📅 {get_text(user_id, 'close_date')}: {data.get('close_date', 'N/A')}
💰 {get_text(user_id, 'amount')}: {data.get('amount', 'N/A')} USD
🎯 {get_text(user_id, 'tp')}: {data.get('take_profit', 'N/A')} USD
⚡ {get_text(user_id, 'status')}: {data.get('status', 'N/A')}
📊 {get_text(user_id, 'strategy')}: {data.get('strategy', 'N/A')}

{get_text(user_id, 'save')}?"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(user_id, "save"), callback_data="save_trade")],
            [InlineKeyboardButton(text=get_text(user_id, "cancel"), callback_data="cancel_trade")],
        ])
        await call.message.edit_text(text, reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data == "hist_trades")
async def hist_trades(call: types.CallbackQuery):
    user_id = call.from_user.id
    db.cursor.execute("SELECT pair, trade_type, open_date, status FROM trades WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user_id,))
    trades = db.cursor.fetchall()
    if not trades:
        await call.message.edit_text(get_text(user_id, "no_trades"))
    else:
        text = f"📋 {get_text(user_id, 'history')}:\n\n"
        for p, t, d, s in trades:
            emoji = "✅" if s == "PROFIT" else ("❌" if s == "LOSS" else "🔘")
            arrow = "📈" if t == "LONG" else "📉"
            text += f"{emoji} {arrow} {p} ({t}) - {d}\n"
        await call.message.edit_text(text)
    await call.answer()

@dp.callback_query(F.data == "del_trades")
async def del_trades(call: types.CallbackQuery):
    user_id = call.from_user.id
    db.cursor.execute("SELECT id, pair FROM trades WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user_id,))
    trades = db.cursor.fetchall()
    if not trades:
        await call.message.edit_text(get_text(user_id, "no_trades"))
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"🗑 {p}", callback_data=f"del_id_{i}")] for i, p in trades])
        await call.message.edit_text(get_text(user_id, "delete"), reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("del_id_"))
async def del_id(call: types.CallbackQuery):
    user_id = call.from_user.id
    try:
        tid = int(call.data.split("_")[2])
    except Exception:
        await call.answer()
        return
    try:
        db.cursor.execute("DELETE FROM trades WHERE id=? AND user_id=?", (tid, user_id))
        db.cursor.execute("UPDATE users SET total_trades=total_trades-1 WHERE user_id=?", (user_id,))
        db.commit()
        await call.message.edit_text(get_text(user_id, "deleted"))
    except Exception:
        logger.exception("del_id failed")
        await call.message.edit_text("❌ Error deleting")
    await call.answer()

@dp.callback_query(F.data == "edit_trades")
async def edit_trades(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    db.cursor.execute("SELECT id, pair FROM trades WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user_id,))
    trades = db.cursor.fetchall()
    if not trades:
        await call.message.edit_text(get_text(user_id, "no_trades"))
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"✏️ {p}", callback_data=f"edit_id_{i}")] for i, p in trades])
        await call.message.edit_text(get_text(user_id, "select_field"), reply_markup=kb)
        await state.set_state(States.edit_trade)
    await call.answer()

@dp.callback_query(F.data.startswith("edit_id_"))
async def edit_id(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    try:
        tid = int(call.data.split("_")[2])
    except Exception:
        await call.answer()
        return
    await state.update_data(trade_id=tid)
    fields = [get_text(user_id, "pair"), get_text(user_id, "type"), get_text(user_id, "open_date"),
              get_text(user_id, "close_date"), get_text(user_id, "amount"), get_text(user_id, "tp"),
              get_text(user_id, "status"), get_text(user_id, "strategy"), get_text(user_id, "notes")]
    keys = ["pair", "trade_type", "open_date", "close_date", "amount", "take_profit", "status", "strategy", "notes"]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f, callback_data=f"ed_f_{k}")] for f, k in zip(fields, keys)])
    await call.message.edit_text(get_text(user_id, "select_field"), reply_markup=kb)
    await state.set_state(States.edit_field)
    await call.answer()

@dp.callback_query(F.data.startswith("ed_f_"))
async def ed_f(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    field_map = {
        "pair": "pair", "trade_type": "trade_type", "open_date": "open_date",
        "close_date": "close_date", "amount": "amount", "take_profit": "take_profit",
        "status": "status", "strategy": "strategy", "notes": "notes"
    }
    field_short = call.data.split("_")[2]
    field = field_map.get(field_short, field_short)
    await state.update_data(field=field)
    await call.message.edit_text(get_text(user_id, "new_value"))
    await state.set_state(States.edit_field)
    await call.answer()

@dp.message(States.edit_field)
async def save_edit(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    tid = data.get("trade_id")
    field = data.get("field")
    value = message.text
    allowed_fields = {"pair", "trade_type", "open_date", "close_date", "amount", "take_profit", "status", "strategy", "notes"}
    if field not in allowed_fields:
        await state.clear()
        await message.answer("❌ Invalid field")
        return
    if field in ("amount", "take_profit"):
        try:
            value = float(value)
        except ValueError:
            await message.answer(f"❌ {get_text(user_id, 'new_value')}")
            return
    try:
        db.cursor.execute(f"UPDATE trades SET {field}=? WHERE id=? AND user_id=?", (value, tid, user_id))
        db.commit()
        await state.clear()
        await message.answer(f"✅ {get_text(user_id, 'saved')}")
    except Exception:
        logger.exception("Failed to update trade")
        await message.answer("❌ Error updating trade")

# ===== ACCOUNTS HANDLERS =====

@dp.message(F.text.regexp(r"💼|Accounts"))
async def accounts(message: types.Message):
    user_id = message.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(user_id, "create_acc"), callback_data="new_acc")],
        [InlineKeyboardButton(text=get_text(user_id, "list_acc"), callback_data="list_acc")],
    ])
    await message.answer(f"💼 {get_text(user_id, 'accounts')}:", reply_markup=kb)

@dp.callback_query(F.data == "new_acc")
async def new_acc(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    await call.message.edit_text(get_text(user_id, "acc_name"))
    await state.set_state(States.add_account_name)
    await call.answer()

@dp.message(States.add_account_name)
async def acc_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await state.update_data(name=message.text)
    await message.answer(get_text(user_id, "acc_balance"))
    await state.set_state(States.add_account_balance)

@dp.message(States.add_account_balance)
async def acc_balance(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    try:
        bal = float(message.text)
        db.cursor.execute("INSERT INTO accounts(user_id, name, initial_balance, current_balance) VALUES(?, ?, ?, ?)",
            (user_id, data["name"], bal, bal))
        db.commit()
        await state.clear()
        await message.answer(get_text(user_id, "acc_created"))
    except Exception:
        logger.exception("acc_balance failed")
        await message.answer(f"❌ {get_text(user_id, 'new_value')}")

@dp.callback_query(F.data == "list_acc")
async def list_acc(call: types.CallbackQuery):
    user_id = call.from_user.id
    db.cursor.execute("SELECT name, initial_balance, current_balance FROM accounts WHERE user_id=?", (user_id,))
    accs = db.cursor.fetchall()
    if not accs:
        await call.message.edit_text(get_text(user_id, "no_trades"))
    else:
        text = f"💼 {get_text(user_id, 'accounts')}:\n\n"
        for n, ini, cur in accs:
            text += f"📊 {n}\n🔷 {get_text(user_id, 'acc_balance')}: {ini}\n🔶 {get_text(user_id, 'current')}: {cur}\n\n"
        await call.message.edit_text(text)
    await call.answer()

# ===== ANALYTICS HANDLERS =====

@dp.message(F.text.regexp(r"📈|Analytics"))
async def analytics(message: types.Message):
    user_id = message.from_user.id
    try:
        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE user_id=?", (user_id,))
        total = db.cursor.fetchone()[0]
        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE user_id=? AND status='PROFIT'", (user_id,))
        wins = db.cursor.fetchone()[0]
        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE user_id=? AND status='LOSS'", (user_id,))
        losses = db.cursor.fetchone()[0]
    except Exception:
        logger.exception("analytics failed")
        total = wins = losses = 0
    wr = (wins / total * 100) if total > 0 else 0
    text = f"""📈 {get_text(user_id, 'analytics')}

📊 {get_text(user_id, 'all_trades')}: {total}
✅ {get_text(user_id, 'profit')}: {wins}
❌ {get_text(user_id, 'loss')}: {losses}
🎯 Win Rate: {wr:.1f}%"""
    await message.answer(text, reply_markup=main_menu(user_id))

# ===== EXPORT HANDLERS =====

@dp.message(F.text == "📊 Экспорт")
async def export_start(message: types.Message):
    user_id = message.from_user.id
    db.cursor.execute("SELECT id, name FROM accounts WHERE user_id=?", (user_id,))
    accs = db.cursor.fetchall()
    if not accs:
        await message.answer(get_text(user_id, "no_account"))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📄 {name}", callback_data=f"expacct_{aid}")] for aid, name in accs
    ])
    await message.answer(get_text(user_id, "select_account_export"), reply_markup=kb)

@dp.callback_query(F.data.startswith("expacct_"))
async def export_do(call: types.CallbackQuery):
    user_id = call.from_user.id
    try:
        aid = int(call.data.split("_")[1])
    except Exception:
        await call.answer()
        return
    db.cursor.execute("SELECT pair, trade_type, open_date, close_date, amount, take_profit, status, strategy, notes FROM trades WHERE user_id=? AND account_id=?", (user_id, aid))
    trades = db.cursor.fetchall()
    if not trades:
        await call.answer(get_text(user_id, "no_trades"))
        return
    wb = Workbook()
    ws = wb.active
    ws.append(["Пара/Pair", "Тип/Type", "Вход/Entry", "Выход/Exit", "Сумма/Amount", "TP", "Статус/Status", "Стратегия/Strategy", "Заметки/Notes"])
    for row in trades:
        ws.append(row)
    fn = f"exports/trades_{user_id}_{aid}_{int(datetime.now().timestamp())}.xlsx"
    try:
        wb.save(fn)
        await bot.send_document(user_id, FSInputFile(fn), caption="✅ Your trades")
        Path(fn).unlink(missing_ok=True)
        await call.answer(get_text(user_id, "sent"))
    except Exception:
        logger.exception("export error")
        await call.answer("❌ Error generating export")

# ===== STATS COMMAND =====

@dp.message(Command("stats"))
async def stats_cmd(message: types.Message):
    user_id = message.from_user.id
    stats = calculate_stats(user_id)
    if not stats:
        await message.answer("Нет сделок для анализа")
        return

    text = f"""
📊 Статистика

Сделок: {stats["total"]}
Winrate: {stats["winrate"]} %
Общая прибыль: {stats["profit"]}
Средняя прибыль: {stats["avg_win"]}
Средний убыток: {stats["avg_loss"]}
Profit Factor: {stats["pf"]}
"""
    await message.answer(text)

# ===== EXPORT COMMAND =====

@dp.message(Command("export"))
async def export_excel(message: types.Message):
    user_id = message.from_user.id
    file = create_excel(user_id)
    if file:
        await message.answer_document(FSInputFile(file))
    else:
        await message.answer("Нет сделок для экспорта")

# ===== EQUITY COMMAND =====

@dp.message(Command("equity"))
async def equity_cmd(message: types.Message):
    user_id = message.from_user.id
    chart = create_equity_chart(user_id)
    if chart:
        await message.answer_photo(chart)
    else:
        await message.answer("Нет данных для графика")

# ===== HELP HANDLER =====

@dp.message(F.text.regexp(r"❓|Help"))
async def help_cmd(message: types.Message):
    user_id = message.from_user.id
    text = """
📚 ПОМОЩЬ

➕ Новая сделка
Добавляет новую торговую сделку.

📊 Мои сделки
Просмотр всех сделок.

📈 Аналитика
Статистика торговли:
• прибыль
• winrate
• profit factor

📊 Экспорт
Скачивает Excel файл со всеми сделками.

💼 Счета
Управление торговыми счетами.

💳 Подписка
Убирает лимит сделок.

Бесплатно:
до 20 сделок

Премиум:
без ограничений
"""
    await message.answer(text, reply_markup=main_menu(user_id))

# ===== SETTINGS HANDLER =====

@dp.message(F.text.regexp(r"⚙️|Settings"))
async def settings(message: types.Message):
    user_id = message.from_user.id
    db.cursor.execute("SELECT username, total_trades, language FROM users WHERE user_id=?", (user_id,))
    res = db.cursor.fetchone()
    if res:
        lang_text = "🇷🇺 Русский" if res[2] == "ru" else "🇬🇧 English"
        text = f"""⚙️ {get_text(user_id, 'settings')}

{get_text(user_id, 'id')}{user_id}
{get_text(user_id, 'username')}{res[0]}
{get_text(user_id, 'trades_count')}{res[1]}

🌐 {get_text(user_id, 'current_lang')}{lang_text}"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=get_text(user_id, "language"), callback_data="change_lang")],
            [InlineKeyboardButton(text=get_text(user_id, "subscribe"), callback_data="subscribe")],
        ])
        await message.answer(text, reply_markup=kb)

# ===== LANGUAGE HANDLERS =====

@dp.callback_query(F.data == "change_lang")
async def change_lang(call: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")],
    ])
    await call.message.edit_text("Выберите язык / Select language:", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("lang_"))
async def set_lang(call: types.CallbackQuery):
    lang = call.data.split("_")[1]
    user_id = call.from_user.id
    db.cursor.execute("UPDATE users SET language=? WHERE user_id=?", (lang, user_id))
    db.commit()
    lang_text = "🇷🇺 Русский" if lang == "ru" else "🇬🇧 English"
    await call.message.edit_text(f"✅ Язык изменен на {lang_text}")
    await call.answer()

# ===== SUBSCRIPTION HANDLERS =====

@dp.callback_query(F.data == "subscribe")
async def subscribe_menu(call: types.CallbackQuery):
    user_id = call.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=get_text(user_id, "plan_2"), callback_data="plan_30_200")],
        [InlineKeyboardButton(text=get_text(user_id, "plan_5"), callback_data="plan_90_500")],
    ])
    await call.message.edit_text(get_text(user_id, "subscribe_info"), reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("plan_"))
async def plan_choice(call: types.CallbackQuery):
    try:
        _, days_str, cents_str = call.data.split("_")
        days = int(days_str)
        price_cents = int(cents_str)
    except Exception:
        await call.answer("Invalid plan")
        return
    user_id = call.from_user.id
    if not PROVIDER_TOKEN:
        await call.answer("Payments not configured", show_alert=True)
        return
    title = f"Premium {days} days"
    description = f"Premium access for {days} days"
    payload = f"subscribe:{user_id}:{days}"
    prices = [LabeledPrice(label=title, amount=price_cents)]
    try:
        await bot.send_invoice(chat_id=user_id, title=title, description=description,
                               payload=payload, provider_token=PROVIDER_TOKEN,
                               currency=CURRENCY, prices=prices, start_parameter="premium")
        await call.answer()
    except Exception:
        logger.exception("send_invoice failed")
        await call.answer("Failed to create invoice")

@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_q: PreCheckoutQuery):
    try:
        await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)
    except Exception:
        logger.exception("pre_checkout error")

@dp.message(F.content_type == "successful_payment")
async def process_successful_payment(message: types.Message):
    payload = message.successful_payment.invoice_payload
    try:
        parts = payload.split(":")
        if parts[0] == "subscribe":
            uid = int(parts[1])
            days = int(parts[2])
            provider_id = message.successful_payment.telegram_payment_charge_id
            period_end = grant_premium(uid, days=days, provider="telegram_payments", provider_id=provider_id)
            if period_end:
                await message.answer(get_text(uid, "grant_success").format(days))
    except Exception:
        logger.exception("process_successful_payment error")

# ===== ADMIN GRANT COMMAND =====

@dp.message(Command("grant_manual"))
async def cmd_grant_manual(message: types.Message):
    user = message.from_user.id
    if user != ADMIN_ID:
        await message.answer(get_text(user, "not_admin"))
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /grant_manual <user_id> <days>")
        return
    try:
        uid = int(parts[1]); days = int(parts[2])
    except Exception:
        await message.answer("Invalid parameters")
        return
    period_end = grant_premium(uid, days=days, provider="admin_manual", provider_id=str(user))
    if period_end:
        await message.answer(get_text(user, "grant_success").format(days))
        try:
            await bot.send_message(uid, get_text(uid, "grant_success").format(days))
        except Exception:
            pass


@dp.message(Command("admin"))
async def admin_login(message: types.Message):

    parts = message.text.split()

    if len(parts) < 2:
        await message.answer("Использование: /admin пароль")
        return

    password = parts[1]

    if password != ADMIN_PASSWORD:
        await message.answer("Неверный пароль")
        return

    user_id = message.from_user.id

    db.cursor.execute(
        "INSERT OR REPLACE INTO subscriptions (user_id, expires_at) VALUES (?, ?)",
        (user_id, "2099-01-01")
    )
    db.conn.commit()

    await message.answer("Админ доступ активирован")

# ===== CHECKLIST TEMPLATES HANDLERS =====

@dp.message(Command("templates"))
async def templates_list(message: types.Message):
    user_id = message.from_user.id
    tdir = Path("checklist_templates")
    templates = list(tdir.glob("*.json"))
    if not templates:
        await message.answer(get_text(user_id, "no_templates"))
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t.stem, callback_data=f"applytpl_{t.name}") ] for t in templates])
    await message.answer(get_text(user_id, "templates"), reply_markup=kb)

@dp.callback_query(F.data.startswith("applytpl_"))
async def apply_template(call: types.CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    fname = call.data.split("_", 1)[1]
    try:
        with open(f"checklist_templates/{fname}", "r", encoding="utf-8") as f:
            tpl = json.load(f)
    except Exception:
        await call.answer("Template error")
        return
    fn = f"trade_checklists/{user_id}_{int(datetime.now().timestamp())}.json"
    try:
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(tpl, f, ensure_ascii=False, indent=2)
        await state.update_data(checklist=fn)
        await call.message.edit_text("Template applied. Continue creating trade.")
        await call.answer()
    except Exception:
        logger.exception("apply_template error")
        await call.answer("❌ Error applying template")

@dp.message(Command("my_checklists"))
async def my_checklists(message: types.Message):
    user_id = message.from_user.id
    files = list(Path("trade_checklists").glob(f"{user_id}_*.json"))
    if not files:
        await message.answer("No checklists")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f.name, callback_data=f"editchk_{f.name}") ] for f in files])
    await message.answer("Your checklists:", reply_markup=kb)

@dp.callback_query(F.data.startswith("editchk_"))
async def edit_checklist_start(call: types.CallbackQuery, state: FSMContext):
    fname = call.data.split("_", 1)[1]
    fpath = Path("trade_checklists") / fname
    if not fpath.exists():
        await call.answer("File not found")
        return
    try:
        data = json.loads(fpath.read_text(encoding="utf-8"))
    except Exception:
        await call.answer("Error reading file")
        return
    text = "Edit checklist items (send new list, each item on new line):\n\n" + "\n".join([f"- {k} [{'x' if v else ' ' }]" for k,v in data.items()])
    await call.message.edit_text(text)
    await state.update_data(editing_file=str(fpath))
    await state.set_state(States.edit_checklist)
    await call.answer()

@dp.message(States.edit_checklist)
async def edit_checklist_save(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    fpath = data.get("editing_file")
    if not fpath:
        await message.answer("No file in context")
        return
    items = [i.strip().lstrip("-").strip() for i in message.text.strip().split("\n") if i.strip()]
    checklist = {item: False for item in items}
    try:
        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(checklist, f, ensure_ascii=False, indent=2)
        await state.clear()
        await message.answer("Checklist updated")
    except Exception:
        logger.exception("edit_checklist_save error")
        await message.answer("Error saving checklist")

# ===== WEBHOOK HANDLER =====

async def handle_webhook(request):
    try:
        data = await request.json()
        update = types.Update(**data)
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return web.Response(text="OK")

async def on_startup(app):
    try:
        if bot and TOKEN:
            await bot.delete_webhook()
            await bot.set_webhook(WEBHOOK_URL)
            logger.info(f"Webhook set: {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Startup error: {e}")

# ===== Создание папок =====
for d in ("trade_photos", "exports", "trade_checklists", "checklist_templates"):
    Path(d).mkdir(parents=True, exist_ok=True)

# ===== Aiohttp приложение =====
app = web.Application()
app.router.add_post(WEBHOOK_PATH, handle_webhook)
app.on_startup.append(on_startup)

# ===== MAIN =====
if __name__ == "__main__":
    if not TOKEN:
        logger.error("BOT_TOKEN not set!")
        exit(1)
    web.run_app(app, port=PORT, host="0.0.0.0")


























