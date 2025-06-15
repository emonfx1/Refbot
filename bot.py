#!/usr/bin/env python3
import os
import sqlite3
import asyncio
import logging
import json
import time
import threading
import uuid
from datetime import datetime, timedelta
from flask import Flask, request, render_template_string, redirect, session, url_for
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.utils.deep_linking import create_start_link
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ======== CONFIGURATION ========
BOT_TOKEN = "7555989501:AAFHBEasBpiiUi4ncmo2OQBvRZoF-mJI03c"
ADMIN_ID = 5559075560  # Replace with your Telegram ID
DB_FILE = "bot_database.db"
WALLET_INFO = "TRC20: TAbcdefghijk1234567890"  # Your wallet info
REFERRAL_REWARD = 0.05  # USDT
MIN_WITHDRAWAL = 1.0  # USDT
NGROK_ENABLED = False  # Set to True if using ngrok
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = 5000
# ===============================

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__)
app.secret_key = os.urandom(24)

# Initialize Aiogram with correct parse mode settings
bot = Bot(
    token=BOT_TOKEN,
    session=AiohttpSession(),
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher()

# ======== DATABASE SETUP ========
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        balance REAL DEFAULT 0.0,
        referrals INTEGER DEFAULT 0,
        referral_by INTEGER,
        last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    # Tasks table
    c.execute('''CREATE TABLE IF NOT EXISTS tasks (
        task_id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_type TEXT,
        title TEXT,
        description TEXT,
        url TEXT,
        reward REAL,
        cooldown INTEGER,
        is_active BOOLEAN DEFAULT 1
    )''')
    
    # Withdrawals table
    c.execute('''CREATE TABLE IF NOT EXISTS withdrawals (
        withdrawal_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount REAL,
        wallet TEXT,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id)
    )''')
    
    # Activity log
    c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        task_id INTEGER,
        reward REAL,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(user_id),
        FOREIGN KEY(task_id) REFERENCES tasks(task_id)
    )''')
    
    # Referrals table
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        referral_id INTEGER PRIMARY KEY AUTOINCREMENT,
        inviter_id INTEGER,
        invited_id INTEGER UNIQUE,
        reward_given BOOLEAN DEFAULT 0,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(inviter_id) REFERENCES users(user_id),
        FOREIGN KEY(invited_id) REFERENCES users(user_id)
    )''')
    
    # Insert default tasks if not exist
    default_tasks = [
        ('website', 'Visit Website', 'Visit our sponsor website for 10 seconds', 'https://example.com', 0.05, 3600),
        ('channel', 'Join Channel', 'Join our official channel', '@example_channel', 0.10, 86400),
        ('ad', 'Watch Ad', 'Watch this promotional video', 'https://youtube.com/watch?v=abc123', 0.07, 7200)
    ]
    
    for task in default_tasks:
        c.execute("SELECT COUNT(*) FROM tasks WHERE title=?", (task[1],))
        if c.fetchone()[0] == 0:
            c.execute("INSERT INTO tasks (task_type, title, description, url, reward, cooldown) VALUES (?, ?, ?, ?, ?, ?)", task)
    
    conn.commit()
    conn.close()

init_db()

# Database helper functions
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def get_user(user_id):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return user

def create_user(user_id, username, full_name, referral_by=None):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, referral_by) VALUES (?, ?, ?, ?)",
                (user_id, username, full_name, referral_by))
    conn.commit()
    conn.close()
    
    # Handle referral
    if referral_by:
        update_referral_count(referral_by)
        award_referral_bonus(referral_by, user_id)

def update_balance(user_id, amount):
    conn = get_db()
    conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

