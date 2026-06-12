"""
Telegram Layer — Bot HTTP API (no API_ID/API_HASH needed).
Uses polling via Telegram Bot API directly.
"""
import asyncio, json, logging, os, time
from pathlib import Path
from typing import Any, Callable
import httpx

LOG = logging.getLogger("agent.telegram")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8343388239:AAFgeAkc9bvjywyCsHqRIa_RiJ6q-rp6uv0")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_USER_IDS", "157228659,5220170786").split(",") if x}
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
OFFSET_FILE = Path("/home/openclaw/projects/1ai-trade-bot/data/agent_offset.txt")


class TelegramLayer:
    def __init__(self):
        self._http = httpx.AsyncClient(timeout=15)
        self._running = False
        self._cmd_router: dict[str, Callable] = {}

    def register_command(self, cmd: str, handler: Callable) -> None:
        self._cmd_router[cmd.lower()] = handler

    def _load_offset(self) -> int:
        try:
            return int(OFFSET_FILE.read_text().strip())
        except Exception:
            return 0

    def _save_offset(self, offset: int) -> None:
        OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
        OFFSET_FILE.write_text(str(offset))

    async def _api(self, method: str, payload: dict) -> dict | None:
        try:
            r = await self._http.post(f"{API_BASE}/{method}", json=payload)
            return r.json() if r.status_code == 200 else None
        except Exception as e:
            LOG.debug("API error: %s", e)
            return None

    async def start(self) -> None:
        self._running = True
        offset = self._load_offset()
        poll_errors = 0
        LOG.info("Bot polling started")
        while self._running:
            try:
                resp = await self._api("getUpdates", {"offset": offset, "timeout": 10})
                if not resp:
                    poll_errors += 1
                    await asyncio.sleep(min(30, poll_errors * 5))
                    continue
                poll_errors = 0
                for upd in resp.get("result", []):
                    new_off = upd["update_id"] + 1
                    if new_off > offset:
                        offset = new_off
                    await self._route(upd)
                if resp.get("result"):
                    self._save_offset(offset)
                else:
                    await asyncio.sleep(0.3)
            except Exception as e:
                poll_errors += 1
                await asyncio.sleep(min(30, poll_errors * 5))
                LOG.error("Poll error #%d", poll_errors)

    async def stop(self) -> None:
        self._running = False
        await self._http.aclose()

    async def _route(self, upd: dict) -> None:
        try:
            cb = upd.get("callback_query")
            if cb:
                await self._callback(cb)
                return
            msg = upd.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = (msg.get("text", "") or "").strip()
            if not text:
                return
            parts = text.split()
            cmd = parts[0].lower().lstrip("/").split("@")[0]
            handler = self._cmd_router.get(cmd)
            if handler:
                resp = await handler(parts[1:], chat_id)
                if resp:
                    if cmd in ("start", "help"):
                        from agent.menu import build_kb
                        is_admin = chat_id in [str(x) for x in os.environ.get("ADMIN_USER_IDS", "157228659,5220170786").split(",")]
                        menu_name = "admin" if is_admin else "main"
                        await self.send(chat_id, resp, buttons=build_kb(menu_name))
                    else:
                        await self.send(chat_id, resp)
            else:
                await self.send(chat_id, "❌ Command tidak dikenal. Ketik /start")
        except Exception as e:
            LOG.error("Route error: %s", e)

    async def _callback(self, cb: dict) -> None:
        try:
            data = cb.get("data", "")
            cb_id = cb.get("id", "")
            chat_id = str(cb.get("from", {}).get("id", ""))
            await self._api("answerCallbackQuery", {"callback_query_id": cb_id})
            if data.startswith("menu:"):
                from agent.menu import get_menu_kb, get_menu_text
                mn = data.split(":", 1)[1]
                if mn == "main" and self.is_admin(chat_id):
                    mn = "admin"
                await self.send(chat_id, get_menu_text(mn), buttons=get_menu_kb(mn))
            elif data.startswith("cmd:"):
                parts = data.split(":", 1)[1].split()
                handler = self._cmd_router.get(parts[0])
                if handler:
                    resp = await handler(parts[1:], chat_id)
                    if resp:
                        await self.send(chat_id, resp)
            elif data.startswith("donate:"):
                amt = {}.get(data)
                if amt:
                    await self.send(chat_id, f"💚 Rp{amt:,} — Hubungi @codergaboets")
        except Exception as e:
            LOG.error("Callback error: %s", e)

    async def send(self, chat_id: str, text: str, buttons: Any = None) -> bool:
        payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if buttons:
            kb = [[{"text": b.text, "url": b.url} if hasattr(b, 'url') and b.url
                   else {"text": b.text, "callback_data": b.data.decode()}
                   for b in row] for row in buttons]
            payload["reply_markup"] = {"inline_keyboard": kb}
        return await self._api("sendMessage", payload) is not None

    def is_admin(self, uid: str) -> bool:
        return int(uid) in ADMIN_IDS

    async def send_message(self, chat_id: str, text: str, buttons=None):
        return await self.send(chat_id, text, buttons)
