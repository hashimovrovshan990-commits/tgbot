# -*- coding: utf-8 -*-
"""
Синхронная база данных для бота (sqlite3).
Схема: users, accounts, trades, subscriptions.
"""
import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path="database.db"):
        self.db_path = str(Path(db_path).resolve())
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                language TEXT DEFAULT 'ru',
                is_admin INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                premium_until TEXT,
                total_trades INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                initial_balance REAL NOT NULL,
                current_balance REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                account_id INTEGER,
                pair TEXT,
                trade_type TEXT,
                open_date TEXT,
                close_date TEXT,
                amount REAL,
                take_profit REAL,
                status TEXT,
                strategy TEXT,
                checklist TEXT,
                notes TEXT,
                screenshot_path TEXT,
                pnl REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                provider TEXT,
                provider_id TEXT,
                status TEXT,
                period_end TEXT,
                expires_at TEXT
            );
        """)
        self.conn.commit()

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

