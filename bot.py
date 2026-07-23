# Last updated: 2026-07-10 17:13
import os
import csv
import re
import aiohttp
import openpyxl
import random
import string
import asyncio
import logging
from datetime import datetime, timedelta

import motor.motor_asyncio
import pyotp
from faker import Faker
from dotenv import load_dotenv
import urllib.parse

from aiogram import Bot, Dispatcher, Router, F, BaseMiddleware
from aiogram.enums import ContentType
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton,
    TelegramObject
)
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from typing import Callable, Dict, Any, Awaitable
from io import BytesIO

async def get_system_date(task_type=None):
    if task_type:
        specific_date = await get_setting(f'manual_date_override_{task_type}')
        if specific_date:
            return specific_date
            
    manual_date = await get_setting('manual_date_override')
    if manual_date:
        return manual_date
    return (datetime.utcnow() + timedelta(hours=6)).strftime("%Y-%m-%d")

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "your_bot_token_here")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8375006707"))
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/+CeAs07N2gGM4MTY1")
CHANNEL_ID_STR = os.getenv("CHANNEL_ID", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "goTaskpaybot")
BOT_NAME = os.getenv("BOT_NAME", "GoTask Pay")
MONGO_URI = os.getenv("MONGO_URI", "")
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = mongo_client['gotaskpay']

CHANNEL_ID = None

class AntiSpamMiddleware(BaseMiddleware):
    def __init__(self, limit: float = 1.0):
        self.limit = limit
        self.users = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        if hasattr(event, "from_user") and event.from_user:
            user_id = event.from_user.id
            
        if user_id:
            now = asyncio.get_event_loop().time()
            last_time = self.users.get(user_id, 0)
            if now - last_time < self.limit:
                return # Drop request
            self.users[user_id] = now
            
        return await handler(event, data)

import time

class SessionTimeoutMiddleware(BaseMiddleware):
    def __init__(self, timeout_seconds: int = 3600):
        self.timeout_seconds = timeout_seconds
        self.last_activity = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user_id = None
        state = data.get('state')
        
        if hasattr(event, "from_user") and event.from_user:
            user_id = event.from_user.id
            
        # Ignore for /start and restart_bot
        if isinstance(event, Message) and event.text and event.text.startswith('/start'):
            if user_id: self.last_activity[user_id] = time.time()
            return await handler(event, data)
            
        if isinstance(event, CallbackQuery) and event.data == "restart_bot":
            if user_id: self.last_activity[user_id] = time.time()
            return await handler(event, data)
            
        if user_id:
            now = time.time()
            last_time = self.last_activity.get(user_id)
            
            if last_time and (now - last_time > self.timeout_seconds):
                self.last_activity[user_id] = now
                if state: await state.clear()
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔄 Restart", callback_data="restart_bot", style="primary")]
                ])
                text = "⏳ **Session Expired!**\n\nYou were inactive for 1 hour. Please click the button below to restart the bot."
                
                try:
                    if isinstance(event, Message):
                        await event.answer(text, reply_markup=kb, parse_mode="Markdown")
                    elif isinstance(event, CallbackQuery):
                        await event.message.answer(text, reply_markup=kb, parse_mode="Markdown")
                        await event.answer()
                except:
                    pass
                return # Block request
                
            self.last_activity[user_id] = now
            
        return await handler(event, data)


try:
    if CHANNEL_ID_STR:
        CHANNEL_ID = int(CHANNEL_ID_STR)
except ValueError:
    pass

fake = Faker()
router = Router()
ADMIN_PERMISSIONS = {}
MAIN_MENU_BUTTONS = {
    "Cancel ❌", "Back 🔙", "Back to Main Menu 🔙", "❌ বাতিল",
    "Admin Panel ⚙️", "Balance 💰", "কাজ 💼", "Leaderboard 🏆", 
    "Contact Support 📞", "Withdraw 💳", "My History 📜", "Work 💼"
}

# Premium Emoji Constants
E_INFO = "<tg-emoji emoji-id='4956475826762679249'>💬</tg-emoji>"
E_CHECK = "<tg-emoji emoji-id='4956721670690702265'>✅</tg-emoji>"
E_FIRE = "<tg-emoji emoji-id='4956222745814762495'>🔥</tg-emoji>"
E_WARN = "<tg-emoji emoji-id='4956611513369494230'>⚠️</tg-emoji>"
E_CASH = "<tg-emoji emoji-id='4956601935592424315'>💵</tg-emoji>"
E_TARGET = "<tg-emoji emoji-id='4958506272551863292'>📊</tg-emoji>"
E_USER = "<tg-emoji emoji-id='4958479549265347295'>👤</tg-emoji>"
E_LOCK = "<tg-emoji emoji-id='4958926882994127612'>🔐</tg-emoji>"
E_BULL = "<tg-emoji emoji-id='4956475826762679249'>📢</tg-emoji>"
E_DOWN = "<tg-emoji emoji-id='4958479549265347295'>⬇️</tg-emoji>"

# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------
async def init_db():
    default_settings = {
        'task_price': '10',
        'password_mode': 'random', 
        'fixed_password': 'GoTaskPassword123',
        'submit_deadline': '23:59',
        'min_withdraw_amount': '50',
        'withdraw_status': 'on',
        'refer_percentage': '10',
        'tutorial_video_id': '',
        'tutorial_message': 'How to work tutorial:',
        'tutorial_fb': 'Facebook Tutorial',
        'tutorial_insta': 'Instagram Tutorial',
        'channel_id': '',
        'channel_link': 'https://t.me/your_channel',
        'auto_export_time': '20:00',
        'auto_export_time_fb_2fa': '21:00',
        'auto_export_time_fb_cookie': '22:00',
        'late_export_time': '23:55',
        'late_export_status': 'on',
        'fb_2fa_price': '10',
        'fb_cookie_price': '10'
    }
    
    existing_start = await db.settings.find_one({'key': 'start_message'})
    if not existing_start:
        await db.settings.insert_one({'key': 'start_message', 'value': f'<tg-emoji emoji-id="6109486907207455487">🔥</tg-emoji> Welcome to {BOT_NAME}, <b>{{name}}</b>!\\n\\nঘরে বসে প্রতিদিন ইনকাম করার সবচেয়ে বিশ্বস্ত প্ল্যাটফর্মে আপনাকে স্বাগতম। \\n<tg-emoji emoji-id="6109271132345471995">✅</tg-emoji> আনলিমিটেড কাজ\\n<tg-emoji emoji-id="6109640851720245755">⚡</tg-emoji> ফাস্ট পেমেন্ট\\n<tg-emoji emoji-id="6109394509576015826">🎁</tg-emoji> রেফার করে এক্সট্রা ইনকাম\\n\\nভিডিও দেখে কাজ শিখতে "কীভাবে কাজ করব ❓" এ ক্লিক করুন। আপনার যাত্রা শুভ হোক! <tg-emoji emoji-id="6109572166603247368">📣</tg-emoji>'})

    # Create Indexes for performance
    await db.users.create_index('tg_id', unique=True)
    await db.users.create_index('referred_by')
    await db.tasks.create_index([('tg_id', 1), ('status', 1)])
    await db.tasks.create_index('status')
    await db.tasks.create_index('date')
    await db.tasks.create_index('name')
    await db.settings.create_index('key', unique=True)
    await db.withdraws.create_index([('tg_id', 1), ('status', 1)])
    await db.withdraws.create_index('status')

    for key, value in default_settings.items():
        existing = await db.settings.find_one({'key': key})
        if not existing:
            await db.settings.insert_one({'key': key, 'value': value})
            
    # Load admins into cache
    cursor = db.admins.find()
    async for admin in cursor:
        ADMIN_PERMISSIONS[str(admin['tg_id'])] = admin.get('permissions', [])
SETTINGS_CACHE = {}

async def get_setting(key):
    if key in SETTINGS_CACHE:
        return SETTINGS_CACHE[key]
    doc = await db.settings.find_one({'key': key})
    if doc:
        SETTINGS_CACHE[key] = doc['value']
        return doc['value']
    return None

async def update_setting(key, value):
    await db.settings.update_one({'key': key}, {'$set': {'value': str(value)}}, upsert=True)
    SETTINGS_CACHE[key] = str(value)

async def add_user(tg_id, referred_by=None):
    from datetime import datetime
    existing = await db.users.find_one({'tg_id': tg_id})
    if existing: return False
    await db.users.insert_one({
        'tg_id': tg_id,
        'balance': 0,
        'referred_by': referred_by,
        'join_date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'referral_earnings': 0,
        'lang': 'bn'
    })
    return True

async def get_user(tg_id):
    doc = await db.users.find_one({'tg_id': tg_id})
    if doc:
        return (str(doc.get('_id')), doc.get('tg_id'), doc.get('balance', 0), doc.get('referred_by'), doc.get('join_date'), doc.get('referral_earnings', 0))
    return None

async def get_all_users():
    cursor = db.users.find({}, {'tg_id': 1})
    users = []
    async for doc in cursor:
        users.append(doc['tg_id'])
    return users

async def update_balance(tg_id, amount):
    await db.users.update_one({'tg_id': tg_id}, {'$inc': {'balance': amount}})

async def add_task(tg_id, name, password, two_fa_key, two_fa_code, task_type='insta_2fa'):
    date_str = await get_system_date(task_type)
    await db.tasks.insert_one({
        'tg_id': tg_id, 'name': name, 'password': password, 
        'two_fa_key': two_fa_key, 'two_fa_code': two_fa_code, 
        'date': date_str, 'status': 'pending', 'task_type': task_type
    })

async def get_pending_tasks_count(tg_id):
    return await db.tasks.count_documents({'tg_id': tg_id, 'status': 'pending'})

async def get_completed_tasks_count(tg_id):
    return await db.tasks.count_documents({'tg_id': tg_id, 'status': 'accepted'})

async def export_tasks_xlsx(date_str, task_type='insta_2fa'):
    if task_type == 'insta_2fa':
        file_path = f'tasks_insta_{date_str}.xlsx'
        headers = ['Username', 'Password', '2FA Key']
    elif task_type == 'fb_2fa':
        file_path = f'tasks_fb_2fa_{date_str}.xlsx'
        headers = ['UID', 'Password', '2FA Key']
    else:
        file_path = f'tasks_fb_cookies_{date_str}.xlsx'
        headers = ['UID', 'Password', 'Cookies']
    
    # Build query - if date_str is 'all', export all dates
    query = {'task_type': task_type}
    if date_str != 'all':
        query['date'] = date_str
        
    cursor = db.tasks.find(query)
    rows = []
    async for doc in cursor:
        rows.append([doc.get('name'), doc.get('password'), doc.get('two_fa_key')])
        
    if not rows:
        return None, 0
        
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(file_path)
    return file_path, len(rows)

async def export_unexported_tasks_xlsx(task_type='insta_2fa'):
    """Export all tasks that haven't been auto-exported yet (exported != true)"""
    now = datetime.utcnow() + timedelta(hours=6)
    date_str = now.strftime("%Y-%m-%d")
    
    if task_type == 'insta_2fa':
        file_path = f'tasks_insta_new_{date_str}.xlsx'
        headers = ['Username', 'Password', '2FA Key']
    elif task_type == 'fb_2fa':
        file_path = f'tasks_fb_2fa_new_{date_str}.xlsx'
        headers = ['UID', 'Password', '2FA Key']
    else:
        file_path = f'tasks_fb_cookies_new_{date_str}.xlsx'
        headers = ['UID', 'Password', 'Cookies']
    
    # Find all tasks that haven't been exported yet
    query = {'task_type': task_type, 'exported': {'$ne': True}}
    
    cursor = db.tasks.find(query)
    rows = []
    task_ids = []
    async for doc in cursor:
        rows.append([doc.get('name'), doc.get('password'), doc.get('two_fa_key')])
        task_ids.append(doc['_id'])
        
    if not rows:
        return None, 0, []
        
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(file_path)
    return file_path, len(rows), task_ids

async def mark_tasks_exported(task_ids):
    """Mark tasks as exported after successful auto export"""
    if task_ids:
        await db.tasks.update_many(
            {'_id': {'$in': task_ids}},
            {'$set': {'exported': True}}
        )


async def verify_and_credit_accounts(valid_usernames_list, bot: Bot):
    accepted_count = 0
    
    try:
        insta_price = float(await get_setting('task_price'))
    except:
        insta_price = 3.15
    try:
        fb_2fa_price = float(await get_setting('fb_2fa_price'))
    except:
        fb_2fa_price = 3.50
    try:
        fb_cookie_price = float(await get_setting('fb_cookie_price'))
    except:
        fb_cookie_price = 4.50
        
    try:
        ref_percent = float(await get_setting('refer_percentage'))
    except:
        ref_percent = 0
        
    user_earnings = {}
    user_approved_accounts = {}
    
    referrer_earnings = {}
    referrer_tasks = {}
    
    for username in valid_usernames_list:
        # নতুন রিপোর্ট হিসেবে — status যাই থাকুক সবাইকে process করবে
        doc = await db.tasks.find_one({'name': username})
        if doc:
            task_type = doc.get('task_type', 'insta_2fa')
            if task_type == 'fb_2fa':
                price = fb_2fa_price
            elif task_type == 'fb_cookie':
                price = fb_cookie_price
            else:
                price = insta_price
                
            ref_reward = price * (ref_percent / 100)
            
            task_id, tg_id = doc['_id'], doc['tg_id']
            prev_status = doc.get('status', 'pending')
            await db.tasks.update_one({'_id': task_id}, {'$set': {'status': 'accepted'}})
            
            # Balance শুধু তখনই যোগ হবে যদি আগে accepted না থাকে (duplicate credit নেই)
            if prev_status != 'accepted':
                await db.users.update_one({'tg_id': tg_id}, {'$inc': {'balance': price}})
                user_earnings[tg_id] = user_earnings.get(tg_id, 0) + price
            
            if tg_id not in user_approved_accounts:
                user_approved_accounts[tg_id] = []
            user_approved_accounts[tg_id].append(username)
            
            # Referral bonus — শুধু প্রথমবার (pending থেকে) accept হলে দেব
            if prev_status == 'pending':
                user = await db.users.find_one({'tg_id': tg_id})
                if user and user.get('referred_by'):
                    ref_id = user['referred_by']
                    refer_income_status = await get_setting('refer_income_status') or 'on'
                    if refer_income_status == 'on' and ref_reward > 0:
                        await db.users.update_one({'tg_id': ref_id}, {'$inc': {'balance': ref_reward, 'referral_earnings': ref_reward}})
                        referrer_earnings[ref_id] = referrer_earnings.get(ref_id, 0) + ref_reward
                        referrer_tasks[ref_id] = referrer_tasks.get(ref_id, 0) + 1
            accepted_count += 1
        
    for u_tg_id, amount in user_earnings.items():
        try:
            accounts = user_approved_accounts[u_tg_id]
            acc_list_str = "\n".join([f"✅ `{acc}`" for acc in accounts])
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="আরও কাজ করুন 💼", callback_data="start_task_submenu", style="primary")]])
            
            text = (
                f"🎉 **অভিনন্দন! আপনার কাজগুলো অ্যাপ্রুভ হয়েছে।**\n\n"
                f"📝 **অ্যাপ্রুভড অ্যাকাউন্টসমূহ ({len(accounts)} টি):**\n"
                f"{acc_list_str}\n\n"
                f"💰 **আপনার ব্যালেন্সে যোগ হয়েছে:** `{amount:.2f} TK`"
            )
            await bot.send_message(chat_id=u_tg_id, text=text, reply_markup=kb, parse_mode="Markdown")
        except: pass

    for ref_id, amount in referrer_earnings.items():
        try:
            total_refs = await db.users.count_documents({'referred_by': ref_id})
            referrer_user = await db.users.find_one({'tg_id': ref_id})
            total_ref_earnings = referrer_user.get('referral_earnings', 0) if referrer_user else 0
            
            task_count = referrer_tasks[ref_id]
            
            text = (
                f"🎉 **রেফারেল বোনাস প্রাপ্তি!**\n\n"
                f"আপনার রেফার করা ইউজাররা কাজ সম্পন্ন করেছে! এর ফলে আপনি বোনাস পেয়েছেন:\n"
                f"🔹 **নতুন বোনাস:** `{amount:.2f} TK`\n"
                f"🔹 **কাজ কমপ্লিট করেছে:** `{task_count}` টি\n\n"
                f"📊 **আপনার সর্বমোট রেফারেল স্ট্যাটাস:**\n"
                f"👥 মোট রেফার করেছেন: `{total_refs}` জন\n"
                f"💰 সর্বমোট রেফার ইনকাম: `{total_ref_earnings:.2f} TK`\n\n"
                f"আরও বেশি ইনকাম করতে বন্ধুদের সাথে আপনার লিংক শেয়ার করুন 👇"
            )
            await bot.send_message(chat_id=ref_id, text=text, reply_markup=get_share_and_earn_inline(BOT_USERNAME, ref_id), parse_mode="Markdown")
        except: pass
        
    return accepted_count

async def reject_accounts(bad_usernames_list, bot: Bot):
    rejected_count = 0
    user_rejected_accounts = {}
    
    try:
        insta_price = float(await get_setting('task_price'))
    except:
        insta_price = 3.15
    try:
        fb_2fa_price = float(await get_setting('fb_2fa_price'))
    except:
        fb_2fa_price = 3.50
    try:
        fb_cookie_price = float(await get_setting('fb_cookie_price'))
    except:
        fb_cookie_price = 4.50
    
    for username in bad_usernames_list:
        # নতুন রিপোর্ট হিসেবে — status যাই থাকুক সবাইকে reject করবে
        doc = await db.tasks.find_one({'name': username})
        if doc:
            task_type = doc.get('task_type', 'insta_2fa')
            if task_type == 'fb_2fa':
                price = fb_2fa_price
            elif task_type == 'fb_cookie':
                price = fb_cookie_price
            else:
                price = insta_price
                
            task_id, tg_id = doc['_id'], doc['tg_id']
            prev_status = doc.get('status', 'pending')
            await db.tasks.update_one({'_id': task_id}, {'$set': {'status': 'rejected'}})
            
            # যদি আগে accepted ছিল (balance দেওয়া হয়েছিল), তাহলে balance কেটে নাও
            if prev_status == 'accepted':
                await db.users.update_one({'tg_id': tg_id}, {'$inc': {'balance': -price}})
            
            if prev_status != 'rejected':
                if tg_id not in user_rejected_accounts:
                    user_rejected_accounts[tg_id] = []
                user_rejected_accounts[tg_id].append(username)
                
            rejected_count += 1
            
    for u_tg_id, accounts in user_rejected_accounts.items():
        try:
            acc_list_str = "\n".join([f"❌ `{acc}`" for acc in accounts])
            text = (
                f"⚠️ **অ্যাকাউন্ট বাতিল!**\n\n"
                f"আপনার জমাকৃত নিচের অ্যাকাউন্টগুলো চেক করার সময় ভুল/নষ্ট পাওয়া গেছে, তাই এগুলো বাতিল করা হয়েছে:\n\n"
                f"{acc_list_str}"
            )
            await bot.send_message(chat_id=u_tg_id, text=text, parse_mode="Markdown")
        except: pass
            
    return rejected_count

async def reject_all_pending_accounts(bot: Bot):
    rejected_count = 0
    user_rejected_accounts = {}
    
    cursor = db.tasks.find({'status': 'pending'})
    async for doc in cursor:
        task_id, tg_id, username = doc['_id'], doc.get('tg_id'), doc.get('name')
        await db.tasks.update_one({'_id': task_id}, {'$set': {'status': 'rejected'}})
        
        if tg_id and username:
            if tg_id not in user_rejected_accounts:
                user_rejected_accounts[tg_id] = []
            user_rejected_accounts[tg_id].append(username)
            rejected_count += 1
            
    for u_tg_id, accounts in user_rejected_accounts.items():
        try:
            acc_list_str = "\n".join([f"❌ `{acc}`" for acc in accounts])
            text = (
                f"⚠️ **অ্যাকাউন্ট বাতিল!**\n\n"
                f"আপনার জমাকৃত নিচের অ্যাকাউন্টগুলো চেক করার সময় ভুল/নষ্ট পাওয়া গেছে, তাই এগুলো বাতিল করা হয়েছে:\n\n"
                f"{acc_list_str}"
            )
            await bot.send_message(chat_id=u_tg_id, text=text, parse_mode="Markdown")
        except: pass
            
    return rejected_count

async def add_withdraw_request(tg_id, method, details, requested_amount, fee=5):
    receive_amount = requested_amount - fee
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = await db.withdraws.insert_one({
        'tg_id': tg_id, 'method': method, 'details': details, 
        'amount': receive_amount, 'status': 'pending', 'date': date_str,
        'requested_amount': requested_amount, 'fee': fee
    })
    await db.users.update_one({'tg_id': tg_id}, {'$inc': {'balance': -requested_amount}})
    return str(result.inserted_id)

async def get_withdraw_request(req_id):
    from bson.objectid import ObjectId
    try:
        doc = await db.withdraws.find_one({'_id': ObjectId(req_id)})
        if doc:
            return (str(doc['_id']), doc['tg_id'], doc['method'], doc['details'], doc['amount'], doc['status'], doc['date'])
    except: pass
    return None

async def update_withdraw_status(req_id, status):
    from bson.objectid import ObjectId
    try:
        await db.withdraws.update_one({'_id': ObjectId(req_id)}, {'$set': {'status': status}})
    except: pass

async def get_pending_withdraw_amount(tg_id):
    cursor = db.withdraws.find({'tg_id': tg_id, 'status': 'pending'})
    total = 0
    async for doc in cursor:
        total += doc.get('amount', 0)
    return total

async def get_successful_withdraw_amount(tg_id):
    cursor = db.withdraws.find({'tg_id': tg_id, 'status': 'accepted'})
    total = 0
    async for doc in cursor:
        total += doc.get('amount', 0)
    return total

# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def generate_hard_password(day):
    symbols = "@#$%"
    random_chars = [
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice(string.digits),
        random.choice(symbols),
        random.choice(symbols)
    ]
    random.shuffle(random_chars)
    return "GoTask" + "".join(random_chars) + f"%{day}"

def generate_username():
    name = fake.name()
    username_base = name.lower().replace(' ', '_').replace('.', '')
    return f"{username_base}{random.randint(10, 999)}"

def generate_2fa_code(key):
    try:
        clean_key = key.replace(" ", "").upper()
        totp = pyotp.TOTP(clean_key)
        return totp.now()
    except Exception:
        return None

LANG_CACHE = {}

async def get_user_lang(user_id):
    if user_id in LANG_CACHE:
        return LANG_CACHE[user_id]
    user = await db.users.find_one({'tg_id': user_id})
    lang = user.get('lang', 'bn') if user else 'bn'
    LANG_CACHE[user_id] = lang
    return lang

def is_admin(user_id, permission=None):
    user_id_str = str(user_id)
    if user_id_str == str(ADMIN_ID):
        return True
    
    if user_id_str not in ADMIN_PERMISSIONS:
        return False
        
    if permission is None:
        return True
        
    user_perms = ADMIN_PERMISSIONS.get(user_id_str, [])
    if 'full_control' in user_perms:
        return True
        
    return permission in user_perms

MEMBERSHIP_CACHE = {}