def update_referral_count(user_id):
    conn = get_db()
    conn.execute("UPDATE users SET referrals = referrals + 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def award_referral_bonus(inviter_id, new_user_id):
    conn = get_db()
    # Check if already awarded
    c = conn.execute("SELECT reward_given FROM referrals WHERE inviter_id = ? AND invited_id = ?", 
                     (inviter_id, new_user_id))
    existing = c.fetchone()
    
    if not existing or not existing['reward_given']:
        # Award inviter
        update_balance(inviter_id, REFERRAL_REWARD)
        # Award new user
        update_balance(new_user_id, REFERRAL_REWARD)
        
        # Log referral
        conn.execute("INSERT OR REPLACE INTO referrals (inviter_id, invited_id, reward_given) VALUES (?, ?, 1)",
                    (inviter_id, new_user_id))
        conn.commit()
    conn.close()

def get_tasks(task_type=None):
    conn = get_db()
    if task_type:
        tasks = conn.execute("SELECT * FROM tasks WHERE task_type = ? AND is_active = 1", (task_type,)).fetchall()
    else:
        tasks = conn.execute("SELECT * FROM tasks WHERE is_active = 1").fetchall()
    conn.close()
    return tasks

def get_task(task_id):
    conn = get_db()
    task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
    conn.close()
    return task

def log_activity(user_id, task_id, reward):
    conn = get_db()
    conn.execute("INSERT INTO activity_log (user_id, task_id, reward) VALUES (?, ?, ?)",
                (user_id, task_id, reward))
    conn.commit()
    conn.close()

def can_do_task(user_id, task_id):
    conn = get_db()
    task = get_task(task_id)
    if not task: return False
    
    # Check cooldown
    last_done = conn.execute(
        "SELECT timestamp FROM activity_log WHERE user_id = ? AND task_id = ? ORDER BY timestamp DESC LIMIT 1",
        (user_id, task_id)
    ).fetchone()
    
    conn.close()
    
    if last_done:
        last_time = datetime.strptime(last_done['timestamp'], "%Y-%m-%d %H:%M:%S")
        cooldown = timedelta(seconds=task['cooldown'])
        return datetime.now() > last_time + cooldown
    return True

def create_withdrawal(user_id, amount, wallet):
    conn = get_db()
    conn.execute("INSERT INTO withdrawals (user_id, amount, wallet) VALUES (?, ?, ?)",
                (user_id, amount, wallet))
    conn.commit()
    conn.close()

def get_withdrawals(status=None):
    conn = get_db()
    if status:
        withdrawals = conn.execute("SELECT * FROM withdrawals WHERE status = ?", (status,)).fetchall()
    else:
        withdrawals = conn.execute("SELECT * FROM withdrawals").fetchall()
    conn.close()
    return withdrawals

def get_withdrawal(wd_id):
    conn = get_db()
    withdrawal = conn.execute("SELECT * FROM withdrawals WHERE withdrawal_id = ?", (wd_id,)).fetchone()
    conn.close()
    return withdrawal

def update_withdrawal(wd_id, status):
    conn = get_db()
    conn.execute("UPDATE withdrawals SET status = ? WHERE withdrawal_id = ?", (status, wd_id))
    conn.commit()
    conn.close()

def get_stats():
    conn = get_db()
    stats = {
        'total_users': conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
        'active_users': conn.execute("SELECT COUNT(*) FROM users WHERE last_activity > datetime('now', '-1 day')").fetchone()[0],
        'total_payouts': conn.execute("SELECT COALESCE(SUM(amount), 0) FROM withdrawals WHERE status = 'approved'").fetchone()[0]
    }
    conn.close()
    return stats

# ======== TELEGRAM BOT HANDLERS ========
class WithdrawState(StatesGroup):
    waiting_for_wallet = State()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    args = message.text.split()
    referral_by = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
    
    user = get_user(message.from_user.id)
    if not user:
        create_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.full_name,
            referral_by
        )
    
    await show_main_menu(message)

async def show_main_menu(message: types.Message):
    user = get_user(message.from_user.id)
    if not user: return
    
    balance = user['balance']
    referrals = user['referrals']
    
    text = (
        f"👋 *Welcome, {message.from_user.full_name}\!*\n\n"
        f"💼 *Your Balance:* `{balance:.2f} USDT`\n"
        f"👥 *Referrals:* `{referrals}`\n\n"
        "📱 *Earn crypto by completing simple tasks\!*"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Earn Coins", callback_data="earn_menu")],
        [
            InlineKeyboardButton(text="📊 Stats", callback_data="stats"),
            InlineKeyboardButton(text="👨‍💼 Referrals", callback_data="referrals")
        ],
        [InlineKeyboardButton(text="💰 Withdraw", callback_data="withdraw_menu")]
    ])
    
    await message.answer(text, reply_markup=keyboard)

@dp.callback_query(F.data == "main_menu")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.delete()
    await show_main_menu(callback.message)

