"""
Shared command handlers — every platform bot registers these once.
Zero duplication across Stockity, Deriv, MT5, Vilona, CCXT bots.

Handlers provided:
  /plans /upgrade /subscribe /confirm   — Plan & payments
  /signals /subscribe /unsubscribe   — Signal categories
  /affiliate /whitelabel             — Growth & referrals
  /set_share /set_rate /set_plan     — Admin commands

Usage in any bot:
  from tradebot.bots.handlers import register_standard_commands
  register_standard_commands(app)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from tradebot.bots.stockity.affiliate import (
    can_use_whitelabel,
    create_whitelabel,
    deactivate_whitelabel,
    get_or_create_affiliate,
    get_referral_stats,
    get_whitelabel,
    set_affiliate_rate,
    set_whitelabel_share,
)
from tradebot.config import settings
from tradebot.services.plans import (
    PLAN_DETAILS,
    PLAN_UPGRADE_PATH,
    Plan,
    add_donation,
    can_access_category,
    confirm_payment,
    create_invoice,
    get_plan_price,
    get_plan_stats,
    get_total_donations,
    get_total_revenue,
    get_user_donations,
    get_user_plan,
    set_plan_price,
)
from tradebot.signals.subscriptions import (
    CATEGORY_DESCRIPTIONS,
    CATEGORY_EMOJI,
    SignalCategory,
    get_user_subscriptions,
    subscribe_user,
    unsubscribe_user,
)

LOG = logging.getLogger(__name__)

AdminCheck = Callable[[str], bool]


def _default_admin_check(user_id: str) -> bool:
    raw = getattr(settings, "ADMIN_USER_IDS", "") or ""
    admin_ids = [uid.strip() for uid in raw.split(",") if uid.strip()]
    return user_id in admin_ids or "ALL" in (uid.upper() for uid in admin_ids)


# ── Plan Commands ─────────────────────────────────────────────────────

async def _h_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    current = get_user_plan(user_id)
    stats = get_plan_stats()

    lines = ["💳 *Trading Plans*\n"]
    for plan in Plan:
        det = PLAN_DETAILS[plan]
        marker = "✅ *CURRENT*" if plan == current else ""
        upgrade_hint = ""
        if plan == current and PLAN_UPGRADE_PATH[plan]:
            next_plan = PLAN_UPGRADE_PATH[plan]
            upgrade_hint = f" → `/upgrade {next_plan.value}`"
        elif plan != current and plan != Plan.FREE:
            price = get_plan_price(plan)
            upgrade_hint = f" — Rp {price:,} `/upgrade {plan.value}`"

        lines.append(
            f"{det['emoji']} *{det['name']}* — {marker}\n"
            f"  _{det['description']}_{upgrade_hint}"
        )

    lines.append(
        f"\n📊 *Stats:* {sum(stats.values())} users | "
        f"Revenue: Rp {get_total_revenue():,}"
    )

    total_donated = get_total_donations(user_id)
    if total_donated > 0:
        lines.append(f"\n❤️ Your donations: Rp {total_donated:,} — terima kasih!")
    lines.append("To donate: `/subscribe <amount>`")
    await update.message.reply_markdown("\n".join(lines))


async def _h_upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []

    if not args:
        await _h_plans(update, context)
        return

    plan_name = args[0].lower()
    try:
        target = Plan(plan_name)
    except ValueError:
        valid = ", ".join(p.value for p in Plan if p != Plan.FREE)
        await update.message.reply_markdown(
            f"❌ Unknown plan: `{plan_name}`\nValid: {valid}"
        )
        return

    if target == Plan.FREE:
        await update.message.reply_markdown(
            "ℹ️ Free plan doesn't require payment. Use `/plans` to see options."
        )
        return

    current = get_user_plan(user_id)
    if current == target:
        await update.message.reply_markdown(
            f"ℹ️ You're already on the *{target.value}* plan."
        )
        return

    amount = get_plan_price(target)
    det = PLAN_DETAILS[target]
    ref = create_invoice(user_id, target, amount)

    payment_url = ""
    payment_methods = "QRIS / Bank Transfer"
    try:
        from tradebot.services.payment import PaymentService
        svc = PaymentService()
        result = await svc.create_payment(
            merchant_ref=ref,
            amount=amount,
            customer_name=f"User {user_id}",
            customer_email=f"{user_id}@telegram.user",
            customer_phone="",
            items=[{
                "name": f"TradeBot {target.value.title()} Plan",
                "price": amount,
                "quantity": 1,
            }],
        )
        if result and result.get("pay_url"):
            payment_url = result["pay_url"]
            payment_methods = result.get("payment_methods", payment_methods)
    except Exception as e:
        LOG.warning("Tripay unavailable: %s", e)

    lines = [
        f"🧾 *Upgrade to {det['name']}*\n",
        f"Plan: *{target.value.upper()}*",
        f"Price: *Rp {amount:,}*",
        "Duration: 30 days",
        f"Ref: `{ref}`\n",
        "💳 *Payment:*",
        f"{payment_methods}",
    ]
    if payment_url:
        lines.append(f"\n[Pay Now]({payment_url})")
    lines.append(f"\nAfter payment:\n`/confirm {ref}`")

    await update.message.reply_markdown("\n".join(lines))


async def _h_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.message.reply_markdown("Usage: `/confirm <ref_code>`")
        return

    ref = args[0].strip()
    result = confirm_payment(ref)
    if not result:
        await update.message.reply_markdown(
            f"❌ Invoice `{ref}` not found or already processed."
        )
        return

    plan_name = result["plan"].upper()
    await update.message.reply_markdown(
        f"✅ *Payment Confirmed!*\n\n"
        f"Plan: *{plan_name}* activated\n"
        f"Expires: 30 days\n\n"
        f"View: /plans | Signals: /signals"
    )


async def _h_donate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []

    if not args:
        total = get_total_donations(user_id)
        donations = get_user_donations(user_id)
        lines = [
            "❤️ *Donations*\n",
            "Support the bot development!\n",
            "Usage: `/subscribe <amount>`\n",
            "Min: Rp 1.000 | Max: Rp 10.000.000\n",
        ]
        if total > 0:
            lines.append(f"*Your total:* Rp {total:,}")
        if donations:
            lines.append("\n*Recent:*")
            for d in donations[:5]:
                msg = d.get("message", "")[:30]
                lines.append(f"  • Rp {d['amount_idr']:,} — {msg}")
        await update.message.reply_markdown("\n".join(lines))
        return

    try:
        amount = int(args[0])
    except ValueError:
        await update.message.reply_markdown("❌ Invalid amount. Use: `/subscribe 50000`")
        return

    if amount < 1000:
        await update.message.reply_markdown("❌ Minimum donation: Rp 1.000")
        return
    if amount > 10_000_000:
        await update.message.reply_markdown("❌ Maximum donation: Rp 10.000.000")
        return

    message = " ".join(args[1:]) if len(args) > 1 else ""
    donation_id = add_donation(user_id, amount, message)

    reply = (
        f"❤️ *Thank You!*\n\n"
        f"Donation: *Rp {amount:,}*\n"
        f"ID: `DON-{donation_id}`\n"
    )
    if message:
        reply += f"Message: _{message}_\n"
    reply += "\nYour support keeps the bot running! 🚀"
    await update.message.reply_markdown(reply)


# ── Signal Subscription Commands ──────────────────────────────────────

async def _h_signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    subs = get_user_subscriptions(user_id)
    active = set(subs)
    plan = get_user_plan(user_id)

    lines = ["📡 *Signal Types*\n"]
    for cat in SignalCategory:
        emoji = CATEGORY_EMOJI.get(cat, "")
        desc = CATEGORY_DESCRIPTIONS.get(cat, "")
        accessible = can_access_category(plan, cat)
        status = "✅" if cat in active else "⬜"
        lock = "" if accessible else " 🔒"
        lines.append(f"{status} {emoji} *{cat.value}*{lock} — {desc}")
    lines.append(f"\nYour plan: *{plan.value}* — Upgrade: /plans")
    lines.append("\nSubscribe: `/subscribe <type>`")
    lines.append("Unsubscribe: `/unsubscribe [type]`")
    await update.message.reply_markdown("\n".join(lines))


async def _h_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []
    plan = get_user_plan(user_id)

    if not args:
        await _h_signals(update, context)
        return

    cat_name = args[0].lower()
    try:
        cat = SignalCategory(cat_name)
    except ValueError:
        valid = ", ".join(c.value for c in SignalCategory)
        await update.message.reply_markdown(
            f"❌ Unknown category: `{cat_name}`\nValid: {valid}"
        )
        return

    if not can_access_category(plan, cat):
        await update.message.reply_markdown(
            f"🔒 *{cat.value}* requires a higher plan.\n"
            f"Your plan: *{plan.value}*\n"
            f"Upgrade: /plans"
        )
        return

    subscribe_user(user_id, cat)
    emoji = CATEGORY_EMOJI.get(cat, "")
    desc = CATEGORY_DESCRIPTIONS.get(cat, "")
    await update.message.reply_markdown(
        f"✅ Subscribed to {emoji} *{cat.value}*\n"
        f"_{desc}_\n\n"
        f"View: /signals | Stop: `/unsubscribe {cat.value}`"
    )


async def _h_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []

    if args:
        cat_name = args[0].lower()
        try:
            cat = SignalCategory(cat_name)
        except ValueError:
            await update.message.reply_markdown(f"❌ Unknown: `{cat_name}`")
            return
        unsubscribe_user(user_id, cat.value)
        await update.message.reply_markdown(f"✅ Unsubscribed from *{cat.value}*")
    else:
        unsubscribe_user(user_id, None)
        await update.message.reply_markdown("✅ Unsubscribed from *all* signals")


# ── Affiliate & Whitelabel Commands ───────────────────────────────────

async def _h_affiliate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    aff = get_or_create_affiliate(user_id)
    stats = get_referral_stats(user_id)
    plan = get_user_plan(user_id)

    bot_link = f"t.me/{context.bot.username}?start=ref_{aff.referral_code}"
    await update.message.reply_markdown(
        f"🤝 *Affiliate Program*\n\n"
        f"Your code: `{aff.referral_code}`\n"
        f"Commission: *{aff.commission_rate}%*\n"
        f"Referrals: *{stats['total_referrals']}* | "
        f"Earned: Rp {stats['total_earned']:,.0f}\n\n"
        f"🔗 Share: `{bot_link}`\n\n"
        f"Earn when referrals subscribe to paid plans.\n"
        f"Plan: *{plan.value}* — higher plans get better rates!"
    )


async def _h_whitelabel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []
    eligible, reason = can_use_whitelabel(user_id)

    if not args:
        wl = get_whitelabel(user_id)
        if wl:
            await update.message.reply_markdown(
                f"🏷 *Whitelabel Bot*\n\n"
                f"Nama: {wl.custom_name}\n"
                f"Username: @{wl.bot_username}\n"
                f"Revenue Share: {wl.revenue_share}%\n"
                f"Status: {'✅ Active' if wl.active else '❌ Inactive'}\n\n"
                f"Eligibility: {reason}\n"
                f"Deactivate: `/whitelabel deactivate`"
            )
        else:
            await update.message.reply_markdown(
                "🏷 *Whitelabel Bot*\n\n"
                "Run your own branded trading bot!\n\n"
                f"Eligibility: {reason}\n\n"
                "`/whitelabel <bot_token> <username>`\n\n"
                "1. Create bot via @BotFather\n"
                "2. Copy token\n"
                "3. Register here\n\n"
                "_Requires active plan (Pro+) or donations ≥ Rp 100K_"
            )
        return

    if args[0] == "deactivate":
        deactivate_whitelabel(user_id)
        await update.message.reply_markdown("✅ Whitelabel deactivated.")
        return

    if len(args) >= 2:
        if not eligible:
            await update.message.reply_markdown(
                f"❌ Cannot create whitelabel.\n{reason}"
            )
            return
        token = args[0]
        username = args[1].lstrip("@")
        create_whitelabel(user_id, token, username)
        await update.message.reply_markdown(
            f"✅ Whitelabel @{username} registered!\n"
            f"Start: t.me/{username}\n"
            f"Revenue share: 10% (admin can adjust)"
        )
    else:
        await update.message.reply_markdown(
            "❌ Format: `/whitelabel <bot_token> <username>`"
        )


# ── Admin Commands ────────────────────────────────────────────────────

async def _h_set_share(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []

    if not _default_admin_check(user_id):
        await update.message.reply_markdown("❌ Admin only.")
        return
    if len(args) < 2:
        await update.message.reply_markdown(
            "Usage: `/set_share <user_id> <percentage>`\n"
            "Example: `/set_share 123456 20`"
        )
        return

    target_user = args[0]
    try:
        share = float(args[1])
    except ValueError:
        await update.message.reply_markdown("❌ Share must be a number (e.g. 15)")
        return
    if share < 0 or share > 100:
        await update.message.reply_markdown("❌ Share must be 0-100%")
        return

    set_whitelabel_share(target_user, share)
    await update.message.reply_markdown(
        f"✅ Whitelabel share for `{target_user}` set to *{share}%*"
    )


async def _h_set_rate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []

    if not _default_admin_check(user_id):
        await update.message.reply_markdown("❌ Admin only.")
        return
    if len(args) < 2:
        await update.message.reply_markdown(
            "Usage: `/set_rate <user_id> <percentage>`\n"
            "Example: `/set_rate 123456 30`"
        )
        return

    target_user = args[0]
    try:
        rate = float(args[1])
    except ValueError:
        await update.message.reply_markdown("❌ Rate must be a number (e.g. 20)")
        return

    set_affiliate_rate(target_user, rate)
    await update.message.reply_markdown(
        f"✅ Affiliate commission for `{target_user}` set to *{rate}%*"
    )


async def _h_set_plan_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = str(update.effective_chat.id)
    args = context.args or []

    if not _default_admin_check(user_id):
        await update.message.reply_markdown("❌ Admin only.")
        return

    if not args:
        prices = "\n".join(
            f"  {p.value}: Rp {get_plan_price(p):,}"
            for p in Plan if p != Plan.FREE
        )
        await update.message.reply_markdown(
            f"📊 *Current Prices*\n{prices}\n\n"
            "Usage: `/set_plan <plan> <price>`\n"
            "Example: `/set_plan pro 75000`"
        )
        return

    plan_name = args[0].lower()
    try:
        plan = Plan(plan_name)
    except ValueError:
        valid = ", ".join(p.value for p in Plan if p != Plan.FREE)
        await update.message.reply_markdown(
            f"❌ Unknown plan: `{plan_name}`\nValid: {valid}"
        )
        return

    if plan == Plan.FREE:
        await update.message.reply_markdown("❌ Free plan cannot be priced.")
        return

    try:
        price = int(args[1])
    except ValueError:
        await update.message.reply_markdown(
            "❌ Price must be an integer (e.g. 75000)"
        )
        return
    if price < 0:
        await update.message.reply_markdown("❌ Price must be positive")
        return

    set_plan_price(plan, price)
    await update.message.reply_markdown(
        f"✅ *{plan.value.upper()}* price updated!\n"
        f"New price: *Rp {price:,}*\nView: /plans"
    )


# ── Registration — call once per bot ──────────────────────────────────

_COMMANDS: list[tuple[str | list[str], Callable]] = [
    (["plans"], _h_plans),
    (["upgrade"], _h_upgrade),
    (["confirm"], _h_confirm),
    (["donate"], _h_donate),
    (["signals"], _h_signals),
    (["subscribe"], _h_subscribe),
    (["unsubscribe"], _h_unsubscribe),
    (["affiliate"], _h_affiliate),
    (["whitelabel"], _h_whitelabel),
    (["set_share"], _h_set_share),
    (["set_rate"], _h_set_rate),
    (["set_plan"], _h_set_plan_price),
]


def register_standard_commands(app: Application) -> None:
    """Register all cross-cutting command handlers on any PTB Application.

    Call once per bot at startup. Handlers read user state from DB
    (plan, subscription, affiliate) — no per-platform code needed.
    """
    for cmds, handler in _COMMANDS:
        app.add_handler(CommandHandler(cmds, handler))

    LOG.info("Registered %d shared command handlers", len(_COMMANDS))