async def check_channel_membership(bot: Bot, user_id: int):
    if is_admin(user_id):
        return True
        
    import time
    now = time.time()
    # If checked in the last 5 minutes and was a member, return True
    if user_id in MEMBERSHIP_CACHE and MEMBERSHIP_CACHE[user_id] > now:
        return True
    
    channel_id = await get_setting('channel_id')
    import os
    if not channel_id and os.getenv('CHANNEL_ID'):
        channel_id = os.getenv('CHANNEL_ID')
        
    main_joined = True
    if channel_id:
        try:
            member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator', 'Member', 'Administrator', 'Creator']:
                main_joined = False
        except Exception as e:
            print(f"Error checking main channel membership: {e}")
            main_joined = True
            
    payout_id = await get_setting('payout_group_id')
    payout_joined = True
    if payout_id:
        try:
            member = await bot.get_chat_member(chat_id=payout_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator', 'Member', 'Administrator', 'Creator']:
                payout_joined = False
        except Exception as e:
            print(f"Error checking payout group membership: {e}")
            payout_joined = True
            
    result = main_joined and payout_joined
    if result:
        MEMBERSHIP_CACHE[user_id] = now + 300 # cache for 5 minutes if joined
    else:
        MEMBERSHIP_CACHE[user_id] = now + 10 # cache for 10 seconds if not joined (prevents spamming API)
        
    return result

async def check_submit_deadline():
    deadline = await get_setting('submit_deadline')
    if not deadline: return True
    now = datetime.now()
    try:
        deadline_time = datetime.strptime(deadline, "%H:%M").time()
        if now.time() > deadline_time:
            return False
    except ValueError:
        pass
    return True

# ---------------------------------------------------------
# STATES
# ---------------------------------------------------------
class FlowStates(StatesGroup):
    waiting_for_task_platform = State()
    waiting_for_tutorial_platform = State()
    in_fb_menu = State()
    in_insta_menu = State()

class TaskStates(StatesGroup):
    waiting_for_2fa_key = State()
    waiting_for_submit = State()
    waiting_for_screenshot = State()

class FBTaskStates(StatesGroup):
    waiting_for_fb_uid_2fa = State()
    waiting_for_fb_uid_cookie = State()
    waiting_for_fb_2fa_key = State()
    waiting_for_fb_cookie = State()
    waiting_for_fb_submit = State()

class AdminManagementStates(StatesGroup):
    waiting_for_admin_id = State()
    waiting_for_admin_permissions = State()
    waiting_for_remove_admin_id = State()

class AdminBuyerStates(StatesGroup):
    waiting_for_buyer_id = State()
    waiting_for_remove_buyer_id = State()
    buyer_id_temp = State()

class AdminUserStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_balance_amount = State()

class AdminExportStates(StatesGroup):
    waiting_for_auto_export_insta = State()
    waiting_for_auto_export_fb_2fa = State()
    waiting_for_auto_export_fb_cookie = State()
    waiting_for_late_export_time = State()

class AdminOCRStates(StatesGroup):
    waiting_for_ocr_key = State()
    waiting_for_remove_ocr_key = State()

class AdminSettingsStates(StatesGroup):
    waiting_for_join_bonus_amount = State()
    waiting_for_refer_percent = State()
    waiting_for_tutorial_video = State()
    waiting_for_tutorial_msg = State()
    waiting_for_channel_id = State()
    waiting_for_channel_link = State()
    waiting_for_export_time = State()
    waiting_for_support_link = State()
    waiting_for_day_reset_time = State()
    waiting_for_manual_date = State()
    waiting_for_payout_channel = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast_msg = State()
    waiting_for_txt_file = State()
    waiting_for_withdraw_screenshot = State()
    waiting_for_new_price = State()
    waiting_for_start_msg = State()
    waiting_for_fixed_password = State()
    waiting_for_min_withdraw = State()
    waiting_for_binance_rate = State()

class AdminVerifyStates(StatesGroup):
    waiting_for_input = State()

class WithdrawStates(StatesGroup):
    waiting_for_method = State()
    waiting_for_details = State()
    waiting_for_amount = State()

# ---------------------------------------------------------
# KEYBOARDS
# ---------------------------------------------------------
def get_main_keyboard(admin_access=False, lang='bn'):
    if lang == 'en':
        kb = [
            [KeyboardButton(text="💰 Balance", style="primary"), KeyboardButton(text="📝 Task", style="success")],
            [KeyboardButton(text="💼 Withdraw", style="danger"), KeyboardButton(text="👨‍💻 Support", style="danger")],
            [KeyboardButton(text="👥 My Referral", style="primary"), KeyboardButton(text="❓ I am new", style="primary")],
            [KeyboardButton(text="🌐 Change Language", style="primary")]
        ]
    else:
        kb = [
            [KeyboardButton(text="💰 ব্যালেন্স", style="primary"), KeyboardButton(text="📝 কাজ", style="success")],
            [KeyboardButton(text="💼 উত্তোলন", style="danger"), KeyboardButton(text="👨‍💻 সাপোর্ট", style="danger")],
            [KeyboardButton(text="👥 আমার রেফারেল", style="primary"), KeyboardButton(text="❓ আমি নতুন", style="primary")],
            [KeyboardButton(text="🌐 ভাষা পরিবর্তন", style="primary")]
        ]
    if admin_access:
        kb.append([KeyboardButton(text="Admin Panel ⚙️", style="primary")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
    
def get_language_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇧🇩 বাংলা (Bangla)", callback_data="set_lang_bn", style="primary")],
        [InlineKeyboardButton(text="🇺🇸 English", callback_data="set_lang_en", style="primary")]
    ])

async def get_platform_keyboard():
    insta_status = await get_setting('task_status_insta_2fa') or 'on'
    fb_2fa_status = await get_setting('task_status_fb_2fa') or 'on'
    fb_cookie_status = await get_setting('task_status_fb_cookie') or 'on'
    
    if insta_status == 'off' and fb_2fa_status == 'off' and fb_cookie_status == 'off':
        return None
        
    kb = []
    if insta_status == 'on':
        kb.append([KeyboardButton(text="📸 Insta 2FA", style="primary")])
        
    fb_row = []
    if fb_2fa_status == 'on':
        fb_row.append(KeyboardButton(text="🌐 FB 2FA", style="primary"))
    if fb_cookie_status == 'on':
        fb_row.append(KeyboardButton(text="🍪 FB Cookie", style="primary"))
    
    if fb_row:
        kb.append(fb_row)
        
    kb.append([KeyboardButton(text="❌ বাতিল", style="danger")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_fb_tasks_keyboard(fb_2fa_price, fb_cookie_price):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=f"🌐 FB 2FA ({fb_2fa_price} BDT)", style="primary")],
        [KeyboardButton(text=f"🌐 Fb Cookies ({fb_cookie_price} BDT)", style="primary")],
        [KeyboardButton(text="🎥 কীভাবে কাজ করব", style="primary")],
        [KeyboardButton(text="❌ বাতিল", style="danger")]
    ], resize_keyboard=True)

def get_insta_tasks_keyboard(insta_price):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=f"📸 Insta 2FA ({insta_price} BDT)", style="primary")],
        [KeyboardButton(text="🎥 কীভাবে কাজ করব", style="primary")],
        [KeyboardButton(text="❌ বাতিল", style="danger")]
    ], resize_keyboard=True)

def get_admin_tutorial_platform():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Facebook Tutorial", callback_data="set_tut_fb", style="primary")],
        [InlineKeyboardButton(text="Instagram Tutorial", callback_data="set_tut_insta", style="primary")]
    ])



def get_task_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Done ✅", style="success")], [KeyboardButton(text="Cancel ❌", style="danger")]], resize_keyboard=True)

def get_submit_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Complete and Submit 📥", style="success")], [KeyboardButton(text="Cancel ❌", style="danger")]], resize_keyboard=True)

def get_submit_uid_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Submit UID 🆔", style="primary")], [KeyboardButton(text="Cancel ❌", style="danger")]], resize_keyboard=True)

def get_permissions_keyboard(current_perms):
    perms_map = {
        'stats': 'Dashboard / Stats',
        'export': 'Export Data',
        'verify': 'Verify & Withdraw',
        'broadcast': 'Broadcast',
        'settings': 'Settings',
        'full_control': 'Full Control (Primary)'
    }
    buttons = []
    for k, v in perms_map.items():
        check = "✅ " if k in current_perms else ""
        buttons.append([InlineKeyboardButton(text=f"{check}{v}", callback_data=f"toggle_perm_{k}", style="primary")])
    buttons.append([InlineKeyboardButton(text="Save 💾", callback_data="save_admin_perms", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_buyer_file_types_keyboard(current_types):
    types_map = {
        'insta_2fa': 'Insta 2FA',
        'fb_2fa': 'FB 2FA',
        'fb_cookie': 'FB Cookie'
    }
    buttons = []
    for k, v in types_map.items():
        check = "✅ " if k in current_types else "❌ "
        buttons.append([InlineKeyboardButton(text=f"{check}{v}", callback_data=f"toggle_buyer_type_{k}", style="primary")])
    buttons.append([InlineKeyboardButton(text="Save & Confirm 💾", callback_data="save_buyer", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_task_submenu_keyboard(insta_price, fb_2fa_price, fb_cookie_price):
    st_insta = await get_setting('task_status_insta_2fa') or 'on'
    st_fb2fa = await get_setting('task_status_fb_2fa') or 'on'
    st_fbc = await get_setting('task_status_fb_cookie') or 'on'

    kb = []
    if st_insta == 'on':
        kb.append([KeyboardButton(text=f"📸 Insta 2FA ({insta_price} BDT)", style="primary")])
    if st_fb2fa == 'on':
        kb.append([KeyboardButton(text=f"🌐 FB 2FA ({fb_2fa_price} BDT)", style="primary")])
    if st_fbc == 'on':
        kb.append([KeyboardButton(text=f"🌐 Fb Cookies ({fb_cookie_price} BDT)", style="primary")])
        
    kb.append([KeyboardButton(text="🎥 কীভাবে কাজ করব", style="primary")])
    kb.append([KeyboardButton(text="❌ বাতিল", style="danger")])
    
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Cancel ❌", style="danger")]], resize_keyboard=True)

def get_refresh_inline():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh Data 🟢", callback_data="refresh_task_data", style="success")]])

async def get_force_join_keyboard(bot: Bot = None):
    kb = []
    
    link = await get_setting('channel_link')
    if not link: link = "https://t.me/your_channel"
    kb.append([InlineKeyboardButton(text="Join Channel 📢", url=link, style="primary")])
    
    if bot:
        payout_id = await get_setting('payout_group_id')
        if payout_id:
            try:
                chat = await bot.get_chat(payout_id)
                if chat.username:
                    payout_link = f"https://t.me/{chat.username}"
                else:
                    payout_link = await bot.export_chat_invite_link(payout_id)
                kb.append([InlineKeyboardButton(text="Payment Proof Group 💰", url=payout_link, style="primary")])
            except:
                pass
                
    kb.append([InlineKeyboardButton(text="Joined ✅", callback_data="check_joined", style="success")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_withdraw_inline():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Withdrawal 💳", callback_data="start_withdraw", style="primary")]])

async def get_withdraw_methods():
    bkash_status = await get_setting('pm_bkash') or 'on'
    nagad_status = await get_setting('pm_nagad') or 'on'
    rocket_status = await get_setting('pm_rocket') or 'on'
    binance_status = await get_setting('pm_binance') or 'on'
    
    methods = []
    row1 = []
    if bkash_status == 'on':
        row1.append(InlineKeyboardButton(text="bKash 🟢", callback_data="wd_bKash", style="primary"))
    if nagad_status == 'on':
        row1.append(InlineKeyboardButton(text="Nagad 🟠", callback_data="wd_Nagad", style="primary"))
    if row1: methods.append(row1)
        
    row2 = []
    if rocket_status == 'on':
        row2.append(InlineKeyboardButton(text="Rocket 🚀", callback_data="wd_Rocket", style="primary"))
    if binance_status == 'on':
        row2.append(InlineKeyboardButton(text="Binance 🟨", callback_data="wd_Binance", style="primary"))
    if row2: methods.append(row2)
        
    methods.append([InlineKeyboardButton(text="Cancel ❌", callback_data="cancel_withdraw", style="danger")])
    return InlineKeyboardMarkup(inline_keyboard=methods)

def get_admin_panel_keyboard(user_id):
    kb = []
    row1 = []
    if is_admin(user_id, 'export'):
        row1.append(KeyboardButton(text="Export Data 📁", style="primary"))
    if is_admin(user_id, 'verify'):
        row1.append(KeyboardButton(text="Verify Accounts ✅", style="success"))
    if row1: kb.append(row1)
        
    row2 = []
    if is_admin(user_id, 'broadcast'):
        row2.append(KeyboardButton(text="Broadcast 📢", style="primary"))
    if is_admin(user_id, 'settings'):
        row2.append(KeyboardButton(text="Settings ⚙️", style="primary"))
    if row2: kb.append(row2)
        
    row_lb = []
    if is_admin(user_id, 'stats'):
        row_lb.append(KeyboardButton(text="Leaderboard 🏆", style="primary"))
    if row_lb: kb.append(row_lb)
        
    row_wd = []
    if is_admin(user_id, 'full_control') or str(user_id) == str(ADMIN_ID):
        row_wd.append(KeyboardButton(text="Pending Withdraws 💳", style="primary"))
    if row_wd: kb.append(row_wd)
        
    row3 = []
    if is_admin(user_id, 'full_control'):
        row3.append(KeyboardButton(text="Manage Admins 👥", style="primary"))
        row3.append(KeyboardButton(text="Manage Buyers 🛍️", style="primary"))
    if row3: kb.append(row3)
        
    row4 = []
    if is_admin(user_id, 'full_control'):
        row4.append(KeyboardButton(text="Manage Users 👤", style="primary"))
    if row4: kb.append(row4)
        
    if str(user_id) != str(ADMIN_ID):
        kb.append([KeyboardButton(text="Leave Admin 🚪", style="danger")])
        
    kb.append([KeyboardButton(text="Back to Main Menu 🔙", style="danger")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def get_tasks_toggle_inline():
    st_insta = await get_setting('task_status_insta_2fa') or 'on'
    st_fb2fa = await get_setting('task_status_fb_2fa') or 'on'
    st_fbc = await get_setting('task_status_fb_cookie') or 'on'
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Insta 2FA: {'🟢 ON' if st_insta == 'on' else '🔴 OFF'}", callback_data="toggle_tsk_insta_2fa", style="primary")],
        [InlineKeyboardButton(text=f"FB 2FA: {'🟢 ON' if st_fb2fa == 'on' else '🔴 OFF'}", callback_data="toggle_tsk_fb_2fa", style="primary")],
        [InlineKeyboardButton(text=f"FB Cookie: {'🟢 ON' if st_fbc == 'on' else '🔴 OFF'}", callback_data="toggle_tsk_fb_cookie", style="primary")]
    ])

def get_verify_action_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Approve (OK) ✅", callback_data="verify_action_approve", style="success")],
        [InlineKeyboardButton(text="Reject (BAD) ❌", callback_data="verify_action_reject", style="danger")],
        [InlineKeyboardButton(text="Both (CSV) 📊", callback_data="verify_action_both", style="primary")],
        [InlineKeyboardButton(text="Approve OK & Auto Reject Rest 🔄", callback_data="verify_action_auto", style="primary")]
    ])

def get_settings_keyboard():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Change Insta Price 💵", style="primary"), KeyboardButton(text="Change FB 2FA Price 💵", style="primary")],
        [KeyboardButton(text="Change FB Cookie Price 💵", style="primary"), KeyboardButton(text="Change Refer % 🎁", style="primary")],
        [KeyboardButton(text="Set Tutorial 🎥", style="primary"), KeyboardButton(text="Set Channel Link 📢", style="primary")],
        [KeyboardButton(text="Set Channel ID 🆔", style="primary"), KeyboardButton(text="Change Start Msg 📝", style="primary")],
        [KeyboardButton(text="Toggle Withdraw 💳", style="primary"), KeyboardButton(text="Change Min Withdraw 💰", style="primary")],
        [KeyboardButton(text="Toggle Payment Methods 💳", style="primary"), KeyboardButton(text="Toggle W/D Force Join 📢", style="primary")],
        [KeyboardButton(text="Set Password Mode 🔐", style="primary"), KeyboardButton(text="Change Binance Rate 💱", style="primary")],
        [KeyboardButton(text="Auto Export (Insta) 🕒", style="primary"), KeyboardButton(text="Auto Export (FB 2FA) 🕒", style="primary")],
        [KeyboardButton(text="Auto Export (Cookie) 🕒", style="primary"), KeyboardButton(text="Set Support Link 🎧", style="primary")],
        [KeyboardButton(text="Set Date 📅", style="primary"), KeyboardButton(text="Toggle Join Bonus 🎁", style="primary")],
        [KeyboardButton(text="Change Join Bonus 💰", style="primary"), KeyboardButton(text="Toggle Refer Income 👥", style="primary")],
        [KeyboardButton(text="Set Payout Group 📢", style="primary"), KeyboardButton(text="Toggle Tasks 📝", style="primary")],
        [KeyboardButton(text="Manage OCR API Keys 🔑", style="primary"), KeyboardButton(text="Toggle Insta Screenshot 📸", style="primary")],
        [KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)

def get_password_mode_inline():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Random (Hard)", callback_data="set_pw_random", style="primary")],
        [InlineKeyboardButton(text="Fixed", callback_data="set_pw_fixed", style="primary")]
    ])

def get_withdraw_approve_keyboard(req_id, user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Approve ✅", callback_data=f"wd_approve_{req_id}_{user_id}", style="success")],
        [InlineKeyboardButton(text="Reject ❌", callback_data=f"wd_reject_{req_id}_{user_id}", style="danger")]
    ])

def get_share_and_earn_inline(bot_user, tg_id):
    share_msg = "🔥 ঘরে বসেই Income করুন!\n\nএখানে আপনি প্রতিদিন আনলিমিটেড **Instagram 2FA Account** সেল করে দারুণ ইনকাম করতে পারবেন। 💯\n\n👇 নিচের লিংকে ক্লিক করে এখনই জয়েন করুন:"
    encoded_text = urllib.parse.quote(share_msg)
    url = f"https://t.me/share/url?url=https://t.me/{bot_user}?start={tg_id}&text={encoded_text}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Share and Earn 🔗", url=url, style="success")]])

def get_broadcast_preview_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Send Broadcast 🚀", callback_data="send_broadcast", style="primary")],
        [InlineKeyboardButton(text="Cancel ❌", callback_data="cancel_broadcast", style="danger")]
    ])

# ---------------------------------------------------------
# USER HANDLERS
# ---------------------------------------------------------
@router.message(F.forward_from_chat)
async def get_forwarded_chat_id(message: Message):
    if is_admin(message.from_user.id):
        await message.answer(f"Channel/Chat ID: `{message.forward_from_chat.id}`\n\nCopy this and set it as CHANNEL_ID in your .env file.", parse_mode="Markdown")



async def process_join_bonus(user_id: int, bot: Bot):
    status = await get_setting('join_bonus_status') or 'off'
    if status != 'on':
        return
        
    amount = await get_setting('join_bonus_amount')
    if not amount:
        return
    amount = float(amount)
        
    user = await db.users.find_one({'tg_id': user_id})
    if user and not user.get('join_bonus_received'):
        await db.users.update_one({'tg_id': user_id}, {'$inc': {'balance': amount}, '$set': {'join_bonus_received': True}})
        try:
            await bot.send_message(user_id, f"🎉 **Congratulations!**\n\nYou received a Joining Bonus of **{amount} ৳** for joining our channel!", parse_mode="Markdown")
        except:
            pass

@router.message(CommandStart(), StateFilter("*"))
async def cmd_start(message: Message, bot: Bot, state: FSMContext):
    data = await state.get_data()
    pending_ref = data.get('referred_by')
    await state.clear()
    try:
        args = message.text.split()[1] if len(message.text.split()) > 1 else None
        referred_by = int(args) if args and args.isdigit() else pending_ref
        
        if not await check_channel_membership(bot, message.from_user.id):
            if referred_by:
                await state.update_data(referred_by=referred_by)
            return await message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))

        is_new = await add_user(message.from_user.id, referred_by)
        if is_new and referred_by:
            try:
                total_refs = await db.users.count_documents({'referred_by': referred_by})
                        
                share_msg = "🔥 ঘরে বসেই Income করুন!\n\nএখানে আপনি প্রতিদিন আনলিমিটেড **Instagram 2FA Account** সেল করে দারুণ ইনকাম করতে পারবেন। 💯\n\n👇 নিচের লিংকে ক্লিক করে এখনই জয়েন করুন:"
                encoded_text = urllib.parse.quote(share_msg)
                url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={referred_by}&text={encoded_text}"
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Share More & Earn More 🔗", url=url, style="success")]])
                
                await bot.send_message(
                    chat_id=referred_by, 
                    text=f"🎉 **নতুন রেফারেল!**\n\n👤 **{message.from_user.first_name}** আপনার রেফারেল লিংক ব্যবহার করে জয়েন করেছেন!\n👥 **মোট রেফারেল:** `{total_refs}` জন", 
                    reply_markup=kb, 
                    parse_mode="Markdown"
                )
            except: pass

        start_msg = await get_setting('start_message')
        full_name = message.from_user.full_name
        final_msg = start_msg.replace('{name}', full_name).replace('\\n', '\n')
        try:
            await message.answer(final_msg, reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)), parse_mode="HTML")
        except Exception:
            await message.answer(final_msg, reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))
        await process_join_bonus(message.from_user.id, bot)
        
        if args == "work":
            await state.clear()
            await state.set_state(FlowStates.waiting_for_task_platform)
            lang = await get_user_lang(message.from_user.id)
            text = f"<tg-emoji emoji-id='4958506272551863292'>📊</tg-emoji> <b>কোন প্লাটফর্মের কাজ করতে চান?</b>" if lang == 'bn' else f"<tg-emoji emoji-id='4958506272551863292'>📊</tg-emoji> <b>Which platform's tasks do you want to do?</b>"
            kb = await get_platform_keyboard()
            if kb:
                await message.answer(text, reply_markup=kb, parse_mode="HTML")
            else:
                await message.answer("⚠️ বর্তমানে কোনো কাজ এভেইলেবল নেই। (No tasks available right now)", reply_markup=get_main_keyboard(is_admin(message.from_user.id), lang), parse_mode="HTML")
        elif args == "ref":
            total_refs = await db.users.count_documents({'referred_by': message.from_user.id})
            user = await get_user(message.from_user.id)
            ref_earnings = user[5] if user and len(user) > 5 and user[5] is not None else 0
            ref_percent = await get_setting('refer_percentage')
            text = (
                f"<tg-emoji emoji-id='6109486907207455487'>🔥</tg-emoji> <b>Refer & Earn Program</b> <tg-emoji emoji-id='6111500783012810838'>🌟</tg-emoji>\n\n"
                f"Invite your friends and earn <b>{ref_percent}%</b> of their task earnings forever! <tg-emoji emoji-id='6109188248066593101'>💰</tg-emoji>\n\n"
                f"📊 <b>Your Statistics:</b>\n"
                f"<tg-emoji emoji-id='6109572166603247368'>📣</tg-emoji> <b>Total Referrals:</b> <code>{total_refs}</code> friends\n"
                f"<tg-emoji emoji-id='6109394509576015826'>🎁</tg-emoji> <b>Total Earned:</b> <code>{ref_earnings} TK</code>\n\n"
                f"<tg-emoji emoji-id='6109636449378767561'>➡️</tg-emoji> Click the button below to share your link instantly!"
            )
            await message.answer(text, reply_markup=get_share_and_earn_inline(BOT_USERNAME, message.from_user.id), parse_mode="HTML")
    except Exception as e:
        import traceback
        await message.answer(f"DEBUG ERROR in /start:\\n{str(e)}\\n{traceback.format_exc()}")


@router.callback_query(F.data == "restart_bot")
async def cb_restart_bot(callback: CallbackQuery, bot: Bot):
    await callback.message.delete()
    
    # Simulate /start behavior without referring to the original message object which is deleted
    if not await check_channel_membership(bot, callback.from_user.id):
        return await callback.message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))

    start_msg = await get_setting('start_message')
    full_name = callback.from_user.full_name
    final_msg = start_msg.replace('{name}', full_name).replace('\\n', '\n')
    try:
        await callback.message.answer(final_msg, reply_markup=get_main_keyboard(is_admin(callback.from_user.id), await get_user_lang(callback.from_user.id)), parse_mode="HTML")
    except Exception:
        await callback.message.answer(final_msg, reply_markup=get_main_keyboard(is_admin(callback.from_user.id), await get_user_lang(callback.from_user.id)))
    await process_join_bonus(callback.from_user.id, bot)

