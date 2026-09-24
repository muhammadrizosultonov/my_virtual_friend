import asyncio
import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message
)

from database.db import Database
from services.billing_service import (
    calculate_days_until_payment,
    format_billing_alert_message
)

logger = logging.getLogger(__name__)


class ClientStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_contact = State()


class ServerStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_price = State()
    waiting_for_day = State()


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="👥 Mijozlar ro'yxati", callback_data="menu_clients"),
                InlineKeyboardButton(text="➕ Mijoz qo'shish", callback_data="add_client")
            ],
            [
                InlineKeyboardButton(text="💳 Yaqin to'lovlar (3 kunlik)", callback_data="menu_upcoming_bills"),
                InlineKeyboardButton(text="📊 Statistika", callback_data="menu_stats")
            ]
        ]
    )


def create_admin_bot(bot_token: str, admin_chat_id: int, db: Database, tz_name: str = "Asia/Tashkent") -> tuple[Bot, Dispatcher]:
    bot = Bot(token=bot_token)
    dp = Dispatcher(storage=MemoryStorage())
    router = Router()

    # Admin filtr: Faqat belgilangan admin_chat_id bilan ishlash
    router.message.filter(F.from_user.id == admin_chat_id)
    router.callback_query.filter(F.from_user.id == admin_chat_id)

    # ==================== ASOSIY MENYU VA START ====================

    @router.message(CommandStart())
    @router.message(Command("admin"))
    @router.message(Command("menu"))
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        text = (
            "🏢 <b>Assalomu alaykum, Muhammadrizo!</b>\n\n"
            "Mijozlar va serverlar obunasi boshqaruv paneliga xush kelibsiz. "
            "Quyidagi tugmalar orqali kerakli bo'limni tanlang:"
        )
        await message.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode=ParseMode.HTML)

    @router.callback_query(F.data == "main_menu")
    async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        text = (
            "🏢 <b>Asosiy boshqaruv paneli:</b>\n\n"
            "Kerakli bo'limni tanlang:"
        )
        try:
            await callback.message.edit_text(text, reply_markup=get_main_menu_keyboard(), parse_mode=ParseMode.HTML)
        except Exception:
            await callback.message.answer(text, reply_markup=get_main_menu_keyboard(), parse_mode=ParseMode.HTML)
        await callback.answer()

    # ==================== MIJOZLAR RO'YXATI ====================

    @router.callback_query(F.data == "menu_clients")
    async def cb_menu_clients(callback: CallbackQuery):
        clients = await db.get_clients()
        buttons = []

        if clients:
            for c in clients:
                btn_text = f"👤 {c['name']} ({c['servers_count']} ta server)"
                buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"view_client:{c['id']}")])
        else:
            text = "ℹ️ <i>Hozircha tizimda mijozlar mavjud emas.</i>"

        buttons.append([InlineKeyboardButton(text="➕ Yangi mijoz qo'shish", callback_data="add_client")])
        buttons.append([InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="main_menu")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        text = "👥 <b>Mijozlar ro'yxati:</b>\n\nBatafsil ko'rish uchun mijoz nomini bosing:"
        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer()

    # ==================== MIJOZ MA'LUMOTLARI VA SERVERLARI ====================

    @router.callback_query(F.data.startswith("view_client:"))
    async def cb_view_client(callback: CallbackQuery):
        client_id = int(callback.data.split(":")[1])
        client = await db.get_client_by_id(client_id)

        if not client:
            await callback.answer("❌ Mijoz topilmadi!", show_alert=True)
            return

        servers = await db.get_client_servers(client_id)
        contact_str = f"@{client['contact_info']}" if client.get("contact_info") else "<i>Kiritilmagan</i>"

        lines = [
            f"👤 <b>MIJOZ: {client['name']}</b>",
            f"📞 <b>Aloqa:</b> {contact_str}",
            f"📅 <b>Qo'shilgan:</b> {client['created_at'][:10]}\n",
            "🖥 <b>BIRIKTIRILGAN SERVERLAR:</b>"
        ]

        if servers:
            for idx, s in enumerate(servers, start=1):
                lines.append(
                    f"<b>{idx}. {s['server_name']}</b>\n"
                    f"   💰 <b>Narxi:</b> {s['price']} | 🗓 <b>To'lov:</b> Har oyning {s['payment_day']}-kuni"
                )
        else:
            lines.append("<i>Ushbu mijozga hali serverlar biriktirilmagan.</i>")

        keyboard_buttons = [
            [
                InlineKeyboardButton(text="➕ Server qo'shish", callback_data=f"add_server:{client_id}")
            ]
        ]

        if servers:
            keyboard_buttons.append([
                InlineKeyboardButton(text="🗑 Serverni o'chirish", callback_data=f"delete_server_menu:{client_id}")
            ])

        keyboard_buttons.append([
            InlineKeyboardButton(text="🗑 Mijozni o'chirish", callback_data=f"confirm_del_client:{client_id}"),
            InlineKeyboardButton(text="⬅️ Mijozlar", callback_data="menu_clients")
        ])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        text = "\n".join(lines)

        try:
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        except Exception:
            await callback.message.answer(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
        await callback.answer()

    # ==================== MIJOZ QO'SHISH (FSM) ====================

    @router.callback_query(F.data == "add_client")
    async def cb_add_client(callback: CallbackQuery, state: FSMContext):
        await state.set_state(ClientStates.waiting_for_name)
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data="menu_clients")]])
        await callback.message.answer("👤 <b>Yangi mijozning ismini yoki kompaniya nomini kiriting:</b>", reply_markup=cancel_kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    @router.message(ClientStates.waiting_for_name)
    async def process_client_name(message: Message, state: FSMContext):
        name = message.text.strip()
        if not name:
            await message.answer("⚠️ Iltimos, mijoz ismini to'g'ri kiriting:")
            return
        await state.update_data(client_name=name)
        await state.set_state(ClientStates.waiting_for_contact)
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="➡️ O'tkazib yuborish", callback_data="skip_contact")]])
        await message.answer(
            f"📞 <b>[{name}] uchun aloqa ma'lumotlarini kiriting:</b>\n(Masalan: @username yoki +998901234567)",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML
        )

    @router.callback_query(F.data == "skip_contact", ClientStates.waiting_for_contact)
    async def process_client_contact_skip(callback: CallbackQuery, state: FSMContext):
        data = await state.get_data()
        name = data["client_name"]
        client_id = await db.add_client(name=name, contact_info=None)
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Server qo'shish", callback_data=f"add_server:{client_id}")],
            [InlineKeyboardButton(text="👥 Mijozlar ro'yxati", callback_data="menu_clients")]
        ])
        await callback.message.answer(f"✅ <b>Mijoz [{name}] muvaffaqiyatli saqlandi!</b>", reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    @router.message(ClientStates.waiting_for_contact)
    async def process_client_contact(message: Message, state: FSMContext):
        contact = message.text.strip().replace("@", "")
        data = await state.get_data()
        name = data["client_name"]
        client_id = await db.add_client(name=name, contact_info=contact)
        await state.clear()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Server qo'shish", callback_data=f"add_server:{client_id}")],
            [InlineKeyboardButton(text="👥 Mijozlar ro'yxati", callback_data="menu_clients")]
        ])
        await message.answer(f"✅ <b>Mijoz [{name}] muvaffaqiyatli saqlandi!</b>", reply_markup=kb, parse_mode=ParseMode.HTML)

    # ==================== SERVER QO'SHISH (FSM) ====================

    @router.callback_query(F.data.startswith("add_server:"))
    async def cb_add_server(callback: CallbackQuery, state: FSMContext):
        client_id = int(callback.data.split(":")[1])
        client = await db.get_client_by_id(client_id)
        if not client:
            await callback.answer("❌ Mijoz topilmadi!", show_alert=True)
            return

        await state.set_state(ServerStates.waiting_for_name)
        await state.update_data(client_id=client_id, client_name=client["name"])

        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"view_client:{client_id}")]])
        await callback.message.answer(
            f"🖥 <b>[{client['name']}] uchun server nomini kiriting:</b>\n(Masalan: Hetzner VPS 1, DigitalOcean Droplet, AWS EC2)",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @router.message(ServerStates.waiting_for_name)
    async def process_server_name(message: Message, state: FSMContext):
        server_name = message.text.strip()
        await state.update_data(server_name=server_name)
        await state.set_state(ServerStates.waiting_for_price)
        data = await state.get_data()
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"view_client:{data['client_id']}")]])
        await message.answer(
            "💰 <b>Oylik obuna narxini kiriting:</b>\n(Masalan: $15, 200,000 so'm, €12, $50)",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML
        )

    @router.message(ServerStates.waiting_for_price)
    async def process_server_price(message: Message, state: FSMContext):
        price = message.text.strip()
        await state.update_data(price=price)
        await state.set_state(ServerStates.waiting_for_day)
        data = await state.get_data()
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"view_client:{data['client_id']}")]])
        await message.answer(
            "📅 <b>Har oyning qaysi sanasida to'lov bo'ladi?</b>\n(1 dan 31 gacha butun son kiriting, masalan: 5 yoki 20):",
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML
        )

    @router.message(ServerStates.waiting_for_day)
    async def process_server_day(message: Message, state: FSMContext):
        text = message.text.strip()
        if not text.isdigit() or not (1 <= int(text) <= 31):
            await message.answer("⚠️ Iltimos, 1 dan 31 gacha bo'lgan to'g'ri kun sonini kiriting (masalan: 1, 15, 28):")
            return

        payment_day = int(text)
        data = await state.get_data()
        client_id = data["client_id"]
        client_name = data["client_name"]
        server_name = data["server_name"]
        price = data["price"]

        await db.add_server(
            client_id=client_id,
            server_name=server_name,
            price=price,
            payment_day=payment_day
        )
        await state.clear()

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"👤 [{client_name}] sahifasiga o'tish", callback_data=f"view_client:{client_id}")],
            [InlineKeyboardButton(text="👥 Barcha mijozlar", callback_data="menu_clients")]
        ])

        await message.answer(
            f"✅ <b>Server muvaffaqiyatli saqlandi!</b>\n\n"
            f"👤 <b>Mijoz:</b> {client_name}\n"
            f"🖥 <b>Server:</b> {server_name}\n"
            f"💰 <b>Narxi:</b> {price}\n"
            f"📅 <b>To'lov kuni:</b> Har oyning {payment_day}-sanasi",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    # ==================== O'CHIRISH AMALLARI ====================

    @router.callback_query(F.data.startswith("confirm_del_client:"))
    async def cb_confirm_del_client(callback: CallbackQuery):
        client_id = int(callback.data.split(":")[1])
        client = await db.get_client_by_id(client_id)
        if not client:
            await callback.answer("Mijoz topilmadi!", show_alert=True)
            return

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🔴 Ha, o'chirilsin", callback_data=f"delete_client_action:{client_id}"),
                InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data=f"view_client:{client_id}")
            ]
        ])
        await callback.message.edit_text(
            f"⚠️ <b>Haqiqatan ham [{client['name']}] mijozini va uning barcha serverlarini o'chirmoqchimisiz?</b>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("delete_client_action:"))
    async def cb_delete_client_action(callback: CallbackQuery):
        client_id = int(callback.data.split(":")[1])
        await db.delete_client(client_id)
        await callback.answer("✅ Mijoz o'chirildi!", show_alert=True)
        # Qayta mijozlar ro'yxatiga o'tish
        await cb_menu_clients(callback)

    @router.callback_query(F.data.startswith("delete_server_menu:"))
    async def cb_delete_server_menu(callback: CallbackQuery):
        client_id = int(callback.data.split(":")[1])
        servers = await db.get_client_servers(client_id)

        buttons = []
        for s in servers:
            btn_text = f"🗑 {s['server_name']} ({s['price']})"
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_server_act:{s['id']}:{client_id}")])

        buttons.append([InlineKeyboardButton(text="⬅️ Bekor qilish", callback_data=f"view_client:{client_id}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await callback.message.edit_text(
            "🗑 <b>O'chirmoqchi bo'lgan serveringizni tanlang:</b>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("del_server_act:"))
    async def cb_del_server_action(callback: CallbackQuery):
        parts = callback.data.split(":")
        server_id = int(parts[1])
        client_id = int(parts[2])

        await db.delete_server(server_id)
        await callback.answer("✅ Server o'chirildi!", show_alert=True)

        callback.data = f"view_client:{client_id}"
        await cb_view_client(callback)

    # ==================== YAQINLASHAYOTGAN TO'LOVLAR ====================

    @router.callback_query(F.data == "menu_upcoming_bills")
    async def cb_menu_upcoming_bills(callback: CallbackQuery):
        servers = await db.get_all_servers_with_clients()
        now_date = datetime.now().date()

        upcoming = []
        for s in servers:
            days_left = calculate_days_until_payment(now_date, s["payment_day"])
            if 0 <= days_left <= 3:
                s_copy = dict(s)
                s_copy["days_left"] = days_left
                upcoming.append(s_copy)

        upcoming.sort(key=lambda x: x["days_left"])

        if upcoming:
            text = format_billing_alert_message(upcoming, now_date)
        else:
            text = (
                "💳 <b>Yaqinlashayotgan to'lovlar (3 kunlik):</b>\n\n"
                "✅ <i>Yaqin 3 kun ichida to'lovi keladigan serverlar mavjud emas. Barchasi tinch!</i>"
            )

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="main_menu")]])
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    # ==================== STATISTIKA BO'LIMI ====================

    @router.callback_query(F.data == "menu_stats")
    async def cb_menu_stats(callback: CallbackQuery):
        clients = await db.get_clients()
        servers = await db.get_all_servers_with_clients()

        total_clients = len(clients)
        total_servers = len(servers)

        text = (
            "📊 <b>SERVERLAR VA MIJOZLAR STATISTIKASI:</b>\n\n"
            f"👥 <b>Jami mijozlar soni:</b> {total_clients} ta\n"
            f"🖥 <b>Jami faol serverlar:</b> {total_servers} ta\n\n"
            f"⏰ <i>Oylik to'lovlar har kuni ertalab soat 09:00 da avtomatik tekshirilib, 3 kun oldindan eslatib boriladi.</i>"
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Asosiy menyu", callback_data="main_menu")]])
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception:
            await callback.message.answer(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    dp.include_router(router)
    return bot, dp