@dp.callback_query(F.data == "earn_menu")
async def earn_menu(callback: types.CallbackQuery):
    tasks = get_tasks()
    
    if not tasks:
        await callback.answer("No tasks available at the moment", show_alert=True)
        return
    
    keyboard = InlineKeyboardBuilder()
    
    for task in tasks:
        keyboard.add(InlineKeyboardButton(
            text=f"{task['title']} (+{task['reward']:.2f} USDT)",
            callback_data=f"task_{task['task_id']}"
        ))
    
    keyboard.adjust(1)
    keyboard.row(InlineKeyboardButton(text="🔙 Back", callback_data="main_menu"))
    
    await callback.message.edit_text(
        "🎯 *Available Tasks*\n\n"
        "Select a task to complete and earn USDT:",
        reply_markup=keyboard.as_markup()
    )

@dp.callback_query(F.data.startswith("task_"))
async def start_task(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    task = get_task(task_id)
    
    if not task:
        await callback.answer("Task not found", show_alert=True)
        return
    
    if not can_do_task(user_id, task_id):
        await callback.answer("You need to wait before doing this task again", show_alert=True)
        return
    
    if task['task_type'] == 'website':
        await handle_website_task(callback, task)
    elif task['task_type'] == 'channel':
        await handle_channel_task(callback, task)
    elif task['task_type'] == 'ad':
        await handle_ad_task(callback, task)

async def handle_website_task(callback: types.CallbackQuery, task):
    user_id = callback.from_user.id
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Visit Website", url=task['url'])],
        [InlineKeyboardButton(text="✅ Done", callback_data=f"verify_website_{task['task_id']}")]
    ])
    
    await callback.message.edit_text(
        f"🌐 *{task['title']}*\n\n"
        f"{task['description']}\n\n"
        f"⚠️ You must stay on the website for *10 seconds*",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("verify_website_"))
async def verify_website(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    task = get_task(task_id)
    
    update_balance(user_id, task['reward'])
    log_activity(user_id, task_id, task['reward'])
    
    await callback.message.edit_text(
        f"✅ *Task Completed\!*\n\n"
        f"You earned: `+{task['reward']:.2f} USDT`\n"
        f"New balance: `{get_user(user_id)['balance']:.2f} USDT`"
    )

async def handle_channel_task(callback: types.CallbackQuery, task):
    user_id = callback.from_user.id
    channel_username = task['url'].replace('@', '')
    
    try:
        chat_member = await bot.get_chat_member(
            chat_id=f"@{channel_username}",
            user_id=user_id
        )
        is_member = chat_member.status in ['member', 'administrator', 'creator']
    except:
        is_member = False
    
    if is_member:
        update_balance(user_id, task['reward'])
        log_activity(user_id, task_id, task['reward'])
        
        await callback.message.edit_text(
            f"✅ *Task Completed\!*\n\n"
            f"You earned: `+{task['reward']:.2f} USDT`\n"
            f"New balance: `{get_user(user_id)['balance']:.2f} USDT`"
        )
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👥 Join Channel", url=f"https://t.me/{channel_username}")],
            [InlineKeyboardButton(text="✅ Verify Join", callback_data=f"verify_channel_{task['task_id']}")]
        ])
        
        await callback.message.edit_text(
            f"👥 *{task['title']}*\n\n"
            f"{task['description']}\n\n"
            f"Join the channel and click verify:",
            reply_markup=keyboard
        )

@dp.callback_query(F.data.startswith("verify_channel_"))
async def verify_channel(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    task = get_task(task_id)
    channel_username = task['url'].replace('@', '')
    
    try:
        chat_member = await bot.get_chat_member(
            chat_id=f"@{channel_username}",
            user_id=user_id
        )
        is_member = chat_member.status in ['member', 'administrator', 'creator']
    except:
        is_member = False
    
    if is_member:
        update_balance(user_id, task['reward'])
        log_activity(user_id, task_id, task['reward'])
        
        await callback.message.edit_text(
            f"✅ *Task Completed\!*\n\n"
            f"You earned: `+{task['reward']:.2f} USDT`\n"
            f"New balance: `{get_user(user_id)['balance']:.2f} USDT`"
        )
    else:
        await callback.answer("You haven't joined the channel yet", show_alert=True)

async def handle_ad_task(callback: types.CallbackQuery, task):
    user_id = callback.from_user.id
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 View Ad", callback_data=f"view_ad_{task['task_id']}")]
    ])
    
    await callback.message.edit_text(
        f"📺 *{task['title']}*\n\n"
        f"{task['description']}\n\n"
        f"Click below to watch the ad:",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("view_ad_"))
