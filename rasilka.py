import asyncio
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError, FloodWaitError, RPCError, UserInvalidError, UsernameInvalidError, PeerFloodError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import nest_asyncio
import os
import pickle
import re
import time
import requests
from datetime import datetime, timedelta

nest_asyncio.apply()

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8029652586:AAF5OmhZySnMBP4Kcej0Sb2O8busNpm3NTk"
CRYPTO_PAY_API_TOKEN = "600178:AAR5ZVvJAdDt0hWKr72Dl9YlthvkMK45w8V"

ADMIN_USERNAMES = ["manager_FriendSenderbot", "Demyan_Egorovich"]
MANAGER_USERNAME = "manager_FriendSenderbot"

TARIFFS = {
    '1m': {'days': 30,  'price': 5,  'label': '1 месяц'},
    '3m': {'days': 90,  'price': 13, 'label': '3 месяца'},
    '6m': {'days': 180, 'price': 24, 'label': '6 месяцев'},
}

REF_BONUS_MINUTES = 10

ACCOUNT_DATA = {
    'api_id': None, 'api_hash': None, 'phone': None, 'client': None,
    'is_authenticated': False, 'name': None, 'is_banned': False, 'ban_reason': None,
}

DEFAULT_USER_DATA = {
    'username': None,
    'first_name': None,
    'accounts': {},
    'next_account_id': 1,
    'targets': [],
    'spam_interval': 60,
    'is_sending': False,
    'sending_task': None,
    'auth_step': None,
    'auth_account_id': None,
    'awaiting': None,
    'temp_data': {},
    'last_spam_message': None,
    'selected_accounts': [],
    'stats': {'sent': 0, 'failed': 0, 'banned_accounts': 0},
    'subscription': {
        'active': False,
        'expires_at': None,
        'payment_id': None,
        'payment_status': None,
        'tariff': None,
    },
    'ref': {
        'invited_by': None,
        'invited_users': [],
        'total_bonus_earned': 0,
    },
    'bonus_active_seconds': 0,
}

user_data = {}
sending_tasks = {}
DATA_FILE = 'bot_data.pickle'

# ========== SAFE EDIT ==========
async def safe_edit(query, text, reply_markup=None, parse_mode=None):
    try:
        await query.edit_message_text(
            text, reply_markup=reply_markup,
            parse_mode=parse_mode, disable_web_page_preview=True
        )
        return
    except Exception as e:
        if 'not modified' in str(e).lower():
            return
    try:
        await query.edit_message_text(
            text, reply_markup=reply_markup,
            parse_mode=None, disable_web_page_preview=True
        )
        return
    except Exception as e2:
        if 'not modified' in str(e2).lower():
            return
    try:
        await query.message.delete()
    except:
        pass
    try:
        await query.message.chat.send_message(
            text, reply_markup=reply_markup,
            parse_mode=None, disable_web_page_preview=True
        )
    except Exception as e3:
        print(f"❌ safe_edit: {e3}")

# ========== ЗАГРУЗКА/СОХРАНЕНИЕ (ПОЛНОСТЬЮ ПЕРЕПИСАНО) ==========
def load_data():
    """Полностью переписанная загрузка — каждый пользователь изолирован"""
    global user_data
    user_data = {}
    if not os.path.exists(DATA_FILE):
        print("📁 Файл данных не найден, стартуем с чистого листа")
        return
    try:
        with open(DATA_FILE, 'rb') as f:
            loaded = pickle.load(f)
        for uid, data in loaded.items():
            uid = int(uid)
            # Создаём чистый шаблон для КАЖДОГО пользователя
            u = {
                'username': data.get('username'),
                'first_name': data.get('first_name'),
                'accounts': {},
                'next_account_id': data.get('next_account_id', 1),
                'targets': data.get('targets', []),
                'spam_interval': data.get('spam_interval', 60),
                'is_sending': False,
                'sending_task': None,
                'auth_step': None,
                'auth_account_id': None,
                'awaiting': None,
                'temp_data': {},
                'last_spam_message': data.get('last_spam_message'),
                'selected_accounts': data.get('selected_accounts', []),
                'stats': data.get('stats', {'sent': 0, 'failed': 0, 'banned_accounts': 0}),
                'subscription': data.get('subscription', {
                    'active': False, 'expires_at': None,
                    'payment_id': None, 'payment_status': None, 'tariff': None
                }),
                'ref': data.get('ref', {
                    'invited_by': None, 'invited_users': [], 'total_bonus_earned': 0
                }),
                'bonus_active_seconds': data.get('bonus_active_seconds', 0),
            }
            # Загружаем аккаунты
            for acc_id, acc_data in data.get('accounts', {}).items():
                u['accounts'][acc_id] = {
                    'api_id': acc_data.get('api_id'),
                    'api_hash': acc_data.get('api_hash'),
                    'phone': acc_data.get('phone'),
                    'client': None,
                    'is_authenticated': acc_data.get('is_authenticated', False),
                    'name': acc_data.get('name'),
                    'is_banned': acc_data.get('is_banned', False),
                    'ban_reason': acc_data.get('ban_reason'),
                }
            # Проверяем валидность рефералов (в базе могут быть удалённые)
            valid_invited = []
            for ref_uid in u['ref'].get('invited_users', []):
                try:
                    ref_uid = int(ref_uid)
                    if ref_uid in loaded:
                        valid_invited.append(ref_uid)
                except:
                    continue
            u['ref']['invited_users'] = valid_invited
            user_data[uid] = u
        print(f"✅ Загружено {len(user_data)} пользователей")
    except Exception as e:
        print(f"❌ Ошибка загрузки: {e}")
        user_data = {}

def save_data():
    try:
        out = {}
        for uid, info in user_data.items():
            accounts_to_save = {}
            for acc_id, acc_data in info.get('accounts', {}).items():
                accounts_to_save[acc_id] = {k: v for k, v in acc_data.items() if k != 'client'}
            out[uid] = {k: v for k, v in info.items() if k not in ('client', 'sending_task', 'accounts')}
            out[uid]['accounts'] = accounts_to_save
        with open(DATA_FILE, 'wb') as f:
            pickle.dump(out, f)
    except Exception as e:
        print(f"❌ Ошибка сохранения: {e}")

def init_user(user_id, tg_user=None):
    """Гарантирует что у пользователя ВСЕ поля, изолированно от других"""
    if user_id not in user_data:
        user_data[user_id] = {
            'username': None,
            'first_name': None,
            'accounts': {},
            'next_account_id': 1,
            'targets': [],
            'spam_interval': 60,
            'is_sending': False,
            'sending_task': None,
            'auth_step': None,
            'auth_account_id': None,
            'awaiting': None,
            'temp_data': {},
            'last_spam_message': None,
            'selected_accounts': [],
            'stats': {'sent': 0, 'failed': 0, 'banned_accounts': 0},
            'subscription': {
                'active': False, 'expires_at': None,
                'payment_id': None, 'payment_status': None, 'tariff': None
            },
            'ref': {
                'invited_by': None, 'invited_users': [], 'total_bonus_earned': 0
            },
            'bonus_active_seconds': 0,
        }
        print(f"👤 Новый пользователь: {user_id}")
    # Обновляем username / first_name
    if tg_user is not None:
        changed = False
        if tg_user.username and user_data[user_id].get('username') != tg_user.username:
            user_data[user_id]['username'] = tg_user.username
            changed = True
        if tg_user.first_name and user_data[user_id].get('first_name') != tg_user.first_name:
            user_data[user_id]['first_name'] = tg_user.first_name
            changed = True
        if changed:
            save_data()
    return user_data[user_id]

def get_user_display(uid):
    u = user_data.get(uid, {})
    if u.get('username'):
        return f"@{u['username']}"
    elif u.get('first_name'):
        return u['first_name']
    return f"ID:{uid}"

# ========== РЕФЕРАЛЬНАЯ СИСТЕМА (ИНДИВИДУАЛЬНАЯ) ==========
def get_ref_code(user_id):
    """Уникальный реф-код строго по ID — никаких коллизий"""
    return f"u{user_id}"

def find_user_by_ref_code(code):
    if not code:
        return None
    if code.startswith('u'):
        try:
            return int(code[1:])
        except:
            return None
    return None

def process_referral(new_user_id, ref_code):
    """Регистрирует реферал. Возвращает referrer_id или None"""
    referrer_id = find_user_by_ref_code(ref_code)
    if referrer_id is None:
        return None
    if referrer_id == new_user_id:
        return None
    if referrer_id not in user_data:
        return None
    new_user = user_data.get(new_user_id)
    if not new_user:
        return None
    # Защита: у нового юзера уже есть пригласивший
    if new_user['ref'].get('invited_by'):
        return None
    # Привязываем
    new_user['ref']['invited_by'] = referrer_id
    ref_list = user_data[referrer_id]['ref'].get('invited_users', [])
    if new_user_id not in ref_list:
        ref_list.append(new_user_id)
        user_data[referrer_id]['ref']['invited_users'] = ref_list
    # Начисляем бонус
    bonus_sec = REF_BONUS_MINUTES * 60
    user_data[referrer_id]['bonus_active_seconds'] = user_data[referrer_id].get('bonus_active_seconds', 0) + bonus_sec
    user_data[referrer_id]['ref']['total_bonus_earned'] = user_data[referrer_id]['ref'].get('total_bonus_earned', 0) + bonus_sec
    save_data()
    return referrer_id

# ========== БОНУСНОЕ ВРЕМЯ ==========
def format_time_left(seconds):
    if seconds <= 0:
        return "0 мин"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    if h > 0:
        return f"{h} ч {m} мин"
    return f"{m} мин"

# ========== ПРОВЕРКА АДМИНА ==========
async def is_admin(user_id, context):
    try:
        user = await context.bot.get_chat(user_id)
        if user.username and user.username.lower() in [a.lower() for a in ADMIN_USERNAMES]:
            return True
    except Exception as e:
        print(f"⚠️ is_admin error: {e}")
    return False

# ========== ПРОВЕРКА ПОДПИСКИ ==========
def check_subscription(user):
    """Возвращает True если есть активная подписка ИЛИ бонусное время"""
    sub = user.get('subscription', {})
    if sub.get('active'):
        exp = sub.get('expires_at')
        if exp:
            try:
                if datetime.now() < datetime.fromisoformat(exp):
                    return True
                # Истекла — сбрасываем
                sub['active'] = False
                save_data()
                return False
            except:
                return False
        return True
    # Бонусное время
    if user.get('bonus_active_seconds', 0) > 0:
        return True
    return False

