import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or "0")
PORT = int(os.getenv("PORT", "8080") or "8080")
CHANNEL_URL = "https://t.me/wilderwestik"

if not BOT_TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN в окружении")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
PAID_INVOICES: dict[str, dict] = {}


def verify_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data:
        return None
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    user_raw = parsed.get("user")
    if not user_raw:
        return None
    return json.loads(user_raw)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤠 Открыть Gift Hub",
                    web_app=WebAppInfo(url=WEBAPP_URL),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📢 Наш Telegram-канал", url=CHANNEL_URL
                )
            ],
        ]
    )
    await message.answer(
        "<b>Добро пожаловать в Gift Hub — Золотой Дикий Запад!</b>\n\n"
        "Запускай приложение, участвуй в ПвП-дуэлях, испытывай удачу в соло-играх и выводи подарки!\n\n"
        "Подписывайся на наш канал, чтобы быть в курсе всех новостей.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@dp.pre_checkout_query()
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    sp = message.successful_payment
    stars = sp.total_amount
    payload = sp.invoice_payload
    PAID_INVOICES[payload] = {
        "user_id": message.from_user.id if message.from_user else 0,
        "stars": stars,
        "charge_id": sp.telegram_payment_charge_id,
    }
    await message.answer(
        f"⭐ Оплата прошла успешно! На твой баланс зачислено <b>{stars} ⭐</b>.",
        parse_mode="HTML",
    )


@dp.message(F.web_app_data)
async def on_webapp_data(message: Message) -> None:
    try:
        data = json.loads(message.web_app_data.data or "{}")
    except json.JSONDecodeError:
        return

    if data.get("action") != "withdraw_gift":
        return

    user_name = (
        message.from_user.full_name
        if message.from_user
        else data.get("user", "Игрок")
    )
    user_id = message.from_user.id if message.from_user else data.get("tg_id", "—")
    gift = data.get("gift", "Подарок")
    paid_stars = data.get("paid_stars", 0)
    mode = data.get("mode", "auto")
    mode_text = "Авто от бота" if mode == "auto" else "Вручную через бота"

    if ADMIN_CHAT_ID:
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"🎁 <b>Заявка на вывод подарка</b>\n"
            f"Режим: <b>{mode_text}</b>\n"
            f"Игрок: <b>{user_name}</b> (ID: <code>{user_id}</code>)\n"
            f"Подарок: <b>{gift}</b>\n"
            f"Списано: <b>{paid_stars} ⭐</b>",
            parse_mode="HTML",
        )

    await message.answer(
        f"🎁 Запрос на <b>{gift}</b> ({paid_stars} ⭐, режим: <i>{mode_text}</i>) принят!",
        parse_mode="HTML",
    )


async def handle_create_invoice(request: web.Request) -> web.Response:
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=cors_headers())

    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "bad_json"}, status=400, headers=cors_headers()
        )

    init_data = str(body.get("initData", ""))
    stars = int(body.get("stars", 0) or 0)
    user = verify_init_data(init_data, BOT_TOKEN)

    if not user:
        return web.json_response(
            {"ok": False, "error": "invalid_init_data"},
            status=401,
            headers=cors_headers(),
        )
    if stars < 1:
        return web.json_response(
            {"ok": False, "error": "min_1_star"}, status=400, headers=cors_headers()
        )

    payload = f"dep_{user['id']}_{os.urandom(4).hex()}"
    invoice_link = await bot.create_invoice_link(
        title="Пополнение баланса Gift Hub",
        description=f"Зачисление {stars} ⭐ на игровой счёт",
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} ⭐", amount=stars)],
    )
    return web.json_response(
        {"ok": True, "invoiceUrl": invoice_link, "payload": payload},
        headers=cors_headers(),
    )


def cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


async def main() -> None:
    app = web.Application()
    app.router.add_route("POST", "/api/create-invoice", handle_create_invoice)
    app.router.add_route("OPTIONS", "/api/create-invoice", handle_create_invoice)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
  