@router.callback_query(F.data == "check_joined")
async def cb_check_joined(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if await check_channel_membership(bot, callback.from_user.id):
        await callback.message.delete()
        
        data = await state.get_data()
        referred_by = data.get('referred_by')
        
        is_new = await add_user(callback.from_user.id, referred_by)
        if is_new and referred_by:
            try:
                total_refs = await db.users.count_documents({'referred_by': referred_by})
                        
                share_msg = "🔥 ঘরে বসেই Income করুন!\n\nএখানে আপনি প্রতিদিন আনলিমিটেড **Instagram 2FA Account** সেল করে দারুণ ইনকাম করতে পারবেন। 💯\n\n👇 নিচের লিংকে ক্লিক করে এখনই জয়েন করুন:"
                encoded_text = urllib.parse.quote(share_msg)
                url = f"https://t.me/share/url?url=https://t.me/{BOT_USERNAME}?start={referred_by}&text={encoded_text}"
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Share More & Earn More 🔗", url=url, style="success")]])
                
                await bot.send_message(
                    chat_id=referred_by, 
                    text=f"🎉 **নতুন রেফারেল!**\n\n👤 **{callback.from_user.first_name}** আপনার রেফারেল লিংক ব্যবহার করে জয়েন করেছেন!\n👥 **মোট রেফারেল:** `{total_refs}` জন", 
                    reply_markup=kb, 
                    parse_mode="Markdown"
                )
            except: pass
            
        await state.update_data(referred_by=None)

        start_msg = await get_setting('start_message')
        full_name = callback.from_user.full_name
        final_msg = start_msg.replace('{name}', full_name).replace('\\n', '\n')
        try:
            await callback.message.answer(final_msg, reply_markup=get_main_keyboard(is_admin(callback.from_user.id), await get_user_lang(callback.from_user.id)), parse_mode="HTML")
        except Exception:
            await callback.message.answer(final_msg, reply_markup=get_main_keyboard(is_admin(callback.from_user.id), await get_user_lang(callback.from_user.id)))
        await process_join_bonus(callback.from_user.id, bot)
    else:
        await callback.answer("⚠️ আপনি এখনো চ্যানেলে Join করেননি!", show_alert=True)

@router.message(F.text.in_({"💰 ব্যালেন্স", "💰 Balance"}), StateFilter("*"))
async def cmd_balance(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    if not await check_channel_membership(bot, message.from_user.id):
        return await message.answer("⚠️ You must join our channel first!", reply_markup=await get_force_join_keyboard(bot))

    try:
        user = await get_user(message.from_user.id)
        if not user:
            return await message.answer("⚠️ আপনার অ্যাকাউন্ট পাওয়া যায়নি। অনুগ্রহ করে /start চাপুন।")
            
        insta_pending = await db.tasks.count_documents({'tg_id': message.from_user.id, 'status': 'pending', 'task_type': 'insta_2fa'})
        fb_2fa_pending = await db.tasks.count_documents({'tg_id': message.from_user.id, 'status': 'pending', 'task_type': 'fb_2fa'})
        fb_cookie_pending = await db.tasks.count_documents({'tg_id': message.from_user.id, 'status': 'pending', 'task_type': 'fb_cookie'})
        pending_count = insta_pending + fb_2fa_pending + fb_cookie_pending
        
        pending_withdraw = await get_pending_withdraw_amount(message.from_user.id)
        success_withdraw = await get_successful_withdraw_amount(message.from_user.id)
        
        try:
            insta_price = float(await get_setting('task_price'))
        except:
            insta_price = 3.15
        try:
            fb_2fa_price = float(await get_setting('fb_2fa_price'))
        except:
            fb_2fa_price = 3.50
        try:
            fb_cookie_price = float(await get_setting('fb_cookie_price'))
        except:
            fb_cookie_price = 4.50
            
        processing_amount = (insta_pending * insta_price) + (fb_2fa_pending * fb_2fa_price) + (fb_cookie_pending * fb_cookie_price)
            
        try:
            min_withdraw = float(await get_setting('min_withdraw_amount'))
        except:
            min_withdraw = 50.0
            
        pending_wd_text = f"\n<tg-emoji emoji-id='4956222745814762495'>🔥</tg-emoji> <b>Pending Withdraw:</b> <code>{pending_withdraw} TK</code>" if pending_withdraw > 0 else ""
        success_wd_text = f"\n<tg-emoji emoji-id='4956721670690702265'>✅</tg-emoji> <b>Successful Withdraw:</b> <code>{success_withdraw} TK</code>" if success_withdraw > 0 else ""
        
        lang = await get_user_lang(message.from_user.id)
        if lang == 'en':
            text = (
                f"<tg-emoji emoji-id='4958926882994127612'>🏦</tg-emoji> <b>Your Account Balance</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"<tg-emoji emoji-id='4956601935592424315'>💵</tg-emoji> <b>Current Balance:</b> <code>{user[2]:.2f} TK</code>\n"
                f"<tg-emoji emoji-id='4958479549265347295'>⚡</tg-emoji> <b>Processing Amount:</b> <code>{processing_amount:.2f} TK</code>\n"
                f"<i>(You have {pending_count} pending tasks)</i>"
                f"{pending_wd_text}"
                f"{success_wd_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<tg-emoji emoji-id='4956611513369494230'>⚠️</tg-emoji> <b>Min Withdraw:</b> <code>{min_withdraw:.2f} TK</code>\n"
                f"<tg-emoji emoji-id='4956475826762679249'>💡</tg-emoji> <i>Click below to withdraw</i>"
            )
        else:
            text = (
                f"<tg-emoji emoji-id='4958926882994127612'>🏦</tg-emoji> <b>আপনার অ্যাকাউন্ট ব্যালেন্স</b>\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
                f"<tg-emoji emoji-id='4956601935592424315'>💵</tg-emoji> <b>Current Balance:</b> <code>{user[2]:.2f} TK</code>\n"
                f"<tg-emoji emoji-id='4958479549265347295'>⚡</tg-emoji> <b>Processing Amount:</b> <code>{processing_amount:.2f} TK</code>\n"
                f"<i>(আপনার {pending_count} টি কাজ পেন্ডিং আছে)</i>"
                f"{pending_wd_text}"
                f"{success_wd_text}\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"<tg-emoji emoji-id='4956611513369494230'>⚠️</tg-emoji> <b>মিনিমাম উইথড্র:</b> <code>{min_withdraw:.2f} TK</code>\n"
                f"<tg-emoji emoji-id='4956475826762679249'>💡</tg-emoji> <i>টাকা তুলতে নিচের বাটনে ক্লিক করুন</i>"
            )
        await message.answer(text, reply_markup=get_withdraw_inline(), parse_mode="HTML")
    except Exception as e:
        print(f"Balance handler error: {e}")
        await message.answer("⚠️ একটি সমস্যা হয়েছে। দয়া করে আবার চেষ্টা করুন।")


@router.message(F.text.in_({"👥 আমার রেফারেল", "👥 My Referral"}), StateFilter("*"))
async def cmd_refer(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    if not await check_channel_membership(bot, message.from_user.id):
        return await message.answer("⚠️ You must join our channel first!", reply_markup=await get_force_join_keyboard(bot))
    
    total_refs = await db.users.count_documents({'referred_by': message.from_user.id})
    user = await get_user(message.from_user.id)
    ref_earnings = user[5] if len(user) > 5 and user[5] is not None else 0
    ref_percent = await get_setting('refer_percentage')
            
    text = (
        f"<tg-emoji emoji-id='6109486907207455487'>🔥</tg-emoji> <b>Refer & Earn Program</b> <tg-emoji emoji-id='6111500783012810838'>🌟</tg-emoji>\n\n"
        f"Invite your friends and earn <b>{ref_percent}%</b> of their task earnings forever! <tg-emoji emoji-id='6109188248066593101'>💰</tg-emoji>\n\n"
        f"📊 <b>Your Statistics:</b>\n"
        f"<tg-emoji emoji-id='6109572166603247368'>📣</tg-emoji> <b>Total Referrals:</b> <code>{total_refs}</code> friends\n"
        f"<tg-emoji emoji-id='6109394509576015826'>🎁</tg-emoji> <b>Total Earned:</b> <code>{ref_earnings} TK</code>\n\n"
        f"<tg-emoji emoji-id='6109636449378767561'>➡️</tg-emoji> Click the button below to share your link instantly!"
    )
    await message.answer(text, reply_markup=get_share_and_earn_inline(BOT_USERNAME, message.from_user.id), parse_mode="HTML")

@router.message(F.text.in_({"❓ আমি নতুন", "❓ I am new"}), StateFilter("*"))
async def tutorial_platform_selection(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(FlowStates.waiting_for_tutorial_platform)
    kb = await get_platform_keyboard()
    if kb:
        await message.answer("কোন কাজের ভিডিও দেখতে চান?\nWhich task's video do you want to watch?", reply_markup=kb)
    else:
        await message.answer("⚠️ বর্তমানে কোনো কাজ এভেইলেবল নেই।")

async def send_tutorial_items(message: Message, bot: Bot, task_key: str):
    cursor = db.tutorials.find({'task_type': task_key}).sort('order', 1)
    items = await cursor.to_list(length=100)
    
    lang = await get_user_lang(message.from_user.id)
    kb = get_main_keyboard(is_admin(message.from_user.id), lang)
    
    if not items:
        await message.answer("No tutorial set for this task yet.", reply_markup=kb)
        return
        
    for item in items:
        try:
            if item['msg_type'] == 'text':
                await message.answer(item['content'])
            elif item['msg_type'] == 'photo':
                await bot.send_photo(chat_id=message.chat.id, photo=item['content'], caption=item.get('caption', ''))
            elif item['msg_type'] == 'video':
                await bot.send_video(chat_id=message.chat.id, video=item['content'], caption=item.get('caption', ''))
        except Exception as e:
            print(f"Error sending tutorial item: {e}")
            
    await message.answer("✅ Tutorial Completed.", reply_markup=kb)

@router.message(F.text == "📸 Insta 2FA", FlowStates.waiting_for_tutorial_platform)
async def show_insta2fa_tutorial(message: Message, state: FSMContext, bot: Bot):
    await send_tutorial_items(message, bot, 'insta_2fa')
    await state.clear()

@router.message(F.text == "🌐 FB 2FA", FlowStates.waiting_for_tutorial_platform)
async def show_fb2fa_tutorial(message: Message, state: FSMContext, bot: Bot):
    await send_tutorial_items(message, bot, 'fb_2fa')
    await state.clear()

@router.message(F.text == "🍪 FB Cookie", FlowStates.waiting_for_tutorial_platform)
async def show_fbcookie_tutorial(message: Message, state: FSMContext, bot: Bot):
    await send_tutorial_items(message, bot, 'fb_cookie')
    await state.clear()

@router.message(F.text == "Report 📊")
async def cmd_report(message: Message, bot: Bot, state: FSMContext):
    await state.clear()
    if not await check_channel_membership(bot, message.from_user.id):
        return await message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))
    pending = await get_pending_tasks_count(message.from_user.id)
    accepted = await get_completed_tasks_count(message.from_user.id)
    text = (
        f"📊 **Work Report (কাজের রিপোর্ট)**\n\n"
        f"⏳ Processing (পেন্ডিং কাজ): {pending}\n"
        f"✅ Completed (অ্যাপ্রুভড কাজ): {accepted}\n\n"
        f"*(বিঃদ্রঃ Failed বা Rejected কাজগুলো এখানে যুক্ত হবে না।)*"
    )
    await message.answer(text, parse_mode="Markdown")


@router.message(F.text.in_({"💼 উত্তোলন", "💼 Withdraw"}), StateFilter("*"))
async def withdraw_menu(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    
    if await get_setting('withdraw_force_join') == 'on':
        if not await check_channel_membership(bot, message.from_user.id):
            return await message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))
    
    user_doc = await db.users.find_one({'tg_id': message.from_user.id})
    if user_doc and user_doc.get('is_banned'):
        return await message.answer("❌ Your account is blocked. You cannot withdraw funds.")
        
    if await get_setting('withdraw_status') == 'off':
        return await message.answer("⚠️ Withdrawals are currently disabled by Admin.")
    user = await get_user(message.from_user.id)
    if not user:
        return await message.answer("⚠️ আপনার অ্যাকাউন্ট পাওয়া যাচ্ছে না। প্রথমে /start চাপুন।")
    min_amount = int(await get_setting('min_withdraw_amount'))
    if user[2] < min_amount:
        return await message.answer(f"⚠️ Minimum withdraw is {min_amount} TK.")
    await message.answer("💳 Select withdrawal method:", reply_markup=await get_withdraw_methods())
    await state.set_state(WithdrawStates.waiting_for_method)

@router.message(F.text.in_({"👨‍💻 সাপোর্ট", "👨‍💻 Support"}), StateFilter("*"))
async def support_menu(message: Message):
    support_link = await get_setting('support_link')
    lang = await get_user_lang(message.from_user.id)
    if support_link:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Contact Admin 💬", url=support_link, style="primary")]
        ])
        msg = f"{E_INFO} <b>For any help or support, please click below to contact the admin.</b>" if lang == 'en' else f"{E_INFO} <b>যেকোনো সমস্যা বা সাহায্যের জন্য নিচে ক্লিক করে এডমিনের সাথে যোগাযোগ করুন।</b>"
        await message.answer(msg, reply_markup=kb, parse_mode="HTML")
    else:
        msg = f"{E_INFO} <b>For any help or support, please contact our support group or admin.</b>" if lang == 'en' else f"{E_INFO} <b>যেকোনো সমস্যা বা সাহায্যের জন্য আমাদের সাপোর্ট গ্রুপ বা এডমিনের সাথে যোগাযোগ করুন।</b>"
        await message.answer(msg, parse_mode="HTML")

@router.message(F.text.in_({"🌐 ভাষা পরিবর্তন", "🌐 Change Language"}), StateFilter("*"))
async def language_menu(message: Message):
    text = "আপনার ভাষা নির্বাচন করুন:\nSelect your language:"
    await message.answer(text, reply_markup=get_language_keyboard())

@router.callback_query(F.data.startswith("set_lang_"))
async def process_language(callback: CallbackQuery):
    lang = callback.data.split("_")[2]
    await db.users.update_one({'tg_id': callback.from_user.id}, {'$set': {'lang': lang}})
    LANG_CACHE[callback.from_user.id] = lang
    text = "✅ ভাষা সফলভাবে পরিবর্তন করা হয়েছে!" if lang == 'bn' else "✅ Language changed successfully!"
    await callback.message.delete()
    await callback.message.answer(text, reply_markup=get_main_keyboard(is_admin(callback.from_user.id), lang))

# ---------------------------------------------------------
# WITHDRAW HANDLERS

# ---------------------------------------------------------
@router.callback_query(F.data == "start_withdraw")
async def start_withdraw(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    
    if await get_setting('withdraw_force_join') == 'on':
        if not await check_channel_membership(bot, callback.from_user.id):
            return await callback.message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))

    if await get_setting('withdraw_status') == 'off':
        return await callback.answer("⚠️ Withdrawals are currently disabled by Admin.", show_alert=True)
    user = await get_user(callback.from_user.id)
    if not user:
        return await callback.answer("⚠️ User not found. Please restart the bot.", show_alert=True)
    lang = await get_user_lang(callback.from_user.id)
    min_amount = int(await get_setting('min_withdraw_amount'))
    if user[2] < min_amount:
        msg = f"⚠️ Minimum withdraw is {min_amount} TK." if lang == 'en' else f"⚠️ মিনিমাম উইথড্র {min_amount} টাকা।"
        return await callback.answer(msg, show_alert=True)
    msg = f"<tg-emoji emoji-id='4958926882994127612'>💰</tg-emoji> <b>Select withdrawal method:</b>" if lang == 'en' else f"<tg-emoji emoji-id='4958926882994127612'>💰</tg-emoji> <b>টাকা তোলার মাধ্যম সিলেক্ট করুন:</b>"
    await callback.message.edit_text(msg, reply_markup=await get_withdraw_methods(), parse_mode="HTML")
    await state.set_state(WithdrawStates.waiting_for_method)