def get_subscription_status(user):
    sub = user.get('subscription', {})
    if sub.get('active'):
        exp = sub.get('expires_at')
        if exp:
            try:
                dt = datetime.fromisoformat(exp)
                days_left = (dt - datetime.now()).days
                if days_left > 0:
                    return f"✅ Активна ({days_left} дн.)"
                return "❌ Истекла"
            except:
                return "❌ Ошибка"
        return "✅ Активна"
    if user.get('bonus_active_seconds', 0) > 0:
        return f"⏳ Бонус: {format_time_left(user['bonus_active_seconds'])}"
    if sub.get('payment_status') == 'pending':
        return "⏳ Ожидает оплаты"
    return "❌ Неактивна"

# ========== CRYPTO PAY ==========
def create_crypto_invoice(amount, days, tariff_label):
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN, "Content-Type": "application/json"}
    data = {
        "asset": "USDT",
        "amount": str(amount),
        "description": f"Friend Sender — {tariff_label}",
    }
    try:
        r = requests.post(url, headers=headers, json=data, timeout=30)
        res = r.json()
        if res.get("ok"):
            inv = res["result"]
            return {'success': True, 'invoice_id': inv['invoice_id'], 'pay_url': inv['bot_invoice_url']}
        return {'success': False, 'error': res.get('error', 'Неизвестная ошибка')}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def check_crypto_payment(invoice_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTO_PAY_API_TOKEN, "Content-Type": "application/json"}
    try:
        r = requests.get(url, headers=headers, params={"invoice_ids": invoice_id}, timeout=30)
        res = r.json()
        if res.get("ok") and res.get("result"):
            return {'success': True, 'status': res["result"][0]['status']}
        return {'success': False, 'error': 'Не удалось проверить'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

# ========== КЛАВИАТУРЫ ==========
def get_no_subscription_keyboard(user_id):
    """Кнопки для пользователя БЕЗ подписки. Со ВСЕМИ нужными опциями."""
    bot_username = "FriendSenderBot"  # fallback
    kb = [
        [InlineKeyboardButton("💎 КУПИТЬ ПОДПИСКУ", callback_data='show_payment_methods')],
        [InlineKeyboardButton("🎁 Пригласить друга (+10 мин)", callback_data='ref_link')],
        [InlineKeyboardButton("👥 Мои рефералы", callback_data='ref_list')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')],
    ]
    return InlineKeyboardMarkup(kb)

def get_tariff_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📅 1 месяц — ${TARIFFS['1m']['price']}", callback_data='tariff_1m')],
        [InlineKeyboardButton(f"📅 3 месяца — ${TARIFFS['3m']['price']} (-13%)", callback_data='tariff_3m')],
        [InlineKeyboardButton(f"📅 6 месяцев — ${TARIFFS['6m']['price']} (-20%)", callback_data='tariff_6m')],
        [InlineKeyboardButton("🏦 Карта / Менеджер", callback_data='pay_card')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_payment_choice')],
    ])

def get_payment_method_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 CryptoBot (USDT)", callback_data='pay_crypto')],
        [InlineKeyboardButton("🏦 Карта / Менеджер", callback_data='pay_card')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')],
    ])

def get_card_payment_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_payment_choice')],
        [InlineKeyboardButton("❓ Помощь", callback_data='help')],
    ])

def get_main_keyboard(user):
    if not check_subscription(user):
        return get_no_subscription_keyboard(None)
    is_sending = user.get('is_sending', False)
    kb = []
    if is_sending:
        kb.append([InlineKeyboardButton("⏸ ОСТАНОВИТЬ РАССЫЛКУ", callback_data='stop_spam')])
    else:
        kb.append([InlineKeyboardButton("🚀 ЗАПУСТИТЬ РАССЫЛКУ", callback_data='start_spam_menu')])
    kb.append([
        InlineKeyboardButton("⚙️ Спам", callback_data='spam_settings'),
        InlineKeyboardButton("📱 Аккаунты", callback_data='account_settings'),
    ])
    kb.append([
        InlineKeyboardButton("👥 Парсинг", callback_data='parse_menu'),
        InlineKeyboardButton("ℹ️ Статус", callback_data='status'),
    ])
    kb.append([
        InlineKeyboardButton("💳 Подписка", callback_data='subscription_info'),
        InlineKeyboardButton("🎁 Рефералы", callback_data='ref_menu'),
    ])
    kb.append([InlineKeyboardButton("❓ Помощь", callback_data='help')])
    return InlineKeyboardMarkup(kb)

def get_admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Список пользователей", callback_data='admin_list')],
        [InlineKeyboardButton("🔍 Найти пользователя", callback_data='admin_search')],
        [InlineKeyboardButton("➕ Выдать подписку", callback_data='admin_add_subscription')],
        [InlineKeyboardButton("➖ Снять подписку", callback_data='admin_remove_subscription')],
        [InlineKeyboardButton("🎁 Бонусы (реф.)", callback_data='admin_bonus_menu')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
    ])

def get_back_keyboard(cb='back_to_menu'):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data=cb)]])

def get_spam_settings_keyboard(user):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Указать цели", callback_data='set_targets')],
        [InlineKeyboardButton("🗑 Удалить цели", callback_data='delete_targets_menu')],
        [InlineKeyboardButton("🎯 Выбрать аккаунты", callback_data='select_accounts')],
        [InlineKeyboardButton("⏱ Интервал", callback_data='set_interval')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
    ])

def get_delete_targets_keyboard(user):
    kb = []
    targets = get_unspammed_targets(user)
    if not targets:
        kb.append([InlineKeyboardButton("❌ Нет целей", callback_data='spam_settings')])
    else:
        for i, t in enumerate(targets[:20]):
            short = t['target'][:25] + ('...' if len(t['target']) > 25 else '')
            kb.append([InlineKeyboardButton(f"🗑 {short}", callback_data=f'del_target_{i}')])
        if len(targets) > 20:
            kb.append([InlineKeyboardButton(f"...и ещё {len(targets)-20}", callback_data='spam_settings')])
        kb.append([InlineKeyboardButton("⚠️ УДАЛИТЬ ВСЕ", callback_data='del_all_targets')])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data='spam_settings')])
    return InlineKeyboardMarkup(kb)

def get_account_settings_keyboard(user):
    accounts = get_active_accounts(user)
    kb = []
    if accounts:
        kb.append([InlineKeyboardButton(f"📱 Мои аккаунты ({len(accounts)})", callback_data='list_accounts')])
        kb.append([InlineKeyboardButton("🗑 Удалить аккаунт", callback_data='delete_account_menu')])
    kb.append([InlineKeyboardButton("🔑 Добавить аккаунт", callback_data='add_account')])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')])
    return InlineKeyboardMarkup(kb)

def get_message_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Новое сообщение", callback_data='new_message')],
        [InlineKeyboardButton("📋 Сохранённое", callback_data='use_saved_message')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
    ])

def get_spam_active_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⏸ ОСТАНОВИТЬ", callback_data='stop_spam')]])

def get_accounts_selection_keyboard(user, selected):
    kb = []
    for acc_id, acc in get_active_accounts(user):
        name = acc.get('name', f'Аккаунт {acc_id}')
        status = "✅" if acc_id in selected else "⬜"
        kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'toggle_acc_{acc_id}')])
    kb.append([
        InlineKeyboardButton("✅ Все", callback_data='select_all_accounts'),
        InlineKeyboardButton("❌ Ничего", callback_data='clear_all_accounts'),
    ])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data='spam_settings')])
    return InlineKeyboardMarkup(kb)

def get_delete_account_keyboard(user):
    kb = []
    accounts = get_active_accounts(user)
    if not accounts:
        kb.append([InlineKeyboardButton("❌ Нет аккаунтов", callback_data='account_settings')])
    else:
        for acc_id, acc in accounts:
            kb.append([InlineKeyboardButton(f"🗑 {acc.get('name', f'Аккаунт {acc_id}')}", callback_data=f'delete_acc_{acc_id}')])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data='account_settings')])
    return InlineKeyboardMarkup(kb)

def get_parse_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Все участники", callback_data='parse_participants')],
        [InlineKeyboardButton("💬 Из сообщений", callback_data='parse_messages_custom')],
        [InlineKeyboardButton("📰 Комментаторы канала", callback_data='parse_channel_posts')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
    ])

def get_message_count_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("500", callback_data='parse_count_500'),
            InlineKeyboardButton("1000", callback_data='parse_count_1000'),
            InlineKeyboardButton("2000", callback_data='parse_count_2000'),
        ],
        [
            InlineKeyboardButton("3000", callback_data='parse_count_3000'),
            InlineKeyboardButton("5000", callback_data='parse_count_5000'),
        ],
        [InlineKeyboardButton("✏️ Своё", callback_data='parse_count_custom')],
        [InlineKeyboardButton("🔙 Назад", callback_data='parse_menu')],
    ])

def get_subscription_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Оплатить", callback_data='show_payment_methods')],
        [InlineKeyboardButton("🔄 Проверить оплату", callback_data='check_payment')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
    ])

def get_ref_menu_keyboard(user):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Пригласить друга", callback_data='ref_link')],
        [InlineKeyboardButton("👥 Мои рефералы", callback_data='ref_list')],
        [InlineKeyboardButton("🏆 Топ-10", callback_data='ref_top')],
        [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
    ])

def get_admin_user_list_keyboard(users):
    kb = []
    for uid in list(users.keys())[:30]:
        sub = users[uid].get('subscription', {})
        status = "✅" if sub.get('active') else "❌"
        display = get_user_display(uid)
        if len(display) > 28:
            display = display[:25] + "..."
        kb.append([InlineKeyboardButton(f"{status} {display}", callback_data=f'admin_user_{uid}')])
    kb.append([InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')])
    return InlineKeyboardMarkup(kb)

def get_admin_user_menu_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Выдать (ввести дни)", callback_data=f'admin_activate_custom_{user_id}')],
        [
            InlineKeyboardButton("+30 дн", callback_data=f'admin_activate_{user_id}_30'),
            InlineKeyboardButton("+7 дн", callback_data=f'admin_activate_{user_id}_7'),
            InlineKeyboardButton("+1 д", callback_data=f'admin_activate_{user_id}_1'),
        ],
        [InlineKeyboardButton("🎁 +10 мин бонуса", callback_data=f'admin_bonus_{user_id}_10')],
        [InlineKeyboardButton("➖ Деактивировать", callback_data=f'admin_deactivate_{user_id}')],
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_list')],
    ])

