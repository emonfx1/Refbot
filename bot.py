#!/usr/bin/env python3
import os
import time
import sqlite3
import uuid
import threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for
from pyrogram import Client, filters, enums
from pyrogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    Message,
    CallbackQuery
)

# Configuration
API_ID = 28593211
API_HASH = "27ad7de4fe5cab9f8e310c5cc4b8d43d"
BOT_TOKEN = "7555989501:AAFHBEasBpiiUi4ncmo2OQBvRZoF-mJI03c"
ADMIN_ID = 5559075560
MIN_WITHDRAW = 0.001  # Minimum withdrawal amount in LTC
DB_NAME = "litecoin_bot.db"
REF_REWARD = 0.0001   # Referral reward amount
DAILY_BONUS = 0.00005 # Daily bonus amount

# Initialize apps
app = Flask(__name__)
bot = Client("litecoin_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Database setup
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        # Users table
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            balance REAL DEFAULT 0,
            wallet TEXT,
            referrer_id INTEGER,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_bonus TIMESTAMP
        )''')
        
        # Tasks table
        c.execute('''CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            task_type TEXT,
            reward REAL,
            content TEXT,
            duration INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # Transactions table
        c.execute('''CREATE TABLE IF NOT EXISTS transactions (
            tx_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            tx_type TEXT,
            status TEXT DEFAULT 'pending',
            address TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # User tasks table
        c.execute('''CREATE TABLE IF NOT EXISTS user_tasks (
            user_id INTEGER,
            task_id TEXT,
            completed_at TIMESTAMP,
            PRIMARY KEY (user_id, task_id)
        )''')
        
        # Admins table
        c.execute('''CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )''')
        
        # Insert main admin
        try:
            c.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (ADMIN_ID,))
        except sqlite3.IntegrityError:
            pass
        conn.commit()

init_db()

# Database helper functions
def get_user(user_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return c.fetchone()

def create_user(user_id, referrer_id=None):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id, referrer_id) VALUES (?, ?)", 
                 (user_id, referrer_id))
        conn.commit()

def update_balance(user_id, amount):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", 
                 (amount, user_id))
        conn.commit()

def add_transaction(user_id, amount, tx_type, address=None):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO transactions 
                  (user_id, amount, tx_type, address) 
                  VALUES (?, ?, ?, ?)''',
                  (user_id, amount, tx_type, address))
        conn.commit()
        return c.lastrowid

def get_task(task_id):
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        return c.fetchone()

def create_task(task_type, reward, content, duration=0):
    task_id = str(uuid.uuid4())
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute('''INSERT INTO tasks 
                  (task_id, task_type, reward, content, duration) 
                  VALUES (?, ?, ?, ?, ?)''',
                  (task_id, task_type, reward, content, duration))
        conn.commit()
    return task_id

def complete_task(user_id, task_id):
    task = get_task(task_id)
    if not task:
        return False
    
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        try:
            # Update balance
            c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                     (task['reward'], user_id))
            
            # Record completion
            c.execute('''INSERT INTO user_tasks (user_id, task_id, completed_at)
                      VALUES (?, ?, CURRENT_TIMESTAMP)''', 
                     (user_id, task_id))
            
            # Record transaction
            c.execute('''INSERT INTO transactions 
                      (user_id, amount, tx_type) 
                      VALUES (?, ?, 'task')''',
                      (user_id, task['reward'],))
            conn.commit()
            return True
        except sqlite3.IntegrityError:  # Already completed
            return False

def get_pending_withdrawals():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM transactions WHERE tx_type = 'withdraw' AND status = 'pending'")
        return c.fetchall()

def update_withdrawal(tx_id, status):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE transactions SET status = ? WHERE tx_id = ?", 
                 (status, tx_id))
        conn.commit()

# Pyrogram Handlers
@bot.on_message(filters.command("start"))
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    referrer_id = None
    
    # Check for referral
    if len(message.command) > 1:
        try:
            referrer_id = int(message.command[1])
            create_user(referrer_id)  # Ensure referrer exists
        except ValueError:
            pass
    
    create_user(user_id, referrer_id)
    user = get_user(user_id)
    
    # Award referral if applicable
    if referrer_id and referrer_id != user_id:
        update_balance(referrer_id, REF_REWARD)
        add_transaction(referrer_id, REF_REWARD, 'referral')
    
    # Send welcome message
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Earn LTC", callback_data="earn_menu")],
        [
            InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard"),
            InlineKeyboardButton("📊 My Stats", callback_data="stats")
        ],
        [
            InlineKeyboardButton("👥 Invite & Earn", callback_data="referral"),
            InlineKeyboardButton("👛 Withdraw", callback_data="withdraw_menu")
        ],
        [InlineKeyboardButton("🎯 Daily Bonus", callback_data="daily_bonus")]
    ])
    
    await message.reply_text(
        f"🚀 **Welcome to Litecoin Click Bot!**\n\n"
        f"💰 **Balance:** `{user['balance']:.6f} LTC`\n"
        f"🔐 **Wallet:** `{user['wallet'] or 'Not set'}`\n\n"
        "Complete tasks to earn LTC and withdraw to your wallet!",
        reply_markup=keyboard
    )