async def view_ad(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    task = get_task(task_id)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Open Content", url=task['url'])],
        [InlineKeyboardButton(text="⏳ Start Timer (10s)", callback_data=f"start_timer_{task_id}")]
    ])
    
    await callback.message.edit_text(
        f"⏱ *Watch the Ad*\n\n"
        f"Please watch the content for at least 10 seconds",
        reply_markup=keyboard
    )

@dp.callback_query(F.data.startswith("start_timer_"))
async def start_timer(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[2])
    user_id = callback.from_user.id
    task = get_task(task_id)
    
    # Start countdown
    msg = await callback.message.edit_text("🕒 10 seconds remaining...")
    for i in range(9, 0, -1):
        await asyncio.sleep(1)
        await msg.edit_text(f"🕒 {i} seconds remaining...")
    
    await asyncio.sleep(1)
    update_balance(user_id, task['reward'])
    log_activity(user_id, task_id, task['reward'])
    
    await msg.edit_text(
        f"✅ *Task Completed\!*\n\n"
        f"You earned: `+{task['reward']:.2f} USDT`\n"
        f"New balance: `{get_user(user_id)['balance']:.2f} USDT`"
    )

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    stats = get_stats()
    
    text = (
        f"📊 *Your Statistics*\n\n"
        f"💰 Balance: `{user['balance']:.2f} USDT`\n"
        f"👥 Referrals: `{user['referrals']}`\n\n"
        f"🌐 *Global Stats*\n"
        f"👤 Total Users: `{stats['total_users']}`\n"
        f"🔥 Active Users: `{stats['active_users']}`\n"
        f"💸 Total Payouts: `{stats['total_payouts']:.2f} USDT`"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)

@dp.callback_query(F.data == "referrals")
async def show_referrals(callback: types.CallbackQuery):
    user = get_user(callback.from_user.id)
    referral_link = await create_start_link(bot, str(user['user_id']), encode=True)
    
    text = (
        f"👥 *Referral Program*\n\n"
        f"🔗 Your referral link:\n`{referral_link}`\n\n"
        f"💸 Earn `{REFERRAL_REWARD:.2f} USDT` for each friend who joins using your link\!\n"
        f"👤 Your referrals: `{user['referrals']}`\n"
        f"💰 Total earned: `{user['referrals'] * REFERRAL_REWARD:.2f} USDT`"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Share Link", 
         switch_inline_query=f"Earn crypto with me! {referral_link}")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard)