def get_search_result_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Выдать подписку", callback_data=f'admin_activate_custom_{user_id}')],
        [InlineKeyboardButton("➕ 30 дней", callback_data=f'admin_activate_{user_id}_30')],
        [InlineKeyboardButton("🎁 +10 мин бонуса", callback_data=f'admin_bonus_{user_id}_10')],
        [InlineKeyboardButton("➖ Деактивировать", callback_data=f'admin_deactivate_{user_id}')],
        [InlineKeyboardButton("🔍 Искать ещё", callback_data='admin_search')],
        [InlineKeyboardButton("🔙 Админ-панель", callback_data='admin_panel')],
    ])

def get_admin_bonus_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Назад", callback_data='admin_panel')],
    ])

# ========== ХЕЛПЕРЫ ==========
def get_active_accounts(user):
    active = []
    for acc_id, acc in user.get('accounts', {}).items():
        if acc.get('is_authenticated') and acc.get('client') is not None and not acc.get('is_banned', False):
            try:
                if acc['client'].is_connected():
                    active.append((acc_id, acc))
                else:
                    try:
                        acc['client'].connect()
                        if acc['client'].is_connected():
                            active.append((acc_id, acc))
                    except:
                        pass
            except:
                pass
    return active

def get_unspammed_targets(user):
    return [t for t in user.get('targets', []) if not t.get('spammed', False)]

def parse_account_data(text):
    lines = text.strip().split('\n')
    all_text = ' '.join(lines)
    phone = None
    for pattern in [r'\+\d{10,15}', r'\d{10,15}']:
        matches = re.findall(pattern, all_text)
        for m in matches:
            clean = re.sub(r'[\s\-\(\)]', '', m)
            if len(clean) >= 10:
                phone = clean if clean.startswith('+') else ('+' + clean)
        if phone:
            break
    api_id = None
    for num in re.findall(r'\b\d{3,8}\b', all_text):
        if phone and num in phone.replace('+', ''):
            continue
        api_id = num
        break
    api_hash = None
    matches = re.findall(r'[a-fA-F0-9]{32}', all_text)
    if matches:
        api_hash = matches[0]
    else:
        for w in re.findall(r'\b[a-zA-Z0-9]{20,40}\b', all_text):
            if phone and w in phone.replace('+', ''):
                continue
            if api_id and w == api_id:
                continue
            api_hash = w
            break
    return {'api_id': api_id, 'api_hash': api_hash, 'phone': phone}

# ========== УСКОРЕННЫЙ ПАРСИНГ ==========
async def parse_chat_participants(client, chat_entity):
    usernames = []
    try:
        async for user in client.iter_participants(chat_entity, aggressive=True):
            if user.username:
                usernames.append(f"@{user.username}")
            elif user.id:
                usernames.append(str(user.id))
    except Exception as e:
        print(f"❌ Ошибка парсинга участников: {e}")
    return usernames

async def parse_from_messages(client, chat_entity, message_limit=3000):
    usernames = []
    unique = set()
    try:
        async for message in client.iter_messages(chat_entity, limit=message_limit):
            try:
                sender = message.sender
                if sender and sender.id not in unique:
                    unique.add(sender.id)
                    if sender.username:
                        usernames.append(f"@{sender.username}")
                    elif sender.id:
                        usernames.append(str(sender.id))
            except:
                continue
    except Exception as e:
        print(f"❌ Ошибка парсинга сообщений: {e}")
    return usernames

async def parse_channel_posts(client, chat_entity, post_limit=100):
    usernames = []
    unique = set()
    posts = []
    try:
        async for message in client.iter_messages(chat_entity, limit=post_limit):
            if message and message.id:
                posts.append(message.id)
    except Exception as e:
        print(f"❌ Ошибка получения постов: {e}")
        return []
    sem = asyncio.Semaphore(5)
    lock = asyncio.Lock()
    async def process_post(post_id):
        try:
            async with sem:
                async for reply in client.iter_messages(chat_entity, reply_to=post_id, limit=200):
                    if reply.sender:
                        sid = reply.sender.id
                        async with lock:
                            if sid not in unique:
                                unique.add(sid)
                                if reply.sender.username:
                                    usernames.append(f"@{reply.sender.username}")
                                elif sid:
                                    usernames.append(str(sid))
        except Exception:
            pass
    tasks = [process_post(pid) for pid in posts]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return usernames

async def send_parse_results_to_saved(client, usernames):
    if not usernames:
        return False
    chunk = 50
    for i in range(0, len(usernames), chunk):
        await client.send_message('me', "\n".join(usernames[i:i+chunk]))
        await asyncio.sleep(0.3)
    return True

# ========== ОСТАНОВКА СПАМА ==========
async def stop_sending(user_id):
    if user_id in sending_tasks and sending_tasks[user_id]:
        task = sending_tasks[user_id]
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        del sending_tasks[user_id]
    if user_id in user_data:
        user_data[user_id]['is_sending'] = False
        save_data()

# ========== HELP ==========
def get_help_text():
    return (
        "❓ Помощь по боту ❓\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💎 Подписка (тарифы):\n"
        f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
        f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
        f"• 6 месяцев — ${TARIFFS['6m']['price']}\n\n"
        "💳 Оплата:\n"
        "• CryptoBot (USDT) — автоактивация\n"
        "• Карта через менеджера\n\n"
        "🎁 Реферальная система:\n"
        f"• +{REF_BONUS_MINUTES} мин за каждого друга\n"
        "• Работает без подписки!\n\n"
        "🔑 API ID: https://my.telegram.org/apps\n\n"
        "⚠️ Проверка бана: @SpamBot\n\n"
        f"📌 Менеджер: @{MANAGER_USERNAME}"
    )

# ========== /START ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = init_user(user_id, update.effective_user)
    
    # Обработка реф-ссылки
    args = context.args
    if args and args[0].startswith('ref_'):
        ref_code = args[0][4:]
        result = process_referral(user_id, ref_code)
        if result:
            try:
                await context.bot.send_message(
                    chat_id=result,
                    text=(
                        f"🎉 Новый реферал!\n"
                        f"👤 {get_user_display(user_id)}\n\n"
                        f"🎁 +{REF_BONUS_MINUTES} мин бонусного времени"
                    )
                )
            except:
                pass
    
    if await is_admin(user_id, context):
        await update.message.reply_text(
            "👑 Админ-панель\n━━━━━━━━━━━━━━━━━━━━\n\nВыберите действие:",
            reply_markup=get_admin_keyboard()
        )
        return
    
    if not check_subscription(user):
        welcome = (
            "🌟 Добро пожаловать в Friend Sender 🌟\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "🤖 Профессиональный бот для рассылок\n\n"
            "💎 Тарифы:\n"
            f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
            f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
            f"• 6 месяцев — ${TARIFFS['6m']['price']}\n\n"
            f"🎁 Или бесплатно: +{REF_BONUS_MINUTES} мин за каждого друга\n"
            "   Приглашай — работай без подписки!\n\n"
            "Выберите действие:"
        )
        await update.message.reply_text(welcome, reply_markup=get_no_subscription_keyboard(user_id))
        return
    
    accounts = get_active_accounts(user)
    targets = get_unspammed_targets(user)
    interval = user.get('spam_interval', 60)
    stats = user.get('stats', {})
    bonus = user.get('bonus_active_seconds', 0)
    invited = len(user['ref'].get('invited_users', []))
    
    text = (
        "🏠 Главное меню\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 Статистика:\n"
        f"├ 📱 Аккаунтов: {len(accounts)}\n"
        f"├ 🎯 Целей: {len(targets)}\n"
        f"├ ✅ Отправлено: {stats.get('sent', 0)}\n"
        f"├ ❌ Ошибок: {stats.get('failed', 0)}\n"
        f"└ ⏱ Интервал: {interval} сек\n\n"
        f"💳 {get_subscription_status(user)}\n"
        f"🎁 Рефералов: {invited}\n"
    )
    if bonus > 0:
        text += f"⏳ Бонус: {format_time_left(bonus)}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━\n🔰 Выберите действие:"
    
    await update.message.reply_text(text, reply_markup=get_main_keyboard(user))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = init_user(user_id, update.effective_user)
    h = get_help_text()
    if await is_admin(user_id, context):
        await update.message.reply_text(h, reply_markup=get_admin_keyboard())
    elif check_subscription(user):
        await update.message.reply_text(h, reply_markup=get_main_keyboard(user))
    else:
        await update.message.reply_text(h, reply_markup=get_no_subscription_keyboard(user_id))