@bot.on_callback_query(filters.regex("^earn_menu$"))
async def earn_menu(client: Client, callback: CallbackQuery):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Visit Websites", callback_data="tasks_visit")],
        [InlineKeyboardButton("📢 Join Channels", callback_data="tasks_join")],
        [InlineKeyboardButton("📝 View Posts", callback_data="tasks_view")],
        [InlineKeyboardButton("🎯 Custom Tasks", callback_data="tasks_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ])
    
    await callback.edit_message_text(
        "💼 **Available Tasks:**\n\n"
        "Choose a task category to start earning LTC:",
        reply_markup=keyboard
    )

@bot.on_callback_query(filters.regex("^tasks_"))
async def show_tasks(client: Client, callback: CallbackQuery):
    task_type = callback.data.split("_")[1]
    
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM tasks WHERE task_type = ?", (task_type,))
        tasks = c.fetchall()
    
    if not tasks:
        await callback.answer("No tasks available at the moment. Check back later!", show_alert=True)
        return
    
    # For simplicity, show first task
    task = tasks[0]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Complete Task", callback_data=f"do_task_{task['task_id']}")],
        [InlineKeyboardButton("🔙 Back", callback_data="earn_menu")]
    ])
    
    await callback.edit_message_text(
        f"**Task:** {task['content']}\n"
        f"💎 **Reward:** `{task['reward']:.6f} LTC`\n"
        f"⏱ **Duration:** {task['duration']} seconds",
        reply_markup=keyboard
    )

@bot.on_callback_query(filters.regex("^do_task_"))
async def perform_task(client: Client, callback: CallbackQuery):
    task_id = callback.data.split("_")[2]
    user_id = callback.from_user.id
    
    # Simulate task completion
    await callback.edit_message_text("⏳ Processing task...")
    time.sleep(2)  # Simulate task delay
    
    if complete_task(user_id, task_id):
        user = get_user(user_id)
        await callback.edit_message_text(
            f"✅ **Task Completed!**\n\n"
            f"➕ **Reward:** `{get_task(task_id)['reward']:.6f} LTC`\n"
            f"💰 **New Balance:** `{user['balance']:.6f} LTC`"
        )
    else:
        await callback.answer("You've already completed this task!", show_alert=True)

@bot.on_callback_query(filters.regex("^withdraw_menu$"))
async def withdraw_menu(client: Client, callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Set Wallet", callback_data="set_wallet")],
        [InlineKeyboardButton("🚀 Request Withdrawal", callback_data="req_withdraw")],
        [InlineKeyboardButton("📜 Withdrawal History", callback_data="withdraw_history")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ])
    
    await callback.edit_message_text(
        f"💳 **Withdrawal Menu**\n\n"
        f"⚖️ **Minimum:** `{MIN_WITHDRAW:.6f} LTC`\n"
        f"💰 **Your Balance:** `{user['balance']:.6f} LTC`\n"
        f"🔐 **Wallet:** `{user['wallet'] or 'Not set'}`",
        reply_markup=keyboard
    )

@bot.on_callback_query(filters.regex("^set_wallet$"))
async def set_wallet(client: Client, callback: CallbackQuery):
    await callback.edit_message_text(
        "🔐 **Set Your Litecoin Wallet:**\n\n"
        "Please send your Litecoin wallet address:"
    )
    # Store state for next message
    # (In production, use a proper state management system)

@bot.on_message(filters.text & filters.private & ~filters.command)
async def handle_wallet_input(client: Client, message: Message):
    # Simplified wallet handler (in production, add validation)
    wallet = message.text.strip()
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET wallet = ? WHERE user_id = ?", 
                 (wallet, message.from_user.id))
        conn.commit()
    
    await message.reply_text(f"✅ Wallet set to: `{wallet}`")

