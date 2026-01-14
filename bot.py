import logging
from telegram import Update, ChatPermissions
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from datetime import datetime, timedelta, timezone
import json
import os
import asyncio
import re

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== ПУТИ К ФАЙЛАМ ДАННЫХ ====================
DATA_DIR = "bot_data"
os.makedirs(DATA_DIR, exist_ok=True)
bot_owner_id = 7294324265
WARNINGS_FILE = f"{DATA_DIR}/warnings.json"
WELCOME_FILE = f"{DATA_DIR}/welcome.json"
RULES_FILE = f"{DATA_DIR}/rules.json"
SUPERADMINS_FILE = f"{DATA_DIR}/superadmins.json"
ADMINS_FILE = f"{DATA_DIR}/admins.json"
STATS_FILE = f"{DATA_DIR}/stats.json"

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДАННЫХ ====================
warnings_data = {}
welcome_data = {}
rules_data = {}
superadmins_data = {"owner": None}
admins_data = {}
stats_data = {"chats": [], "users": []}


# ==================== ЗАГРУЗКА ДАННЫХ ====================
def load_data():
    """Загрузка всех данных из JSON-файлов"""
    global warnings_data, welcome_data, rules_data, superadmins_data, admins_data, stats_data
    files_to_load = [
        (WARNINGS_FILE, warnings_data, {}),
        (WELCOME_FILE, welcome_data, {}),
        (RULES_FILE, rules_data, {}),
        (SUPERADMINS_FILE, superadmins_data, {"owner": None}),
        (ADMINS_FILE, admins_data, {}),
        (STATS_FILE, stats_data, {"chats": [], "users": []})
    ]
    for file_path, var_ref, default in files_to_load:
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    if isinstance(var_ref, dict):
                        var_ref.clear()
                        var_ref.update(loaded if isinstance(loaded, dict) else default)
                    logger.info(f"Успешно загружено из {file_path}")
            else:
                save_data(file_path, default)
                logger.info(f"Создан новый файл {file_path}")
        except Exception as e:
            logger.error(f"Ошибка загрузки {file_path}: {e}")
            var_ref.clear()
            var_ref.update(default)


def save_data(file_path: str, data):
    """Сохранение данных в JSON-файл"""
    try:
        temp_path = file_path + ".tmp"
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, file_path)
        logger.info(f"Данные успешно сохранены в {file_path}")
    except Exception as e:
        logger.error(f"Ошибка сохранения в {file_path}: {e}")


# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def get_user_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchini olish: reply yoki @username/user_id orqali
    Returns: (user_object, user_id) yoki (None, None)
    """
    try:
        # 1. Reply orqali
        if update.message.reply_to_message:
            user = update.message.reply_to_message.from_user
            return user, user.id

        # 2. @username yoki user_id orqali
        if context.args:
            identifier = context.args[0]

            # Username (@username yoki username)
            if identifier.startswith('@'):
                username = identifier[1:]
            elif not identifier.isdigit():
                username = identifier
            else:
                # User ID
                try:
                    user_id = int(identifier)
                    member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
                    return member.user, user_id
                except Exception as e:
                    logger.error(f"User ID orqali topib bo'lmadi: {e}")
                    return None, None

            # Username orqali qidirish (chat memberlarini tekshirish)
            try:
                # Telegram API username orqali to'g'ridan-to'g'ri qidirishni qo'llab-quvvatlamaydi
                # Shuning uchun xabar matni orqali username ni olish kerak
                chat_id = update.effective_chat.id

                # Kichik xatoliklar uchun: chat a'zolaridan qidirish imkonsiz
                # Faqat mention qilingan userlarni olish mumkin
                await update.message.reply_text(
                    f"❌ @{username} topilmadi!\n\n"
                    f"💡 <b>Qanday ishlatiladi:</b>\n"
                    f"• Foydalanuvchi xabariga reply qiling\n"
                    f"• Yoki user ID kiriting: <code>/admin 123456789</code>",
                    parse_mode=ParseMode.HTML
                )
                return None, None

            except Exception as e:
                logger.error(f"Username orqali qidirishda xato: {e}")
                return None, None

        return None, None

    except Exception as e:
        logger.error(f"get_user_from_message xatosi: {e}")
        return None, None


async def is_chat_admin(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int = None) -> bool:
    """Проверка, является ли пользователь администратором Telegram-чата"""
    try:
        if user_id is None:
            user_id = update.effective_user.id
        member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        return member.status in ['creator', 'administrator']
    except Exception as e:
        logger.error(f"Ошибка проверки статуса администратора: {e}")
        return False


def is_superadmin(user_id: int) -> bool:
    """Проверка, является ли пользователь суперадмином (только owner)"""
    owner = superadmins_data.get("owner")
    return user_id == owner


def is_bot_admin(chat_id: int, user_id: int) -> bool:
    """Проверка, является ли пользователь обычным админом бота в данном чате"""
    chat_id_str = str(chat_id)
    return user_id in admins_data.get(chat_id_str, [])


async def can_full_moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Полные права модерации: superadmin или Telegram-админ чата"""
    user_id = update.effective_user.id
    if is_superadmin(user_id):
        return True
    if await is_chat_admin(update, context, user_id):
        return True
    return False