@router.callback_query(F.data == "cancel_withdraw")
async def cb_cancel_withdraw(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.message.answer("❌ Withdrawal cancelled.", reply_markup=get_main_keyboard(is_admin(callback.from_user.id), await get_user_lang(callback.from_user.id)))

@router.callback_query(WithdrawStates.waiting_for_method, F.data.startswith("wd_"))
async def process_withdraw_method(callback: CallbackQuery, state: FSMContext):
    method = callback.data.split("_")[1]
    await state.update_data(method=method)
    await callback.message.delete()
    await callback.message.answer(f"📝 Selected **{method}**.\nEnter account number:", reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(WithdrawStates.waiting_for_details)

@router.message(WithdrawStates.waiting_for_details)
async def process_withdraw_details(message: Message, state: FSMContext):
    if message.text in ["Cancel ❌", "Back 🔙", "Back to Main Menu 🔙", "❌ বাতিল"]:
        await state.clear()
        lang = await get_user_lang(message.from_user.id)
        msg = "❌ Cancelled." if lang == 'en' else "❌ বাতিল করা হয়েছে।"
        return await message.answer(msg, reply_markup=get_main_keyboard(is_admin(message.from_user.id), lang))
        
    await state.update_data(details=message.text)
    user = await get_user(message.from_user.id)
    balance = user[2] if user and len(user) > 2 else 0.0
    await message.answer(f"💰 Current Balance: **{balance:.2f} TK**\n\n💵 Enter amount to withdraw:", reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(WithdrawStates.waiting_for_amount)

@router.message(WithdrawStates.waiting_for_amount)
async def process_withdraw_amount(message: Message, state: FSMContext, bot: Bot):
    if message.text in ["Cancel ❌", "Back 🔙", "Back to Main Menu 🔙", "❌ বাতিল"]:
        await state.clear()
        lang = await get_user_lang(message.from_user.id)
        msg = "❌ Cancelled." if lang == 'en' else "❌ বাতিল করা হয়েছে।"
        return await message.answer(msg, reply_markup=get_main_keyboard(is_admin(message.from_user.id), lang))

    try:
        amount = float(message.text)
    except ValueError:
        return await message.answer("⚠️ Enter a valid number.")
        
    user = await get_user(message.from_user.id)
    try:
        min_amount = float(await get_setting('min_withdraw_amount'))
    except:
        min_amount = 30.0
        
    if amount < min_amount: return await message.answer(f"⚠️ Min withdraw is {min_amount} TK.")
    if amount > user[2]: return await message.answer(f"⚠️ Not enough balance. You have {user[2]:.2f} TK.")
    
    data = await state.get_data()
    method = data['method']
    
    fee = 0
    if method == "bKash" or method == "Nagad":
        fee = 5
        
    receive_amount = amount - fee
    if receive_amount <= 0: return await message.answer(f"⚠️ Amount is too low. After {fee} TK fee, you receive 0 or less.")
    
    req_id = await add_withdraw_request(message.from_user.id, method, data['details'], amount, fee=fee)
    
    if method == "Binance":
        try:
            binance_rate = float(await get_setting('binance_rate'))
        except:
            binance_rate = 129.0
        receive_text = f"{(amount / binance_rate):.2f} USD (Rate: {binance_rate} TK/$)"
    else:
        receive_text = f"{receive_amount} TK"
    
    text = (
        f"✅ **Withdrawal Request Sent!**\n\n"
        f"💰 **Requested:** `{amount} TK`\n"
        f"âž– **Fee:** `{fee} TK`\n"
        f"💵 **You Will Receive:** `{receive_text}`"
    )
    await message.answer(text, parse_mode="Markdown")
    await state.clear()
    
    admin_text = (
        f"🚨 **New Withdrawal Request**\n\n"
        f"👤 User: [{message.from_user.full_name}](tg://user?id={message.from_user.id}) (ID: `{message.from_user.id}`)\n"
        f"💳 Method: {method}\n"
        f"📝 Details: `{data['details']}`\n"
        f"💰 Requested Amount: {amount} TK\n"
        f"💵 Amount to Send: {receive_text} (Fee: {fee})\n"
        f"💵 User's Remaining Balance: {user[2] - amount:.2f} TK"
    )
    try:
        await bot.send_message(chat_id=ADMIN_ID, text=admin_text, reply_markup=get_withdraw_approve_keyboard(req_id, message.from_user.id), parse_mode="Markdown")
    except: pass

# ---------------------------------------------------------
# TASK HANDLERS
# ---------------------------------------------------------
async def get_task_data(is_facebook=False, task_type='insta_2fa'):
    pw_mode = await get_setting(f'password_mode_{task_type}')
    if not pw_mode:
        pw_mode = await get_setting('password_mode') or 'random'
        
    date_str = await get_system_date()
    day = int(date_str.split('-')[2])
    
    if pw_mode == 'random':
        password = generate_hard_password(day)
    else:
        password = await get_setting(f'fixed_password_{task_type}')
        if not password:
            password = await get_setting('fixed_password') or 'GoTaskPassword123'
            
    if is_facebook:
        first_name = fake.first_name()
        last_name = fake.last_name()
        return first_name, last_name, password
    return generate_username(), password

@router.message(F.text.in_({"📝 কাজ", "📝 Task"}), StateFilter("*"))
async def task_platform_selection(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(FlowStates.waiting_for_task_platform)
    lang = await get_user_lang(message.from_user.id)
    text = f"<tg-emoji emoji-id='4958506272551863292'>📊</tg-emoji> <b>কোন প্লাটফর্মের কাজ করতে চান?</b>" if lang == 'bn' else f"<tg-emoji emoji-id='4958506272551863292'>📊</tg-emoji> <b>Which platform's tasks do you want to do?</b>"
    kb = await get_platform_keyboard()
    if not kb:
        await state.clear()
        return await message.answer("⚠️ দুঃখিত, বর্তমানে কোনো কাজ এভেইলেবল নেই। দয়া করে পরে আবার চেষ্টা করুন।", reply_markup=get_main_keyboard(is_admin(message.from_user.id), lang))
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

@router.message(F.text == "📘 Facebook", FlowStates.waiting_for_task_platform)
async def show_fb_tasks(message: Message, state: FSMContext):
    await state.set_state(FlowStates.in_fb_menu)
    fb_2fa_price = await get_setting('fb_2fa_price')
    fb_cookie_price = await get_setting('fb_cookie_price')
    await message.answer("Facebook এর কাজগুলো:", reply_markup=get_fb_tasks_keyboard(fb_2fa_price, fb_cookie_price))

@router.message(F.text == "📸 Instagram", FlowStates.waiting_for_task_platform)
async def show_insta_tasks(message: Message, state: FSMContext):
    await state.set_state(FlowStates.in_insta_menu)
    insta_price = await get_setting('task_price')
    await message.answer("Instagram এর কাজগুলো:", reply_markup=get_insta_tasks_keyboard(insta_price))

@router.message(F.text == "🎥 কীভাবে কাজ করব")
async def show_how_to_work(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == FlowStates.in_fb_menu.state:
        tut = await get_setting('tutorial_fb')
        if not tut: tut = "No tutorial set for Facebook yet."
        await message.answer(tut)
    elif current_state == FlowStates.in_insta_menu.state:
        tut = await get_setting('tutorial_insta')
        if not tut: tut = "No tutorial set for Instagram yet."
        await message.answer(tut)
    else:
        tut = await get_setting('tutorial_message')
        if not tut: tut = "No tutorial set."
        await message.answer(tut)

@router.message(F.text == "❌ বাতিল", StateFilter("*"))
async def cancel_menus(message: Message, state: FSMContext):
    await state.clear()
    lang = await get_user_lang(message.from_user.id)
    await message.answer("❌ Cancelled.", reply_markup=get_main_keyboard(is_admin(message.from_user.id), lang))


@router.callback_query(F.data == "start_task_submenu")
async def cb_start_task_submenu(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await state.clear()
    insta_price = await get_setting('task_price')
    fb_2fa_price = await get_setting('fb_2fa_price')
    fb_cookie_price = await get_setting('fb_cookie_price')
    await callback.message.answer("Select a task from the menu below:", reply_markup=await get_task_submenu_keyboard(insta_price, fb_2fa_price, fb_cookie_price))
    await callback.answer()

import re
import asyncio
import time

async def task_timeout_worker(state: FSMContext, start_time: float, bot: Bot, chat_id: int):
    try:
        countdown_started = 0
        while True:
            await asyncio.sleep(10)
            data = await state.get_data()
            if data.get('task_start_time') != start_time:
                break
                
            task_type = data.get('task_type', 'insta_2fa')
            db_key = f'task_status_{task_type}'
            status = await get_setting(db_key) or 'on'
            
            if status == 'off':
                if countdown_started == 0:
                    countdown_started = time.time()
                elif time.time() - countdown_started >= 300:
                    await state.clear()
                    if 'task_msg_id' in data:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=data['task_msg_id'])
                        except Exception:
                            pass
                    lang = await get_user_lang(chat_id)
                    text = "⚠️ এই টাস্কটি বন্ধ করে দেওয়া হয়েছে এবং আপনার ৫ মিনিট সময় শেষ! (Task expired)" if lang == 'bn' else "⚠️ This task was closed and your 5 minutes expired!"
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception:
                        pass
                    break
            else:
                countdown_started = 0
    except Exception:
        pass

def generate_full_name(username: str) -> str:
    base = re.sub(r'[0-9]+', '', username)
    base = base.replace('_', ' ').replace('.', ' ')
    full_name = base.title().strip()
    if not full_name:
        return 'Random Name'
    return full_name

@router.message(F.text.startswith("📸 Insta 2FA"), StateFilter("*"))
async def start_task(message: Message, bot: Bot, state: FSMContext):
    if not await check_channel_membership(bot, message.from_user.id):
        return await message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))
    if not await check_submit_deadline():
        return await message.answer("⚠️ আজকের জন্য টাস্ক সাবমিট করার সময় শেষ হয়ে গেছে! দয়া করে আগামীকাল আবার চেষ্টা করুন।")
    
    status = await get_setting('task_status_insta_2fa') or 'on'
    if status == 'off':
        return await message.answer("⚠️ এই টাস্কটি সাময়িকভাবে বন্ধ আছে। (Currently disabled by admin)")

    username, password = await get_task_data()
    full_name = generate_full_name(username)
    start_time = time.time()
    await state.update_data(task_username=username, task_password=password, task_type='insta_2fa', task_start_time=start_time)
    price = await get_setting('task_price')
    
    # Send a small helper message to show the Reply Keyboard (Done / Cancel)
    lang = await get_user_lang(message.from_user.id)
    msg_help = f"{E_DOWN} <b>নিচে থাকা username and password ব্যবহার করে account খুলে Done ✅ এ ক্লিক করুন, এরপর আপনার 2fa key দিন:</b>" if lang == 'bn' else f"{E_DOWN} <b>Use the username and password below to open an account, click Done ✅, then provide your 2fa key:</b>"
    await message.answer(msg_help, reply_markup=get_task_keyboard(), parse_mode="HTML")

    text = (
        f"{E_TARGET} <b>আপনার নতুন কাজ প্রস্তুত!</b>\n\n" if lang == 'bn' else f"{E_TARGET} <b>Your new task is ready!</b>\n\n"
    )
    text += (
        f"{E_FIRE} <b>টাস্ক:</b> Instagram 2FA\n" if lang == 'bn' else f"{E_FIRE} <b>Task:</b> Instagram 2FA\n"
    )
    text += (
        f"{E_CASH} <b>মূল্য:</b> <code>{price} TK</code>\n\n" if lang == 'bn' else f"{E_CASH} <b>Price:</b> <code>{price} TK</code>\n\n"
    )
    text += (
        f"📝 <b>পুরো নাম:</b> <code>{full_name}</code> (ঐচ্ছিক/Optional)\n" if lang == 'bn' else f"📝 <b>Full Name:</b> <code>{full_name}</code> (Optional)\n"
    )
    text += (
        f"{E_USER} <b>ইউজারনেম:</b> <code>{username}</code>\n" if lang == 'bn' else f"{E_USER} <b>Username:</b> <code>{username}</code>\n"
    )
    text += (
        f"{E_LOCK} <b>পাসওয়ার্ড:</b> <code>{password}</code>\n\n" if lang == 'bn' else f"{E_LOCK} <b>Password:</b> <code>{password}</code>\n\n"
    )
    text += (
        f"{E_BULL} <i>অ্যাকাউন্ট খোলার পর অবশ্যই Profile Picture অ্যাড করবেন এবং ২-৩ জনকে Follow করবেন!</i>" if lang == 'bn' else f"{E_BULL} <i>Make sure to add a Profile Picture and Follow 2-3 people after opening the account!</i>"
    )
    msg = await message.answer(text, reply_markup=get_refresh_inline(), parse_mode="HTML")
    await state.update_data(task_username=username, task_password=password, task_msg_id=msg.message_id)
    asyncio.create_task(task_timeout_worker(state, start_time, bot, message.from_user.id))

@router.callback_query(F.data == "refresh_task_data")
async def refresh_task(callback: CallbackQuery, state: FSMContext):
    username, password = await get_task_data()
    full_name = generate_full_name(username)
    start_time = time.time()
    await state.update_data(task_username=username, task_password=password, task_start_time=start_time)
    price = await get_setting('task_price')
    lang = await get_user_lang(callback.from_user.id)
    
    text = (
        f"🔄 **নতুন অ্যাকাউন্ট দেওয়া হয়েছে!**\n\n" if lang == 'bn' else f"🔄 **New account provided!**\n\n"
    )
    text += (
        f"🔥 **টাস্ক:** Instagram 2FA\n" if lang == 'bn' else f"🔥 **Task:** Instagram 2FA\n"
    )
    text += (
        f"💸 **মূল্য:** `{price} TK`\n\n" if lang == 'bn' else f"💸 **Price:** `{price} TK`\n\n"
    )
    text += (
        f"📝 **পুরো নাম:** `{full_name}` (ঐচ্ছিক/Optional)\n" if lang == 'bn' else f"📝 **Full Name:** `{full_name}` (Optional)\n"
    )
    text += (
        f"👤 **ইউজারনেম:** `{username}`\n" if lang == 'bn' else f"👤 **Username:** `{username}`\n"
    )
    text += (
        f"🔐 **পাসওয়ার্ড:** `{password}`\n\n" if lang == 'bn' else f"🔐 **Password:** `{password}`\n\n"
    )
    text += (
        f"📢 *অ্যাকাউন্ট খোলার পর অবশ্যই Profile Picture অ্যাড করবেন এবং ২-৩ জনকে Follow করবেন!*" if lang == 'bn' else f"📢 *Make sure to add a Profile Picture and Follow 2-3 people after opening the account!*"
    )
    
    await callback.message.edit_text(text, reply_markup=get_refresh_inline(), parse_mode="Markdown")
    await callback.answer()
    asyncio.create_task(task_timeout_worker(state, start_time, callback.bot, callback.from_user.id))

@router.message(F.text == "Done ✅")
async def task_done(message: Message, state: FSMContext):
    data = await state.get_data()
    if 'task_username' not in data:
        return await message.answer("⚠️ Start a task first.")
    await message.answer("✅ Send me the 2FA Key:", reply_markup=get_task_keyboard())
    await state.set_state(TaskStates.waiting_for_2fa_key)

@router.message(TaskStates.waiting_for_2fa_key)
async def process_2fa_key(message: Message, state: FSMContext, bot: Bot):
    if message.text in ["Done ✅", "Cancel ❌", "কাজ 💼", "Balance 💰", "Report 📊", "Admin Panel ⚙️", "Back 🔙"]:
        if message.text == "Cancel ❌" or message.text == "Back 🔙":
            data = await state.get_data()
            if 'task_msg_id' in data:
                try:
                    await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
                except Exception:
                    pass
            await state.clear()
            return await message.answer("❌ Task cancelled.", reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))
        return
        
    clean_key = message.text.replace(" ", "").upper()
    if len(clean_key) != 32 or not all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=" for c in clean_key):
        return await message.answer("⚠️ আপনার 2FA Key-টি সঠিক নয়! Instagram 2FA Key ঠিক ৩২ অক্ষরের Base32 কোড হতে হবে।")
        
    existing = await db.tasks.find_one({'two_fa_key': clean_key})
    if existing:
        return await message.answer("⚠️ Duplicate 2FA Key পাওয়া গেছে! এই Key-টি আগে কেউ সাবমিট করেছে। ফেক কাজ করলে অ্যাকাউন্ট ব্যান করা হবে!")
        
    otp = generate_2fa_code(clean_key)
    if not otp: return await message.answer("⚠️ Invalid 2FA Key. কোড জেনারেট করা সম্ভব হয়নি।")
    
    await state.update_data(two_fa_key=clean_key, two_fa_code=otp)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Refresh Code 🔄", callback_data="refresh_2fa_code", style="success")]])
    await message.answer(f"✅ Key সফলভাবে গ্রহণ করা হয়েছে।\n\n🔐 **2FA Code:** `{otp}`", reply_markup=kb, parse_mode="Markdown")
    
    await message.answer("Instagram-এ এই কোডটি বসিয়ে নিচে থেকে Complete and Submit বাটনে ক্লিক করুন।", reply_markup=get_submit_keyboard())
    await state.set_state(TaskStates.waiting_for_submit)

@router.callback_query(F.data == "refresh_2fa_code")
async def cb_refresh_2fa_code(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    clean_key = data.get('two_fa_key')
    if not clean_key:
        return await callback.answer("⚠️ No active task found.", show_alert=True)
    
    otp = generate_2fa_code(clean_key)
    if not otp:
        return await callback.answer("⚠️ Failed to generate code.", show_alert=True)
        
    await state.update_data(two_fa_code=otp)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Refresh Code 🔄", callback_data="refresh_2fa_code", style="success")]])
    new_text = f"✅ Key সফলভাবে গ্রহণ করা হয়েছে।\n\n🔐 **2FA Code:** `{otp}`"
    
    # Only edit if the text actually changed (otherwise Telegram throws an error)
    if callback.message.text and new_text.strip() not in callback.message.text.strip():
        try:
            await callback.message.edit_text(new_text, reply_markup=kb, parse_mode="Markdown")
            await callback.answer("✅ Code Refreshed!")
        except Exception:
            await callback.answer("✅ Code is same, wait a few seconds.", show_alert=False)
    else:
        await callback.answer("⚠️ Wait a few seconds before refreshing again.", show_alert=False)

@router.message(F.text == "Complete and Submit 📥", TaskStates.waiting_for_submit)
async def submit_task(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    username = data.get('task_username')
    
    # Check if user is already banned
    user_doc = await db.users.find_one({'tg_id': message.from_user.id})
    if user_doc and user_doc.get('is_banned', False):
        return await message.answer("❌ Your account is blocked due to fake submissions.")
        
    ss_status = await get_setting('insta_screenshot_status') or 'on'
    
    if ss_status == 'off':
        # Bypass screenshot and submit directly
        task_type = data.get('task_type', 'insta_2fa')
        await add_task(message.from_user.id, data.get('task_username', ''), data.get('task_password', ''), data.get('two_fa_key', ''), data.get('two_fa_code', ''), task_type)
        
        if 'task_msg_id' in data:
            try:
                await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
            except Exception:
                pass
        await state.clear()
        
        pending_count = await get_pending_tasks_count(message.from_user.id)
        accepted_count = await get_completed_tasks_count(message.from_user.id)
        
        text = (
            f"🎉 **Task Submitted Successfully!**\n\n"
            f"📊 **আপনার কাজের রিপোর্ট:**\n"
            f"⏳ **পেন্ডিং কাজ:** `{pending_count}` টি\n"
            f"✅ **অ্যাপ্রুভড কাজ:** `{accepted_count}` টি\n\n"
            f"নতুন কাজ শুরু করতে নিচের বাটনে ক্লিক করুন 👇"
        )
        insta_price = await get_setting('task_price')
        fb_2fa_price = await get_setting('fb_2fa_price')
        fb_cookie_price = await get_setting('fb_cookie_price')
        await message.answer(text, reply_markup=await get_task_submenu_keyboard(insta_price, fb_2fa_price, fb_cookie_price), parse_mode="Markdown")
        return
        
    await message.answer(
        f"📷 <b>স্ক্রিনশট দিন</b>\n\n"
        f"অনুগ্রহ করে আপনার Instagram প্রোফাইলের একটি স্ক্রিনশট দিন, যেখানে <b>{username}</b> ইউজারনেমটি পরিষ্কার দেখা যায়।\n\n"
        f"<i>(ফেক স্ক্রিনশট দিলে অ্যাকাউন্ট ব্লক করা হবে!)</i>",
        reply_markup=get_cancel_keyboard(),
        parse_mode="HTML"
    )
    await state.set_state(TaskStates.waiting_for_screenshot)

@router.message(~F.text.in_(MAIN_MENU_BUTTONS), TaskStates.waiting_for_submit)
async def submit_task_fallback(message: Message, state: FSMContext, bot: Bot):
    return await message.answer("⚠️ অনুগ্রহ করে নিচের 'Complete and Submit 📥' বাটনে ক্লিক করুন।", reply_markup=get_submit_keyboard())

@router.message(F.text & ~F.text.in_(MAIN_MENU_BUTTONS), TaskStates.waiting_for_screenshot)
async def process_screenshot_text_fallback(message: Message, state: FSMContext, bot: Bot):
    await message.answer("⚠️ অনুগ্রহ করে একটি **ছবি (Screenshot)** দিন। আপনি টেক্সট পাঠিয়েছেন।", parse_mode="Markdown")

async def get_valid_ocr_key():
    current_month = datetime.now().strftime("%Y-%m")
    keys = await db.ocr_keys.find({}).to_list(None)
    
    for k in keys:
        if k.get('month') != current_month:
            await db.ocr_keys.update_one({'_id': k['_id']}, {'$set': {'usage': 0, 'month': current_month}})
            k['usage'] = 0
            k['month'] = current_month
            
        if k.get('usage', 0) < 25000:
            return k['key'], k['_id']
            
    return 'helloworld', None

@router.message(F.photo, TaskStates.waiting_for_screenshot)
async def process_screenshot(message: Message, state: FSMContext, bot: Bot):
    processing_msg = await message.answer("🔄 স্ক্রিনশট স্ক্যান করা হচ্ছে, দয়া করে অপেক্ষা করুন...")
    
    try:
        data = await state.get_data()
        expected_username = data.get('task_username')
        
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                image_data = await resp.read()
                
            api_key, key_id = await get_valid_ocr_key()
                
            payload = aiohttp.FormData()
            payload.add_field('apikey', api_key) 
            payload.add_field('file', image_data, filename='screenshot.jpg', content_type='image/jpeg')
            payload.add_field('OCREngine', '2')
            
            async with session.post('https://api.ocr.space/parse/image', data=payload) as ocr_resp:
                ocr_result = await ocr_resp.json()
                if key_id:
                    await db.ocr_keys.update_one({'_id': key_id}, {'$inc': {'usage': 1}})
                
        extracted_text = ""
        if ocr_result and not ocr_result.get('IsErroredOnProcessing'):
            for p in ocr_result.get('ParsedResults', []):
                extracted_text += p.get('ParsedText', '')
                
        extracted_text = extracted_text.lower().replace(" ", "")
        expected_username_clean = expected_username.lower().replace(" ", "")
        
        if expected_username_clean in extracted_text:
            await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
            
            task_type = data.get('task_type', 'insta_2fa')
            await add_task(message.from_user.id, data['task_username'], data['task_password'], data['two_fa_key'], data['two_fa_code'], task_type)
            
            if 'task_msg_id' in data:
                try:
                    await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
                except Exception:
                    pass
            await state.clear()
            
            await db.users.update_one({'tg_id': message.from_user.id}, {'$set': {'screenshot_strikes': 0}})
            
            pending_count = await get_pending_tasks_count(message.from_user.id)
            accepted_count = await get_completed_tasks_count(message.from_user.id)
            
            text = (
                f"🎉 **Task Submitted Successfully!**\n\n"
                f"📊 **আপনার কাজের রিপোর্ট:**\n"
                f"⏳ **পেন্ডিং কাজ:** `{pending_count}` টি\n"
                f"✅ **অ্যাপ্রুভড কাজ:** `{accepted_count}` টি\n\n"
                f"নতুন কাজ শুরু করতে নিচের বাটনে ক্লিক করুন 👇"
            )
            insta_price = await get_setting('task_price')
            fb_2fa_price = await get_setting('fb_2fa_price')
            fb_cookie_price = await get_setting('fb_cookie_price')
            await message.answer(text, reply_markup=await get_task_submenu_keyboard(insta_price, fb_2fa_price, fb_cookie_price), parse_mode="Markdown")
        else:
            await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
            
            user_doc = await db.users.find_one({'tg_id': message.from_user.id})
            today_str = await get_system_date()
            
            strikes = 0
            if user_doc:
                last_strike_date = user_doc.get('screenshot_strikes_date', '')
                if last_strike_date == today_str:
                    strikes = user_doc.get('screenshot_strikes', 0)
            
            strikes += 1
            
            if strikes >= 3:
                await db.users.update_one({'tg_id': message.from_user.id}, {'$set': {'is_banned': True, 'screenshot_strikes': strikes, 'screenshot_strikes_date': today_str}})
                await state.clear()
                await message.answer("❌ আপনি আজ বারবার ফেক স্ক্রিনশট দিয়েছেন! আপনার অ্যাকাউন্ট ব্লক করা হয়েছে এবং উইথড্র বন্ধ করে দেওয়া হয়েছে।")
            else:
                await db.users.update_one({'tg_id': message.from_user.id}, {'$set': {'screenshot_strikes': strikes, 'screenshot_strikes_date': today_str}})
                remaining = 3 - strikes
                await message.answer(f"⚠️ আপনার স্ক্রিনশটে `{expected_username}` ইউজারনেমটি পাওয়া যায়নি! এটি আপনার ভুল স্ক্রিনশট।\nআজ আর মাত্র {remaining} বার ভুল করলে আপনার অ্যাকাউন্ট ব্লক করা হবে এবং ব্যালেন্স কেটে নেওয়া হবে (Warning)!\n\nসঠিক স্ক্রিনশট দিন:")
                
    except Exception as e:
        print(f"OCR Error: {e}")
        await bot.delete_message(chat_id=message.chat.id, message_id=processing_msg.message_id)
        await message.answer("⚠️ স্ক্রিনশট চেক করতে সমস্যা হয়েছে। দয়া করে আবার স্ক্রিনশট দিন বা কিছুক্ষণ পর চেষ্টা করুন।")

# ---------------------------------------------------------
# FACEBOOK TASK HANDLERS
# ---------------------------------------------------------
@router.message(F.text.startswith("🌐 FB 2FA") | F.text.startswith("🌐 Fb Cookies") | F.text.startswith("🍪 FB Cookie"), StateFilter("*"))
async def start_fb_task(message: Message, bot: Bot, state: FSMContext):
    if not await check_channel_membership(bot, message.from_user.id):
        return await message.answer("⚠️ বটটি ব্যবহার করতে হলে প্রথমে আমাদের টেলিগ্রাম চ্যানেলে Join করতে হবে!", reply_markup=await get_force_join_keyboard(bot))
    if not await check_submit_deadline():
        return await message.answer("⚠️ আজকের জন্য টাস্ক সাবমিট করার সময় শেষ হয়ে গেছে! দয়া করে আগামীকাল আবার চেষ্টা করুন।")
    
    is_cookie_task = "Cookie" in message.text
    task_type = 'fb_cookie' if is_cookie_task else 'fb_2fa'
    
    db_key = 'task_status_fb_cookie' if is_cookie_task else 'task_status_fb_2fa'
    status = await get_setting(db_key) or 'on'
    if status == 'off':
        return await message.answer("⚠️ এই টাস্কটি সাময়িকভাবে বন্ধ আছে। (Currently disabled by admin)")
    
    first_name, last_name, password = await get_task_data(is_facebook=True, task_type=task_type)
    start_time = time.time()
    await state.update_data(fb_first_name=first_name, fb_last_name=last_name, fb_password=password, task_type=task_type, task_start_time=start_time)
    
    price = await get_setting('fb_cookie_price') if is_cookie_task else await get_setting('fb_2fa_price')
    task_name = "Facebook Cookies" if is_cookie_task else "Facebook 2FA"
    
    lang = await get_user_lang(message.from_user.id)
    msg_help = f"{E_DOWN} <b>নিচে থাকা First name, Last name and password ব্যবহার করে account খুলে Submit UID 🆔 এ ক্লিক করুন:</b>" if lang == 'bn' else f"{E_DOWN} <b>Use the First name, Last name and password below to open an account and click Submit UID 🆔:</b>"
    await message.answer(msg_help, reply_markup=get_submit_uid_keyboard(), parse_mode="HTML")

    text = (
        f"{E_TARGET} <b>আপনার নতুন কাজ প্রস্তুত!</b>\n\n" if lang == 'bn' else f"{E_TARGET} <b>Your new task is ready!</b>\n\n"
    )
    text += (
        f"{E_FIRE} <b>টাস্ক:</b> {task_name}\n" if lang == 'bn' else f"{E_FIRE} <b>Task:</b> {task_name}\n"
    )
    text += (
        f"{E_CASH} <b>মূল্য:</b> <code>{price} TK</code>\n\n" if lang == 'bn' else f"{E_CASH} <b>Price:</b> <code>{price} TK</code>\n\n"
    )
    text += (
        f"{E_USER} <b>First Name:</b> <code>{first_name}</code>\n"
        f"{E_USER} <b>Last Name:</b> <code>{last_name}</code>\n"
    )
    text += (
        f"{E_LOCK} <b>পাসওয়ার্ড:</b> <code>{password}</code>\n\n" if lang == 'bn' else f"{E_LOCK} <b>Password:</b> <code>{password}</code>\n\n"
    )
    text += (
        f"{E_BULL} <i>অ্যাকাউন্ট খোলার পর Submit UID তে ক্লিক করুন!</i>" if lang == 'bn' else f"{E_BULL} <i>Click Submit UID after opening the account!</i>"
    )
    msg = await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh Data 🟢", callback_data="refresh_fb_task_data", style="success")]]), parse_mode="HTML")
    await state.update_data(task_msg_id=msg.message_id)
    
    asyncio.create_task(task_timeout_worker(state, start_time, bot, message.from_user.id))

@router.callback_query(F.data == "refresh_fb_task_data")
async def refresh_fb_task(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    task_type = data.get('task_type', 'fb_2fa')
    is_cookie_task = task_type == 'fb_cookie'
    
    first_name, last_name, password = await get_task_data(is_facebook=True, task_type=task_type)
    start_time = time.time()
    await state.update_data(fb_first_name=first_name, fb_last_name=last_name, fb_password=password, task_start_time=start_time)
    
    price = await get_setting('fb_cookie_price') if is_cookie_task else await get_setting('fb_2fa_price')
    task_name = "Facebook Cookies" if is_cookie_task else "Facebook 2FA"
    lang = await get_user_lang(callback.from_user.id)
    
    text = (
        f"🔄 **নতুন অ্যাকাউন্ট দেওয়া হয়েছে!**\n\n" if lang == 'bn' else f"🔄 **New account provided!**\n\n"
    )
    text += (
        f"🔥 **টাস্ক:** {task_name}\n" if lang == 'bn' else f"🔥 **Task:** {task_name}\n"
    )
    text += (
        f"💸 **মূল্য:** `{price} TK`\n\n" if lang == 'bn' else f"💸 **Price:** `{price} TK`\n\n"
    )
    text += (
        f"👤 **First Name:** `{first_name}`\n"
        f"👤 **Last Name:** `{last_name}`\n"
    )
    text += (
        f"🔐 **পাসওয়ার্ড:** `{password}`\n\n" if lang == 'bn' else f"🔐 **Password:** `{password}`\n\n"
    )
    text += (
        f"📢 *অ্যাকাউন্ট খোলার পর Submit UID তে ক্লিক করুন!*" if lang == 'bn' else f"📢 *Click Submit UID after opening the account!*"
    )

    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄 Refresh Data 🟢", callback_data="refresh_fb_task_data", style="success")]]), parse_mode="Markdown")
    await callback.answer()
    
    asyncio.create_task(task_timeout_worker(state, start_time, callback.bot, callback.from_user.id))

@router.message(F.text == "Submit UID 🆔")
async def ask_for_uid(message: Message, state: FSMContext):
    data = await state.get_data()
    if 'fb_first_name' not in data:
        return await message.answer("⚠️ Start a task first.")
    await message.answer("✅ Send me the UID (User ID):", reply_markup=get_cancel_keyboard())
    
    task_type = data.get('task_type', 'fb_2fa')
    if task_type == 'fb_cookie':
        await state.set_state(FBTaskStates.waiting_for_fb_uid_cookie)
    else:
        await state.set_state(FBTaskStates.waiting_for_fb_uid_2fa)

@router.message(FBTaskStates.waiting_for_fb_uid_2fa)
@router.message(FBTaskStates.waiting_for_fb_uid_cookie)
async def process_fb_uid(message: Message, state: FSMContext, bot: Bot):
    if message.text in ["Cancel ❌", "কাজ 💼", "Balance 💰", "Report 📊", "Admin Panel ⚙️", "Back 🔙"]:
        if message.text == "Cancel ❌" or message.text == "Back 🔙":
            data = await state.get_data()
            if 'task_msg_id' in data:
                try:
                    await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
                except Exception:
                    pass
            await state.clear()
            return await message.answer("❌ Task cancelled.", reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))
        return
        
    uid = message.text.strip()
    if not uid.isdigit():
        return await message.answer("⚠️ UID শুধুমাত্র সংখ্যা (Number) হতে হবে। দয়া করে সঠিক UID দিন:")
        
    existing_task = await db.tasks.find_one({'name': uid, 'task_type': {'$in': ['fb_2fa', 'fb_cookie']}, 'status': {'$in': ['pending', 'accepted']}})
    if existing_task:
        return await message.answer("⚠️ এই UID টি ইতিমধ্যে সাবমিট করা হয়েছে। দয়া করে অন্য UID দিন:")
        
    await state.update_data(fb_uid=uid)
    
    current_state = await state.get_state()
    if current_state == FBTaskStates.waiting_for_fb_uid_cookie.state:
        await message.answer("✅ UID Received. Now send me the Cookies data:", reply_markup=get_cancel_keyboard())
        await state.set_state(FBTaskStates.waiting_for_fb_cookie)
    else:
        await message.answer("✅ UID Received. Now send me the 2FA Key:", reply_markup=get_cancel_keyboard())
        await state.set_state(FBTaskStates.waiting_for_fb_2fa_key)

@router.message(FBTaskStates.waiting_for_fb_cookie)
async def process_fb_cookie(message: Message, state: FSMContext, bot: Bot):
    if message.text in ["Cancel ❌", "কাজ 💼", "Balance 💰", "Report 📊", "Admin Panel ⚙️", "Back 🔙"]:
        if message.text == "Cancel ❌" or message.text == "Back 🔙":
            data = await state.get_data()
            if 'task_msg_id' in data:
                try:
                    await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
                except Exception:
                    pass
            await state.clear()
            return await message.answer("❌ Task cancelled.", reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))
        return
        
    cookie_data = message.text.strip()
    if not cookie_data:
        return await message.answer("⚠️ Please enter valid Cookies data.")
        
    await state.update_data(fb_cookie_data=cookie_data)
    await message.answer("✅ Cookies Received. Click below to submit.", reply_markup=get_submit_keyboard())
    await state.set_state(FBTaskStates.waiting_for_fb_submit)

@router.message(FBTaskStates.waiting_for_fb_2fa_key)
async def process_fb_2fa_key(message: Message, state: FSMContext, bot: Bot):
    if message.text in ["Cancel ❌", "কাজ 💼", "Balance 💰", "Report 📊", "Admin Panel ⚙️", "Back 🔙"]:
        if message.text == "Cancel ❌" or message.text == "Back 🔙":
            data = await state.get_data()
            if 'task_msg_id' in data:
                try:
                    await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
                except Exception:
                    pass
            await state.clear()
            return await message.answer("❌ Task cancelled.", reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))
        return
        
    clean_key = message.text.replace(" ", "").upper()
    if len(clean_key) < 16 or not all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567=" for c in clean_key):
        return await message.answer("⚠️ আপনার 2FA Key-টি সঠিক নয়! এটি একটি সঠিক Base32 কোড হতে হবে।")
        
    existing = await db.tasks.find_one({'two_fa_key': clean_key, 'task_type': 'fb_2fa'})
    if existing:
        return await message.answer("⚠️ Duplicate 2FA Key পাওয়া গেছে! এই Key-টি আগে কেউ সাবমিট করেছে। ফেক কাজ করলে অ্যাকাউন্ট ব্যান করা হবে!")
        
    otp = generate_2fa_code(clean_key)
    if not otp: return await message.answer("⚠️ Invalid 2FA Key. কোড জেনারেট করা সম্ভব হয়নি।")
    
    await state.update_data(two_fa_key=clean_key, two_fa_code=otp)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Refresh Code 🔄", callback_data="refresh_2fa_code", style="success")]])
    await message.answer(f"✅ Key সফলভাবে গ্রহণ করা হয়েছে।\n\n🔐 **2FA Code:** `{otp}`", reply_markup=kb, parse_mode="Markdown")
    
    await message.answer("Facebook-এ এই কোডটি বসিয়ে নিচে থেকে Complete and Submit বাটনে ক্লিক করুন।", reply_markup=get_submit_keyboard())
    await state.set_state(FBTaskStates.waiting_for_fb_submit)

@router.message(F.text == "Complete and Submit 📥", FBTaskStates.waiting_for_fb_submit)
async def submit_fb_task(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    task_type = data.get('task_type')
    uid = data.get('fb_uid')
    password = data.get('fb_password')
    
    if task_type == 'fb_cookie':
        two_fa_key = data.get('fb_cookie_data') # store cookies in two_fa_key column for now or we could add a new column
        two_fa_code = ""
    else:
        two_fa_key = data.get('two_fa_key')
        two_fa_code = data.get('two_fa_code')
        
    await add_task(message.from_user.id, uid, password, two_fa_key, two_fa_code, task_type)
    
    if 'task_msg_id' in data:
        try:
            await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
        except Exception:
            pass
            
    await state.clear()
    
    pending_count = await get_pending_tasks_count(message.from_user.id)
    accepted_count = await get_completed_tasks_count(message.from_user.id)
    
    text = (
        f"🎉 **Task Submitted Successfully!**\n\n"
        f"📊 **আপনার কাজের রিপোর্ট:**\n"
        f"⏳ **পেন্ডিং কাজ:** `{pending_count}` টি\n"
        f"✅ **অ্যাপ্রুভড কাজ:** `{accepted_count}` টি\n\n"
        f"নতুন কাজ শুরু করতে নিচের বাটনে ক্লিক করুন 👇"
    )
    
    insta_price = await get_setting('task_price')
    fb_2fa_price = await get_setting('fb_2fa_price')
    fb_cookie_price = await get_setting('fb_cookie_price')
    await message.answer(text, reply_markup=await get_task_submenu_keyboard(insta_price, fb_2fa_price, fb_cookie_price), parse_mode="Markdown")

@router.message(F.text.in_({"Back 🔙", "Cancel ❌", "Back to Main Menu 🔙"}), StateFilter("*"))
async def handle_cancel_main(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if 'task_msg_id' in data:
        try:
            await bot.delete_message(chat_id=message.from_user.id, message_id=data['task_msg_id'])
        except Exception:
            pass
    current_state = await state.get_state()
    await state.clear()
    
    if current_state and current_state.startswith("AdminSettingsStates"):
        await message.answer("⚙️ Settings Menu", reply_markup=get_settings_keyboard())
    elif current_state and current_state.startswith("Admin"):
        await message.answer("⚙️ Admin Panel", reply_markup=get_admin_panel_keyboard(message.from_user.id))
    else:
        await message.answer("🏠 Main Menu", reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))

# ---------------------------------------------------------
# ADMIN HANDLERS
# ---------------------------------------------------------
@router.message(F.text == "Admin Panel ⚙️", StateFilter("*"))
async def admin_panel(message: Message, state: FSMContext):
    await state.clear()
    if is_admin(message.from_user.id):
        total_users = await db.users.count_documents({})
        date_str_insta = await get_system_date('insta_2fa')
        date_str_fb = await get_system_date('fb_2fa')
        date_str_cookie = await get_system_date('fb_cookie')
        
        todays_tasks = (
            await db.tasks.count_documents({'date': date_str_insta, 'task_type': 'insta_2fa'}) +
            await db.tasks.count_documents({'date': date_str_fb, 'task_type': 'fb_2fa'}) +
            await db.tasks.count_documents({'date': date_str_cookie, 'task_type': 'fb_cookie'})
        )
        
        # Approximate unique users today by checking across the 3 dates
        unique_users_today = len(await db.tasks.distinct('tg_id', {'$or': [
            {'date': date_str_insta, 'task_type': 'insta_2fa'},
            {'date': date_str_fb, 'task_type': 'fb_2fa'},
            {'date': date_str_cookie, 'task_type': 'fb_cookie'}
        ]}))
        
        total_pending = await db.tasks.count_documents({'status': 'pending'})
        total_pending_withdraws = await db.withdraws.count_documents({'status': 'pending'})
        
        # Get details
        pending_insta = await db.tasks.count_documents({'status': 'pending', 'task_type': 'insta_2fa'})
        pending_fb_2fa = await db.tasks.count_documents({'status': 'pending', 'task_type': 'fb_2fa'})
        pending_fb_cookie = await db.tasks.count_documents({'status': 'pending', 'task_type': 'fb_cookie'})
        
        today_insta = await db.tasks.count_documents({'date': date_str_insta, 'task_type': 'insta_2fa'})
        today_fb_2fa = await db.tasks.count_documents({'date': date_str_fb, 'task_type': 'fb_2fa'})
        today_fb_cookie = await db.tasks.count_documents({'date': date_str_cookie, 'task_type': 'fb_cookie'})
        
        text = (
            f"⚙️ **Admin Panel**\n\n"
            f"👥 **Total Users:** `{total_users}`\n"
            f"👤 **Users Submitted Today:** `{unique_users_today}`\n\n"
            f"📊 **Today's Submits ({todays_tasks}):**\n"
            f" ├ Insta 2FA: `{today_insta}`\n"
            f" ├ FB 2FA: `{today_fb_2fa}`\n"
            f" └ FB Cookie: `{today_fb_cookie}`\n\n"
            f"⏳ **Pending Verification ({total_pending}):**\n"
            f" ├ Insta 2FA: `{pending_insta}`\n"
            f" ├ FB 2FA: `{pending_fb_2fa}`\n"
            f" └ FB Cookie: `{pending_fb_cookie}`\n\n"
            f"💳 **Pending Withdrawals:** `{total_pending_withdraws}`"
        )
        await message.answer(text, reply_markup=get_admin_panel_keyboard(message.from_user.id), parse_mode="Markdown")

@router.message(F.text == "Back to Admin Panel 🔙", StateFilter("*"))
async def back_to_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("⚙️ Admin Panel", reply_markup=get_admin_panel_keyboard(message.from_user.id))

async def _get_filtered_pending_withdraws(filter_type):
    """Get pending withdrawals filtered by date. filter_type: all/today/yesterday/older"""
    now = datetime.utcnow() + timedelta(hours=6)
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    
    pending_list = []
    cursor = db.withdraws.find({'status': 'pending'}).sort('date', -1)
    async for doc in cursor:
        if filter_type == 'all':
            pending_list.append(doc)
        else:
            doc_date = doc.get('date', '')[:10]  # "2026-07-10 12:30:00" -> "2026-07-10"
            if filter_type == 'today' and doc_date == today_str:
                pending_list.append(doc)
            elif filter_type == 'yesterday' and doc_date == yesterday_str:
                pending_list.append(doc)
            elif filter_type == 'older' and doc_date < yesterday_str:
                pending_list.append(doc)
    return pending_list

async def _get_pending_counts():
    """Get counts of pending withdrawals by date category"""
    now = datetime.utcnow() + timedelta(hours=6)
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    
    total = 0
    today_count = 0
    yesterday_count = 0
    older_count = 0
    
    cursor = db.withdraws.find({'status': 'pending'})
    async for doc in cursor:
        total += 1
        doc_date = doc.get('date', '')[:10]
        if doc_date == today_str:
            today_count += 1
        elif doc_date == yesterday_str:
            yesterday_count += 1
        else:
            older_count += 1
    
    return total, today_count, yesterday_count, older_count

async def _build_pwd_card(doc, page, total, filter_type, bot):
    """Build a pending withdraw card message text and keyboard"""
    req_id = str(doc['_id'])
    tg_id = doc['tg_id']
    method = doc.get('method', 'N/A')
    details = doc.get('details', 'N/A')
    amount = doc.get('amount', 0)
    requested_amount = doc.get('requested_amount', amount)
    fee = doc.get('fee', 0)
    date = doc.get('date', 'N/A')
    
    try:
        user_chat = await bot.get_chat(tg_id)
        user_name = user_chat.full_name
    except:
        user_name = "Unknown"
    
    filter_labels = {'all': '📋 All', 'today': '📅 Today', 'yesterday': '📆 Yesterday', 'older': '📁 Older'}
    filter_label = filter_labels.get(filter_type, '📋 All')
    
    text = (
        f"📋 **Pending Withdraw [{page+1}/{total}]**\n"
        f"🏷️ Filter: **{filter_label}**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 **User:** [{user_name}](tg://user?id={tg_id})\n"
        f"🆔 **ID:** `{tg_id}`\n"
        f"💳 **Method:** {method}\n"
        f"📱 **Number:** `{details}`\n"
        f"💰 **Requested:** `{requested_amount} TK`\n"
        f"➖ **Fee:** `{fee} TK`\n"
        f"💵 **To Send:** `{amount} TK`\n"
        f"📅 **Date:** `{date}`\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text=f"⬅️ Prev", callback_data=f"pwdnav_{page-1}_{filter_type}"))
    if page < total - 1:
        nav_buttons.append(InlineKeyboardButton(text=f"➡️ Next", callback_data=f"pwdnav_{page+1}_{filter_type}"))
    
    kb_rows = [
        [InlineKeyboardButton(text="✅ Approve", callback_data=f"pwd_approve_{req_id}_{tg_id}_{filter_type}"),
         InlineKeyboardButton(text="❌ Reject", callback_data=f"pwd_reject_{req_id}_{tg_id}_{filter_type}")]
    ]
    if nav_buttons:
        kb_rows.append(nav_buttons)
    kb_rows.append([InlineKeyboardButton(text="🔄 Refresh", callback_data=f"pwd_refresh_{filter_type}"),
                    InlineKeyboardButton(text="🔙 Filter Menu", callback_data="pwd_menu")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    return text, kb

@router.message(F.text == "Pending Withdraws 💳", StateFilter("*"))
async def pending_withdraws_panel(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    if not (is_admin(message.from_user.id, 'full_control') or str(message.from_user.id) == str(ADMIN_ID)):
        return
    
    total, today_count, yesterday_count, older_count = await _get_pending_counts()
    
    if total == 0:
        return await message.answer(
            "✅ কোনো pending withdrawal নেই।",
            reply_markup=get_admin_panel_keyboard(message.from_user.id)
        )
    
    text = (
        f"💳 **Pending Withdrawals**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **মোট Pending:** `{total}` টি\n"
        f"📅 **আজকের:** `{today_count}` টি\n"
        f"📆 **গতকালের:** `{yesterday_count}` টি\n"
        f"📁 **পুরোনো:** `{older_count}` টি\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"কোন গুলো দেখতে চান? নিচে সিলেক্ট করুন:"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 All Pending ({total})", callback_data="pwdfilter_all")],
        [InlineKeyboardButton(text=f"📅 Today ({today_count})", callback_data="pwdfilter_today"),
         InlineKeyboardButton(text=f"📆 Yesterday ({yesterday_count})", callback_data="pwdfilter_yesterday")],
        [InlineKeyboardButton(text=f"📁 Older ({older_count})", callback_data="pwdfilter_older")]
    ])
    
    await message.answer(text, reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "pwd_menu", StateFilter("*"))
async def pending_withdraw_menu(callback: CallbackQuery, bot: Bot):
    total, today_count, yesterday_count, older_count = await _get_pending_counts()
    
    if total == 0:
        return await callback.message.edit_text("✅ কোনো pending withdrawal নেই।")
    
    text = (
        f"💳 **Pending Withdrawals**\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 **মোট Pending:** `{total}` টি\n"
        f"📅 **আজকের:** `{today_count}` টি\n"
        f"📆 **গতকালের:** `{yesterday_count}` টি\n"
        f"📁 **পুরোনো:** `{older_count}` টি\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"কোন গুলো দেখতে চান? নিচে সিলেক্ট করুন:"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📋 All Pending ({total})", callback_data="pwdfilter_all")],
        [InlineKeyboardButton(text=f"📅 Today ({today_count})", callback_data="pwdfilter_today"),
         InlineKeyboardButton(text=f"📆 Yesterday ({yesterday_count})", callback_data="pwdfilter_yesterday")],
        [InlineKeyboardButton(text=f"📁 Older ({older_count})", callback_data="pwdfilter_older")]
    ])
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except:
        await callback.answer("No changes.")

@router.callback_query(F.data.startswith("pwdfilter_"), StateFilter("*"))
async def pending_withdraw_filter(callback: CallbackQuery, bot: Bot):
    filter_type = callback.data.split("_")[1]  # all/today/yesterday/older
    
    pending_list = await _get_filtered_pending_withdraws(filter_type)
    total = len(pending_list)
    
    filter_labels = {'all': 'All', 'today': 'আজকের', 'yesterday': 'গতকালের', 'older': 'পুরোনো'}
    
    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Filter Menu", callback_data="pwd_menu")]
        ])
        return await callback.message.edit_text(
            f"✅ {filter_labels.get(filter_type, '')} কোনো pending withdrawal নেই।",
            reply_markup=kb
        )
    
    doc = pending_list[0]
    text, kb = await _build_pwd_card(doc, 0, total, filter_type, bot)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except:
        await callback.answer("No changes.")