@bot.on_callback_query(filters.regex("^req_withdraw$"))
async def request_withdrawal(client: Client, callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    
    if not user['wallet']:
        await callback.answer("Please set your wallet first!", show_alert=True)
        return
    
    if user['balance'] < MIN_WITHDRAW:
        await callback.answer(
            f"Minimum withdrawal is {MIN_WITHDRAW:.6f} LTC!", 
            show_alert=True
        )
        return
    
    tx_id = add_transaction(
        user_id=user['user_id'],
        amount=user['balance'],
        tx_type='withdraw',
        address=user['wallet']
    )
    
    # Reset balance
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance = 0 WHERE user_id = ?", (user['user_id'],))
        conn.commit()
    
    await callback.edit_message_text(
        "📬 **Withdrawal Request Sent!**\n\n"
        "Your withdrawal is pending admin approval. "
        "You'll be notified when processed."
    )
    
    # Notify admin
    await bot.send_message(
        ADMIN_ID,
        f"⚠️ **New Withdrawal Request**\n\n"
        f"👤 User: {callback.from_user.mention}\n"
        f"💳 Wallet: `{user['wallet']}`\n"
        f"💎 Amount: `{user['balance']:.6f} LTC`\n"
        f"📋 TX ID: `{tx_id}`"
    )

# Admin commands
@bot.on_message(filters.command("admin") & filters.user(ADMIN_ID))
async def admin_panel(client: Client, message: Message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📝 Create Task", callback_data="admin_create_task")],
        [InlineKeyboardButton("📋 Withdrawals", callback_data="admin_withdrawals")]
    ])
    
    await message.reply_text("🔧 **Admin Panel**", reply_markup=keyboard)

@bot.on_callback_query(filters.regex("^admin_withdrawals$") & filters.user(ADMIN_ID))
async def show_withdrawals(client: Client, callback: CallbackQuery):
    withdrawals = get_pending_withdrawals()
    
    if not withdrawals:
        await callback.answer("No pending withdrawals", show_alert=True)
        return
    
    # Show first withdrawal for simplicity
    wd = withdrawals[0]
    user = get_user(wd['user_id'])
    
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"wd_approve_{wd['tx_id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"wd_reject_{wd['tx_id']}")
        ],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]
    ])
    
    await callback.edit_message_text(
        f"📋 **Withdrawal Request**\n\n"
        f"👤 User: {user['user_id']}\n"
        f"💳 Wallet: `{wd['address']}`\n"
        f"💎 Amount: `{wd['amount']:.6f} LTC`"
    )

@bot.on_callback_query(filters.regex("^wd_") & filters.user(ADMIN_ID))
async def handle_withdrawal_action(client: Client, callback: CallbackQuery):
    action, tx_id = callback.data.split("_")[1:]
    tx_id = int(tx_id)
    
    if action == "approve":
        update_withdrawal(tx_id, "approved")
        await callback.answer("Withdrawal approved!", show_alert=True)
    elif action == "reject":
        update_withdrawal(tx_id, "rejected")
        # Return funds to user
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute("SELECT user_id, amount FROM transactions WHERE tx_id = ?", (tx_id,))
            tx = c.fetchone()
            if tx:
                c.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?",
                         (tx[1], tx[0]))
                conn.commit()
        await callback.answer("Withdrawal rejected!", show_alert=True)
    
    await callback.edit_message_text("✅ Action completed")

# Flask Admin Panel
@app.route('/admin', methods=['GET'])
def admin_dashboard():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        users_count = c.fetchone()[0]
        
        c.execute("SELECT SUM(balance) FROM users")
        total_balance = c.fetchone()[0] or 0
        
        c.execute("SELECT COUNT(*) FROM transactions WHERE tx_type = 'withdraw' AND status = 'pending'")
        pending_wd = c.fetchone()[0]
    
    return render_template(
        'admin_dashboard.html',
        users_count=users_count,
        total_balance=total_balance,
        pending_wd=pending_wd
    )

@app.route('/admin/tasks', methods=['GET', 'POST'])
def manage_tasks():
    if request.method == 'POST':
        task_type = request.form.get('task_type')
        reward = float(request.form.get('reward'))
        content = request.form.get('content')
        duration = int(request.form.get('duration', 0))
        
        create_task(task_type, reward, content, duration)
        return redirect(url_for('manage_tasks'))
    
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM tasks")
        tasks = c.fetchall()
    
    return render_template('admin_tasks.html', tasks=tasks)

@app.route('/admin/withdrawals', methods=['GET'])
def manage_withdrawals():
    with sqlite3.connect(DB_NAME) as conn:
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''SELECT t.*, u.user_id 
                  FROM transactions t 
                  JOIN users u ON t.user_id = u.user_id
                  WHERE t.tx_type = 'withdraw' AND t.status = 'pending' ''')
        withdrawals = c.fetchall()
    
    return render_template('admin_withdrawals.html', withdrawals=withdrawals)

@app.route('/admin/withdrawals/<action>/<tx_id>')
def handle_withdrawal(action, tx_id):
    update_withdrawal(tx_id, action)
    return redirect(url_for('manage_withdrawals'))

# Start applications
def run_flask():
    app.run(host='0.0.0.0', port=5000, use_reloader=False)

if __name__ == "__main__":
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start the bot
    print("Starting Telegram bot...")
    bot.run()