# ========== КНОПКИ ==========
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = init_user(user_id, update.effective_user)
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # ========== ПОМОЩЬ ==========
    if data == 'help':
        h = get_help_text()
        if await is_admin(user_id, context):
            await safe_edit(query, h, reply_markup=get_admin_keyboard())
        elif check_subscription(user):
            await safe_edit(query, h, reply_markup=get_main_keyboard(user))
        else:
            await safe_edit(query, h, reply_markup=get_no_subscription_keyboard(user_id))
        return
    
    # ========== РЕФЕРАЛЬНАЯ СИСТЕМА ==========
    if data == 'ref_menu':
        invited = len(user['ref'].get('invited_users', []))
        bonus = user.get('bonus_active_seconds', 0)
        total = user['ref'].get('total_bonus_earned', 0)
        text = (
            "🎁 Реферальная система\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Приглашено: {invited}\n"
            f"⏳ Бонус сейчас: {format_time_left(bonus)}\n"
            f"🏆 Всего заработано: {format_time_left(total)}\n\n"
            f"💡 За каждого друга +{REF_BONUS_MINUTES} мин\n"
            "🔗 У каждого своя личная ссылка"
        )
        await safe_edit(query, text, reply_markup=get_ref_menu_keyboard(user))
        return
    
    if data == 'ref_link':
        bot_username = (await context.bot.get_me()).username
        ref_code = get_ref_code(user_id)
        link = f"https://t.me/{bot_username}?start=ref_{ref_code}"
        text = (
            "🔗 Ваша реферальная ссылка\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{link}\n\n"
            f"💡 Отправь друзьям — за каждого\n"
            f"получишь +{REF_BONUS_MINUTES} мин бонуса!\n\n"
            f"⏳ Сейчас у тебя: {format_time_left(user.get('bonus_active_seconds', 0))}"
        )
        share = f"https://t.me/share/url?url={link}&text=Попробуй этого бота!"
        await safe_edit(
            query, text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📤 Поделиться", url=share)],
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_menu')],
            ])
        )
        return
    
    if data == 'ref_list':
        invited = user['ref'].get('invited_users', [])
        if not invited:
            text = "👥 У вас пока нет рефералов.\n\nПоделитесь своей ссылкой!"
        else:
            text = f"👥 Ваши рефералы ({len(invited)}):\n━━━━━━━━━━━━━━━━━━━━\n\n"
            for uid in invited[:30]:
                text += f"├ {get_user_display(uid)}\n"
            if len(invited) > 30:
                text += f"└ ... и ещё {len(invited) - 30}"
        back_cb = 'back_to_menu' if check_subscription(user) else 'ref_menu_no_sub'
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Пригласить ещё", callback_data='ref_link')],
            [InlineKeyboardButton("🔙 Назад", callback_data=back_cb)],
        ])
        await safe_edit(query, text, reply_markup=kb)
        return
    
    if data == 'ref_menu_no_sub':
        invited = len(user['ref'].get('invited_users', []))
        bonus = user.get('bonus_active_seconds', 0)
        text = (
            "🎁 Реферальная система\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Приглашено: {invited}\n"
            f"⏳ Бонус: {format_time_left(bonus)}\n\n"
            f"💡 За каждого друга +{REF_BONUS_MINUTES} мин\n"
            "🔗 У каждого своя личная ссылка"
        )
        await safe_edit(query, text, reply_markup=get_no_subscription_keyboard(user_id))
        return
    
    if data == 'ref_top':
        top = sorted(
            user_data.items(),
            key=lambda x: len(x[1].get('ref', {}).get('invited_users', [])),
            reverse=True
        )[:10]
        text = "🏆 Топ-10 рефереров\n━━━━━━━━━━━━━━━━━━━━\n\n"
        place = 1
        for uid, u in top:
            count = len(u.get('ref', {}).get('invited_users', []))
            if count > 0:
                medal = ["🥇", "🥈", "🥉"][place-1] if place <= 3 else f"{place}."
                text += f"{medal} {get_user_display(uid)} — {count}\n"
                place += 1
        if place == 1:
            text += "Пока никто не приглашал 😔"
        else:
            text += "\n💡 Приглашай больше — попади в топ!"
        back_cb = 'back_to_menu' if check_subscription(user) else 'ref_menu_no_sub'
        await safe_edit(query, text, reply_markup=get_back_keyboard(back_cb))
        return
    
    # ========== ОПЛАТА ==========
    if data == 'show_payment_methods':
        if check_subscription(user):
            await safe_edit(query, "✅ У вас уже есть доступ!", reply_markup=get_main_keyboard(user))
            return
        text = (
            "💎 Выберите способ оплаты\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📅 Тарифы:\n"
            f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
            f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
            f"• 6 месяцев — ${TARIFFS['6m']['price']}"
        )
        await safe_edit(query, text, reply_markup=get_payment_method_keyboard())
        return
    
    if data == 'back_to_payment_choice':
        text = (
            "💎 Выберите способ оплаты\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📅 Тарифы:\n"
            f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
            f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
            f"• 6 месяцев — ${TARIFFS['6m']['price']}"
        )
        await safe_edit(query, text, reply_markup=get_payment_method_keyboard())
        return
    
    # ===== КАРТА / МЕНЕДЖЕР =====
    if data == 'pay_card':
        card_text = (
            "🏦 Оплата через менеджера\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📅 Тарифы:\n"
            f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
            f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
            f"• 6 месяцев — ${TARIFFS['6m']['price']}\n\n"
            "📩 Напишите менеджеру для оплаты\n"
            "и активации подписки:\n\n"
            f"👉 @{MANAGER_USERNAME}\n\n"
            "⚠️ После оплаты обязательно напишите менеджеру!"
        )
        await safe_edit(query, card_text, reply_markup=get_card_payment_keyboard())
        return
    
    # ===== CRYPTOBOT =====
    if data == 'pay_crypto':
        if check_subscription(user):
            await safe_edit(query, "✅ У вас уже есть доступ!", reply_markup=get_main_keyboard(user))
            return
        text = (
            "💎 Оплата через CryptoBot\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Выберите тариф:\n\n"
            f"📅 1 месяц — ${TARIFFS['1m']['price']}\n"
            f"📅 3 месяца — ${TARIFFS['3m']['price']} (выгодно!)\n"
            f"📅 6 месяцев — ${TARIFFS['6m']['price']} (макс. выгода!)\n\n"
            "✅ Автоактивация после оплаты!"
        )
        await safe_edit(query, text, reply_markup=get_tariff_keyboard())
        return
    
    # ===== СОЗДАНИЕ ИНВОЙСА =====
    if data.startswith('tariff_'):
        tariff_key = data.replace('tariff_', '')
        tariff = TARIFFS.get(tariff_key)
        if not tariff:
            await safe_edit(query, "❌ Неверный тариф", reply_markup=get_payment_method_keyboard())
            return
        await safe_edit(query, "⏳ Создаю инвойс...", reply_markup=get_back_keyboard('pay_crypto'))
        result = create_crypto_invoice(tariff['price'], tariff['days'], tariff['label'])
        if not result['success']:
            # При ошибке создания инвойса — предложить менеджера как fallback
            await safe_edit(
                query,
                f"❌ Не удалось создать инвойс\n\n"
                f"Ошибка: {result.get('error', 'Unknown')}\n\n"
                f"Попробуйте другой способ оплаты.",
                reply_markup=get_payment_method_keyboard()
            )
            return
        sub = user['subscription']
        sub['payment_id'] = result['invoice_id']
        sub['payment_status'] = 'pending'
        sub['tariff'] = tariff_key
        sub['payment_created_at'] = datetime.now().isoformat()
        save_data()
        kb = [
            [InlineKeyboardButton(f"💎 ОПЛАТИТЬ ${tariff['price']}", url=result['pay_url'])],
            [InlineKeyboardButton("🔄 Проверить оплату", callback_data='check_payment')],
            [InlineKeyboardButton("🔙 Назад", callback_data='pay_crypto')],
        ]
        text = (
            f"💎 Оплата подписки\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📅 Тариф: {tariff['label']}\n"
            f"💰 Сумма: ${tariff['price']}\n\n"
            "📌 Инструкция:\n"
            "1. Нажмите кнопку ОПЛАТИТЬ\n"
            "2. Оплатите в CryptoBot\n"
            "3. Вернитесь → «🔄 Проверить оплату»\n\n"
            "✅ Подписка активируется автоматически!"
        )
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    # ===== ГЛАВНОЕ МЕНЮ / НАЗАД =====
    if data in ('back_to_menu', 'cancel'):
        user['auth_step'] = None
        user['awaiting'] = None
        user['temp_data'] = {}
        save_data()
        if await is_admin(user_id, context):
            await safe_edit(query, "👑 Админ-панель", reply_markup=get_admin_keyboard())
        elif check_subscription(user):
            accounts = get_active_accounts(user)
            targets = get_unspammed_targets(user)
            stats = user.get('stats', {})
            bonus = user.get('bonus_active_seconds', 0)
            invited = len(user['ref'].get('invited_users', []))
            text = (
                "🏠 Главное меню\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "📊 Статистика:\n"
                f"├ 📱 Аккаунтов: {len(accounts)}\n"
                f"├ 🎯 Целей: {len(targets)}\n"
                f"├ ✅ Отправлено: {stats.get('sent', 0)}\n"
                f"└ ❌ Ошибок: {stats.get('failed', 0)}\n\n"
                f"💳 {get_subscription_status(user)}\n"
                f"🎁 Рефералов: {invited}\n"
            )
            if bonus > 0:
                text += f"⏳ Бонус: {format_time_left(bonus)}\n"
            text += "\n🔰 Выберите действие:"
            await safe_edit(query, text, reply_markup=get_main_keyboard(user))
        else:
            welcome = (
                "🌟 Добро пожаловать в Friend Sender 🌟\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "🤖 Профессиональный бот для рассылок\n\n"
                "💎 Тарифы:\n"
                f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
                f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
                f"• 6 месяцев — ${TARIFFS['6m']['price']}\n\n"
                f"🎁 Или бесплатно: +{REF_BONUS_MINUTES} мин за друга\n\n"
                "Выберите действие:"
            )
            await safe_edit(query, welcome, reply_markup=get_no_subscription_keyboard(user_id))
        return
    
    # ========== АДМИН ==========
    if data.startswith('admin_') or data == 'admin_panel':
        if not await is_admin(user_id, context):
            await safe_edit(query, "❌ Нет доступа!", reply_markup=get_back_keyboard())
            return
        
        if data in ('admin_panel', 'admin_list'):
            if data == 'admin_list':
                if not user_data:
                    await safe_edit(query, "📭 Нет пользователей", reply_markup=get_admin_keyboard())
                    return
                text = "📋 Список пользователей\n━━━━━━━━━━━━━━━━━━━━\n\n"
                for uid, u in user_data.items():
                    sub = u.get('subscription', {})
                    status = "✅" if sub.get('active') else "❌"
                    display = get_user_display(uid)
                    text += f"{status} {display}\n"
                await safe_edit(query, text, reply_markup=get_admin_user_list_keyboard(user_data))
            else:
                await safe_edit(query, "👑 Админ-панель\n━━━━━━━━━━━━━━━━━━━━\n\nВыберите действие:", reply_markup=get_admin_keyboard())
            return
        
        if data == 'admin_search':
            await safe_edit(
                query,
                "🔍 Поиск пользователя\n━━━━━━━━━━━━━━━━━━━━\n\n"
                "Отправьте @username или ID:\n"
                "• @username\n"
                "• 123456789",
                reply_markup=get_back_keyboard('admin_panel')
            )
            user['awaiting'] = 'admin_search_input'
            save_data()
            return
        
        if data == 'admin_add_subscription':
            await safe_edit(
                query,
                "➕ Выдача подписки\n━━━━━━━━━━━━━━━━━━━━\n\n"
                "Отправьте @username и количество дней:\n"
                "Пример: @username 30\n\n"
                "Или выберите из списка:",
                reply_markup=get_admin_user_list_keyboard(user_data)
            )
            user['awaiting'] = 'admin_add_subscription_input'
            user['temp_data'] = {}
            save_data()
            return
        
        if data == 'admin_remove_subscription':
            await safe_edit(
                query,
                "➖ Снятие подписки\n━━━━━━━━━━━━━━━━━━━━\n\n"
                "Выберите пользователя:",
                reply_markup=get_admin_user_list_keyboard(user_data)
            )
            user['awaiting'] = 'admin_remove_subscription_input'
            save_data()
            return
        
        if data == 'admin_bonus_menu':
            total_bonus = sum(u.get('bonus_active_seconds', 0) for u in user_data.values())
            total_earned = sum(u.get('ref', {}).get('total_bonus_earned', 0) for u in user_data.values())
            total_refs = sum(len(u.get('ref', {}).get('invited_users', [])) for u in user_data.values())
            text = (
                "🎁 Бонусы (реферальная система)\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"👥 Всего рефералов: {total_refs}\n"
                f"⏳ Активных бонусов: {format_time_left(total_bonus)}\n"
                f"🏆 Всего заработано: {format_time_left(total_earned)}\n\n"
                f"💡 За каждого рефера: +{REF_BONUS_MINUTES} мин"
            )
            await safe_edit(query, text, reply_markup=get_admin_bonus_keyboard())
            return
        
        if data.startswith('admin_user_'):
            target_id = int(data.split('_')[2])
            if target_id not in user_data:
                await safe_edit(query, "❌ Не найден", reply_markup=get_admin_keyboard())
                return
            u = user_data[target_id]
            sub = u.get('subscription', {})
            bonus = u.get('bonus_active_seconds', 0)
            invited = len(u.get('ref', {}).get('invited_users', []))
            if sub.get('active') and sub.get('expires_at'):
                try:
                    exp = datetime.fromisoformat(sub['expires_at'])
                    status = "✅ Активна" if datetime.now() < exp else "❌ Истекла"
                    exp_str = exp.strftime("%d.%m.%Y %H:%M")
                except:
                    status = "❌ Ошибка"
                    exp_str = "—"
            else:
                status = "❌ Неактивна"
                exp_str = "—"
            accounts = u.get('accounts', {})
            active_accs = sum(1 for a in accounts.values() if a.get('is_authenticated') and not a.get('is_banned'))
            banned = sum(1 for a in accounts.values() if a.get('is_banned'))
            stats = u.get('stats', {})
            text = (
                f"👤 {get_user_display(target_id)}\n"
                f"🆔 ID: {target_id}\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💳 Подписка: {status}\n"
                f"📅 До: {exp_str}\n"
                f"💵 Оплата: {sub.get('payment_status', '—')}\n\n"
                f"🎁 Рефералов: {invited}\n"
                f"⏳ Бонус: {format_time_left(bonus)}\n\n"
                f"📱 Аккаунтов: {active_accs} / бан: {banned}\n"
                f"✅ Отправлено: {stats.get('sent', 0)}\n"
                f"❌ Ошибок: {stats.get('failed', 0)}"
            )
            await safe_edit(query, text, reply_markup=get_admin_user_menu_keyboard(target_id))
            return
        
        if data.startswith('admin_activate_custom_'):
            target_id = int(data.split('_')[3])
            await safe_edit(
                query,
                f"➕ Выдача подписки → {get_user_display(target_id)}\n\n"
                "Отправьте количество дней:\n"
                "• 30 — месяц\n"
                "• 90 — 3 месяца\n"
                "• 180 — 6 месяцев",
                reply_markup=get_admin_keyboard()
            )
            user['awaiting'] = 'admin_add_subscription_input'
            user['temp_data'] = {'target_id': target_id}
            save_data()
            return
        
        if data.startswith('admin_activate_'):
            parts = data.split('_')
            target_id = int(parts[2])
            days = int(parts[3])
            if target_id not in user_data:
                init_user(target_id)
            sub = user_data[target_id]['subscription']
            sub['active'] = True
            sub['expires_at'] = (datetime.now() + timedelta(days=days)).isoformat()
            sub['payment_status'] = 'admin_activated'
            save_data()
            await safe_edit(query, f"✅ Подписка выдана {get_user_display(target_id)} на {days} дн.", reply_markup=get_admin_keyboard())
            try:
                await context.bot.send_message(
                    chat_id=target_id,
                    text=f"🎉 Подписка активирована!\n📅 На {days} дней",
                    reply_markup=get_main_keyboard(user_data[target_id])
                )
            except:
                pass
            return
        
        if data.startswith('admin_bonus_'):
            parts = data.split('_')
            target_id = int(parts[2])
            minutes = int(parts[3])
            if target_id not in user_data:
                await safe_edit(query, "❌ Пользователь не найден", reply_markup=get_admin_keyboard())
                return
            user_data[target_id]['bonus_active_seconds'] = user_data[target_id].get('bonus_active_seconds', 0) + minutes * 60
            save_data()
            await safe_edit(
                query,
                f"🎁 {get_user_display(target_id)} получил +{minutes} мин бонуса",
                reply_markup=get_admin_keyboard()
            )
            return
        
        if data.startswith('admin_deactivate_'):
            target_id = int(data.split('_')[2])
            if target_id in user_data:
                user_data[target_id]['subscription']['active'] = False
                save_data()
            await safe_edit(query, f"❌ Подписка снята у {get_user_display(target_id)}", reply_markup=get_admin_keyboard())
            return
    
    # ========== ЗАЩИТА ОТ НЕТ ПОДПИСКИ ==========
    if not check_subscription(user):
        await safe_edit(
            query,
            "❌ Доступ запрещён!\n\n"
            "Купите подписку или пригласите друзей.",
            reply_markup=get_no_subscription_keyboard(user_id)
        )
        return
    
    if data == 'buy_subscription':
        await safe_edit(query, "💎 Выберите способ оплаты:", reply_markup=get_payment_method_keyboard())
        return
    
    # ========== ПРОВЕРКА ОПЛАТЫ (АВТОАКТИВАЦИЯ) ==========
    if data == 'check_payment':
        sub = user['subscription']
        payment_id = sub.get('payment_id')
        if not payment_id:
            await safe_edit(query, "❌ Нет активного платежа", reply_markup=get_payment_method_keyboard())
            return
        result = check_crypto_payment(payment_id)
        if not result['success']:
            await safe_edit(
                query,
                f"❌ Ошибка проверки: {result.get('error')}\n\n"
                "Попробуйте позже или обратитесь в поддержку.",
                reply_markup=get_subscription_keyboard()
            )
            return
        status = result['status']
        sub['payment_status'] = status
        save_data()
        if status == 'paid':
            tariff_key = sub.get('tariff', '1m')
            tariff = TARIFFS.get(tariff_key, TARIFFS['1m'])
            sub['active'] = True
            sub['expires_at'] = (datetime.now() + timedelta(days=tariff['days'])).isoformat()
            save_data()
            await safe_edit(
                query,
                "✅ ОПЛАЧЕНО!\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"🎉 Подписка активирована автоматически\n"
                f"📅 Тариф: {tariff['label']}\n"
                f"💰 Сумма: ${tariff['price']}",
                reply_markup=get_main_keyboard(user)
            )
        elif status in ('active', 'pending'):
            await safe_edit(
                query,
                "⏳ Ожидание оплаты...\n\n"
                "Платёж обрабатывается.\n"
                "Нажмите «🔄 Проверить оплату» через минуту.",
                reply_markup=get_subscription_keyboard()
            )
        else:
            await safe_edit(
                query,
                f"❌ Платёж не удался (статус: {status})\n\n"
                "Попробуйте создать новый или обратитесь в поддержку.",
                reply_markup=get_payment_method_keyboard()
            )
        return
    
    if data == 'subscription_info':
        sub = user['subscription']
        bonus = user.get('bonus_active_seconds', 0)
        text = (
            "💎 Информация о подписке\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Статус: {get_subscription_status(user)}\n\n"
            "📅 Тарифы:\n"
            f"• 1 месяц — ${TARIFFS['1m']['price']}\n"
            f"• 3 месяца — ${TARIFFS['3m']['price']}\n"
            f"• 6 месяцев — ${TARIFFS['6m']['price']}\n"
        )
        if bonus > 0:
            text += f"\n⏳ Бонус: {format_time_left(bonus)}"
        await safe_edit(query, text, reply_markup=get_main_keyboard(user))
        return
    
    # ========== РАССЫЛКА ==========
    if data == 'start_spam_menu':
        accounts = get_active_accounts(user)
        if not accounts:
            await safe_edit(query, "❌ Сначала добавьте аккаунт!", reply_markup=get_main_keyboard(user))
            return
        if not user.get('selected_accounts'):
            await safe_edit(query, "❌ Выберите аккаунты для рассылки!", reply_markup=get_main_keyboard(user))
            return
        if not get_unspammed_targets(user):
            await safe_edit(query, "❌ Нет целей для рассылки!", reply_markup=get_main_keyboard(user))
            return
        await safe_edit(query, "✏️ Выберите сообщение для рассылки:", reply_markup=get_message_choice_keyboard())
    
    elif data == 'new_message':
        await safe_edit(query, "✏️ Введите текст сообщения:", reply_markup=get_back_keyboard('back_to_menu'))
        user['awaiting'] = 'spam_message_input'
        save_data()
    
    elif data == 'use_saved_message':
        if not user.get('last_spam_message'):
            await safe_edit(query, "❌ Нет сохранённого сообщения", reply_markup=get_main_keyboard(user))
            return
        await start_spam_with_message_callback(update, context, user, user['last_spam_message'])
    
    elif data == 'stop_spam':
        if user.get('is_sending'):
            await stop_sending(user_id)
            await safe_edit(query, "⏸ Рассылка остановлена", reply_markup=get_main_keyboard(user))
        else:
            await safe_edit(query, "ℹ️ Рассылка не запущена", reply_markup=get_main_keyboard(user))
    
    elif data == 'spam_settings':
        targets = len(get_unspammed_targets(user))
        selected = len(user.get('selected_accounts', []))
        interval = user.get('spam_interval', 60)
        text = (
            "⚙️ Настройки рассылки\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎯 Целей: {targets}\n"
            f"📱 Выбрано аккаунтов: {selected}\n"
            f"⏱ Интервал: {interval} сек"
        )
        await safe_edit(query, text, reply_markup=get_spam_settings_keyboard(user))
    
    elif data == 'set_targets':
        await safe_edit(
            query,
            "📝 Укажите цели\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "Отправьте список @username или ID:\n"
            "@user1 123456789 @user2\n\n"
            "Через пробел или с новой строки",
            reply_markup=get_back_keyboard('spam_settings')
        )
        user['awaiting'] = 'targets_input'
        save_data()
    
    elif data == 'delete_targets_menu':
        await safe_edit(query, "🗑 Удаление целей\n\nВыберите:", reply_markup=get_delete_targets_keyboard(user))
    
    elif data.startswith('del_target_'):
        idx = int(data.split('_')[2])
        targets = get_unspammed_targets(user)
        if 0 <= idx < len(targets):
            removed = targets[idx]
            user['targets'] = [t for t in user['targets'] if t['target'] != removed['target']]
            save_data()
            await safe_edit(query, f"✅ Удалено: {removed['target']}", reply_markup=get_delete_targets_keyboard(user))
    
    elif data == 'del_all_targets':
        user['targets'] = []
        save_data()
        await safe_edit(query, "✅ Все цели удалены", reply_markup=get_spam_settings_keyboard(user))
    
    elif data == 'select_accounts':
        selected = user.get('selected_accounts', [])
        await safe_edit(query, "🎯 Выберите аккаунты:", reply_markup=get_accounts_selection_keyboard(user, selected))
    
    elif data.startswith('toggle_acc_'):
        acc_id = int(data.split('_')[2])
        selected = user.get('selected_accounts', []).copy()
        if acc_id in selected:
            selected.remove(acc_id)
        else:
            selected.append(acc_id)
        user['selected_accounts'] = selected
        save_data()
        await safe_edit(query, "🎯 Выберите аккаунты:", reply_markup=get_accounts_selection_keyboard(user, selected))
    
    elif data == 'select_all_accounts':
        accounts = get_active_accounts(user)
        user['selected_accounts'] = [a[0] for a in accounts]
        save_data()
        await safe_edit(query, "🎯 Выберите аккаунты:", reply_markup=get_accounts_selection_keyboard(user, user['selected_accounts']))
    
    elif data == 'clear_all_accounts':
        user['selected_accounts'] = []
        save_data()
        await safe_edit(query, "🎯 Выберите аккаунты:", reply_markup=get_accounts_selection_keyboard(user, []))
    
    elif data == 'account_settings':
        accounts = get_active_accounts(user)
        text = (
            "📱 Настройки аккаунтов\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Активных аккаунтов: {len(accounts)}"
        )
        await safe_edit(query, text, reply_markup=get_account_settings_keyboard(user))
    
    elif data == 'add_account':
        await safe_edit(
            query,
            "🔑 Добавление аккаунта\n━━━━━━━━━━━━━━━━━━━━\n\n"
            "Отправьте одним сообщением:\n"
            "• API ID\n• API Hash\n• Номер телефона\n\n"
            "Пример:\n37125395 fbe52d8c74c9b927f7d70f8c32b8c84f +380991234567",
            reply_markup=get_back_keyboard('back_to_menu')
        )
        user['auth_step'] = 'waiting_api'
        save_data()
    
    elif data == 'set_interval':
        await safe_edit(
            query,
            "⏱ Интервал между отправками\n\n"
            f"Текущий: {user.get('spam_interval', 60)} сек\n\n"
            "Отправьте число в секундах (30, 60, 120):",
            reply_markup=get_back_keyboard('spam_settings')
        )
        user['awaiting'] = 'interval_input'
        save_data()
    
    elif data == 'list_accounts':
        accounts = get_active_accounts(user)
        if not accounts:
            await safe_edit(query, "Нет активных аккаунтов", reply_markup=get_account_settings_keyboard(user))
            return
        text = "📱 Ваши аккаунты:\n━━━━━━━━━━━━━━━━━━━━\n\n"
        for acc_id, acc in accounts:
            text += f"• {acc.get('name', f'Аккаунт {acc_id}')}\n"
        banned = [(i, a) for i, a in user.get('accounts', {}).items() if a.get('is_banned')]
        if banned:
            text += "\n🚫 Забаненные:\n"
            for i, a in banned:
                text += f"• {a.get('name', f'Аккаунт {i}')}\n"
        await safe_edit(query, text, reply_markup=get_account_settings_keyboard(user))
    
    elif data == 'delete_account_menu':
        await safe_edit(query, "🗑 Удаление аккаунта\n\nВыберите:", reply_markup=get_delete_account_keyboard(user))
    
    elif data.startswith('delete_acc_'):
        acc_id = int(data.split('_')[2])
        if acc_id in user['accounts']:
            name = user['accounts'][acc_id].get('name', f'Аккаунт {acc_id}')
            if acc_id in user.get('selected_accounts', []):
                user['selected_accounts'].remove(acc_id)
            del user['accounts'][acc_id]
            save_data()
            await safe_edit(query, f"✅ Удалён: {name}", reply_markup=get_account_settings_keyboard(user))
    
    elif data == 'parse_menu':
        text = (
            "👥 Парсинг пользователей\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Выберите метод:\n\n"
            "👥 Все участники — все из чата\n"
            "💬 Из сообщений — активные юзеры\n"
            "📰 Комментаторы — под постами канала"
        )
        await safe_edit(query, text, reply_markup=get_parse_keyboard())
    
    elif data == 'parse_participants':
        await safe_edit(
            query,
            "👥 Все участники чата\n\n"
            "Отправьте ссылку или ID:\n@channel\n-1001234567890\nhttps://t.me/...",
            reply_markup=get_back_keyboard('parse_menu')
        )
        user['awaiting'] = 'parse_chat_link'
        user['temp_data'] = {'parse_method': 'participants'}
        save_data()
    
    elif data == 'parse_messages_custom':
        await safe_edit(query, "📊 Сколько сообщений парсить?", reply_markup=get_message_count_keyboard())
    
    elif data == 'parse_channel_posts':
        await safe_edit(
            query,
            "📰 Комментаторы канала\n\n"
            "Сколько последних постов обработать?",
            reply_markup=get_message_count_keyboard()
        )
        user['temp_data'] = {'parse_method': 'channel_posts'}
        save_data()
    
    elif data.startswith('parse_count_'):
        if data == 'parse_count_custom':
            await safe_edit(query, "✏️ Введите число:", reply_markup=get_back_keyboard('parse_menu'))
            user['awaiting'] = 'parse_count_input'
            save_data()
        else:
            count = int(data.split('_')[2])
            method = user.get('temp_data', {}).get('parse_method', 'messages')
            if method == 'channel_posts':
                await safe_edit(
                    query,
                    f"✅ {count} постов\n\n📰 Ссылка на канал:",
                    reply_markup=get_back_keyboard('parse_menu')
                )
                user['awaiting'] = 'parse_channel_link'
                user['temp_data'] = {'parse_method': 'channel_posts', 'post_count': count}
            else:
                await safe_edit(
                    query,
                    f"✅ {count} сообщений\n\n🔗 Ссылка на чат:",
                    reply_markup=get_back_keyboard('parse_menu')
                )
                user['awaiting'] = 'parse_chat_link'
                user['temp_data'] = {'parse_method': 'messages', 'message_count': count}
            save_data()
    
    elif data == 'status':
        accounts = get_active_accounts(user)
        selected = user.get('selected_accounts', [])
        targets = get_unspammed_targets(user)
        stats = user.get('stats', {})
        bonus = user.get('bonus_active_seconds', 0)
        invited = len(user['ref'].get('invited_users', []))
        text = (
            "ℹ️ Статус\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "📊 Активность:\n"
            f"├ 📱 Аккаунтов: {len(accounts)}\n"
            f"├ 🎯 Выбрано: {len(selected)}\n"
            f"└ 👥 Целей: {len(targets)}\n\n"
            "📈 Результаты:\n"
            f"├ ✅ Отправлено: {stats.get('sent', 0)}\n"
            f"├ ❌ Ошибок: {stats.get('failed', 0)}\n"
            f"└ 🚫 Банов: {stats.get('banned_accounts', 0)}\n\n"
            f"⏱ Интервал: {user.get('spam_interval', 60)} сек\n"
            f"🚦 Статус: {'🔴 Активен' if user.get('is_sending') else '⚫ Остановлен'}\n\n"
            "💎 Доступ:\n"
            f"├ 💳 {get_subscription_status(user)}\n"
            f"└ 🎁 Рефералов: {invited}\n"
        )
        if bonus > 0:
            text += f"⏳ Бонус: {format_time_left(bonus)}\n"
        await safe_edit(query, text, reply_markup=get_main_keyboard(user))

