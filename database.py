import aiosqlite


class Database:
    def __init__(self, db_path="database.db"):
        self.db_path = db_path

    async def create_tables(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                trades INTEGER DEFAULT 0,
                subscription INTEGER DEFAULT 0
            )
            """)
            await db.commit()

    async def add_user(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
                (user_id,)
            )
            await db.commit()

    async def count_user_trades(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT trades FROM users WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def add_trade(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET trades = trades + 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()

    async def has_subscription(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT subscription FROM users WHERE user_id = ?",
                (user_id,)
            )
            row = await cursor.fetchone()
            return bool(row[0]) if row else False

    async def activate_subscription(self, user_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE users SET subscription = 1 WHERE user_id = ?",
                (user_id,)
            )
            await db.commit()