@router.callback_query(F.data.startswith("pwdnav_"))
async def pending_withdraw_navigate(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split("_")
    page = int(parts[1])
    filter_type = parts[2] if len(parts) > 2 else 'all'
    
    pending_list = await _get_filtered_pending_withdraws(filter_type)
    total = len(pending_list)
    
    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Filter Menu", callback_data="pwd_menu")]
        ])
        return await callback.message.edit_text("✅ কোনো pending withdrawal নেই।", reply_markup=kb)
    
    if page >= total:
        page = 0
    
    doc = pending_list[page]
    text, kb = await _build_pwd_card(doc, page, total, filter_type, bot)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except:
        await callback.answer("No changes.")

@router.callback_query(F.data.startswith("pwd_refresh_"), StateFilter("*"))
async def pending_withdraw_refresh(callback: CallbackQuery, bot: Bot):
    filter_type = callback.data.split("_")[2] if len(callback.data.split("_")) > 2 else 'all'
    
    pending_list = await _get_filtered_pending_withdraws(filter_type)
    total = len(pending_list)
    
    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Filter Menu", callback_data="pwd_menu")]
        ])
        return await callback.message.edit_text("✅ কোনো pending withdrawal নেই।", reply_markup=kb)
    
    doc = pending_list[0]
    text, kb = await _build_pwd_card(doc, 0, total, filter_type, bot)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    except:
        await callback.answer("No changes.")

@router.callback_query(F.data.startswith("pwd_approve_") | F.data.startswith("pwd_reject_"), StateFilter("*"))
async def process_pending_withdraw_panel_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_")
    action = parts[1]  # approve or reject
    req_id = parts[2]
    user_id = parts[3]
    filter_type = parts[4] if len(parts) > 4 else 'all'
    
    if action == "reject":
        await update_withdraw_status(req_id, "rejected")
        req = await get_withdraw_request(req_id)
        if req:
            await update_balance(int(user_id), req[4])
        
        await callback.answer("❌ Rejected & Refunded!", show_alert=True)
        try:
            await bot.send_message(chat_id=user_id, text="❌ আপনার Withdraw Request প্রত্যাখ্যান করা হয়েছে। টাকা ফেরত দেওয়া হয়েছে।")
        except:
            pass
        
        # Refresh the filtered list
        pending_list = await _get_filtered_pending_withdraws(filter_type)
        total = len(pending_list)
        
        if total == 0:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Filter Menu", callback_data="pwd_menu")]
            ])
            return await callback.message.edit_text("✅ সব withdrawal process করা হয়েছে। কোনো pending নেই।", reply_markup=kb)
        
        doc = pending_list[0]
        text, kb = await _build_pwd_card(doc, 0, total, filter_type, bot)
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        
    elif action == "approve":
        await state.update_data(approve_req_id=req_id, approve_user_id=user_id)
        await callback.message.edit_reply_markup(reply_markup=None)
        
        req = await get_withdraw_request(req_id)
        number = req[3] if req else "Unknown"
        amount = req[4] if req else "Unknown"
        method = req[2] if req else "Unknown"
        
        try:
            user_chat = await bot.get_chat(user_id)
            user_name = user_chat.full_name
            user_name = user_name.replace('<', '&lt;').replace('>', '&gt;')
        except:
            user_name = "Unknown"
            
        await callback.message.answer(
            f"✅ <b>Approve Selected!</b>\n\n"
            f"👤 <b>User:</b> {user_name} (ID: <code>{user_id}</code>)\n"
            f"💳 <b>Method:</b> {method}\n"
            f"📱 <b>Number:</b> <code>{number}</code>\n"
            f"💰 <b>Amount to Send:</b> <code>{amount} TK</code>\n\n"
            f"📸 অনুগ্রহ করে উপরের নাম্বারে টাকা পাঠিয়ে এখানে <b>পেমেন্ট স্ক্রিনশট</b> আপলোড করুন।",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_for_withdraw_screenshot)

@router.message(F.text == "Leaderboard 🏆", StateFilter("*"))
async def worker_leaderboard(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    if not is_admin(message.from_user.id, 'stats'): return
    
    await message.answer("⏳ লিডারবোর্ড লোড হচ্ছে...")
    
    # Aggregate top 20 workers by accepted tasks
    pipeline = [
        {'$match': {'status': 'accepted'}},
        {'$group': {'_id': '$tg_id', 'total_tasks': {'$sum': 1}}},
        {'$sort': {'total_tasks': -1}},
        {'$limit': 20}
    ]
    
    top_workers = []
    async for doc in db.tasks.aggregate(pipeline):
        top_workers.append(doc)
    
    if not top_workers:
        return await message.answer("❌ কোনো approved task নেই।", reply_markup=get_admin_panel_keyboard(message.from_user.id))
    
    # Also get today's stats
    date_str_insta = await get_system_date('insta_2fa')
    date_str_fb = await get_system_date('fb_2fa')
    date_str_cookie = await get_system_date('fb_cookie')
    
    pipeline_today = [
        {'$match': {'status': 'accepted', '$or': [
            {'task_type': 'insta_2fa', 'date': date_str_insta},
            {'task_type': 'fb_2fa', 'date': date_str_fb},
            {'task_type': 'fb_cookie', 'date': date_str_cookie}
        ]}},
        {'$group': {'_id': '$tg_id', 'today_tasks': {'$sum': 1}}},
        {'$sort': {'today_tasks': -1}},
        {'$limit': 20}
    ]
    today_data = {}
    async for doc in db.tasks.aggregate(pipeline_today):
        today_data[doc['_id']] = doc['today_tasks']
    
    medals = ['🥇', '🥈', '🥉']
    lines = []
    
    for i, worker in enumerate(top_workers):
        tg_id = worker['_id']
        total = worker['total_tasks']
        today = today_data.get(tg_id, 0)
        
        # Get user info
        try:
            chat = await bot.get_chat(tg_id)
            name = chat.first_name or 'Unknown'
            name = name.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
            uname = f"@{chat.username}" if chat.username else f"<code>{tg_id}</code>"
        except:
            name = 'Unknown'
            uname = f"<code>{tg_id}</code>"
        
        rank = medals[i] if i < 3 else f"<code>{i+1}.</code>"
        today_str = f" ┃ আজ: <code>{today}</code>" if today > 0 else ""
        lines.append(f"{rank} {name} ({uname})\n   ┗ মোট: <b>{total}</b> টি{today_str}")
    
    total_accepted = await db.tasks.count_documents({'status': 'accepted'})
    total_workers = len(await db.tasks.distinct('tg_id', {'status': 'accepted'}))
    
    text = (
        f"🏆 <b>Worker Leaderboard (Top 20)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        + "\n\n".join(lines)
        + f"\n\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>সর্বমোট:</b> <code>{total_accepted}</code> approved tasks\n"
        f"👥 <b>মোট Workers:</b> <code>{total_workers}</code> জন"
    )
    
    await message.answer(text, reply_markup=get_admin_panel_keyboard(message.from_user.id), parse_mode="HTML")

@router.message(F.text == "Export Data 📁")
async def export_data(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'export'): return
    
    date_str_insta = await get_system_date('insta_2fa')
    date_str_fb = await get_system_date('fb_2fa')
    date_str_cookie = await get_system_date('fb_cookie')
    
    # Get counts for each type
    insta_today = await db.tasks.count_documents({'date': date_str_insta, 'task_type': 'insta_2fa'})
    fb2fa_today = await db.tasks.count_documents({'date': date_str_fb, 'task_type': 'fb_2fa'})
    fbcookie_today = await db.tasks.count_documents({'date': date_str_cookie, 'task_type': 'fb_cookie'})
    
    insta_all = await db.tasks.count_documents({'task_type': 'insta_2fa'})
    fb2fa_all = await db.tasks.count_documents({'task_type': 'fb_2fa'})
    fbcookie_all = await db.tasks.count_documents({'task_type': 'fb_cookie'})
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📸 Insta 2FA (আজ: {insta_today} | সব: {insta_all})", callback_data="exp_type_insta_2fa", style="primary")],
        [InlineKeyboardButton(text=f"🌐 FB 2FA (আজ: {fb2fa_today} | সব: {fb2fa_all})", callback_data="exp_type_fb_2fa", style="primary")],
        [InlineKeyboardButton(text=f"🍪 FB Cookie (আজ: {fbcookie_today} | সব: {fbcookie_all})", callback_data="exp_type_fb_cookie", style="primary")],
        [InlineKeyboardButton(text="📦 সব Export (আজকের)", callback_data="exp_type_all_today", style="success")],
        [InlineKeyboardButton(text="❌ বাতিল", callback_data="exp_cancel", style="danger")]
    ])
    
    await message.answer(
        f"📁 **Export Data**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 **আজকের তারিখসমূহ:**\n"
        f"  • Insta 2FA: `{date_str_insta}`\n"
        f"  • FB 2FA: `{date_str_fb}`\n"
        f"  • FB Cookie: `{date_str_cookie}`\n\n"
        f"কোন টাইপের ডেটা export করতে চান? 👇",
        reply_markup=kb, parse_mode="Markdown"
    )