@dp.callback_query(F.data == "withdraw_menu")
async def withdraw_menu(callback: types.CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    
    if user['balance'] < MIN_WITHDRAWAL:
        await callback.answer(
            f"Minimum withdrawal amount is {MIN_WITHDRAWAL} USDT", 
            show_alert=True
        )
        return
    
    text = (
        f"💰 *Withdraw Funds*\n\n"
        f"Your balance: `{user['balance']:.2f} USDT`\n"
        f"Minimum withdrawal: `{MIN_WITHDRAWAL} USDT`\n\n"
        f"Please send your TRC20/BEP20 wallet address:"
    )
    
    await callback.message.edit_text(text)
    await state.set_state(WithdrawState.waiting_for_wallet)

@dp.message(WithdrawState.waiting_for_wallet)
async def process_withdrawal(message: types.Message, state: FSMContext):
    user = get_user(message.from_user.id)
    wallet_address = message.text.strip()
    
    # Basic validation
    if len(wallet_address) < 20:
        await message.answer("❌ Invalid wallet address. Please try again.")
        return
    
    if user['balance'] < MIN_WITHDRAWAL:
        await message.answer(f"❌ Your balance is below the minimum withdrawal of {MIN_WITHDRAWAL} USDT")
        await state.clear()
        return
    
    # Create withdrawal request
    create_withdrawal(message.from_user.id, user['balance'], wallet_address)
    update_balance(message.from_user.id, -user['balance'])
    
    # Notify admin
    await bot.send_message(
        ADMIN_ID,
        f"⚠️ *New Withdrawal Request*\n\n"
        f"👤 User: [{message.from_user.full_name}](tg://user?id={message.from_user.id})\n"
        f"💳 Wallet: `{wallet_address}`\n"
        f"💸 Amount: `{user['balance']:.2f} USDT`\n\n"
        "Use /withdrawals to manage requests"
    )
    
    await message.answer(
        "✅ *Withdrawal Request Submitted\!*\n\n"
        "Your request has been sent for approval. "
        "Administrator will process it within 24 hours."
    )
    await state.clear()

# ======== ADMIN COMMANDS ========
@dp.message(Command("admin"))
async def admin_menu(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📤 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🛠 Manage Tasks", callback_data="manage_tasks")],
        [InlineKeyboardButton(text="💳 Withdrawals", callback_data="admin_withdrawals")],
        [InlineKeyboardButton(text="🌐 Web Panel", web_app=WebAppInfo(url=f"http://localhost:{WEB_SERVER_PORT}/admin"))]
    ])
    
    await message.answer("👑 *Admin Panel*", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    stats = get_stats()
    
    text = (
        "📊 *Admin Statistics*\n\n"
        f"👤 Total Users: `{stats['total_users']}`\n"
        f"🔥 Active Users: `{stats['active_users']}`\n"
        f"💸 Total Payouts: `{stats['total_payouts']:.2f} USDT`"
    )
    
    await callback.message.edit_text(text)

@dp.callback_query(F.data == "admin_withdrawals")
async def admin_withdrawals(callback: types.CallbackQuery):
    withdrawals = get_withdrawals('pending')
    
    if not withdrawals:
        await callback.answer("No pending withdrawals", show_alert=True)
        return
    
    text = "⏳ *Pending Withdrawals*\n\n"
    keyboard = InlineKeyboardBuilder()
    
    for wd in withdrawals:
        user = get_user(wd['user_id'])
        text += (
            f"💳 #{wd['withdrawal_id']}\n"
            f"👤 User: [{user['full_name']}](tg://user?id={user['user_id']})\n"
            f"💸 Amount: `{wd['amount']:.2f} USDT`\n"
            f"🔗 Wallet: `{wd['wallet']}`\n\n"
        )
        keyboard.add(InlineKeyboardButton(
            text=f"Approve #{wd['withdrawal_id']}",
            callback_data=f"approve_wd_{wd['withdrawal_id']}"
        ))
        keyboard.add(InlineKeyboardButton(
            text=f"Reject #{wd['withdrawal_id']}",
            callback_data=f"reject_wd_{wd['withdrawal_id']}"
        ))
    
    keyboard.adjust(2)
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("approve_wd_"))
async def approve_withdrawal(callback: types.CallbackQuery):
    wd_id = int(callback.data.split("_")[2])
    withdrawal = get_withdrawal(wd_id)
    if not withdrawal:
        await callback.answer("Withdrawal not found", show_alert=True)
        return
        
    user = get_user(withdrawal['user_id'])
    if not user:
        await callback.answer("User not found", show_alert=True)
        return
    
    # Update status
    update_withdrawal(wd_id, 'approved')
    
    # Notify user
    await bot.send_message(
        user['user_id'],
        f"✅ *Withdrawal Approved\!*\n\n"
        f"Your withdrawal of `{withdrawal['amount']:.2f} USDT` has been approved.\n"
        f"Funds have been sent to your wallet."
    )
    
    await callback.answer("Withdrawal approved!", show_alert=True)
    await callback.message.delete()

@dp.callback_query(F.data.startswith("reject_wd_"))
async def reject_withdrawal(callback: types.CallbackQuery):
    wd_id = int(callback.data.split("_")[2])
    withdrawal = get_withdrawal(wd_id)
    if not withdrawal:
        await callback.answer("Withdrawal not found", show_alert=True)
        return
        
    user = get_user(withdrawal['user_id'])
    if not user:
        await callback.answer("User not found", show_alert=True)
        return
    
    # Update status and refund
    update_withdrawal(wd_id, 'rejected')
    update_balance(user['user_id'], withdrawal['amount'])
    
    # Notify user
    await bot.send_message(
        user['user_id'],
        f"❌ *Withdrawal Rejected*\n\n"
        f"Your withdrawal of `{withdrawal['amount']:.2f} USDT` was rejected.\n"
        f"Funds have been returned to your balance."
    )
    
    await callback.answer("Withdrawal rejected!", show_alert=True)
    await callback.message.delete()