async def can_limited_moderate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ограниченные права: block/mute/delete (включает full moderate)"""
    if await can_full_moderate(update, context):
        return True
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    if is_bot_admin(chat_id, user_id):
        return True
    return False


def collect_stats(update: Update):
    """Сбор статистики"""
    if not update.effective_chat or not update.effective_user:
        return
    chat_id_str = str(update.effective_chat.id)
    user_id_str = str(update.effective_user.id)
    changed = False
    if chat_id_str not in stats_data["chats"]:
        stats_data["chats"].append(chat_id_str)
        changed = True
    if user_id_str not in stats_data["users"]:
        stats_data["users"].append(user_id_str)
        changed = True
    if changed:
        save_data(STATS_FILE, stats_data)


# ==================== КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /start"""
    try:
        collect_stats(update)
        if update.effective_chat.type == "private":
            if superadmins_data.get("owner") is None:
                superadmins_data["owner"] = update.effective_user.id
                save_data(SUPERADMINS_FILE, superadmins_data)
                await update.message.reply_text(
                    "👑 <b>Siz botning egasi bo'ldingiz!</b>\n\n"
                    "📋 <b>Asosiy buyruqlar:</b>\n"
                    "/admin - admin tayinlash\n"
                    "/help - barcha buyruqlar\n"
                    "/statsbot - bot statistikasi",
                    parse_mode=ParseMode.HTML
                )
                return

        welcome_message = (
            "👋 <b>Salom! Men guruh moderatsiya boti.</b>\n\n"
            "🔧 <b>Meni qanday ishlatish:</b>\n"
            "1. Guruhga qo'shing\n"
            "2. Administrator huquqlarini bering\n"
            "3. /help - barcha buyruqlar ro'yxati\n\n"
            "💡 <b>Maxsus imkoniyatlar:</b>\n"
            "• Avtomatik moderatsiya\n"
            "• Ogohlantirish tizimi\n"
            "• Kutish xabarlari\n"
            "• Va ko'p narsalar!"
        )
        await update.message.reply_text(welcome_message, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /help"""
    try:
        collect_stats(update)
        help_text = """
📚 <b>Bot buyruqlari</b>

<b>🔰 Umumiy:</b>
/start — botni ishga tushirish
/help — yordam
/rules — guruh qoidalari
/info [reply/@user] — foydalanuvchi ma'lumotlari
/chatid — guruh ID
/admins — guruh administratorlari ro'yxati

<b>🛡️ Moderatsiya:</b>
/ban [reply/@user] — bloklash
/unban [reply/@user] — blokdan chiqarish
/kick [reply/@user] — guruhdan haydash
/mute [reply/@user] [vaqt] — ovozni o'chirish
/unmute [reply/@user] — ovozni yoqish
/warn [reply/@user] [sabab] — ogohlantirish
/warns [reply/@user] — ogohlantirishlar
/resetwarns [reply/@user] — ogohlantirishlarni tozalash
/del [reply] — xabarni o'chirish
/pin [reply] — xabarni pin qilish

<b>👤 Admin boshqaruvi:</b>
/admin [reply/@user/ID] — admin tayinlash (faqat owner)
/unadmin [reply/@user/ID] — adminlikdan olish
/setwelcome [matn] — salomlashuv xabarini o'rnatish
/setrules [matn] — guruh qoidalarini o'rnatish

<b>📊 Superadmin uchun:</b>
/statsbot — bot statistikasi

<b>💡 Vaqt formati:</b>
• m = daqiqa (5m)
• h = soat (2h)
• d = kun (1d)

<b>📝 Foydalanish misollari:</b>
<code>/admin</code> (reply bilan)
<code>/admin @username</code>
<code>/admin 123456789</code>
<code>/mute 30m</code> (reply bilan)
<code>/ban @user Spam uchun</code>
"""
        await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка в /help: {e}")


async def set_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /setwelcome"""
    try:
        collect_stats(update)
        if not await can_full_moderate(update, context):
            await update.message.reply_text("❌ Faqat to'liq huquqli adminlar.")
            return
        if not context.args:
            await update.message.reply_text(
                "ℹ️ <b>Foydalanish:</b> /setwelcome <matn>\n\n"
                "<b>Maxsus kodlar:</b>\n"
                "{user} — yangi a'zo nomi\n"
                "{chat} — guruh nomi\n\n"
                "<b>Misol:</b>\n"
                "<code>/setwelcome Xush kelibsiz {user}! {chat} guruhiga qo'shilganingiz bilan!</code>",
                parse_mode=ParseMode.HTML
            )
            return
        chat_id = str(update.effective_chat.id)
        welcome_text = " ".join(context.args)
        welcome_data[chat_id] = welcome_text
        save_data(WELCOME_FILE, welcome_data)

        # Test preview
        preview = welcome_text.replace("{user}", update.effective_user.mention_html()) \
            .replace("{chat}", update.effective_chat.title)

        await update.message.reply_text(
            f"✅ <b>Kutish xabari o'rnatildi!</b>\n\n"
            f"<b>Namuna:</b>\n{preview}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /setwelcome: {e}")


async def welcome_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие новых участников"""
    try:
        chat_id = str(update.effective_chat.id)
        collect_stats(update)
        if chat_id not in welcome_data:
            return
        for member in update.message.new_chat_members:
            if member.is_bot:
                continue
            if str(member.id) not in stats_data["users"]:
                stats_data["users"].append(str(member.id))
                save_data(STATS_FILE, stats_data)
            text = welcome_data[chat_id] \
                .replace("{user}", member.mention_html()) \
                .replace("{chat}", update.effective_chat.title)
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка в welcome: {e}")


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /rules"""
    try:
        collect_stats(update)
        chat_id = str(update.effective_chat.id)
        if chat_id in rules_data and rules_data[chat_id].strip():
            await update.message.reply_text(
                f"📜 <b>Guruh qoidalari:</b>\n\n{rules_data[chat_id]}",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                "❌ Guruh qoidalari hali o'rnatilmagan.\n\n"
                "💡 Adminlar /setrules buyrug'i bilan qoidalar qo'shishi mumkin."
            )
    except Exception as e:
        logger.error(f"Ошибка в /rules: {e}")


async def set_rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /setrules"""
    try:
        collect_stats(update)
        if not await can_full_moderate(update, context):
            await update.message.reply_text("❌ Faqat to'liq huquqli adminlar.")
            return
        if not context.args:
            await update.message.reply_text(
                "ℹ️ <b>Foydalanish:</b> /setrules <qoidalar>\n\n"
                "<b>Misol:</b>\n"
                "<code>/setrules 1. Spam qilmang\n2. Hurmat bilan muomala qiling</code>",
                parse_mode=ParseMode.HTML
            )
            return
        chat_id = str(update.effective_chat.id)
        rules_text = " ".join(context.args)
        rules_data[chat_id] = rules_text
        save_data(RULES_FILE, rules_data)

        await update.message.reply_text(
            f"✅ <b>Guruh qoidalari o'rnatildi!</b>\n\n"
            f"📜 <b>Qoidalar:</b>\n{rules_text}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /setrules: {e}")


# ==================== УПРАВЛЕНИЕ АДМИНАМИ ====================
async def admins_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admins - список админов Telegram чата"""
    try:
        collect_stats(update)
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        text = "<b>📋 Guruh administratorlari:</b>\n\n"
        for a in admins:
            status = "👑 Egasi" if a.status == "creator" else "🛡️ Admin"
            username = f"@{a.user.username}" if a.user.username else "Username yo'q"
            text += f"{status}: {a.user.mention_html()} ({username})\n"

        text += f"\n<b>Jami:</b> {len(admins)} ta admin"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Ошибка в /admins: {e}")


async def make_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin - foydalanuvchini guruhda haqiqiy admin qilish
    (reply, linked @username yoki user ID orqali)"""
    try:
        collect_stats(update)

        # Kim ishlatishi mumkin:
        user_id = update.effective_user.id
        bot_owner_id = 7294324265  # Sizning ID'ingiz

        if user_id != bot_owner_id and not await is_chat_admin(update, context, user_id):
            await update.message.reply_text(
                "❌ Faqat guruh adminlari yoki bot egasi ishlatishi mumkin."
            )
            return

        target_user = None
        target_id = None

        # 1-usul: reply to message (eng ishonchli)
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
            target_id = target_user.id

        # 2-usul: linked mention (@username Telegram avto ko'k link qilgan bo'lsa)
        elif update.message.entities:
            for entity in update.message.entities:
                if entity.type in ["mention", "text_mention"]:
                    if entity.user:
                        target_user = entity.user
                        target_id = target_user.id
                        break

        # 3-usul: user ID ni to'g'ridan-to'g'ri yozish (/admin 123456789)
        elif context.args:
            arg = " ".join(context.args).strip()
            if arg.startswith('@'):
                arg = arg[1:]  # @username ni tozalash (agar qo'lda yozilgan bo'lsa)
            if arg.isdigit():
                target_id = int(arg)
                try:
                    member = await context.bot.get_chat_member(update.effective_chat.id, target_id)
                    target_user = member.user
                except:
                    target_user = None

        # Agar hali ham topilmagan bo'lsa — aniq yo'riqnoma
        if target_id is None:
            await update.message.reply_text(
                "❌ Admin beriladigan foydalanuvchini aniqlab bo'lmadi!\n\n"
                "✅ Eng ishonchli usullar:\n"
                "1. Foydalanuvchi xabariga <b>reply</b> qilib /admin yozing\n"
                "2. /admin <b>123456789</b> — user ID ni yozing\n"
                "   • ID ni olish uchun: foydalanuvchi xabariga reply qilib <b>/info</b> yozing\n\n"
                "⚠️ /admin @username faqat Telegram avto <b>ko'k link</b> qilsa ishlaydi\n"
                "   (ya'ni user guruh a'zosi bo'lib, privacy sozlamalari ruxsat bersa).",
                parse_mode=ParseMode.HTML
            )
            return

        chat_id = update.effective_chat.id

        # Tekshirish: allaqachon adminmi yoki guruhda emasmi?
        try:
            member = await context.bot.get_chat_member(chat_id, target_id)
            if member.status in ['administrator', 'creator']:
                await update.message.reply_text("❌ Bu foydalanuvchi allaqachon guruh admini.")
                return
            if member.status in ['left', 'kicked']:
                await update.message.reply_text("❌ Bu foydalanuvchi guruhda emas yoki banlangan.")
                return
        except Exception as e:
            logger.error(f"Status tekshirishda xato: {e}")
            await update.message.reply_text("❌ Foydalanuvchi guruhda emas yoki statusini tekshirib bo'lmadi.")
            return

        # Botning o'z huquqlarini olish (xavfsizlik uchun oshib ketmasin)
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        except Exception as e:
            logger.error(f"Bot statusini olishda xato: {e}")
            bot_member = None

        # Promote qilish: cheklangan huquqlar + bot huquqlaridan oshmasin
        try:
            await context.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=target_id,
                is_anonymous=False,
                can_delete_messages=True,  # majburiy
                can_restrict_members=True,  # majburiy (ban/mute/kick)
                can_pin_messages=bot_member.can_pin_messages if bot_member else False,
                can_change_info=False,
                can_invite_users=bot_member.can_invite_users if bot_member else False,
                can_promote_members=False,  # yangi admin o'ziga admin bera olmasin
                can_manage_chat=False,
                can_post_messages=False,
                can_edit_messages=False,
                can_manage_video_chats=False
            )

            # Qayta tekshirish
            await asyncio.sleep(2)
            new_member = await context.bot.get_chat_member(chat_id, target_id)

            mention = target_user.mention_html() if target_user else f"<code>{target_id}</code>"

            if new_member.status not in ['administrator', 'creator']:
                await update.message.reply_text(
                    f"⚠️ {mention} ga adminlik berildi, lekin hali adminlar ro'yxatida ko'rinmayapti.\n\n"
                    f"• 1-2 daqiqa kutib guruhni yangilang\n"
                    f"• Botga 'Add Administrators' huquqi berilganligini tekshiring!",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"✅ {mention} muvaffaqiyatli guruh admini qilindi!\n\n"
                    f"🔓 <b>Berilgan huquqlar:</b>\n"
                    f"• Xabarlarni oʻchirish\n"
                    f"• Foydalanuvchilarni bloklash/mute/kick qilish\n"
                    f"• Pin qilish (agar botga berilgan bo'lsa)\n\n"
                    f"⚠️ Boshqa huquqlar yo'q (admin tayinlash mumkin emas).",
                    parse_mode=ParseMode.HTML
                )
        except Exception as promote_error:
            logger.error(f"Promote xatosi: {promote_error}")
            await update.message.reply_text(
                "❌ Admin tayinlab bo'lmadi!\n\n"
                "Eng ko'p uchraydigan sabablar:\n"
                "• Botga 'Add Administrators' huquqi berilmagan\n"
                "• Foydalanuvchi guruh a'zosi emas\n\n"
                "🔄 Botni guruhdan chiqarib, qayta qo'shing va bu huquqni yoqing.",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка в /admin: {e}")


async def remove_bot_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /unadmin - guruhdan adminlikni olib tashlash (reply/@username/ID)"""
    try:
        collect_stats(update)

        user_id = update.effective_user.id

        if user_id != bot_owner_id and not await is_chat_admin(update, context, user_id):
            await update.message.reply_text("❌ Faqat guruh adminlari yoki bot egasi ishlatishi mumkin.")
            return

        # Foydalanuvchini olish
        target_user, target_id = await get_user_from_message(update, context)

        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 <b>Qanday ishlatiladi:</b>\n"
                "• Foydalanuvchi xabariga reply qiling\n"
                "• User ID kiriting: <code>/unadmin 123456789</code>\n"
                "• Username: <code>/unadmin @username</code>",
                parse_mode=ParseMode.HTML
            )
            return

        chat_id = update.effective_chat.id

        # Tekshirish: target user adminmi?
        try:
            member = await context.bot.get_chat_member(chat_id, target_id)
            if member.status not in ['administrator', 'creator']:
                await update.message.reply_text(
                    f"❌ {target_user.mention_html()} guruh admini emas.",
                    parse_mode=ParseMode.HTML
                )
                return
            if member.status == 'creator':
                await update.message.reply_text("❌ Guruh egasini adminlikdan olish mumkin emas.")
                return
        except Exception as e:
            logger.error(f"Status tekshirishda xato: {e}")
            await update.message.reply_text("❌ Foydalanuvchi statusini tekshirib bo'lmadi.")
            return

        # Demote qilish
        try:
            await context.bot.demote_chat_member(chat_id=chat_id, user_id=target_id)

            await asyncio.sleep(2)
            new_member = await context.bot.get_chat_member(chat_id, target_id)

            if new_member.status in ['administrator', 'creator']:
                await update.message.reply_text(
                    f"⚠️ <b>{target_user.mention_html()} adminligi olib tashlandi</b>, lekin guruh ro'yxatida hali admin ko'rinmoqda.\n\n"
                    f"📌 <b>Sabablar:</b>\n"
                    f"• Telegram kesh — 1-2 daqiqa kutib yangilang\n"
                    f"• Botga 'Add Administrators' huquqi berilmagan\n\n"
                    f"🔄 Botni guruhdan chiqarib, qayta qo'shing va bu huquqni yoqing!",
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    f"✅ <b>{target_user.mention_html()} muvaffaqiyatli adminlikdan olindi!</b>\n\n"
                    f"Endi oddiy a'zo holatida.",
                    parse_mode=ParseMode.HTML
                )
        except Exception as demote_error:
            logger.error(f"Demote xatosi: {demote_error}")
            await update.message.reply_text(
                "❌ <b>Adminlikni olib bo'lmadi!</b>\n\n"
                "<b>Sabablar:</b>\n"
                "• Botga 'Add Administrators' huquqi berilmagan\n"
                "• Botning o'zi admin emas yoki huquqlari cheklangan\n\n"
                "🔄 Botni guruhdan chiqarib, qayta qo'shing va 'Add Administrators' huquqini yoqing.",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка в /unadmin: {e}")


async def stats_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /statsbot"""
    try:
        collect_stats(update)
        if not is_superadmin(update.effective_user.id):
            await update.message.reply_text("❌ Faqat bot egasi.")
            return
        chats_count = len(stats_data["chats"])
        users_count = len(stats_data["users"])

        # Warnings statistikasi
        total_warnings = sum(len(users) for users in warnings_data.values())

        await update.message.reply_text(
            f"📊 <b>Bot statistikasi:</b>\n\n"
            f"👥 <b>Guruhlar:</b> {chats_count}\n"
            f"🧑‍💼 <b>Foydalanuvchilar:</b> {users_count}\n"
            f"⚠️ <b>Aktiv ogohlantirishlar:</b> {total_warnings}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /statsbot: {e}")


# ==================== МОДЕРАЦИЯ ====================
async def warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_full_moderate(update, context):
            await update.message.reply_text("❌ Faqat to'liq huquqli adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /warn [reply/@user/ID] [sabab]",
                parse_mode=ParseMode.HTML
            )
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(target_id)

        # Agar @username ishlatilgan bo'lsa, context.args[0]ni sabab uchun ishlatmaymiz
        if context.args and context.args[0].startswith('@'):
            reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Sabab ko'rsatilmagan"
        else:
            reason = " ".join(context.args) if context.args else "Sabab ko'rsatilmagan"

        if chat_id not in warnings_data:
            warnings_data[chat_id] = {}
        if user_id not in warnings_data[chat_id]:
            warnings_data[chat_id][user_id] = []

        warnings_data[chat_id][user_id].append({
            "reason": reason,
            "date": datetime.now().isoformat(),
            "by": update.effective_user.id
        })
        count = len(warnings_data[chat_id][user_id])
        save_data(WARNINGS_FILE, warnings_data)

        await update.message.reply_text(
            f"⚠️ <b>{target_user.mention_html()} ogohlantirildi!</b>\n"
            f"📝 <b>Sabab:</b> {reason}\n"
            f"📊 <b>Jami:</b> {count}/3",
            parse_mode=ParseMode.HTML
        )

        if count >= 3:
            await context.bot.ban_chat_member(update.effective_chat.id, target_id)
            await update.message.reply_text(
                f"🔨 <b>{target_user.mention_html()} 3 ogohlantirish uchun bloklandi!</b>",
                parse_mode=ParseMode.HTML
            )
            del warnings_data[chat_id][user_id]
            save_data(WARNINGS_FILE, warnings_data)
    except Exception as e:
        logger.error(f"Ошибка в /warn: {e}")


async def warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user and not target_id:
            # Agar reply/username bo'lmasa, o'zi haqida
            target_user = update.effective_user
            target_id = target_user.id

        chat_id = str(update.effective_chat.id)
        user_id = str(target_id)

        if chat_id in warnings_data and user_id in warnings_data[chat_id]:
            list_text = "\n".join([
                f"{i}. {w['reason']} ({w['date'][:10]})"
                for i, w in enumerate(warnings_data[chat_id][user_id], 1)
            ])
            await update.message.reply_text(
                f"⚠️ <b>{target_user.mention_html()} ogohlantirishlari:</b>\n\n{list_text}\n\n"
                f"📊 <b>Jami:</b> {len(warnings_data[chat_id][user_id])}/3",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"✅ {target_user.mention_html()} ogohlantirishlari yo'q.",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка в /warns: {e}")


async def reset_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_full_moderate(update, context):
            await update.message.reply_text("❌ Faqat to'liq huquqli adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /resetwarns [reply/@user/ID]",
                parse_mode=ParseMode.HTML
            )
            return

        chat_id = str(update.effective_chat.id)
        user_id = str(target_id)

        if chat_id in warnings_data and user_id in warnings_data[chat_id]:
            count = len(warnings_data[chat_id][user_id])
            del warnings_data[chat_id][user_id]
            save_data(WARNINGS_FILE, warnings_data)
            await update.message.reply_text(
                f"✅ <b>{target_user.mention_html()} ogohlantirishlari tozalandi!</b>\n"
                f"Tozalangan: {count} ta ogohlantirish",
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                f"❌ {target_user.mention_html()} ogohlantirishlari yo'q.",
                parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"Ошибка в /resetwarns: {e}")


async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_limited_moderate(update, context):
            await update.message.reply_text("❌ Faqat adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /ban [reply/@user/ID] [sabab]",
                parse_mode=ParseMode.HTML
            )
            return

        if context.args and context.args[0].startswith('@'):
            reason = " ".join(context.args[1:]) if len(context.args) > 1 else "Sabab ko'rsatilmagan"
        else:
            reason = " ".join(context.args) if context.args else "Sabab ko'rsatilmagan"

        await context.bot.ban_chat_member(update.effective_chat.id, target_id)
        await update.message.reply_text(
            f"🔨 <b>{target_user.mention_html()} bloklandi!</b>\n"
            f"📝 <b>Sabab:</b> {reason}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /ban: {e}")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_limited_moderate(update, context):
            await update.message.reply_text("❌ Faqat adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /unban [reply/@user/ID]",
                parse_mode=ParseMode.HTML
            )
            return

        await context.bot.unban_chat_member(update.effective_chat.id, target_id)
        await update.message.reply_text(
            f"✅ <b>{target_user.mention_html()} blokdan chiqarildi!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /unban: {e}")


async def kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_limited_moderate(update, context):
            await update.message.reply_text("❌ Faqat adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /kick [reply/@user/ID]",
                parse_mode=ParseMode.HTML
            )
            return

        await context.bot.ban_chat_member(update.effective_chat.id, target_id)
        await context.bot.unban_chat_member(update.effective_chat.id, target_id)
        await update.message.reply_text(
            f"👞 <b>{target_user.mention_html()} guruhdan haydaldi!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /kick: {e}")


async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_limited_moderate(update, context):
            await update.message.reply_text("❌ Faqat adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /mute [reply/@user/ID] [5m/2h/1d]",
                parse_mode=ParseMode.HTML
            )
            return

        until_date = None
        time_str = " doimiy"

        # Agar @username ishlatilgan bo'lsa
        time_arg = None
        if context.args:
            if context.args[0].startswith('@'):
                time_arg = context.args[1] if len(context.args) > 1 else None
            else:
                time_arg = context.args[0]

        if time_arg:
            arg = time_arg.lower()
            now_utc = datetime.now(timezone.utc)
            if arg.endswith('m'):
                mins = int(arg[:-1])
                until_date = int((now_utc + timedelta(minutes=mins)).timestamp())
                time_str = f" {mins} daqiqaga"
            elif arg.endswith('h'):
                hours = int(arg[:-1])
                until_date = int((now_utc + timedelta(hours=hours)).timestamp())
                time_str = f" {hours} soatga"
            elif arg.endswith('d'):
                days = int(arg[:-1])
                until_date = int((now_utc + timedelta(days=days)).timestamp())
                time_str = f" {days} kunga"
            else:
                await update.message.reply_text("❌ Vaqt formati noto'g'ri (m/h/d).")
                return

        permissions = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target_id,
            permissions=permissions,
            until_date=until_date
        )
        await update.message.reply_text(
            f"🔇 <b>{target_user.mention_html()}{time_str} ovozi o'chirildi!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /mute: {e}")


async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_limited_moderate(update, context):
            await update.message.reply_text("❌ Faqat adminlar.")
            return

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user or not target_id:
            await update.message.reply_text(
                "❌ <b>Foydalanuvchi topilmadi!</b>\n\n"
                "💡 /unmute [reply/@user/ID]",
                parse_mode=ParseMode.HTML
            )
            return

        full_perms = ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_change_info=True,
            can_invite_users=True,
            can_pin_messages=True
        )
        await context.bot.restrict_chat_member(
            update.effective_chat.id,
            target_id,
            permissions=full_perms
        )
        await update.message.reply_text(
            f"🔊 <b>{target_user.mention_html()} ovozi yoqildi!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /unmute: {e}")


async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_limited_moderate(update, context):
            await update.message.reply_text("❌ Faqat adminlar.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ O'chiriladigan xabarga reply qiling.")
            return
        await update.message.reply_to_message.delete()
        # Buyruq xabarini ham o'chirish
        await update.message.delete()
    except Exception as e:
        logger.error(f"Ошибка в /del: {e}")


async def pin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        if not await can_full_moderate(update, context):
            await update.message.reply_text("❌ Faqat to'liq huquqli adminlar.")
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Pin qilinadigan xabarga reply qiling.")
            return
        await context.bot.pin_chat_message(
            update.effective_chat.id,
            update.message.reply_to_message.message_id,
            disable_notification=True
        )
        await update.message.reply_text("📌 Xabar pin qilindi!")
    except Exception as e:
        logger.error(f"Ошибка в /pin: {e}")


# ==================== ДОПОЛНИТЕЛЬНЫЕ ФУНКЦИИ ====================
async def check_keywords_and_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка @admins и ключевых слов (donat, donater, garant)"""
    try:
        if not update.message.text:
            return
        text_lower = update.message.text.lower()

        # Ключевые слова — реклама
        if any(word in text_lower for word in ["donat", "donater", "garant"]):
            reply_text = """
🔥 <b>Eng ishonchli MLBB akkaunt savdo joyi!</b> 🔥

💎 Donatli, garantli va premium akkauntlar mavjud
👑 Tez yetkazib berish va to'liq garant
👤 Admin: @Mlbbmonster
📢 Rasmiy kanal: @monster_akkauntsavdo

Xavfsiz savdo, minglab ijobiy fikrlar! 🚀
Bog'laning va o'z orzuingizdagi akkauntni oling 😎
            """
            await update.message.reply_text(reply_text, parse_mode=ParseMode.HTML)

        # @admins — уведомление всех Telegram-админов чата
        if "@admins" in update.message.text:
            admins = await context.bot.get_chat_administrators(update.effective_chat.id)
            mentions = []
            for admin in admins:
                if not admin.user.is_bot:
                    mentions.append(admin.user.mention_html())
            if mentions:
                mentions_text = " ".join(mentions)
                await update.message.reply_text(
                    f"🆘 <b>Adminlar chaqirildi!</b>\n{mentions_text}",
                    parse_mode=ParseMode.HTML
                )
    except Exception as e:
        logger.error(f"Ошибка в check_keywords_and_admins: {e}")


async def user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /info"""
    try:
        collect_stats(update)

        target_user, target_id = await get_user_from_message(update, context)
        if not target_user:
            target_user = update.effective_user
            target_id = target_user.id

        info_text = (
            "<b>👤 Foydalanuvchi ma'lumotlari:</b>\n\n"
            f"<b>Ism:</b> {target_user.mention_html()}\n"
            f"<b>ID:</b> <code>{target_id}</code>\n"
            f"<b>Username:</b> @{target_user.username if target_user.username else 'yoʻq'}\n"
            f"<b>Premium:</b> {'✅ Bor' if getattr(target_user, 'is_premium', False) else '❌ Yoʻq'}\n"
            f"<b>Bot:</b> {'✅ Ha' if target_user.is_bot else '❌ Yoʻq'}\n"
            f"<b>Til:</b> {target_user.language_code or 'nomaʼlum'}\n\n"
            f"🔗 <a href=\"tg://user?id={target_id}\">Profil</a>"
        )
        await update.message.reply_text(info_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Ошибка в /info: {e}")


async def chat_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        collect_stats(update)
        await update.message.reply_text(
            f"<b>📊 Guruh ma'lumotlari:</b>\n\n"
            f"<b>ID:</b> <code>{update.effective_chat.id}</code>\n"
            f"<b>Nomi:</b> {update.effective_chat.title}\n"
            f"<b>Turi:</b> {update.effective_chat.type}",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка в /chatid: {e}")


# ==================== ЗАПУСК БОТА ====================
def main():
    """Основная функция запуска бота"""
    try:
        load_data()
        BOT_TOKEN = "8312081729:AAH9IZR1dF_QLA4WamD6Wwd36v-ZE7XN_o0"
        if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
            logger.error("❌ Bot tokeni topilmadi! BotFather'dan token oling.")
            return
        logger.info("🔄 Bot ishga tushmoqda...")
        application = Application.builder().token(BOT_TOKEN).concurrent_updates(True).build()

        # Команды
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("rules", rules))
        application.add_handler(CommandHandler("setrules", set_rules))
        application.add_handler(CommandHandler("setwelcome", set_welcome))

        # Модерация
        application.add_handler(CommandHandler("warn", warn))
        application.add_handler(CommandHandler("warns", warns))
        application.add_handler(CommandHandler("resetwarns", reset_warns))
        application.add_handler(CommandHandler("ban", ban))
        application.add_handler(CommandHandler("unban", unban))
        application.add_handler(CommandHandler("kick", kick))
        application.add_handler(CommandHandler("mute", mute))
        application.add_handler(CommandHandler("unmute", unmute))
        application.add_handler(CommandHandler("del", delete_message))
        application.add_handler(CommandHandler("pin", pin_message))

        # Информация
        application.add_handler(CommandHandler("info", user_info))
        application.add_handler(CommandHandler("admins", admins_list))
        application.add_handler(CommandHandler("chatid", chat_id_command))

        # Управление админами
        application.add_handler(CommandHandler("admin", make_bot_admin))
        application.add_handler(CommandHandler("unadmin", remove_bot_admin))
        application.add_handler(CommandHandler("statsbot", stats_bot))

        # Системные обработчики
        application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_user))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_keywords_and_admins))

        logger.info("✅ Bot muvaffaqiyatli ishga tushdi!")
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"❌ Bot ishga tushmadi: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()