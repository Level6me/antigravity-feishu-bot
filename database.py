import os
import json
import sqlite3
import asyncio
import aiosqlite
from logger import log

DB_FILE = "antigravity_bot.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS chat_sessions (
            chat_id TEXT PRIMARY KEY,
            data JSON NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            data JSON NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def migrate_from_json():
    conn = get_db()
    cursor = conn.cursor()
    if os.path.exists("chat_sessions.json"):
        try:
            with open("chat_sessions.json", "r") as f:
                sessions = json.load(f)
            for chat_id, data in sessions.items():
                cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
            os.rename("chat_sessions.json", "chat_sessions.json.bak")
        except Exception as e:
            log.error(f"Error migrating sessions: {e}")
            
    if os.path.exists("user_profiles.json"):
        try:
            with open("user_profiles.json", "r") as f:
                profiles = json.load(f)
            for user_id, data in profiles.items():
                cursor.execute('INSERT OR REPLACE INTO user_profiles (user_id, data) VALUES (?, ?)', (user_id, json.dumps(data)))
            os.rename("user_profiles.json", "user_profiles.json.bak")
        except Exception as e:
            log.error(f"Error migrating profiles: {e}")
            
    conn.commit()
    conn.close()

init_db()
migrate_from_json()

async def get_session_async(chat_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT data FROM chat_sessions WHERE chat_id = ?', (chat_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row['data'])
            return {"conversation": "", "model": "Gemini 3.5 Flash", "role": "无"}

async def save_session_async(chat_id, data):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
        await db.commit()

async def get_profile_async(user_id):
    async with aiosqlite.connect(DB_FILE) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT data FROM user_profiles WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return json.loads(row['data'])
            return []

async def save_profile_async(user_id, data):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute('INSERT OR REPLACE INTO user_profiles (user_id, data) VALUES (?, ?)', (user_id, json.dumps(data)))
        await db.commit()

def load_sessions():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT chat_id, data FROM chat_sessions')
    rows = cursor.fetchall()
    conn.close()
    return {row['chat_id']: json.loads(row['data']) for row in rows}

def save_sessions(sessions):
    conn = get_db()
    cursor = conn.cursor()
    for chat_id, data in sessions.items():
        cursor.execute('INSERT OR REPLACE INTO chat_sessions (chat_id, data) VALUES (?, ?)', (chat_id, json.dumps(data)))
    conn.commit()
    conn.close()
