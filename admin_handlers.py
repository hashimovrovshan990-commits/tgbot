# -*- coding: utf-8 -*-
"""
admin_handlers.py – Полноценная админ-панель для Trader's Journal.

Содержит все необходимые команды для управления ботом:
- Статистика, информация о пользователях
- Выдача/отзыв премиума
- Рассылка сообщений
- Управление тарифами
- Блокировка пользователей
- Просмотр логов
- Справка

Для работы требует:
- Глобальные объекты bot, dp, db из основного файла
- Переменные ADMIN_ID, ADMIN_PASSWORD
- Таблица users должна иметь поле blocked (0/1) – добавим автоматически.
"""

import logging
import os
from datetime import datetime
from aiogram import types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pathlib import Path

logger = logging.getLogger(__name__)

# Импортируем глобальные объекты из основного модуля.
# В основном файле (bot.py) после создания bot, dp, db нужно добавить:
# from admin_handlers import register_admin_handlers
# register_admin_handlers(dp)
# Поэтому здесь мы не делаем импорт, а получаем их через аргументы.

def register_admin_handlers(dp, bot, db, ADMIN_ID, ADMIN_PASSWORD):
    """
    Регистрирует все админ-обработчики в диспетчере.
    Вызывается из основного файла после инициализации.
    """

    # ---------- Вспомогательные функции ----------
    def is_admin(user_id: int) -> bool:
        """Проверка, является ли пользователь администратором."""
        # Можно также проверять флаг is_admin в БД, если нужно несколько админов
        return user_id == ADMIN_ID

    async def ensure_admin(message: types.Message) -> bool:
        """Проверка прав с отправкой сообщения об ошибке."""
        if not is_admin(message.from_user.id):
            await message.answer("⛔ Доступ запрещён.")
            return False
        return True

    # ---------- Команда /admin (вход по паролю) ----------
    # (Если вы хотите оставить её в bot.py, можно не регистрировать здесь.
    # Но для полноты добавим её и здесь, чтобы вся админка была в одном месте.)

    from aiogram.filters import Command
    from aiogram.fsm.state import State, StatesGroup

    class AdminStates(StatesGroup):
        wait_password = State()

    async def _activate_admin_premium(user_id: int, username: str, first_name: str) -> bool:
        """Активация бессрочного премиума для администратора."""
        far_future = "2100-01-01T00:00:00"
        try:
            # Убедимся, что у пользователя есть запись
            db.cursor.execute(
                "UPDATE users SET is_premium=1, premium_until=? WHERE user_id=?",
                (far_future, user_id)
            )
            if db.cursor.rowcount == 0:
                db.cursor.execute(
                    """INSERT INTO users(user_id, username, first_name, is_premium, premium_until)
                       VALUES(?, ?, ?, 1, ?)""",
                    (user_id, username or "", first_name or "User", far_future)
                )
            db.cursor.execute(
                "INSERT INTO subscriptions(user_id, provider, provider_id, status, period_end) VALUES(?, ?, ?, ?, ?)",
                (user_id, "admin_login", "", "active", far_future)
            )
            db.commit()
            return True
        except Exception as e:
            logger.exception("admin_login failed")
            return False

    @dp.message(Command("admin"))
    async def admin_login(message: types.Message, state: FSMContext):
        if not ADMIN_PASSWORD:
            await message.answer("Админ-панель не настроена (пароль не задан).")
            return
        parts = message.text.split()
        if len(parts) >= 2:
            password = parts[1].strip()
            if password != ADMIN_PASSWORD:
                await message.answer("Неверный пароль")
                return
            ok = await _activate_admin_premium(
                message.from_user.id,
                message.from_user.username,
                message.from_user.first_name
            )
            if ok:
                await message.answer("✅ Админ-доступ активирован. Безлимитные сделки включены.")
            else:
                await message.answer("❌ Ошибка при активации. Попробуйте позже.")
            return
        await state.set_state(AdminStates.wait_password)
        await message.answer("Введите пароль администратора:")

    @dp.message(AdminStates.wait_password)
    async def admin_password_step(message: types.Message, state: FSMContext):
        user_id = message.from_user.id
        password = (message.text or "").strip()
        if password != ADMIN_PASSWORD:
            await state.clear()
            await message.answer("Неверный пароль")
            return
        ok = await _activate_admin_premium(user_id, message.from_user.username, message.from_user.first_name)
        await state.clear()
        if ok:
            await message.answer("✅ Админ-доступ активирован. Безлимитные сделки включены.")
        else:
            await message.answer("❌ Ошибка при активации. Попробуйте позже.")

    # ---------- Команда /admin_stats – общая статистика ----------
    @dp.message(Command("admin_stats"))
    async def admin_stats(message: types.Message):
        if not await ensure_admin(message):
            return

        # Получаем данные из БД
        db.cursor.execute("SELECT COUNT(*) FROM users")
        users_total = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT COUNT(*) FROM users WHERE is_premium=1")
        premium_total = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT COUNT(*) FROM trades")
        trades_total = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE status='PROFIT'")
        profit_trades = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE status='LOSS'")
        loss_trades = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT SUM(pnl) FROM trades")
        total_pnl = db.cursor.fetchone()[0] or 0.0

        db.cursor.execute("SELECT COUNT(*) FROM accounts")
        accounts_total = db.cursor.fetchone()[0]

        # Подписки по провайдерам
        db.cursor.execute("SELECT provider, COUNT(*) FROM subscriptions GROUP BY provider")
        subs = db.cursor.fetchall()
        subs_text = "\n".join([f"  {row[0]}: {row[1]}" for row in subs]) or "  нет данных"

        text = f"""
📊 **Статистика бота**

👥 **Пользователи:**
  Всего: {users_total}
  Премиум: {premium_total}

📒 **Сделки:**
  Всего: {trades_total}
  Прибыльных: {profit_trades}
  Убыточных: {loss_trades}
  Общий PnL: {total_pnl:.2f} USD

🏦 **Счета:**
  Всего: {accounts_total}

💳 **Подписки:**
{subs_text}

🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
        """
        await message.answer(text)

    # ---------- Команда /user_info – информация о пользователе ----------
    @dp.message(Command("user_info"))
    async def user_info(message: types.Message):
        if not await ensure_admin(message):
            return
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /user_info <user_id>")
            return
        try:
            uid = int(args[1])
        except ValueError:
            await message.answer("Неверный ID. Должно быть число.")
            return

        user = db.get_user(uid)
        if not user:
            await message.answer("Пользователь не найден.")
            return

        # Доп. информация
        db.cursor.execute("SELECT COUNT(*) FROM trades WHERE user_id=?", (uid,))
        trades_count = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT COUNT(*) FROM accounts WHERE user_id=?", (uid,))
        accounts_count = db.cursor.fetchone()[0]

        db.cursor.execute("SELECT SUM(pnl) FROM trades WHERE user_id=?", (uid,))
        total_pnl = db.cursor.fetchone()[0] or 0.0

        blocked = user.get("blocked", 0)

        text = f"""
👤 **Информация о пользователе** `{uid}`

📛 **Имя:** {user['first_name']}
🔗 **Username:** @{user['username'] or '—'}
🌐 **Язык:** {user['language']}
📅 **Регистрация:** {user.get('created_at', '—')}

📊 **Статистика:**
  Сделок: {trades_count}
  Счетов: {accounts_count}
  Суммарный PnL: {total_pnl:.2f} USD

⭐ **Премиум:** {'✅' if user['is_premium'] else '❌'}
⏳ **Действует до:** {user['premium_until'] or '—'}
🚫 **Заблокирован:** {'✅' if blocked else '❌'}
        """
        # Кнопки управления
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🚫 Заблокировать", callback_data=f"block_{uid}"),
                InlineKeyboardButton(text="✅ Разблокировать", callback_data=f"unblock_{uid}")
            ],
            [
                InlineKeyboardButton(text="⭐ Выдать премиум", callback_data=f"grant_{uid}"),
                InlineKeyboardButton(text="❌ Отозвать премиум", callback_data=f"revoke_{uid}")
            ]
        ])
        await message.answer(text, reply_markup=kb)

    # Обработчики inline-кнопок для user_info
    @dp.callback_query(F.data.startswith("block_"))
    async def block_user(call: types.CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer("⛔ Доступ запрещён", show_alert=True)
            return
        uid = int(call.data.split("_")[1])
        db.cursor.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
        db.commit()
        await call.answer("Пользователь заблокирован")
        await call.message.edit_reply_markup(reply_markup=None)  # убираем кнопки
        await call.message.answer(f"✅ Пользователь {uid} заблокирован.")

    @dp.callback_query(F.data.startswith("unblock_"))
    async def unblock_user(call: types.CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer("⛔ Доступ запрещён", show_alert=True)
            return
        uid = int(call.data.split("_")[1])
        db.cursor.execute("UPDATE users SET blocked=0 WHERE user_id=?", (uid,))
        db.commit()
        await call.answer("Пользователь разблокирован")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"✅ Пользователь {uid} разблокирован.")

    @dp.callback_query(F.data.startswith("grant_"))
    async def grant_premium_from_button(call: types.CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer("⛔ Доступ запрещён", show_alert=True)
            return
        uid = int(call.data.split("_")[1])
        # Запросим количество дней через сообщение
        await call.message.answer(f"Введите количество дней для премиума пользователю {uid} (например, 30):")
        # Сохраним состояние, чтобы следующий шаг обработать
        # Можно использовать FSM, но для простоты используем словарь в памяти
        # Предположим, что у нас есть FSM, но здесь не будем усложнять
        await call.answer("Функция в разработке. Используйте /grant_manual")

    @dp.callback_query(F.data.startswith("revoke_"))
    async def revoke_premium_from_button(call: types.CallbackQuery):
        if not is_admin(call.from_user.id):
            await call.answer("⛔ Доступ запрещён", show_alert=True)
            return
        uid = int(call.data.split("_")[1])
        db.revoke_premium(uid)  # предполагаем, что метод есть
        await call.answer("Премиум отозван")
        await call.message.edit_reply_markup(reply_markup=None)
        await call.message.answer(f"✅ Премиум у пользователя {uid} отозван.")

    # ---------- Команда /grant_manual – выдача премиума ----------
    @dp.message(Command("grant_manual"))
    async def cmd_grant_manual(message: types.Message):
        if not await ensure_admin(message):
            return
        if not ADMIN_PASSWORD:
            await message.answer("Админ-панель не настроена (пароль не задан).")
            return
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Использование: /grant_manual <user_id> <days>")
            return
        try:
            uid = int(parts[1])
            days = int(parts[2])
        except ValueError:
            await message.answer("Неверные параметры. Должны быть числа.")
            return
        period_end = db.grant_premium(uid, days, provider="admin_manual", provider_id=str(message.from_user.id))
        if period_end:
            await message.answer(f"✅ Премиум выдан пользователю {uid} на {days} дней.")
            try:
                await bot.send_message(uid, f"⭐ Вам выдан премиум-доступ на {days} дней!")
            except Exception as e:
                logger.warning(f"Не удалось уведомить пользователя {uid}: {e}")
        else:
            await message.answer("❌ Ошибка при выдаче премиума.")

    # ---------- Команда /revoke_premium – отзыв премиума ----------
    @dp.message(Command("revoke_premium"))
    async def cmd_revoke_premium(message: types.Message):
        if not await ensure_admin(message):
            return
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /revoke_premium <user_id>")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await message.answer("Неверный ID.")
            return
        db.revoke_premium(uid)
        await message.answer(f"✅ Премиум у пользователя {uid} отозван.")
        try:
            await bot.send_message(uid, "❌ Ваш премиум-доступ был отозван администратором.")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {uid}: {e}")

    # ---------- Команда /broadcast – рассылка сообщений ----------
    @dp.message(Command("broadcast"))
    async def broadcast(message: types.Message):
        if not await ensure_admin(message):
            return
        text = message.text.replace("/broadcast", "", 1).strip()
        if not text:
            await message.answer("Введите текст для рассылки.\nПример: /broadcast Всем привет!")
            return
        # Получаем всех пользователей (кроме заблокированных)
        db.cursor.execute("SELECT user_id FROM users WHERE blocked IS NULL OR blocked=0")
        users = [row[0] for row in db.cursor.fetchall()]
        if not users:
            await message.answer("Нет пользователей для рассылки.")
            return
        await message.answer(f"📨 Начинаю рассылку {len(users)} пользователям...")
        sent = 0
        failed = 0
        for uid in users:
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning(f"Ошибка отправки пользователю {uid}: {e}")
        await message.answer(f"✅ Рассылка завершена.\n📨 Отправлено: {sent}\n❌ Не доставлено: {failed}")

    # ---------- Команда /set_tariff – изменение тарифа ----------
    # Для хранения тарифов используем таблицу settings (key-value)
    @dp.message(Command("set_tariff"))
    async def set_tariff(message: types.Message):
        if not await ensure_admin(message):
            return
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("Использование: /set_tariff <days> <stars>")
            return
        try:
            days = int(parts[1])
            stars = int(parts[2])
        except ValueError:
            await message.answer("Неверные параметры. Должны быть числа.")
            return
        # Сохраняем в БД
        key = f"tariff_{days}"
        db.cursor.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, str(stars), str(stars))
        )
        db.commit()
        await message.answer(f"✅ Тариф {days} дней = {stars} ⭐ установлен.")

    # ---------- Команда /tariffs – просмотр текущих тарифов ----------
    @dp.message(Command("tariffs"))
    async def view_tariffs(message: types.Message):
        if not await ensure_admin(message):
            return
        db.cursor.execute("SELECT key, value FROM settings WHERE key LIKE 'tariff_%'")
        rows = db.cursor.fetchall()
        if not rows:
            await message.answer("Тарифы не заданы. Используйте /set_tariff")
            return
        text = "**Текущие тарифы:**\n"
        for key, value in rows:
            days = key.replace("tariff_", "")
            text += f"  {days} дней: {value} ⭐\n"
        await message.answer(text)

    # ---------- Команда /admin_logs – просмотр логов ----------
    @dp.message(Command("admin_logs"))
    async def admin_logs(message: types.Message):
        if not await ensure_admin(message):
            return
        # Определяем путь к файлу лога (предположим, это bot.log в корне)
        log_file = Path("bot.log")
        if not log_file.exists():
            await message.answer("Файл лога не найден.")
            return
        args = message.text.split()
        lines = 20  # по умолчанию
        if len(args) >= 2:
            try:
                lines = int(args[1])
                if lines > 100:
                    lines = 100
            except ValueError:
                pass
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                last_lines = all_lines[-lines:]
                text = f"**Последние {len(last_lines)} строк лога:**\n```\n" + "".join(last_lines) + "\n```"
                # Telegram ограничение 4096 символов
                if len(text) > 4000:
                    text = text[:4000] + "\n... (обрезано)"
                await message.answer(text)
        except Exception as e:
            await message.answer(f"❌ Ошибка чтения лога: {e}")

    # ---------- Команда /admin_help – справка по админ-командам ----------
    @dp.message(Command("admin_help"))
    async def admin_help(message: types.Message):
        if not await ensure_admin(message):
            return
        text = """
**Админ-команды:**

/admin [пароль] – вход в админ-панель
/admin_stats – общая статистика
/user_info <id> – информация о пользователе
/grant_manual <id> <days> – выдать премиум
/revoke_premium <id> – отозвать премиум
/broadcast <текст> – рассылка всем пользователям
/set_tariff <days> <stars> – установить тариф
/tariffs – показать текущие тарифы
/admin_logs [N] – показать последние N строк лога
/block_user <id> – заблокировать пользователя
/unblock_user <id> – разблокировать
/admin_help – это сообщение
        """
        await message.answer(text)

    # ---------- Команды блокировки (текстовые) ----------
    @dp.message(Command("block_user"))
    async def block_user_text(message: types.Message):
        if not await ensure_admin(message):
            return
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /block_user <user_id>")
            return
        try:
            uid = int(args[1])
        except ValueError:
            await message.answer("Неверный ID.")
            return
        db.cursor.execute("UPDATE users SET blocked=1 WHERE user_id=?", (uid,))
        db.commit()
        await message.answer(f"✅ Пользователь {uid} заблокирован.")

    @dp.message(Command("unblock_user"))
    async def unblock_user_text(message: types.Message):
        if not await ensure_admin(message):
            return
        args = message.text.split()
        if len(args) < 2:
            await message.answer("Использование: /unblock_user <user_id>")
            return
        try:
            uid = int(args[1])
        except ValueError:
            await message.answer("Неверный ID.")
            return
        db.cursor.execute("UPDATE users SET blocked=0 WHERE user_id=?", (uid,))
        db.commit()
        await message.answer(f"✅ Пользователь {uid} разблокирован.")

    # ---------- Добавление поля blocked в таблицу users, если его нет ----------
    try:
        db.cursor.execute("ALTER TABLE users ADD COLUMN blocked INTEGER DEFAULT 0")
        db.commit()
        logger.info("Поле 'blocked' добавлено в таблицу users")
    except Exception as e:
        # Поле уже существует или другая ошибка – игнорируем
        pass

    # ---------- Создание таблицы settings, если её нет ----------
    db.cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    db.commit()

    logger.info("Admin handlers registered successfully")