# ======== FLASK WEB PANEL ========
# HTML Templates
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Admin Login</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
</head>
<body class="bg-light">
    <div class="container py-5">
        <div class="row justify-content-center">
            <div class="col-md-6">
                <div class="card shadow-sm">
                    <div class="card-body p-4">
                        <h2 class="card-title text-center mb-4">Admin Login</h2>
                        <form method="POST">
                            <div class="mb-3">
                                <label for="token" class="form-label">Security Token</label>
                                <input type="password" class="form-control" id="token" name="token" required>
                            </div>
                            <button type="submit" class="btn btn-primary w-100">Login</button>
                        </form>
                    </div>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Admin Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-light">
    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container">
            <a class="navbar-brand" href="#">Bot Admin Panel</a>
            <div class="d-flex">
                <a href="/admin/logout" class="btn btn-outline-light">Logout</a>
            </div>
        </div>
    </nav>
    
    <div class="container py-4">
        <div class="row">
            <div class="col-md-4 mb-4">
                <div class="card shadow-sm h-100">
                    <div class="card-body">
                        <h5 class="card-title">Total Users</h5>
                        <p class="display-4">{{ stats.total_users }}</p>
                    </div>
                </div>
            </div>
            <div class="col-md-4 mb-4">
                <div class="card shadow-sm h-100">
                    <div class="card-body">
                        <h5 class="card-title">Active Users</h5>
                        <p class="display-4">{{ stats.active_users }}</p>
                    </div>
                </div>
            </div>
            <div class="col-md-4 mb-4">
                <div class="card shadow-sm h-100">
                    <div class="card-body">
                        <h5 class="card-title">Total Payouts</h5>
                        <p class="display-4">{{ stats.total_payouts }} USDT</p>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="row">
            <div class="col-12">
                <div class="card shadow-sm mb-4">
                    <div class="card-header bg-white">
                        <h5 class="mb-0">Recent Withdrawals</h5>
                    </div>
                    <div class="card-body">
                        {% if withdrawals %}
                        <div class="table-responsive">
                            <table class="table table-hover">
                                <thead>
                                    <tr>
                                        <th>ID</th>
                                        <th>User</th>
                                        <th>Amount</th>
                                        <th>Wallet</th>
                                        <th>Status</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for wd in withdrawals %}
                                    <tr>
                                        <td>#{{ wd.withdrawal_id }}</td>
                                        <td>{{ wd.user_id }}</td>
                                        <td>{{ wd.amount }} USDT</td>
                                        <td><code>{{ wd.wallet }}</code></td>
                                        <td>
                                            <span class="badge bg-warning">{{ wd.status }}</span>
                                        </td>
                                        <td>
                                            <a href="/admin/approve_withdrawal/{{ wd.withdrawal_id }}" class="btn btn-sm btn-success">Approve</a>
                                            <a href="/admin/reject_withdrawal/{{ wd.withdrawal_id }}" class="btn btn-sm btn-danger">Reject</a>
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <p class="text-center text-muted">No pending withdrawals</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>
        
        <div class="card shadow-sm">
            <div class="card-header bg-white">
                <h5 class="mb-0">Payout Wallet</h5>
            </div>
            <div class="card-body">
                <p class="text-center"><code>{{ wallet_info }}</code></p>
            </div>
        </div>
    </div>
</body>
</html>
"""

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        token = request.form.get('token')
        if token == BOT_TOKEN:  # Simple token auth
            session['admin'] = True
            return redirect(url_for('admin_dashboard'))
        return "Invalid token", 401
    return render_template_string(LOGIN_HTML)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    stats = get_stats()
    withdrawals = get_withdrawals('pending')
    
    return render_template_string(
        DASHBOARD_HTML, 
        stats=stats,
        withdrawals=withdrawals,
        wallet_info=WALLET_INFO
    )

@app.route('/admin/approve_withdrawal/<int:wd_id>')
def approve_withdrawal_web(wd_id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    update_withdrawal(wd_id, 'approved')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject_withdrawal/<int:wd_id>')
def reject_withdrawal_web(wd_id):
    if not session.get('admin'):
        return redirect(url_for('admin_login'))
    
    withdrawal = get_withdrawal(wd_id)
    if withdrawal:
        update_balance(withdrawal['user_id'], withdrawal['amount'])
        update_withdrawal(wd_id, 'rejected')
    return redirect(url_for('admin_dashboard'))

# ======== START APPLICATION ========
async def on_startup():
    logger.info("Bot started")
    # Skip any pending updates
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Bot is ready and polling for updates")

def run_flask():
    app.run(host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)

async def run_bot():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start Aiogram bot in the main thread
    asyncio.run(run_bot())