@router.callback_query(F.data == "exp_cancel", StateFilter("*"))
async def exp_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Export বাতিল করা হয়েছে।")
    await callback.answer()

@router.callback_query(F.data == "exp_type_all_today", StateFilter("*"))
async def exp_all_today(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id, 'export'): return
    await callback.answer("⏳ সব export হচ্ছে...")
    
    types = ['insta_2fa', 'fb_2fa', 'fb_cookie']
    sent_any = False
    
    for t in types:
        date_str = await get_system_date(t)
        file_path, count = await export_tasks_xlsx(date_str, t)
        if file_path and count > 0 and os.path.exists(file_path):
            await callback.message.answer_document(
                FSInputFile(file_path), 
                caption=f"📊 Export: {date_str}\n📌 Type: {t}\n📋 Total: {count} accounts"
            )
            sent_any = True
            
    if not sent_any:
        await callback.message.answer("❌ আজকের জন্য কোনো ডেটা নেই।")
    else:
        await callback.message.answer("✅ সব export সম্পন্ন!")
    
    await callback.message.delete()

@router.callback_query(F.data.startswith("exp_type_"), StateFilter("*"))
async def exp_select_type(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id, 'export'): return
    
    task_type = callback.data.replace("exp_type_", "")
    type_labels = {'insta_2fa': '📸 Insta 2FA', 'fb_2fa': '🌐 FB 2FA', 'fb_cookie': '🍪 FB Cookie'}
    label = type_labels.get(task_type, task_type)
    
    date_str = await get_system_date(task_type)
    # For date history buttons, we need logical today
    logical_today = datetime.strptime(date_str, "%Y-%m-%d")
    
    # Get counts
    today_count = await db.tasks.count_documents({'date': date_str, 'task_type': task_type})
    all_count = await db.tasks.count_documents({'task_type': task_type})
    
    # Get last 7 days with counts
    date_buttons = []
    for i in range(7):
        d = (logical_today - timedelta(days=i)).strftime("%Y-%m-%d")
        c = await db.tasks.count_documents({'date': d, 'task_type': task_type})
        if c > 0 or i == 0:
            day_label = "আজ" if i == 0 else ("গতকাল" if i == 1 else d)
            date_buttons.append(
                [InlineKeyboardButton(text=f"📅 {day_label} ({c} টি)", callback_data=f"exp_do_{task_type}_{d}", style="primary")]
            )
    
    kb_rows = []
    kb_rows.extend(date_buttons)
    if all_count > 0:
        kb_rows.append([InlineKeyboardButton(text=f"📦 সব তারিখ ({all_count} টি)", callback_data=f"exp_do_{task_type}_all", style="success")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Back", callback_data="exp_back", style="danger")])
    
    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    
    await callback.message.edit_text(
        f"📁 **{label} Export**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"কোন তারিখের ডেটা export করতে চান? 👇",
        reply_markup=kb, parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data == "exp_back", StateFilter("*"))
async def exp_back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Go back to export type selection"""
    if not is_admin(callback.from_user.id, 'export'): return
    
    date_str_insta = await get_system_date('insta_2fa')
    date_str_fb = await get_system_date('fb_2fa')
    date_str_cookie = await get_system_date('fb_cookie')
    
    insta_today = await db.tasks.count_documents({'date': date_str_insta, 'task_type': 'insta_2fa'})
    fb2fa_today = await db.tasks.count_documents({'date': date_str_fb, 'task_type': 'fb_2fa'})
    fbcookie_today = await db.tasks.count_documents({'date': date_str_cookie, 'task_type': 'fb_cookie'})
    
    insta_all = await db.tasks.count_documents({'task_type': 'insta_2fa'})
    fb2fa_all = await db.tasks.count_documents({'task_type': 'fb_2fa'})
    fbcookie_all = await db.tasks.count_documents({'task_type': 'fb_cookie'})
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📸 Insta 2FA (আজ: {insta_today} | সব: {insta_all})", callback_data="exp_type_insta_2fa", style="primary")],
        [InlineKeyboardButton(text=f"🌐 FB 2FA (আজ: {fb2fa_today} | সব: {fb2fa_all})", callback_data="exp_type_fb_2fa", style="primary")],
        [InlineKeyboardButton(text=f"🍪 FB Cookie (আজ: {fbcookie_today} | সব: {fbcookie_all})", callback_data="exp_type_fb_cookie", style="primary")],
        [InlineKeyboardButton(text="📦 সব Export (আজকের)", callback_data="exp_type_all_today", style="success")],
        [InlineKeyboardButton(text="❌ বাতিল", callback_data="exp_cancel", style="danger")]
    ])
    
    await callback.message.edit_text(
        f"📁 **Export Data**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 **আজকের তারিখসমূহ:**\n"
        f"  • Insta 2FA: `{date_str_insta}`\n"
        f"  • FB 2FA: `{date_str_fb}`\n"
        f"  • FB Cookie: `{date_str_cookie}`\n\n"
        f"কোন টাইপের ডেটা export করতে চান? 👇",
        reply_markup=kb, parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("exp_do_"), StateFilter("*"))
async def exp_do_export(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id, 'export'): return
    
    # Parse: exp_do_{task_type}_{date}
    parts = callback.data.replace("exp_do_", "").rsplit("_", 1)
    if len(parts) == 2:
        # Handle task types with underscores (e.g., insta_2fa, fb_2fa, fb_cookie)
        date_part = parts[1]
        type_part = callback.data.replace("exp_do_", "").replace(f"_{date_part}", "", 1)
    else:
        await callback.answer("❌ Error parsing request")
        return
    
    # For 'all' date, the date_part could be 'all' or a date like '2026-07-07'
    # But since task_type has underscores, we need smarter parsing
    # Format: exp_do_insta_2fa_2026-07-07 or exp_do_insta_2fa_all
    # Let's parse from the known task types
    task_type = None
    date_str = None
    remaining = callback.data.replace("exp_do_", "")
    
    for tt in ['insta_2fa', 'fb_2fa', 'fb_cookie']:
        if remaining.startswith(tt + "_"):
            task_type = tt
            date_str = remaining[len(tt) + 1:]
            break
    
    if not task_type or not date_str:
        await callback.answer("❌ Error")
        return
    
    await callback.answer("⏳ Export হচ্ছে...")
    
    type_labels = {'insta_2fa': '📸 Insta 2FA', 'fb_2fa': '🌐 FB 2FA', 'fb_cookie': '🍪 FB Cookie'}
    label = type_labels.get(task_type, task_type)
    date_label = "সব তারিখ" if date_str == "all" else date_str
    
    file_path, count = await export_tasks_xlsx(date_str, task_type)
    
    if file_path and count > 0 and os.path.exists(file_path):
        await callback.message.answer_document(
            FSInputFile(file_path),
            caption=f"📊 **Export Complete**\n\n📌 Type: {label}\n📅 Date: {date_label}\n📋 Total: {count} accounts",
            parse_mode="Markdown"
        )
        await callback.message.edit_text(f"✅ {label} — {date_label} — {count} টি account export হয়েছে!")
    else:
        await callback.message.edit_text(f"❌ {label} — {date_label} — কোনো ডেটা পাওয়া যায়নি।")

@router.message(F.text == "Verify Accounts ✅")
async def start_verify(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'verify'): return
    await message.answer("Select verification action:", reply_markup=get_verify_action_keyboard())

@router.callback_query(F.data.startswith("verify_action_"))
async def process_verify_action(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[2]
    await state.update_data(verify_action=action)
    await callback.message.delete()
    
    if action == "both":
        prompt = "Send a `.csv` file where Column 1 = OK IDs, Column 2 = BAD IDs."
    elif action == "approve":
        prompt = "✅ **Approve Accounts**\n\nSend a `.txt` file OR directly paste the usernames here (one per line):"
    elif action == "auto":
        prompt = "🔄 **Approve OK & Auto Reject Rest**\n\nSend a `.txt` file OR directly paste the **OK** usernames here (one per line).\n\n⚠️ **WARNING:** ALL other pending tasks will be automatically REJECTED!"
    else:
        prompt = "❌ **Reject Accounts**\n\nSend a `.txt` file OR directly paste the usernames here (one per line):"
        
    await callback.message.answer(prompt, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AdminVerifyStates.waiting_for_input)

@router.message(AdminVerifyStates.waiting_for_input)
async def process_verify_input(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    action = data.get('verify_action', 'both')
    
    valid_usernames = []
    bad_usernames = []
    
    if message.document:
        file_name = message.document.file_name
        if not (file_name.endswith('.txt') or file_name.endswith('.csv')):
            return await message.answer("⚠️ Please send a .txt or .csv file.")
            
        file_path = (await bot.get_file(message.document.file_id)).file_path
        download_path = f"temp_{message.from_user.id}_{file_name}"
        await bot.download_file(file_path, download_path)
        
        try:
            if file_name.endswith('.csv'):
                import csv
                with open(download_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if not row: continue
                        if row[0].strip().lower() in ['ok_ids', 'ok', 'good', 'username']: continue
                        
                        if action == "both":
                            if len(row) > 0 and row[0].strip():
                                valid_usernames.append(row[0].strip())
                            if len(row) > 1 and row[1].strip():
                                bad_usernames.append(row[1].strip())
                        elif action == "approve":
                            if len(row) > 0 and row[0].strip():
                                valid_usernames.append(row[0].strip())
                        elif action == "reject":
                            if len(row) > 0 and row[0].strip():
                                bad_usernames.append(row[0].strip())
            else:
                with open(download_path, 'r', encoding='utf-8') as f:
                    lines = [line.strip() for line in f if line.strip()]
                    if action == "approve" or action == "auto":
                        valid_usernames = lines
                    elif action == "reject":
                        bad_usernames = lines
                    else:
                        valid_usernames = lines
        finally:
            if os.path.exists(download_path): os.remove(download_path)
            
    elif message.text:
        if action == "both":
            await state.clear()
            return await message.answer("⚠️ For 'Both', you must upload a CSV file with 2 columns.")
            
        lines = [line.strip() for line in message.text.split('\n') if line.strip()]
        if action == "approve" or action == "auto":
            valid_usernames = lines
        elif action == "reject":
            bad_usernames = lines
            
    else:
        await state.clear()
        return await message.answer("⚠️ Please send text or a file.")
        
    try:
        accepted = await verify_and_credit_accounts(valid_usernames, bot) if valid_usernames else 0
        rejected = 0
        
        if action == "auto":
            rejected = await reject_all_pending_accounts(bot)
        elif bad_usernames:
            rejected = await reject_accounts(bad_usernames, bot)
            
        await message.answer(f"✅ {accepted} accounts credited.\n❌ {rejected} accounts rejected.", reply_markup=get_main_keyboard(is_admin(message.from_user.id), await get_user_lang(message.from_user.id)))
    except Exception as e:
        await message.answer(f"❌ Error: {e}")
        
    await state.clear()


@router.message(F.text == "Manage Admins 👥", StateFilter("*"))
async def manage_admins(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'full_control'): return
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Add Admin âž•", style="primary"), KeyboardButton(text="Remove Admin âž–", style="danger")],
        [KeyboardButton(text="View Admins 👁️", style="primary"), KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)
    await message.answer("👥 **Admin Management**", reply_markup=kb, parse_mode="Markdown")

@router.message(F.text == "View Admins 👁️", StateFilter("*"))
async def view_admins(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    admins = await db.admins.find().to_list(length=100)
    if not admins:
        return await message.answer("No additional admins found. (Only you are the primary admin)")
    
    text = "👥 **Current Admins:**\n\n"
    for admin in admins:
        perms = admin.get('permissions', [])
        if isinstance(perms, list):
            perms_str = ", ".join(perms)
        else:
            perms_str = str(perms)
        tg_id = admin.get('tg_id', 'Unknown')
        if str(tg_id).isdigit():
            text += f"Profile: [{tg_id}](tg://user?id={tg_id})\nPerms: {perms_str}\n\n"
        else:
            text += f"ID: `{tg_id}`\nPerms: {perms_str}\n\n"
    await message.answer(text, parse_mode="Markdown")

@router.message(F.text == "Add Admin âž•", StateFilter("*"))
async def add_admin_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    await message.answer("Send the Telegram ID of the new admin:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminManagementStates.waiting_for_admin_id)

@router.message(AdminManagementStates.waiting_for_admin_id)
async def process_add_admin_id(message: Message, state: FSMContext, bot: Bot):
    tg_id = message.text.strip()
    
    if not tg_id.isdigit():
        if not tg_id.startswith('@'):
            tg_id = '@' + tg_id
        try:
            chat = await bot.get_chat(tg_id)
            tg_id = str(chat.id)
            await message.answer(f"✅ Username resolved to ID: `{tg_id}`", parse_mode="Markdown")
        except Exception:
            return await message.answer("⚠️ Username not found or bot hasn't interacted with them. Please use their numeric ID.")

    await state.update_data(new_admin_id=tg_id, new_admin_perms=[])
    await message.answer("Select permissions:", reply_markup=get_permissions_keyboard([]))
    await state.set_state(AdminManagementStates.waiting_for_admin_permissions)

@router.callback_query(F.data.startswith("toggle_perm_"), AdminManagementStates.waiting_for_admin_permissions)
async def toggle_admin_perm(callback: CallbackQuery, state: FSMContext):
    perm = callback.data.replace("toggle_perm_", "")
    data = await state.get_data()
    perms = data.get('new_admin_perms', [])
    if perm in perms:
        perms.remove(perm)
    else:
        perms.append(perm)
    await state.update_data(new_admin_perms=perms)
    await callback.message.edit_reply_markup(reply_markup=get_permissions_keyboard(perms))
    await callback.answer()

@router.callback_query(F.data == "save_admin_perms", AdminManagementStates.waiting_for_admin_permissions)
async def save_admin_perms(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get('new_admin_id')
    perms = data.get('new_admin_perms', [])
    await db.admins.update_one({'tg_id': tg_id}, {'$set': {'permissions': perms}}, upsert=True)
    ADMIN_PERMISSIONS[str(tg_id)] = perms
    await callback.answer("✅ Admin Saved!", show_alert=False)
    await callback.message.edit_text(f"✅ Admin `{tg_id}` saved with perms: {', '.join(perms) if perms else 'None'}", parse_mode="Markdown")
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Add Admin âž•", style="primary"), KeyboardButton(text="Remove Admin âž–", style="danger")],
        [KeyboardButton(text="View Admins 👁️", style="primary"), KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)
    await callback.message.answer("👥 Admin Saved.", reply_markup=kb)
    await state.clear()

@router.message(F.text == "Remove Admin âž–", StateFilter("*"))
async def remove_admin_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    await message.answer("Send the Telegram ID of the admin to remove:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminManagementStates.waiting_for_remove_admin_id)

@router.message(AdminManagementStates.waiting_for_remove_admin_id)
async def process_remove_admin_id(message: Message, state: FSMContext):
    tg_id = message.text.strip()
    await db.admins.delete_one({'tg_id': tg_id})
    if str(tg_id) in ADMIN_PERMISSIONS:
        del ADMIN_PERMISSIONS[str(tg_id)]
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Add Admin âž•", style="primary"), KeyboardButton(text="Remove Admin âž–", style="danger")],
        [KeyboardButton(text="View Admins 👁️", style="primary"), KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)
    await message.answer(f"✅ Admin `{tg_id}` removed.", reply_markup=kb, parse_mode="Markdown")
    await state.clear()

@router.message(F.text == "Leave Admin 🚪", StateFilter("*"))
async def leave_admin_role(message: Message, state: FSMContext):
    await state.clear()
    tg_id_str = str(message.from_user.id)
    if tg_id_str == str(ADMIN_ID):
        return await message.answer("⚠️ You are the primary admin. You cannot leave the admin role.")
    
    if tg_id_str not in ADMIN_PERMISSIONS:
        return await message.answer("You are not an admin.")
        
    await db.admins.delete_one({'tg_id': tg_id_str})
    if tg_id_str in ADMIN_PERMISSIONS:
        del ADMIN_PERMISSIONS[tg_id_str]
        
    await message.answer("✅ You have successfully resigned from your Admin role.", reply_markup=get_main_keyboard(False, await get_user_lang(message.from_user.id)))


@router.message(F.text == "Toggle Join Bonus 🎁", StateFilter("*"))
async def toggle_join_bonus(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_setting('join_bonus_status') or 'off'
    new_st = 'on' if current == 'off' else 'off'
    await db.settings.update_one({'key': 'join_bonus_status'}, {'$set': {'value': new_st}}, upsert=True)
    await message.answer(f"✅ Join Bonus is now **{'ON' if new_st=='on' else 'OFF'}**.", parse_mode="Markdown")

@router.message(F.text == "Toggle Refer Income 👥", StateFilter("*"))
async def toggle_refer_income(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_setting('refer_income_status') or 'on'
    new_st = 'off' if current == 'on' else 'on'
    await update_setting('refer_income_status', new_st)
    status_text = '🟢 **ON**' if new_st == 'on' else '🔴 **OFF**'
    await message.answer(f"✅ Refer Income is now {status_text}.", parse_mode="Markdown")

@router.message(F.text == "Change Join Bonus 💰", StateFilter("*"))
async def change_join_bonus_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_setting('join_bonus_amount') or 0
    await message.answer(f"Current Join Bonus Amount: `{current}` à§³\n\nSend the new amount for Join Bonus (e.g. 5):", reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AdminSettingsStates.waiting_for_join_bonus_amount)

@router.message(AdminSettingsStates.waiting_for_join_bonus_amount)
async def process_join_bonus_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    try:
        new_amt = float(message.text.strip())
        if new_amt < 0: raise ValueError
        await db.settings.update_one({'key': 'join_bonus_amount'}, {'$set': {'value': new_amt}}, upsert=True)
        await message.answer(f"✅ Join Bonus Amount successfully updated to `{new_amt}` ৳.", reply_markup=get_settings_keyboard(), parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await message.answer("⚠️ Please enter a valid number (e.g., 5 or 5.5). Try again:")

@router.message(F.text == "Settings ⚙️")
async def admin_settings(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id): return
    insta_price = await get_setting('task_price')
    fb_2fa_price = await get_setting('fb_2fa_price')
    fb_cookie_price = await get_setting('fb_cookie_price')
    pw_mode = await get_setting('password_mode')
    min_wd = await get_setting('min_withdraw_amount')
    wd_status = await get_setting('withdraw_status')
    text = f"⚙️ **Settings:**\n💵 Insta Price: `{insta_price} TK`\n💵 FB 2FA Price: `{fb_2fa_price} TK`\n💵 FB Cookie Price: `{fb_cookie_price} TK`\n💰 Min WD: `{min_wd} TK`\n💳 WD: `{wd_status.upper()}`\n🔐 Mode: `{pw_mode}`"
    await message.answer(text, reply_markup=get_settings_keyboard(), parse_mode="Markdown")

@router.message(F.text == "Change Insta Price 💵")
async def change_price_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new Insta price:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_new_price)

@router.message(AdminStates.waiting_for_new_price)
async def process_new_price(message: Message, state: FSMContext):
    try:
        float(message.text)
    except ValueError:
        return await message.answer("⚠️ Number only.")
    await update_setting('task_price', message.text)
    await message.answer("✅ Updated!", reply_markup=get_settings_keyboard())
    await state.clear()

class AdminFBStates(StatesGroup):
    waiting_for_fb_2fa_price = State()
    waiting_for_fb_cookie_price = State()

@router.message(F.text == "Change FB 2FA Price 💵")
async def change_fb_2fa_price_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new FB 2FA price:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminFBStates.waiting_for_fb_2fa_price)

@router.message(AdminFBStates.waiting_for_fb_2fa_price)
async def process_fb_2fa_price(message: Message, state: FSMContext):
    try:
        float(message.text)
    except ValueError:
        return await message.answer("⚠️ Number only.")
    await update_setting('fb_2fa_price', message.text)
    await message.answer("✅ Updated!", reply_markup=get_settings_keyboard())
    await state.clear()

@router.message(F.text == "Change FB Cookie Price 💵")
async def change_fb_cookie_price_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new FB Cookie price:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminFBStates.waiting_for_fb_cookie_price)

@router.message(AdminFBStates.waiting_for_fb_cookie_price)
async def process_fb_cookie_price(message: Message, state: FSMContext):
    try:
        float(message.text)
    except ValueError:
        return await message.answer("⚠️ Number only.")
    await update_setting('fb_cookie_price', message.text)
    await message.answer("✅ Updated!", reply_markup=get_settings_keyboard())
    await state.clear()



@router.message(F.text == "Change Refer % 🎁")
async def change_refer_percent(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new referral reward percentage (e.g. 10 for 10%):", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminSettingsStates.waiting_for_refer_percent)

@router.message(AdminSettingsStates.waiting_for_refer_percent)
async def process_refer_percent(message: Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Number only.")
    await update_setting('refer_percentage', message.text)
    await message.answer("✅ Updated!", reply_markup=get_settings_keyboard())
    await state.clear()




async def parse_and_save_time(message: Message, state: FSMContext, key_name: str, label: str):
    time_str = message.text.strip().upper()
    # Clean up: remove extra spaces, dots to colons
    time_str = time_str.replace(".", ":").replace(" ", "")
    parsed_time = None
    
    # Try multiple formats
    formats_to_try = []
    if "AM" in time_str or "PM" in time_str:
        formats_to_try = ["%I:%M%p", "%I%p", "%I:%M %p", "%I %p"]
    else:
        formats_to_try = ["%H:%M", "%H"]
    
    for fmt in formats_to_try:
        try:
            parsed_time = datetime.strptime(time_str, fmt)
            break
        except ValueError:
            continue
            
    if parsed_time:
        db_time = parsed_time.strftime("%H:%M")
        await update_setting(key_name, db_time)
        display_time = parsed_time.strftime("%I:%M %p")
        await message.answer(
            f"✅ **{label}** সময় সেট হয়েছে: **{display_time}** (BD Time)\n\n"
            f"📌 প্রতিদিন এই সময়ে auto export হবে।",
            reply_markup=get_settings_keyboard(), parse_mode="Markdown"
        )
        await state.clear()
    else:
        await message.answer(
            "⚠️ **ভুল ফরম্যাট!**\n\n"
            "✅ সঠিক উদাহরণ:\n"
            "• `8PM` বা `8 PM`\n"
            "• `8:30 PM` বা `8:30PM`\n"
            "• `20:00` (24hr ফরম্যাট)\n\n"
            "আবার চেষ্টা করুন:", parse_mode="Markdown"
        )

async def get_current_export_time_display(key_name):
    """Get current export time in 12-hour AM/PM format"""
    time_val = await get_setting(key_name)
    if not time_val:
        return "Not Set"
    try:
        t = datetime.strptime(time_val, "%H:%M")
        return t.strftime("%I:%M %p")
    except:
        return time_val

@router.message(F.text == "Auto Export (Insta) 🕒")
async def set_export_insta(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_current_export_time_display('auto_export_time')
    await message.answer(
        f"🕒 **Insta 2FA Auto Export Time**\n\n"
        f"📌 বর্তমান সময়: **{current}**\n\n"
        f"নতুন সময় লিখুন:\n"
        f"• `8PM` / `8:30 PM` (12hr)\n"
        f"• `20:00` (24hr)",
        reply_markup=get_cancel_keyboard(), parse_mode="Markdown"
    )
    await state.set_state(AdminExportStates.waiting_for_auto_export_insta)

@router.message(AdminExportStates.waiting_for_auto_export_insta)
async def process_export_insta(message: Message, state: FSMContext):
    await parse_and_save_time(message, state, 'auto_export_time', 'Insta 2FA Export')

@router.message(F.text == "Auto Export (FB 2FA) 🕒")
async def set_export_fb_2fa(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_current_export_time_display('auto_export_time_fb_2fa')
    await message.answer(
        f"🕒 **FB 2FA Auto Export Time**\n\n"
        f"📌 বর্তমান সময়: **{current}**\n\n"
        f"নতুন সময় লিখুন:\n"
        f"• `9PM` / `9:30 PM` (12hr)\n"
        f"• `21:00` (24hr)",
        reply_markup=get_cancel_keyboard(), parse_mode="Markdown"
    )
    await state.set_state(AdminExportStates.waiting_for_auto_export_fb_2fa)

@router.message(AdminExportStates.waiting_for_auto_export_fb_2fa)
async def process_export_fb_2fa(message: Message, state: FSMContext):
    await parse_and_save_time(message, state, 'auto_export_time_fb_2fa', 'FB 2FA Export')

@router.message(F.text == "Auto Export (Cookie) 🕒")
async def set_export_fb_cookie(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_current_export_time_display('auto_export_time_fb_cookie')
    await message.answer(
        f"🕒 **FB Cookie Auto Export Time**\n\n"
        f"📌 বর্তমান সময়: **{current}**\n\n"
        f"নতুন সময় লিখুন:\n"
        f"• `10PM` / `10:30 PM` (12hr)\n"
        f"• `22:00` (24hr)",
        reply_markup=get_cancel_keyboard(), parse_mode="Markdown"
    )
    await state.set_state(AdminExportStates.waiting_for_auto_export_fb_cookie)

@router.message(AdminExportStates.waiting_for_auto_export_fb_cookie)
async def process_export_fb_cookie(message: Message, state: FSMContext):
    await parse_and_save_time(message, state, 'auto_export_time_fb_cookie', 'FB Cookie Export')


@router.message(F.text == "Set Support Link 🎧")
async def set_support_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Please enter the support username (e.g., @admin_support or https://t.me/admin_support):", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminSettingsStates.waiting_for_support_link)

@router.message(AdminSettingsStates.waiting_for_support_link)
async def process_support_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    link = message.text.strip()
    if link.startswith('@'):
        link = f"https://t.me/{link[1:]}"
    await update_setting('support_link', link)
    await state.clear()
    await message.answer("🎧 Support Link Updated!", reply_markup=get_settings_keyboard())



@router.message(F.text == "Set Date 📅")
async def set_manual_date_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    
    current_insta = await get_setting('manual_date_override_insta_2fa')
    current_fb2fa = await get_setting('manual_date_override_fb_2fa')
    current_cookie = await get_setting('manual_date_override_fb_cookie')
    current_global = await get_setting('manual_date_override')
    
    status = f"Global: `{current_global}`\nInsta 2FA: `{current_insta}`\nFB 2FA: `{current_fb2fa}`\nFB Cookie: `{current_cookie}`"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Insta 2FA", callback_data="set_date_type_insta_2fa", style="primary"), InlineKeyboardButton(text="🌐 FB 2FA", callback_data="set_date_type_fb_2fa", style="primary")],
        [InlineKeyboardButton(text="🍪 FB Cookie", callback_data="set_date_type_fb_cookie", style="primary"), InlineKeyboardButton(text="📦 Global/All", callback_data="set_date_type_global", style="success")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="set_date_cancel", style="danger")]
    ])
    
    await message.answer(
        f"📅 **Set System Date**\n\n"
        f"**Current Dates:**\n{status}\n\n"
        f"Select which task type you want to set the date for 👇",
        reply_markup=kb, parse_mode="Markdown"
    )

@router.callback_query(F.data.startswith("set_date_type_"))
async def process_set_date_type(callback: CallbackQuery, state: FSMContext):
    type_map = {
        'set_date_type_insta_2fa': ('insta_2fa', 'Insta 2FA'),
        'set_date_type_fb_2fa': ('fb_2fa', 'FB 2FA'),
        'set_date_type_fb_cookie': ('fb_cookie', 'FB Cookie'),
        'set_date_type_global': ('global', 'Global/All')
    }
    
    task_key, task_name = type_map[callback.data]
    await state.update_data(target_date_type=task_key)
    
    await callback.message.edit_text(
        f"📅 **Set Date for {task_name}**\n\n"
        f"Enter the new date in exactly `YYYY-MM-DD` format (e.g., `2026-07-15`).",
        parse_mode="Markdown"
    )
    await state.set_state(AdminSettingsStates.waiting_for_manual_date)
    await callback.answer()

@router.callback_query(F.data == "set_date_cancel")
async def set_date_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await state.clear()
    await callback.answer()

@router.message(AdminSettingsStates.waiting_for_manual_date)
async def process_manual_date(message: Message, state: FSMContext):
    data = await state.get_data()
    target = data.get('target_date_type', 'global')
    
    try:
        # validate format
        datetime.strptime(message.text.strip(), "%Y-%m-%d")
        
        db_key = 'manual_date_override' if target == 'global' else f'manual_date_override_{target}'
        await update_setting(db_key, message.text.strip())
        
        await message.answer(f"✅ Date for `{target}` set to `{message.text.strip()}`.", reply_markup=get_settings_keyboard(), parse_mode="Markdown")
        await state.clear()
    except ValueError:
        await message.answer("❌ Invalid format. Please use exactly `YYYY-MM-DD` (e.g., `2026-07-15`).", parse_mode="Markdown")


@router.message(F.text == "Set Payout Group 📢")
async def set_payout_channel(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_setting('payout_group_id')
    await message.answer(f"Enter the Payout Group/Channel ID (e.g., -100123456789) where payment proofs will be posted.\nCurrent: `{current}`", reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AdminSettingsStates.waiting_for_payout_channel)

@router.message(AdminSettingsStates.waiting_for_payout_channel)
async def process_payout_channel(message: Message, bot: Bot, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    payout_id = message.text.strip()
    
    if payout_id.lower() in ['none', '0', 'off', 'disable', 'cancel', 'no']:
        await update_setting('payout_group_id', '')
        await message.answer("✅ Payout Group Disabled!", reply_markup=get_settings_keyboard())
        await state.clear()
        return

    try:
        # Check if bot can access the group
        chat = await bot.get_chat(payout_id)
        if not chat.username:
            # Need to export link if no username, also checks admin rights
            await bot.export_chat_invite_link(payout_id)
        await update_setting('payout_group_id', payout_id)
        await message.answer("✅ Payout Group Updated successfully! Users can now join it.", reply_markup=get_settings_keyboard())
        await state.clear()
    except Exception as e:
        await message.answer(f"⚠️ **Error:** Could not verify Payout Group.\n\nMake sure the bot is an **Admin** in the group/channel and the ID is correct. Error details: `{e}`\n\nPlease try again or type `0` to disable.", parse_mode="Markdown")
@router.message(F.text == "Set Channel ID 🆔")
async def change_channel_id(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Please enter the new Channel ID (e.g., -100123456789 or @username):", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminSettingsStates.waiting_for_channel_id)

@router.message(AdminSettingsStates.waiting_for_channel_id)
async def process_channel_id(message: Message, bot: Bot, state: FSMContext):
    channel_id = message.text.strip()
    
    if channel_id.lower() in ['none', '0', 'off', 'disable', 'cancel', 'no']:
        await update_setting('channel_id', '')
        await message.answer("✅ Channel Requirement Disabled!", reply_markup=get_settings_keyboard())
        await state.clear()
        return

    try:
        chat = await bot.get_chat(channel_id)
        if not chat.username:
            await bot.export_chat_invite_link(channel_id)
        await update_setting('channel_id', channel_id)
        await message.answer("✅ Channel ID Updated successfully!", reply_markup=get_settings_keyboard())
        await state.clear()
    except Exception as e:
        await message.answer(f"⚠️ **Error:** Could not verify Channel.\n\nMake sure the bot is an **Admin** in the channel and the ID is correct. Error details: `{e}`\n\nPlease try again or type `0` to disable.", parse_mode="Markdown")

@router.message(F.text == "Set Channel Link 📢")
async def change_channel_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Please enter the new Channel Link (e.g., https://t.me/yourchannel):", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminSettingsStates.waiting_for_channel_link)

@router.message(AdminSettingsStates.waiting_for_channel_link)
async def process_channel_link(message: Message, state: FSMContext):
    await update_setting('channel_link', message.text.strip())
    await message.answer("✅ Channel Link Updated!", reply_markup=get_settings_keyboard())
    await state.clear()

async def get_admin_tutorial_task_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Insta 2FA", callback_data="admin_tut_insta_2fa", style="primary")],
        [InlineKeyboardButton(text="🌐 FB 2FA", callback_data="admin_tut_fb_2fa", style="primary")],
        [InlineKeyboardButton(text="🍪 FB Cookie", callback_data="admin_tut_fb_cookie", style="primary")]
    ])

@router.message(F.text == "Set Tutorial 🎥")
async def set_tutorial_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Select a task to manage tutorials:", reply_markup=await get_admin_tutorial_task_keyboard())

@router.callback_query(F.data.startswith("admin_tut_"))
async def admin_tut_manage(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback.from_user.id, 'settings'): return
    if callback.data == "admin_tut_back":
        return await callback.message.edit_text("Select a task to manage tutorials:", reply_markup=await get_admin_tutorial_task_keyboard())
    
    task_key = callback.data.replace("admin_tut_", "")
    
    cursor = db.tutorials.find({'task_type': task_key}).sort('order', 1)
    items = await cursor.to_list(length=100)
    
    text = f"<b>Tutorials for {task_key}</b>\n\n"
    if not items:
        text += "No tutorials set yet.\n"
    
    kb_buttons = []
    for idx, item in enumerate(items):
        text += f"{idx+1}. Type: {item['msg_type']}\n"
        kb_buttons.append([InlineKeyboardButton(text=f"\U0001f5d1 Delete {idx+1}", callback_data=f"del_tut_{item['_id']}", style="danger")])
        
    kb_buttons.append([InlineKeyboardButton(text="\u2795 Add New Message", callback_data=f"add_tut_{task_key}", style="success")])
    kb_buttons.append([InlineKeyboardButton(text="\U0001f519 Back", callback_data="admin_tut_back", style="primary")])
    
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons), parse_mode="HTML")

@router.callback_query(F.data.startswith("del_tut_"))
async def del_tut_item(callback: CallbackQuery, state: FSMContext, bot: Bot):
    if not is_admin(callback.from_user.id, 'settings'): return
    tut_id = callback.data.replace("del_tut_", "")
    from bson import ObjectId
    await db.tutorials.delete_one({'_id': ObjectId(tut_id)})
    await callback.answer("\u2705 Tutorial deleted!", show_alert=True)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await bot.send_message(chat_id=callback.from_user.id, text="Select a task to manage tutorials:", reply_markup=await get_admin_tutorial_task_keyboard())

@router.callback_query(F.data.startswith("add_tut_"))
async def add_tut_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id, 'settings'): return
    task_key = callback.data.replace("add_tut_", "")
    await state.update_data(tut_task_key=task_key)
    await state.set_state(AdminSettingsStates.waiting_for_tutorial_msg)
    await callback.message.answer("Send the tutorial message (Text, Photo, or Video).", reply_markup=get_cancel_keyboard())

@router.message(AdminSettingsStates.waiting_for_tutorial_msg, F.content_type.in_({ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO}))
async def process_tutorial_msg(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    data = await state.get_data()
    task_key = data.get('tut_task_key')
    
    msg_type = ''
    content = ''
    caption = ''
    if message.text:
        msg_type = 'text'
        content = message.text
    elif message.photo:
        msg_type = 'photo'
        content = message.photo[-1].file_id
        caption = message.caption or ''
    elif message.video:
        msg_type = 'video'
        content = message.video.file_id
        caption = message.caption or ''
        
    order = await db.tutorials.count_documents({'task_type': task_key})
    
    await db.tutorials.insert_one({
        'task_type': task_key,
        'msg_type': msg_type,
        'content': content,
        'caption': caption,
        'order': order
    })
    
    await state.clear()
    await message.answer("✅ Tutorial message added!", reply_markup=get_settings_keyboard())

@router.message(F.text == "Change Min Withdraw 💰")
async def change_min_wd_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new min withdraw:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_min_withdraw)

@router.message(AdminStates.waiting_for_min_withdraw)
async def process_new_min_wd(message: Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ Number only.")
    await update_setting('min_withdraw_amount', message.text)
    await message.answer("✅ Updated!", reply_markup=get_settings_keyboard())
    await state.clear()

@router.message(F.text == "Change Binance Rate 💱")
async def change_binance_rate_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new Binance rate (e.g. 122):", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_binance_rate)

@router.message(AdminStates.waiting_for_binance_rate)
async def process_new_binance_rate(message: Message, state: FSMContext):
    try:
        float(message.text)
    except ValueError:
        return await message.answer("⚠️ Number only.")
    await update_setting('binance_rate', message.text)
    await message.answer("✅ Binance Rate Updated!", reply_markup=get_settings_keyboard())
    await state.clear()

@router.message(F.text == "Toggle Insta Screenshot 📸")
async def toggle_insta_screenshot(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    current = await get_setting('insta_screenshot_status') or 'on'
    new_status = 'off' if current == 'on' else 'on'
    await update_setting('insta_screenshot_status', new_status)
    await message.answer(f"✅ Insta Screenshot requirement is now **{new_status.upper()}**")

@router.message(F.text == "Toggle Tasks 📝")
async def toggle_tasks_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Select a task to toggle ON/OFF:", reply_markup=await get_tasks_toggle_inline())

@router.callback_query(F.data.startswith("toggle_tsk_"))
async def process_toggle_task(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id, 'settings'): return
    task_key = callback.data.replace("toggle_tsk_", "")
    db_key = f"task_status_{task_key}"
    current = await get_setting(db_key) or 'on'
    new_status = 'off' if current == 'on' else 'on'
    await update_setting(db_key, new_status)
    await callback.message.edit_text("Select a task to toggle ON/OFF:", reply_markup=await get_tasks_toggle_inline())

async def get_payment_methods_toggle_inline():
    bkash_status = await get_setting('pm_bkash') or 'on'
    nagad_status = await get_setting('pm_nagad') or 'on'
    rocket_status = await get_setting('pm_rocket') or 'on'
    binance_status = await get_setting('pm_binance') or 'on'
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"bKash: {'🟢 ON' if bkash_status == 'on' else '🔴 OFF'}", callback_data="toggle_pm_bkash", style="primary")],
        [InlineKeyboardButton(text=f"Nagad: {'🟢 ON' if nagad_status == 'on' else '🔴 OFF'}", callback_data="toggle_pm_nagad", style="primary")],
        [InlineKeyboardButton(text=f"Rocket: {'🟢 ON' if rocket_status == 'on' else '🔴 OFF'}", callback_data="toggle_pm_rocket", style="primary")],
        [InlineKeyboardButton(text=f"Binance: {'🟢 ON' if binance_status == 'on' else '🔴 OFF'}", callback_data="toggle_pm_binance", style="primary")]
    ])

@router.message(F.text == "Toggle Payment Methods 💳")
async def toggle_pm_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Select a payment method to toggle ON/OFF:", reply_markup=await get_payment_methods_toggle_inline())

@router.callback_query(F.data.startswith("toggle_pm_"))
async def process_toggle_pm(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id, 'settings'): return
    pm_key = callback.data.replace("toggle_", "") # e.g. pm_bkash
    current = await get_setting(pm_key) or 'on'
    new_status = 'off' if current == 'on' else 'on'
    await update_setting(pm_key, new_status)
    await callback.message.edit_text("Select a payment method to toggle ON/OFF:", reply_markup=await get_payment_methods_toggle_inline())

@router.message(F.text == "Toggle Withdraw 💳")
async def toggle_withdraw(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    new_status = 'off' if await get_setting('withdraw_status') == 'on' else 'on'
    await update_setting('withdraw_status', new_status)
    await message.answer(f"✅ Withdrawals are **{new_status.upper()}**")

@router.message(F.text == "Toggle W/D Force Join 📢")
async def toggle_withdraw_force_join(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    new_status = 'off' if await get_setting('withdraw_force_join') == 'on' else 'on'
    await update_setting('withdraw_force_join', new_status)
    await message.answer(f"✅ Withdraw Force Join is **{new_status.upper()}**")

@router.message(F.text == "Set Password Mode 🔐")
async def set_pw_mode(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'settings'): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Insta 2FA", callback_data="pw_task_insta_2fa")],
        [InlineKeyboardButton(text="🌐 FB 2FA", callback_data="pw_task_fb_2fa")],
        [InlineKeyboardButton(text="🍪 FB Cookie", callback_data="pw_task_fb_cookie")]
    ])
    await message.answer("কোন টাস্কের Password Mode সেট করতে চান?", reply_markup=kb)

@router.callback_query(F.data.startswith("pw_task_"))
async def pw_task_select(callback: CallbackQuery, state: FSMContext):
    task_type = callback.data.replace("pw_task_", "")
    await state.update_data(pw_task_type=task_type)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Random (Hard)", callback_data="set_pw_random")],
        [InlineKeyboardButton(text="Fixed", callback_data="set_pw_fixed")]
    ])
    await callback.message.edit_text(f"Select Mode for `{task_type}`:", reply_markup=kb)

@router.callback_query(F.data.startswith("set_pw_"))
async def process_pw_mode(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    task_type = data.get('pw_task_type', 'insta_2fa')
    mode = callback.data.split("_")[2]
    
    await update_setting(f'password_mode_{task_type}', mode)
    
    if mode == "random":
        await callback.message.edit_text(f"✅ `{task_type}` password mode set to Random.")
    else:
        await callback.message.delete()
        await callback.message.answer(f"Enter fixed password for `{task_type}`:", reply_markup=get_cancel_keyboard())
        await state.set_state(AdminStates.waiting_for_fixed_password)

@router.message(AdminStates.waiting_for_fixed_password)
async def process_fixed_pw(message: Message, state: FSMContext):
    data = await state.get_data()
    task_type = data.get('pw_task_type', 'insta_2fa')
    
    await update_setting(f'fixed_password_{task_type}', message.text)
    await message.answer(f"✅ Updated fixed password for `{task_type}`!", reply_markup=get_settings_keyboard())
    await state.clear()

@router.message(F.text == "Change Start Msg 📝")
async def change_start_msg(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'settings'): return
    await message.answer("Enter new /start msg:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_start_msg)

@router.message(AdminStates.waiting_for_start_msg)
async def process_start_msg(message: Message, state: FSMContext):
    await update_setting('start_message', message.text)
    await message.answer("✅ Updated!", reply_markup=get_settings_keyboard())
    await state.clear()

@router.callback_query(F.data.startswith("wd_approve_") | F.data.startswith("wd_reject_"), StateFilter("*"))
async def process_withdraw_action(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer()
    parts = callback.data.split("_")
    action, req_id, user_id = parts[1], parts[2], parts[3]
    
    if action == "reject":
        await update_withdraw_status(req_id, "rejected")
        req = await get_withdraw_request(req_id)
        if req: await update_balance(int(user_id), req[4])
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.reply("❌ Rejected and refunded.")
        try: await bot.send_message(chat_id=user_id, text="❌ Withdraw rejected, refunded.")
        except: pass
    elif action == "approve":
        await state.update_data(approve_req_id=req_id, approve_user_id=user_id)
        await callback.message.delete()
        
        req = await get_withdraw_request(req_id)
        number = req[3] if req else "Unknown"
        amount = req[4] if req else "Unknown"
        method = req[2] if req else "Unknown"
        
        try:
            user_chat = await bot.get_chat(user_id)
            user_name = user_chat.full_name
            user_name = user_name.replace('<', '&lt;').replace('>', '&gt;')
        except:
            user_name = "Unknown"
            
        await callback.message.answer(
            f"✅ <b>Approve Selected!</b>\n\n"
            f"👤 <b>User:</b> {user_name} (ID: <code>{user_id}</code>)\n"
            f"💳 <b>Method:</b> {method}\n"
            f"📱 <b>Number:</b> <code>{number}</code>\n"
            f"💰 <b>Amount to Send:</b> <code>{amount} TK</code>\n\n"
            f"📸 অনুগ্রহ করে উপরের নাম্বারে টাকা পাঠিয়ে এখানে <b>পেমেন্ট স্ক্রিনশট</b> আপলোড করুন।",
            reply_markup=get_cancel_keyboard(),
            parse_mode="HTML"
        )
        await state.set_state(AdminStates.waiting_for_withdraw_screenshot)

@router.message(AdminStates.waiting_for_withdraw_screenshot, F.photo)
async def process_withdraw_screenshot(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    req_id, user_id = data['approve_req_id'], data['approve_user_id']
    await update_withdraw_status(req_id, "approved")
    req = await get_withdraw_request(req_id)
    amount = req[4] if req else "Unknown"
    
    user_doc = await db.users.find_one({'tg_id': int(user_id)})
    try:
        user_chat = await bot.get_chat(user_id)
        full_name = user_chat.full_name
    except:
        full_name = 'Unknown'
    balance = user_doc.get('balance', 0) if user_doc else 0
    
    withdraw_count = await db.withdraws.count_documents({'tg_id': int(user_id), 'status': 'approved'})
    
    cursor = db.tasks.aggregate([
        {'$match': {'tg_id': int(user_id), 'status': 'accepted'}},
        {'$group': {'_id': '$task_type', 'count': {'$sum': 1}}}
    ])
    task_counts = {}
    async for doc in cursor:
        task_counts[doc['_id']] = doc['count']
        
    task_text = ""
    for t_type, t_count in task_counts.items():
        if not t_type or t_type == 'insta_2fa': task_text += f"- Insta 2FA: {t_count}\n"
        elif t_type == 'fb_2fa': task_text += f"- FB 2FA: {t_count}\n"
        elif t_type == 'fb_cookie': task_text += f"- FB Cookie: {t_count}\n"
        else: task_text += f"- {t_type}: {t_count}\n"
    if not task_text: task_text = "N/A\n"
        
    number = req[3] if req else "Unknown"
    method = req[2] if req else "Unknown"
    masked_number = number[:3] + "*****" + number[-2:] if len(number) > 5 else "*****"

    user_caption = (
        f"✅ <b>Payment Complete!</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📱 <b>Sent Number:</b> <code>{number}</code>\n"
        f"💸 <b>Amount Received:</b> <code>{amount} TK</code>\n"
        f"💰 <b>Current Balance:</b> <code>{balance} TK</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>Keep working and earning!</i>"
    )
    
    bot_username = (await bot.me()).username
    share_msg = "💸 আমি কাজ করে পেমেন্ট পেলাম!\n\nআপনিও চাইলে খুব সহজেই প্রতিদিন **Instagram 2FA Account** জমা দিয়ে পেমেন্ট নিতে পারবেন 💯\n\n👇 এখনই জয়েন করুন এবং কাজ শুরু করুন:"
    encoded_text = __import__('urllib').parse.quote(share_msg)
    share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}?start={user_id}&text={encoded_text}"
    start_work_url = f"https://t.me/{bot_username}?start=work"
    
    user_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Share with your friends 🔗", url=share_url)],
        [InlineKeyboardButton(text="Start Work 💼", url=start_work_url)]
    ])
    
    group_caption = (
        f"🎉 <b>New Withdrawal Successful!</b> 🎉\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Name:</b> <code>{full_name}</code>\n"
        f"💰 <b>Total Amount:</b> <code>{amount} BDT</code>\n"
        f"💳 <b>Method:</b> <code>{method}</code>\n"
        f"📱 <b>Number:</b> <code>{masked_number}</code>\n\n"
        f"📊 <b>Tasks Done:</b>\n"
        f"{task_text}\n"
        f"🏆 <b>Total Withdrawals:</b> <code>{withdraw_count} times</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔥 <i>Keep working and earning!</i>"
    )
    group_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Start Work 💼", url=start_work_url)],
        [InlineKeyboardButton(text="Refer and Earn 💸", url=f"https://t.me/{bot_username}?start=ref")]
    ])
    
    try:
        await bot.send_photo(chat_id=user_id, photo=message.photo[-1].file_id, caption=user_caption, reply_markup=user_kb, parse_mode="HTML")
        await message.answer("✅ Screenshot sent to user.")
        
        payout_group_id = await get_setting('payout_group_id')
        if payout_group_id:
            try:
                await bot.send_photo(chat_id=payout_group_id, photo=message.photo[-1].file_id, caption=group_caption, reply_markup=group_kb, parse_mode="HTML")
                await message.answer("✅ Forwarded to Payout Group.")
            except Exception as e:
                await message.answer(f"⚠️ Failed to forward to Payout Group: {e}")
                
    except Exception as e:
        await message.answer(f"❌ Failed: {e}")
        
    await state.clear()
    
    # Auto-show next pending withdraw
    pending_list = await _get_filtered_pending_withdraws('all')
    total = len(pending_list)
    if total > 0:
        doc = pending_list[0]
        text, kb = await _build_pwd_card(doc, 0, total, 'all', bot)
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await message.answer("✅ সব Withdrawal সম্পূর্ণ করা হয়েছে! আর কোনো pending নেই।")

@router.message(F.text == "Broadcast 📢")
async def start_broadcast(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'broadcast'): return
    users = await get_all_users()
    await message.answer(f"📢 **Broadcast**\nTotal Users: {len(users)}\nSend message/media.", reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AdminStates.waiting_for_broadcast_msg)

@router.message(AdminStates.waiting_for_broadcast_msg)
async def preview_broadcast(message: Message, state: FSMContext):
    await state.update_data(broadcast_msg_id=message.message_id, broadcast_chat_id=message.chat.id)
    await message.reply("Preview. Send it?", reply_markup=get_broadcast_preview_keyboard())

@router.callback_query(F.data == "send_broadcast", AdminStates.waiting_for_broadcast_msg)
async def send_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    msg_id, chat_id = data['broadcast_msg_id'], data['broadcast_chat_id']
    users = await get_all_users()
    
    status_msg = await callback.message.edit_text("🚀 Broadcasting... (0%)")
    
    success = 0
    failed = 0
    total = len(users)
    
    if total == 0:
        await status_msg.edit_text("✅ No users found.")
        await state.clear()
        return

    batch_size = 35
    
    # Helper to send message
    async def send_msg(uid):
        try:
            await bot.copy_message(chat_id=uid, from_chat_id=chat_id, message_id=msg_id)
            return True
        except Exception:
            return False

    for i in range(0, total, batch_size):
        batch = users[i:i + batch_size]
        tasks = [send_msg(uid) for uid in batch]
        results = await asyncio.gather(*tasks)
        
        for res in results:
            if res:
                success += 1
            else:
                failed += 1
        
        # Update progress every 3 batches (~100 users) to avoid edit rate limits
        if (i // batch_size) % 3 == 0:
            percent = int(((i + len(batch)) / total) * 100)
            try:
                await status_msg.edit_text(f"🚀 Broadcasting... ({percent}%)\n🟢 Sent: {success} | 🔴 Failed: {failed}")
            except Exception:
                pass
                
        await asyncio.sleep(1.0)

    await status_msg.edit_text(f"✅ **Completed!**\n🟢 Sent: {success}\n🔴 Failed: {failed}")
    await state.clear()

@router.callback_query(F.data == "cancel_broadcast", AdminStates.waiting_for_broadcast_msg)
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Cancelled.")
    await state.clear()
    await callback.answer()

# ---------------------------------------------------------
# MAIN 
# ---------------------------------------------------------
async def process_export_and_send(bot: Bot, date_str, task_type, export_admins, file_path, count):
    if not file_path or not __import__('os').path.exists(file_path):
        logging.error(f"process_export_and_send: file_path is invalid or doesn't exist: {file_path}")
        return
        
    sent_to = set()
    # Send to buyers
    buyers = await db.buyers.find().to_list(length=100)
    logging.info(f"Auto export: Found {len(buyers)} buyers in database for {task_type}")
    
    for buyer in buyers:
        types = buyer.get('types', [])
        if task_type in types:
            buyer_id = buyer.get('tg_id')
            username = buyer.get('username', 'No Username')
            try:
                support_link = await get_setting('support_link')
                kb = None
                if support_link:
                    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Contact Admin 🎧", url=support_link)]])
                    
                caption = f"📁 Auto Export: {date_str}\n📌 Type: {task_type}\n📊 Total Accounts: {count}"
                await bot.send_document(chat_id=int(buyer_id), document=FSInputFile(file_path), caption=caption, reply_markup=kb)
                sent_to.add(str(buyer_id))
                logging.info(f"Auto export: Sent {task_type} file to buyer {buyer_id} (@{username})")
                
                # Notify Main Admin
                admin_msg = f"✅ <b>File Successfully Sent to Buyer</b>\n\n👤 Buyer: @{username}\n🆔 ID: <code>{buyer_id}</code>\n📌 Type: {task_type}\n📊 Total Accounts: {count}"
                await bot.send_document(chat_id=int(ADMIN_ID), document=FSInputFile(file_path), caption=admin_msg, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Auto export: Failed to send to buyer {buyer_id} (@{username}): {e}")
                try:
                    await bot.send_message(chat_id=int(ADMIN_ID), text=f"⚠️ <b>Auto Export Failed for Buyer</b>\n\n👤 Buyer: @{username}\n🆔 ID: <code>{buyer_id}</code>\n📌 Type: {task_type}\n❌ Error: {str(e)}", parse_mode="HTML")
                except:
                    pass
        else:
            logging.info(f"Auto export: Buyer {buyer.get('tg_id')} skipped - {task_type} not in their types: {types}")
                
    for admin in export_admins:
        if str(admin) in sent_to:
            continue
        try: 
            caption = f"📊 Auto Export for {date_str} ({task_type})\nTotal Accounts: {count}"
            await bot.send_document(chat_id=int(admin), document=FSInputFile(file_path), caption=caption)
        except Exception as e:
            logging.error(f"Auto export: Failed to send to admin {admin}: {e}")

@router.message(F.text == "Manage Buyers 🛍️", StateFilter("*"))
async def manage_buyers(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id, 'full_control'): return
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Add/Edit Buyer ➕✏️", style="primary"), KeyboardButton(text="Remove Buyer ➖", style="danger")],
        [KeyboardButton(text="View Buyers 📋", style="primary"), KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)
    await message.answer("🛍️ **Buyer Management**", reply_markup=kb, parse_mode="Markdown")

@router.message(F.text == "View Buyers 📋", StateFilter("*"))
async def view_buyers(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    try:
        buyers = await db.buyers.find().to_list(length=100)
        if not buyers:
            return await message.answer("No buyers found.")
        
        text = "📋 <b>Current Buyers:</b>\n\n"
        for buyer in buyers:
            types = buyer.get('types', [])
            types_str = ", ".join(types) if isinstance(types, list) else str(types)
            tg_id = buyer.get('tg_id', 'Unknown')
            username = buyer.get('username', 'No Username')
            buyer_info = f"ID: <code>{tg_id}</code>\nUsername: @{username}\nFiles: {types_str}\n\n"
            if len(text) + len(buyer_info) > 4000:
                await message.answer(text, parse_mode="HTML")
                text = ""
            text += buyer_info
        if text:
            await message.answer(text, parse_mode="HTML")
    except Exception as e:
        await message.answer(f"⚠️ Error viewing buyers: {str(e)}")

@router.message(F.text == "Add/Edit Buyer ➕✏️", StateFilter("*"))
async def add_buyer_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    await message.answer("Send the Telegram ID of the buyer:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminBuyerStates.waiting_for_buyer_id)

@router.message(AdminBuyerStates.waiting_for_buyer_id)
async def process_buyer_id(message: Message, state: FSMContext, bot: Bot):
    tg_id = message.text.strip()
    
    if not tg_id.isdigit():
        if not tg_id.startswith('@'):
            tg_id = '@' + tg_id
        try:
            chat = await bot.get_chat(tg_id)
            tg_id = str(chat.id)
            await message.answer(f"✅ Username resolved to ID: `{tg_id}`", parse_mode="Markdown")
        except Exception:
            return await message.answer("⚠️ Username not found. Please use their numeric ID.")
            
    username = "No Username"
    try:
        chat = await bot.get_chat(int(tg_id))
        username = chat.username or chat.first_name or "No Username"
    except Exception:
        pass

    existing = await db.buyers.find_one({'tg_id': tg_id})
    current_types = existing.get('types', []) if existing else []

    await state.update_data(buyer_id_temp=tg_id, buyer_username_temp=username, new_buyer_types=current_types)
    await message.answer(f"Select files for buyer (ID: {tg_id}, Name: {username}):", reply_markup=get_buyer_file_types_keyboard(current_types))
    await state.set_state(AdminBuyerStates.buyer_id_temp)

@router.callback_query(F.data.startswith("toggle_buyer_type_"), AdminBuyerStates.buyer_id_temp)
async def toggle_buyer_type(callback: CallbackQuery, state: FSMContext):
    file_type = callback.data.replace("toggle_buyer_type_", "")
    data = await state.get_data()
    types = data.get('new_buyer_types', [])
    if file_type in types:
        types.remove(file_type)
    else:
        types.append(file_type)
    await state.update_data(new_buyer_types=types)
    await callback.message.edit_reply_markup(reply_markup=get_buyer_file_types_keyboard(types))
    await callback.answer()

@router.callback_query(F.data == "save_buyer", AdminBuyerStates.buyer_id_temp)
async def save_buyer(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_id = data.get('buyer_id_temp')
    username = data.get('buyer_username_temp')
    types = data.get('new_buyer_types', [])
    
    await db.buyers.update_one({'tg_id': tg_id}, {'$set': {'username': username, 'types': types}}, upsert=True)
    
    await callback.answer("✅ Buyer Saved!", show_alert=False)
    await callback.message.edit_text(f"✅ Buyer <code>{tg_id}</code> (@{username}) saved with files: {', '.join(types) if types else 'None'}", parse_mode="HTML")
    
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Add/Edit Buyer ➕✏️", style="primary"), KeyboardButton(text="Remove Buyer ➖", style="danger")],
        [KeyboardButton(text="View Buyers 📋", style="primary"), KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)
    await callback.message.answer("🛍️ Buyer Saved.", reply_markup=kb)
    await state.clear()

@router.message(F.text == "Remove Buyer ➖", StateFilter("*"))
async def remove_buyer_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    await message.answer("Send the Telegram ID of the buyer to remove:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminBuyerStates.waiting_for_remove_buyer_id)

@router.message(AdminBuyerStates.waiting_for_remove_buyer_id)
async def process_remove_buyer(message: Message, state: FSMContext):
    tg_id = message.text.strip()
    await db.buyers.delete_one({'tg_id': tg_id})
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="Add/Edit Buyer ➕✏️", style="primary"), KeyboardButton(text="Remove Buyer ➖", style="danger")],
        [KeyboardButton(text="View Buyers 📋", style="primary"), KeyboardButton(text="Back to Admin Panel 🔙", style="danger")]
    ], resize_keyboard=True)
    await message.answer(f"✅ Buyer `{tg_id}` removed.", reply_markup=kb, parse_mode="Markdown")
    await state.clear()

# ==========================================
# USER MANAGEMENT (ADD BALANCE)
# ==========================================
@router.message(F.text == "Manage Users 👤", StateFilter("*"))
async def manage_users_start(message: Message, state: FSMContext):
    await state.clear()
    if not is_admin(message.from_user.id, 'full_control'): return
    await message.answer("Please send the Telegram ID of the user you want to manage:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminUserStates.waiting_for_user_id)

@router.message(AdminUserStates.waiting_for_user_id)
async def process_user_search(message: Message, state: FSMContext):
    tg_id = message.text.strip()
    if not tg_id.isdigit():
        return await message.answer("⚠️ Invalid ID. Please send a numeric Telegram ID.", reply_markup=get_cancel_keyboard())
    
    user = await get_user(int(tg_id))
    if not user:
        return await message.answer(f"❌ User with ID `{tg_id}` not found. Try another ID or click Cancel.", reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
        
    user_doc = await db.users.find_one({'tg_id': int(tg_id)})
    is_banned = user_doc.get('is_banned', False) if user_doc else False
    status_text = "🚫 Banned" if is_banned else "✅ Active"
        
    await state.update_data(target_user_id=int(tg_id))
    
    full_name = tg_id  # tg_id used as identifier (name not stored in DB)
    balance = user[2]
    
    text = (
        f"👤 **User Found!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 **ID:** `{tg_id}`\n"
        f"📝 **Name:** {full_name}\n"
        f"💰 **Current Balance:** {balance} TK\n"
        f"📊 **Status:** {status_text}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💳 Please enter the amount to **ADD** to their balance (e.g., 50.5):\n"
        f"*(Use a negative number like -50 to deduct)*\n"
        f"*(Type `unban` to unblock this user if banned)*"
    )
    await message.answer(text, reply_markup=get_cancel_keyboard(), parse_mode="Markdown")
    await state.set_state(AdminUserStates.waiting_for_balance_amount)

@router.message(AdminUserStates.waiting_for_balance_amount)
async def process_add_balance(message: Message, state: FSMContext, bot: Bot):
    text_input = message.text.strip().lower()
    data = await state.get_data()
    target_id = data.get('target_user_id')
    
    if text_input == 'unban':
        await db.users.update_one({'tg_id': target_id}, {'$set': {'is_banned': False, 'screenshot_strikes': 0}})
        await state.clear()
        return await message.answer(f"✅ User {target_id} has been successfully unbanned and strikes reset.", reply_markup=get_admin_panel_keyboard(message.from_user.id))

    try:
        amount = float(message.text.strip())
    except ValueError:
        return await message.answer("⚠️ Invalid amount. Please enter a valid number (e.g. 50 or -20), or 'unban'.", reply_markup=get_cancel_keyboard())
        
    user = await get_user(target_id)
    if not user:
        await state.clear()
        return await message.answer("❌ Error: User not found anymore.", reply_markup=get_admin_panel_keyboard(message.from_user.id))
        
    new_balance = user[2] + amount
    await db.users.update_one({'tg_id': target_id}, {'$set': {'balance': new_balance}})
    
    await message.answer(f"✅ Successfully updated balance!\n\n👤 **ID:** `{target_id}`\n💵 **Amount:** {amount} TK\n💰 **New Balance:** {new_balance} TK", reply_markup=get_admin_panel_keyboard(message.from_user.id), parse_mode="Markdown")
    
    try:
        if amount > 0:
            await bot.send_message(target_id, f"🎁 **Bonus Received!**\n\nAdmin has added **{amount} TK** to your account.\n💰 Current Balance: {new_balance} TK", parse_mode="Markdown")
        elif amount < 0:
            await bot.send_message(target_id, f"⚠️ **Balance Adjusted!**\n\nAdmin has deducted **{abs(amount)} TK** from your account.\n💰 Current Balance: {new_balance} TK", parse_mode="Markdown")
    except:
        await message.answer("⚠️ Note: Could not send notification to the user (they might have blocked the bot).")
        
    await state.clear()



async def auto_export_task(bot: Bot):
    last_exported = {'insta_2fa': None, 'fb_2fa': None, 'fb_cookie': None}
    last_late_exported = None
    while True:
        try:
            # Use Bangladesh Time (UTC+6) instead of server time
            now = datetime.utcnow() + timedelta(hours=6)
            current_time = now.strftime("%H:%M")
            date_str = await get_system_date()
            date_str_insta = await get_system_date('insta_2fa')
            date_str_fb = await get_system_date('fb_2fa')
            date_str_cookie = await get_system_date('fb_cookie')
            
            insta_time = await get_setting('auto_export_time')
            fb_2fa_time = await get_setting('auto_export_time_fb_2fa')
            fb_cookie_time = await get_setting('auto_export_time_fb_cookie')
            
            if not insta_time: insta_time = "20:00"
            if not fb_2fa_time: fb_2fa_time = "21:00"
            if not fb_cookie_time: fb_cookie_time = "22:00"
            
            # Send to main admin and any admin with 'export' perm
            export_admins = set([str(ADMIN_ID)])
            for uid_str, perms in ADMIN_PERMISSIONS.items():
                if 'export' in perms or 'full_control' in perms:
                    export_admins.add(uid_str)

            # Helper: check if current time is within 1.5 minutes of target time
            def is_time_match(target_time_str):
                try:
                    target = datetime.strptime(target_time_str, "%H:%M")
                    current = datetime.strptime(current_time, "%H:%M")
                    diff = abs((current - target).total_seconds())
                    return diff <= 90
                except:
                    return current_time == target_time_str

            # === Auto Export: Export ALL tasks for the day ===
            async def do_auto_export(task_type, type_key, target_time, t_date):
                file_path, count = await export_tasks_xlsx(t_date, task_type)
                if file_path and count > 0:
                    asyncio.create_task(process_export_and_send(bot, t_date, task_type, export_admins, file_path, count))
                    last_exported[type_key] = f"{t_date}_{target_time}"
                    logging.info(f"Auto export {task_type}: {count} accounts")
                else:
                    last_exported[type_key] = f"{t_date}_{target_time}"
                    logging.info(f"Auto export {task_type}: No tasks found for {t_date}")
                    
            if is_time_match(insta_time) and last_exported['insta_2fa'] != f"{date_str_insta}_{insta_time}":
                await do_auto_export('insta_2fa', 'insta_2fa', insta_time, date_str_insta)
                
            if is_time_match(fb_2fa_time) and last_exported['fb_2fa'] != f"{date_str_fb}_{fb_2fa_time}":
                await do_auto_export('fb_2fa', 'fb_2fa', fb_2fa_time, date_str_fb)
                
            if is_time_match(fb_cookie_time) and last_exported['fb_cookie'] != f"{date_str_cookie}_{fb_cookie_time}":
                await do_auto_export('fb_cookie', 'fb_cookie', fb_cookie_time, date_str_cookie)
            
            # === Late Night Export (Optional): Re-export full day's data ===
            late_status = await get_setting('late_export_status') or 'on'
            late_time = await get_setting('late_export_time') or '23:55'
            
            if late_status == 'on' and is_time_match(late_time) and last_late_exported != f"{date_str}_{late_time}":
                logging.info(f"Late night export started for {date_str}")
                
                # Late export: re-export ALL tasks for the day (Full Day's Data)
                for task_type in ['insta_2fa', 'fb_2fa', 'fb_cookie']:
                    t_date = await get_system_date(task_type)
                    file_path, count = await export_tasks_xlsx(t_date, task_type)
                    if file_path and count > 0:
                        async def process_late_export_and_send(task_type_inner, file_path_inner, count_inner, date_str_inner):
                            sent_to = set()
                            buyers = await db.buyers.find().to_list(length=100)
                            for buyer in buyers:
                                types = buyer.get('types', [])
                                if task_type_inner in types:
                                    buyer_id = buyer.get('tg_id')
                                    username = buyer.get('username', 'No Username')
                                    try:
                                        support_link = await get_setting('support_link')
                                        kb = None
                                        if support_link:
                                            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Contact Admin 🎧", url=support_link)]])
                                            
                                        caption = (
                                            f"🌙 Late Night Export (Final): {date_str_inner}\n"
                                            f"📌 Type: {task_type_inner}\n"
                                            f"📊 Total Accounts: {count_inner}\n\n"
                                            f"⚠️ এটি আজকের সম্পূর্ণ ও চূড়ান্ত ফাইল"
                                        )
                                        await bot.send_document(chat_id=int(buyer_id), document=FSInputFile(file_path_inner), caption=caption, reply_markup=kb)
                                        sent_to.add(str(buyer_id))
                                        
                                        admin_msg = (
                                            f"🌙 <b>Late Export Sent to Buyer</b>\n\n"
                                            f"👤 Buyer: @{username}\n"
                                            f"🆔 ID: <code>{buyer_id}</code>\n"
                                            f"📌 Type: {task_type_inner}\n"
                                            f"📊 Total: {count_inner}"
                                        )
                                        await bot.send_document(chat_id=int(ADMIN_ID), document=FSInputFile(file_path_inner), caption=admin_msg, parse_mode="HTML")
                                    except Exception as e:
                                        logging.error(f"Late export: Failed to send to buyer {buyer_id}: {e}")
                            
                            # Send to admins
                            for admin in export_admins:
                                if str(admin) in sent_to:
                                    continue
                                try:
                                    caption = f"🌙 Late Night Export: {date_str_inner} ({task_type_inner})\nTotal: {count_inner} (Full day's data)"
                                    await bot.send_document(chat_id=int(admin), document=FSInputFile(file_path_inner), caption=caption)
                                except:
                                    pass
                                    
                            logging.info(f"Late export {task_type_inner}: {count_inner} accounts")
                            
                        asyncio.create_task(process_late_export_and_send(task_type, file_path, count, t_date))
                
                last_late_exported = f"{date_str}_{late_time}"
                
            await asyncio.sleep(30)
            
        except Exception as e:
            logging.error(f"Auto export error: {e}")
            import traceback
            logging.error(traceback.format_exc())
            await asyncio.sleep(60)


@router.message(F.text == "Manage OCR API Keys 🔑", StateFilter("*"))
async def manage_ocr_keys(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Keys 👁️", callback_data="ocr_view")],
        [InlineKeyboardButton(text="Add Key ➕", callback_data="ocr_add"), InlineKeyboardButton(text="Delete Key 🗑️", callback_data="ocr_del")]
    ])
    await message.answer("🔑 **Manage OCR API Keys**\nSelect an option below:", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "ocr_view")
async def ocr_view_keys(callback: CallbackQuery):
    keys = await db.ocr_keys.find({}).to_list(None)
    if not keys:
        return await callback.message.edit_text("No OCR API keys found. Using default 'helloworld'.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="ocr_back")]]))
    
    text = "🔑 **Saved OCR API Keys:**\n\n"
    for k in keys:
        text += f"• `{k['key']}` (Usage: {k.get('usage', 0)}/25000) [Month: {k.get('month', 'N/A')}]\n"
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="ocr_back")]]))

@router.callback_query(F.data == "ocr_back")
async def ocr_back_menu(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Keys 👁️", callback_data="ocr_view")],
        [InlineKeyboardButton(text="Add Key ➕", callback_data="ocr_add"), InlineKeyboardButton(text="Delete Key 🗑️", callback_data="ocr_del")]
    ])
    await callback.message.edit_text("🔑 **Manage OCR API Keys**\nSelect an option below:", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "ocr_add")
async def ocr_add_key_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.message.answer("Send the new OCR API Key to add:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminOCRStates.waiting_for_ocr_key)

@router.message(AdminOCRStates.waiting_for_ocr_key)
async def process_add_ocr_key(message: Message, state: FSMContext):
    key = message.text.strip()
    current_month = datetime.now().strftime("%Y-%m")
    await db.ocr_keys.update_one({'key': key}, {'$set': {'usage': 0, 'month': current_month}}, upsert=True)
    await state.clear()
    await message.answer(f"✅ OCR API Key `{key}` added successfully!", parse_mode="Markdown", reply_markup=get_settings_keyboard())

@router.callback_query(F.data == "ocr_del")
async def ocr_del_key_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.message.answer("Send the OCR API Key you want to delete:", reply_markup=get_cancel_keyboard())
    await state.set_state(AdminOCRStates.waiting_for_remove_ocr_key)

@router.message(AdminOCRStates.waiting_for_remove_ocr_key)
async def process_del_ocr_key(message: Message, state: FSMContext):
    key = message.text.strip()
    res = await db.ocr_keys.delete_one({'key': key})
    await state.clear()
    if res.deleted_count > 0:
        await message.answer(f"✅ OCR API Key `{key}` deleted!", parse_mode="Markdown", reply_markup=get_settings_keyboard())
    else:
        await message.answer("❌ Key not found.", reply_markup=get_settings_keyboard())

async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        print("Error: Provide BOT_TOKEN in .env")
        return

    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(AntiSpamMiddleware(limit=1.0))
    dp.callback_query.middleware(AntiSpamMiddleware(limit=1.0))
    session_timeout_middleware = SessionTimeoutMiddleware(timeout_seconds=3600)
    dp.message.middleware(session_timeout_middleware)
    dp.callback_query.middleware(session_timeout_middleware)

    dp.include_router(router)

    # Start auto export background task
    asyncio.create_task(auto_export_task(bot))

    # Start dummy web server for Render Free Tier
    try:
        from aiohttp import web
        async def handle_ping(request):
            return web.Response(text="Bot is running!")
        app = web.Application()
        app.add_routes([web.get('/', handle_ping)])
        runner = web.AppRunner(app)
        await runner.setup()
        port = int(os.getenv("PORT", 8000))
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        print(f"Dummy web server started on port {port}")
    except Exception as e:
        print(f"Failed to start web server: {e}")

    print("Bot is running...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("Bot stopped.")