# ========== ЗАПУСК РАССЫЛКИ ==========
async def start_spam_with_message_callback(update, context, user, message_text):
    user_id = update.effective_user.id
    if user.get('is_sending'):
        await update.callback_query.message.reply_text("⚠️ Уже запущено")
        return
    selected = user.get('selected_accounts', [])
    targets = get_unspammed_targets(user)
    if not selected or not targets:
        await update.callback_query.message.reply_text("❌ Нет аккаунтов или целей")
        return
    accounts_to_use = []
    for acc_id in selected:
        if acc_id in user['accounts']:
            acc = user['accounts'][acc_id]
            if acc.get('is_authenticated') and not acc.get('is_banned') and acc.get('client'):
                try:
                    if not acc['client'].is_connected():
                        await acc['client'].connect()
                    if acc['client'].is_connected():
                        accounts_to_use.append((acc_id, acc))
                except:
                    pass
    if not accounts_to_use:
        await update.callback_query.message.reply_text("❌ Нет активных аккаунтов")
        return
    user['last_spam_message'] = message_text
    user['is_sending'] = True
    save_data()
    interval = user.get('spam_interval', 60)
    bonus = user.get('bonus_active_seconds', 0)
    status_msg = await update.callback_query.message.reply_text(
        f"🚀 РАССЫЛКА ЗАПУЩЕНА\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📨 {message_text[:80]}...\n"
        f"📱 Аккаунтов: {len(accounts_to_use)}\n"
        f"🎯 Целей: {len(targets)}\n"
        f"⏱ Интервал: {interval} сек\n"
        f"⏳ Бонус: {format_time_left(bonus)}",
        reply_markup=get_spam_active_keyboard()
    )
    task = asyncio.create_task(send_spam_round_robin(user_id, context, message_text, accounts_to_use, targets, interval, status_msg))
    sending_tasks[user_id] = task

async def start_spam_with_message(update, context, user, message_text):
    user_id = update.effective_user.id
    if user.get('is_sending'):
        await update.message.reply_text("⚠️ Уже запущено")
        return
    selected = user.get('selected_accounts', [])
    targets = get_unspammed_targets(user)
    if not selected or not targets:
        await update.message.reply_text("❌ Нет аккаунтов или целей")
        return
    accounts_to_use = []
    for acc_id in selected:
        if acc_id in user['accounts']:
            acc = user['accounts'][acc_id]
            if acc.get('is_authenticated') and not acc.get('is_banned') and acc.get('client'):
                try:
                    if not acc['client'].is_connected():
                        await acc['client'].connect()
                    if acc['client'].is_connected():
                        accounts_to_use.append((acc_id, acc))
                except:
                    pass
    if not accounts_to_use:
        await update.message.reply_text("❌ Нет активных аккаунтов")
        return
    user['last_spam_message'] = message_text
    user['is_sending'] = True
    save_data()
    interval = user.get('spam_interval', 60)
    bonus = user.get('bonus_active_seconds', 0)
    status_msg = await update.message.reply_text(
        f"🚀 РАССЫЛКА ЗАПУЩЕНА\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📨 {message_text[:80]}...\n"
        f"📱 Аккаунтов: {len(accounts_to_use)}\n"
        f"🎯 Целей: {len(targets)}\n"
        f"⏱ Интервал: {interval} сек\n"
        f"⏳ Бонус: {format_time_left(bonus)}",
        reply_markup=get_spam_active_keyboard()
    )
    task = asyncio.create_task(send_spam_round_robin(user_id, context, message_text, accounts_to_use, targets, interval, status_msg))
    sending_tasks[user_id] = task

async def send_spam_round_robin(user_id, context, message_text, accounts, targets, interval, status_message):
    user = user_data.get(user_id)
    if not user:
        return
    successful = 0
    failed = 0
    idx = 0
    n = len(accounts)
    remaining = [t.copy() for t in targets]
    try:
        while remaining and user.get('is_sending'):
            acc_id, acc = accounts[idx]
            target = remaining[0]['target']
            client = acc.get('client')
            is_active = False
            if client and not acc.get('is_banned'):
                try:
                    if not client.is_connected():
                        await client.connect()
                    if client.is_connected():
                        is_active = True
                except:
                    pass
            if not is_active:
                failed += 1
            else:
                try:
                    await client.send_message(target, message_text)
                    successful += 1
                    remaining.pop(0)
                    stats = user.get('stats', {})
                    stats['sent'] = successful
                    user['stats'] = stats
                    user['targets'] = [t for t in user['targets'] if t['target'] != target]
                    if user.get('bonus_active_seconds', 0) > 0:
                        user['bonus_active_seconds'] = max(0, user['bonus_active_seconds'] - 1)
                    save_data()
                    try:
                        await status_message.edit_text(
                            f"🚀 РАССЫЛКА\n━━━━━━━━━━━━━━━━━━━━\n\n"
                            f"📨 {message_text[:80]}...\n"
                            f"📱 Аккаунтов: {n}\n"
                            f"🎯 Осталось: {len(remaining)}\n"
                            f"✅ Отправлено: {successful}\n"
                            f"❌ Ошибок: {failed}\n"
                            f"⏱ {interval} сек | {acc.get('name', acc_id)} → {target}",
                            reply_markup=get_spam_active_keyboard()
                        )
                    except:
                        pass
                except FloodWaitError as e:
                    acc['is_banned'] = True
                    acc['ban_reason'] = f"FloodWait: {e.seconds}с"
                    failed += 1
                    stats = user.get('stats', {})
                    stats['failed'] = failed
                    stats['banned_accounts'] = stats.get('banned_accounts', 0) + 1
                    user['stats'] = stats
                    user['is_sending'] = False
                    save_data()
                    await context.bot.send_message(chat_id=user_id, text=f"🚫 Бан аккаунта {acc.get('name', acc_id)}")
                    break
                except (UserInvalidError, UsernameInvalidError, PeerFloodError) as e:
                    failed += 1
                    stats = user.get('stats', {})
                    stats['failed'] = failed
                    user['stats'] = stats
                except RPCError as e:
                    em = str(e).lower()
                    if 'flood' in em or 'too many' in em:
                        acc['is_banned'] = True
                        acc['ban_reason'] = str(e)[:100]
                        failed += 1
                        stats = user.get('stats', {})
                        stats['failed'] = failed
                        stats['banned_accounts'] = stats.get('banned_accounts', 0) + 1
                        user['stats'] = stats
                        user['is_sending'] = False
                        save_data()
                        await context.bot.send_message(chat_id=user_id, text=f"🚫 Бан аккаунта {acc.get('name', acc_id)}")
                        break
                    else:
                        failed += 1
            idx = (idx + 1) % n
            if remaining and user.get('is_sending'):
                for s in range(int(interval), 0, -1):
                    if not user.get('is_sending'):
                        break
                    if s % 10 == 0 or s <= 5:
                        try:
                            await status_message.edit_text(
                                f"🚀 РАССЫЛКА\n━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"⏳ Следующая через {s}с\n"
                                f"🎯 Осталось: {len(remaining)}\n"
                                f"✅ {successful} / ❌ {failed}",
                                reply_markup=get_spam_active_keyboard()
                            )
                        except:
                            pass
                    await asyncio.sleep(1)
        if user.get('is_sending'):
            user['is_sending'] = False
            save_data()
            try:
                await status_message.edit_text(
                    f"✅ РАССЫЛКА ЗАВЕРШЕНА\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ Отправлено: {successful}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"🎯 Осталось: {len(remaining)}",
                    reply_markup=get_main_keyboard(user)
                )
            except:
                pass
    except asyncio.CancelledError:
        user['is_sending'] = False
        save_data()
    if user_id in sending_tasks:
        del sending_tasks[user_id]

# ========== ОБРАБОТКА ТЕКСТА ==========
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = init_user(user_id, update.effective_user)
    text = update.message.text
    awaiting = user.get('awaiting')
    auth_step = user.get('auth_step')
    
    # ===== АДМИН: ПОИСК =====
    if awaiting == 'admin_search_input':
        if not await is_admin(user_id, context):
            return
        q = text.strip()
        target_id = None
        if q.startswith('@') or not q.lstrip('-').isdigit():
            for uid, u in user_data.items():
                if u.get('username') and u['username'].lower() == q.lower().lstrip('@'):
                    target_id = uid
                    break
        else:
            try:
                target_id = int(q)
            except:
                pass
        if target_id is None or target_id not in user_data:
            await update.message.reply_text(f"❌ Не найден: {q}", reply_markup=get_admin_keyboard())
            user['awaiting'] = None
            save_data()
            return
        u = user_data[target_id]
        sub = u.get('subscription', {})
        bonus = u.get('bonus_active_seconds', 0)
        invited = len(u.get('ref', {}).get('invited_users', []))
        accounts = u.get('accounts', {})
        active_accs = sum(1 for a in accounts.values() if a.get('is_authenticated') and not a.get('is_banned'))
        banned = sum(1 for a in accounts.values() if a.get('is_banned'))
        stats = u.get('stats', {})
        status = "❌ Неактивна"
        exp_str = "—"
        if sub.get('active') and sub.get('expires_at'):
            try:
                exp = datetime.fromisoformat(sub['expires_at'])
                status = "✅ Активна" if datetime.now() < exp else "❌ Истекла"
                exp_str = exp.strftime("%d.%m.%Y %H:%M")
            except:
                pass
        text_out = (
            f"👤 {get_user_display(target_id)}\n"
            f"🆔 {target_id}\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            f"💳 Подписка: {status}\n"
            f"📅 До: {exp_str}\n\n"
            f"🎁 Рефералов: {invited}\n"
            f"⏳ Бонус: {format_time_left(bonus)}\n\n"
            f"📱 Аккаунтов: {active_accs} / бан: {banned}\n"
            f"✅ Отправлено: {stats.get('sent', 0)}\n"
            f"❌ Ошибок: {stats.get('failed', 0)}"
        )
        await update.message.reply_text(text_out, reply_markup=get_search_result_keyboard(target_id))
        user['awaiting'] = None
        save_data()
        return
    
    # ===== АДМИН: ВЫДАЧА =====
    if awaiting == 'admin_add_subscription_input':
        if not await is_admin(user_id, context):
            return
        temp = user.get('temp_data', {})
        target_id = temp.get('target_id')
        if target_id:
            try:
                days = float(text.strip().replace(',', '.'))
                if days <= 0:
                    return
            except:
                await update.message.reply_text("❌ Введите число дней")
                return
        else:
            parts = text.strip().split()
            if len(parts) < 2:
                await update.message.reply_text("❌ Формат: @username 30")
                return
            ident = parts[0]
            try:
                days = float(parts[1].replace(',', '.'))
            except:
                await update.message.reply_text("❌ Введите число")
                return
            if ident.startswith('@') or not ident.lstrip('-').isdigit():
                target_id = None
                for uid, u in user_data.items():
                    if u.get('username') and u['username'].lower() == ident.lower().lstrip('@'):
                        target_id = uid
                        break
                if target_id is None:
                    await update.message.reply_text("❌ Не найден", reply_markup=get_admin_keyboard())
                    user['awaiting'] = None
                    save_data()
                    return
            else:
                try:
                    target_id = int(ident)
                except:
                    return
        init_user(target_id)
        sub = user_data[target_id]['subscription']
        sub['active'] = True
        sub['expires_at'] = (datetime.now() + timedelta(days=days)).isoformat()
        sub['payment_status'] = 'admin_activated'
        save_data()
        await update.message.reply_text(
            f"✅ Подписка выдана {get_user_display(target_id)} на {days} дн.",
            reply_markup=get_admin_keyboard()
        )
        try:
            await context.bot.send_message(
                chat_id=target_id,
                text=f"🎉 Подписка активирована!\n📅 На {days} дней",
                reply_markup=get_main_keyboard(user_data[target_id])
            )
        except:
            pass
        user['awaiting'] = None
        user['temp_data'] = {}
        save_data()
        return
    
    # ===== АДМИН: СНЯТИЕ =====
    if awaiting == 'admin_remove_subscription_input':
        if not await is_admin(user_id, context):
            return
        ident = text.strip()
        target_id = None
        if ident.startswith('@') or not ident.lstrip('-').isdigit():
            for uid, u in user_data.items():
                if u.get('username') and u['username'].lower() == ident.lower().lstrip('@'):
                    target_id = uid
                    break
        else:
            try:
                target_id = int(ident)
            except:
                pass
        if target_id and target_id in user_data:
            user_data[target_id]['subscription']['active'] = False
            save_data()
            await update.message.reply_text(f"❌ Подписка снята: {get_user_display(target_id)}", reply_markup=get_admin_keyboard())
        else:
            await update.message.reply_text("❌ Не найден", reply_markup=get_admin_keyboard())
        user['awaiting'] = None
        save_data()
        return
    
    if auth_step not in ('waiting_api', 'waiting_code', 'waiting_password') and awaiting not in ('targets_input', 'interval_input', 'spam_message_input', 'parse_chat_link', 'parse_channel_link', 'parse_count_input'):
        if not await is_admin(user_id, context):
            if not check_subscription(user):
                await update.message.reply_text("❌ Нет доступа. Купите подписку или пригласите друзей.", reply_markup=get_no_subscription_keyboard(user_id))
                return
    
    # ===== АВТОРИЗАЦИЯ =====
    if auth_step == 'waiting_api':
        try:
            parsed = parse_account_data(text)
            if not parsed['api_id'] or not parsed['api_hash'] or not parsed['phone']:
                await update.message.reply_text("❌ Не все данные найдены", reply_markup=get_back_keyboard('back_to_menu'))
                return
            acc_id = user.get('next_account_id', 1)
            user['accounts'][acc_id] = ACCOUNT_DATA.copy()
            user['accounts'][acc_id]['api_id'] = parsed['api_id']
            user['accounts'][acc_id]['api_hash'] = parsed['api_hash']
            user['accounts'][acc_id]['phone'] = parsed['phone']
            user['next_account_id'] = acc_id + 1
            user['auth_account_id'] = acc_id
            client = TelegramClient(f"session_{user_id}_{acc_id}", int(parsed['api_id']), parsed['api_hash'])
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(parsed['phone'])
                user['accounts'][acc_id]['client'] = client
                user['auth_step'] = 'waiting_code'
                save_data()
                await update.message.reply_text("✅ Код отправлен\n\nВведите код из Telegram:")
            else:
                me = await client.get_me()
                user['accounts'][acc_id]['client'] = client
                user['accounts'][acc_id]['is_authenticated'] = True
                user['accounts'][acc_id]['name'] = me.first_name
                user['auth_step'] = None
                save_data()
                await update.message.reply_text(f"✅ Аккаунт {me.first_name} добавлен!", reply_markup=get_main_keyboard(user))
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}", reply_markup=get_back_keyboard('back_to_menu'))
    
    elif auth_step == 'waiting_code':
        try:
            code = re.sub(r'\D', '', text.strip())
            acc_id = user['auth_account_id']
            acc = user['accounts'][acc_id]
            try:
                await acc['client'].sign_in(acc['phone'], code)
                me = await acc['client'].get_me()
                acc['is_authenticated'] = True
                acc['name'] = me.first_name
                user['auth_step'] = None
                save_data()
                await update.message.reply_text(f"✅ Аккаунт {me.first_name} добавлен!", reply_markup=get_main_keyboard(user))
            except SessionPasswordNeededError:
                user['auth_step'] = 'waiting_password'
                save_data()
                await update.message.reply_text("🔐 Введите пароль 2FA:")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    elif auth_step == 'waiting_password':
        try:
            password = text.strip()
            acc_id = user['auth_account_id']
            acc = user['accounts'][acc_id]
            await acc['client'].sign_in(password=password)
            me = await acc['client'].get_me()
            acc['is_authenticated'] = True
            acc['name'] = me.first_name
            user['auth_step'] = None
            save_data()
            await update.message.reply_text(f"✅ Аккаунт {me.first_name} добавлен!", reply_markup=get_main_keyboard(user))
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {e}")
    
    # ===== ЦЕЛИ =====
    elif awaiting == 'targets_input':
        found = list(dict.fromkeys([t for t in re.findall(r'@\w+|-\d+|\d+', text) if t]))
        existing = {t['target'] for t in user.get('targets', [])}
        new = [{'target': t, 'spammed': False} for t in found if t not in existing]
        if new:
            user['targets'] = user.get('targets', []) + new
            save_data()
            await update.message.reply_text(f"✅ Добавлено целей: {len(new)}", reply_markup=get_spam_settings_keyboard(user))
        else:
            await update.message.reply_text("❌ Все цели уже есть", reply_markup=get_spam_settings_keyboard(user))
        user['awaiting'] = None
        save_data()
    
    # ===== ИНТЕРВАЛ =====
    elif awaiting == 'interval_input':
        try:
            val = float(text.replace(',', '.'))
            if val <= 0:
                return
            if val < 1:
                val *= 60
            user['spam_interval'] = val
            save_data()
            await update.message.reply_text(f"✅ Интервал: {val} сек", reply_markup=get_spam_settings_keyboard(user))
        except:
            await update.message.reply_text("❌ Введите число", reply_markup=get_spam_settings_keyboard(user))
        user['awaiting'] = None
        save_data()
    
    # ===== СООБЩЕНИЕ =====
    elif awaiting == 'spam_message_input':
        if not text.strip():
            await update.message.reply_text("❌ Пустое сообщение")
            return
        await start_spam_with_message(update, context, user, text)
    
    # ===== ПАРСИНГ =====
    elif awaiting == 'parse_count_input':
        try:
            c = int(text.strip())
            if c <= 0:
                return
            method = user.get('temp_data', {}).get('parse_method', 'messages')
            if method == 'channel_posts':
                await update.message.reply_text(f"✅ {c} постов\n\n📰 Ссылка на канал:", reply_markup=get_back_keyboard('parse_menu'))
                user['awaiting'] = 'parse_channel_link'
                user['temp_data'] = {'parse_method': 'channel_posts', 'post_count': c}
            else:
                await update.message.reply_text(f"✅ {c} сообщений\n\n🔗 Ссылка на чат:", reply_markup=get_back_keyboard('parse_menu'))
                user['awaiting'] = 'parse_chat_link'
                user['temp_data'] = {'parse_method': 'messages', 'message_count': c}
            save_data()
        except:
            await update.message.reply_text("❌ Введите число")
    
    elif awaiting == 'parse_chat_link':
        link = text.strip()
        temp = user.get('temp_data', {})
        method = temp.get('parse_method', 'participants')
        count = temp.get('message_count', 3000)
        status = await update.message.reply_text("⏳ Парсинг...")
        accounts = get_active_accounts(user)
        if not accounts:
            await status.edit_text("❌ Нет аккаунтов", reply_markup=get_no_subscription_keyboard(user_id))
            return
        _, acc = accounts[0]
        client = acc.get('client')
        try:
            entity = await client.get_entity(int(link)) if link.lstrip('-').isdigit() else await client.get_entity(link)
            start = time.time()
            if method == 'participants':
                users = await parse_chat_participants(client, entity)
            else:
                users = await parse_from_messages(client, entity, count)
            elapsed = int(time.time() - start)
            if users:
                await send_parse_results_to_saved(client, users)
                if user.get('bonus_active_seconds', 0) > 0:
                    user['bonus_active_seconds'] = max(0, user['bonus_active_seconds'] - elapsed)
                    save_data()
                await status.edit_text(
                    f"✅ Парсинг завершён\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👥 Найдено: {len(users)}\n"
                    f"⏱ Время: {elapsed} сек\n"
                    f"📤 Отправлено в «Избранное»",
                    reply_markup=get_main_keyboard(user)
                )
            else:
                await status.edit_text("❌ Ничего не найдено", reply_markup=get_main_keyboard(user))
        except Exception as e:
            await status.edit_text(f"❌ Ошибка: {str(e)[:200]}", reply_markup=get_main_keyboard(user))
        user['awaiting'] = None
        user['temp_data'] = {}
        save_data()
    
    elif awaiting == 'parse_channel_link':
        link = text.strip()
        temp = user.get('temp_data', {})
        count = temp.get('post_count', 100)
        status = await update.message.reply_text("📰 Парсинг комментаторов...")
        accounts = get_active_accounts(user)
        if not accounts:
            await status.edit_text("❌ Нет аккаунтов", reply_markup=get_no_subscription_keyboard(user_id))
            return
        _, acc = accounts[0]
        client = acc.get('client')
        try:
            entity = await client.get_entity(int(link)) if link.lstrip('-').isdigit() else await client.get_entity(link)
            title = getattr(entity, 'title', 'канал')
            start = time.time()
            users = await parse_channel_posts(client, entity, count)
            elapsed = int(time.time() - start)
            if users:
                await send_parse_results_to_saved(client, users)
                if user.get('bonus_active_seconds', 0) > 0:
                    user['bonus_active_seconds'] = max(0, user['bonus_active_seconds'] - elapsed)
                    save_data()
                await status.edit_text(
                    f"✅ Парсинг завершён\n━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📌 Канал: {title}\n"
                    f"👥 Найдено: {len(users)}\n"
                    f"⏱ Время: {elapsed} сек\n"
                    f"📤 Отправлено в «Избранное»",
                    reply_markup=get_main_keyboard(user)
                )
            else:
                await status.edit_text("❌ Не найдено комментаторов", reply_markup=get_main_keyboard(user))
        except Exception as e:
            await status.edit_text(f"❌ Ошибка: {str(e)[:200]}", reply_markup=get_main_keyboard(user))
        user['awaiting'] = None
        user['temp_data'] = {}
        save_data()

# ========== ГЛАВНАЯ ==========
async def main():
    load_data()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    
    print("🤖 Бот запущен!")
    print(f"✅ Админы: {', '.join('@' + a for a in ADMIN_USERNAMES)}")
    print(f"✅ Менеджер: @{MANAGER_USERNAME}")
    print(f"✅ Реф-код: u<user_id> (уникальный для каждого)")
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        print("🛑 Остановлен")
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
