import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_URL = "https://llm-flip.vercel.app"
DEFAULT_MODEL = "mistral-small"
MAX_TURNS_PER_CHAT = 16
CALLBACK_MODEL_PREFIX = "model:"
FALLBACK_MODELS = [
    "mistral-small",
    "mistral-medium",
    "mistral-large-2411",
    "mistral-large-3",
    "mixtral-8x22b",
    "deepseek-v3",
    "glm-4-flash",
]


@dataclass
class ChatSession:
    model: str = DEFAULT_MODEL
    sid: Optional[str] = None
    history: List[dict] = field(default_factory=list)


class FlipClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.http.headers.update(
            {
                "origin": self.base_url,
                "x-requested-with": "XMLHttpRequest",
            }
        )

    def models(self) -> List[str]:
        response = self.http.get(f"{self.base_url}/models", timeout=30)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return self._normalize_models(data)
        if isinstance(data, dict):
            for key in ("models", "data", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    return self._normalize_models(value)
        raise ValueError("Unexpected /models response format")

    @staticmethod
    def _normalize_models(raw_models: List[object]) -> List[str]:
        models: List[str] = []
        for item in raw_models:
            if isinstance(item, str):
                models.append(item)
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("model")
                if isinstance(model_id, str) and model_id:
                    models.append(model_id)
        if not models:
            raise ValueError("No valid model identifiers found in /models response")
        return models

    def new_session(self, model: str, system_prompt: Optional[str] = None) -> str:
        params = {"model": model}
        if system_prompt:
            params["system_prompt"] = system_prompt
        response = self.http.get(f"{self.base_url}/session/new", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        sid = payload.get("sid") or payload.get("session_id")
        if not sid:
            raise ValueError(f"Session ID missing in /session/new response: {payload}")
        return sid

    def chat(self, model: str, sid: str, text: str) -> str:
        response = self.http.get(
            f"{self.base_url}/{model}/chat",
            params={"text": text, "sid": sid},
            timeout=60,
        )
        response.raise_for_status()
        return response.text

    def session_info(self, sid: str) -> str:
        response = self.http.get(f"{self.base_url}/session/{sid}", timeout=30)
        response.raise_for_status()
        return response.text


class SuperTelegramBot:
    def __init__(self, token: str):
        self.flip = FlipClient()
        self.sessions: Dict[int, ChatSession] = {}
        self.valid_models: List[str] = FALLBACK_MODELS.copy()
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_cmd))
        self.app.add_handler(CommandHandler("model", self.model_cmd))
        self.app.add_handler(CommandHandler("models", self.models_cmd))
        self.app.add_handler(CommandHandler("checkmodels", self.check_models_cmd))
        self.app.add_handler(CommandHandler("newchat", self.new_chat_cmd))
        self.app.add_handler(CommandHandler("history", self.history_cmd))
        self.app.add_handler(CommandHandler("reset", self.reset_cmd))
        self.app.add_handler(CallbackQueryHandler(self.model_button_cb, pattern=f"^{CALLBACK_MODEL_PREFIX}"))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.chat_msg))

    def _chat_session(self, chat_id: int) -> ChatSession:
        if chat_id not in self.sessions:
            self.sessions[chat_id] = ChatSession()
        return self.sessions[chat_id]

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.message.reply_text(
            "🤖 *Super Bot is online!*\n\n"
            "Commands:\n"
            "/model <name> - switch model\n"
            "/models - show model toggle buttons\n"
            "/checkmodels - check which models are reachable\n"
            "/newchat - start a clean chat with the current model\n"
            "/history - show current session history\n"
            "/reset - reset your session\n"
            "\nSend any message to chat.",
            parse_mode="Markdown",
        )

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.start(update, context)

    async def model_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        session = self._chat_session(chat_id)
        if not context.args:
            await update.message.reply_text(f"Current model: {session.model}")
            return

        requested_model = context.args[0]
        try:
            all_models = self._get_valid_models()
            if requested_model not in all_models:
                await update.message.reply_text(
                    f"Unknown model: {requested_model}\nUse /models to see valid models."
                )
                return
            session.model = requested_model
            session.sid = None
            session.history.clear()
            await update.message.reply_text(f"✅ Model changed to: {requested_model}")
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Failed to switch model: {exc}")

    async def models_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        try:
            models = self._get_valid_models(force_refresh=True)
            keyboard = self._model_keyboard(models, columns=2)
            current = self._chat_session(update.effective_chat.id).model
            await update.message.reply_text(
                f"Choose a model (current: `{current}`):",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Failed to fetch models: {exc}")

    async def check_models_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        try:
            models = self._get_valid_models(force_refresh=True)
            checks: List[Tuple[str, bool, str]] = []
            for model in models:
                try:
                    sid = self.flip.new_session(model=model)
                    checks.append((model, True, sid))
                except Exception as exc:  # noqa: BLE001
                    checks.append((model, False, str(exc)))

            lines = ["Model status:"]
            for model, ok, note in checks:
                marker = "✅" if ok else "❌"
                lines.append(f"{marker} {model}")
                if not ok:
                    lines.append(f"   └─ {note[:120]}")
            await self._reply_long(update, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Model health check failed: {exc}")

    async def history_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        chat_id = update.effective_chat.id
        session = self._chat_session(chat_id)
        if not session.sid:
            await update.message.reply_text("No active session yet. Send a message first.")
            return

        try:
            info = self.flip.session_info(session.sid)
            await update.message.reply_text(info[:3900])
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Failed to load history: {exc}")

    async def reset_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        chat_id = update.effective_chat.id
        self.sessions[chat_id] = ChatSession()
        await update.message.reply_text(f"♻️ Session reset. Model is back to {DEFAULT_MODEL}.")

    async def new_chat_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        chat_id = update.effective_chat.id
        session = self._chat_session(chat_id)
        session.sid = None
        session.history.clear()
        await update.message.reply_text("🧼 Started a new chat thread with your current model.")

    async def model_button_cb(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        requested_model = data.replace(CALLBACK_MODEL_PREFIX, "", 1)

        chat_id = update.effective_chat.id
        session = self._chat_session(chat_id)
        try:
            all_models = self.flip.models()
            if requested_model not in all_models:
                await query.edit_message_text("Selected model is no longer available. Use /models again.")
                return
            session.model = requested_model
            session.sid = None
            session.history.clear()
            await query.edit_message_text(f"✅ Model changed to: `{requested_model}`", parse_mode="Markdown")
        except Exception as exc:  # noqa: BLE001
            await query.edit_message_text(f"Failed to switch model: {exc}")

    async def chat_msg(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        chat_id = update.effective_chat.id
        session = self._chat_session(chat_id)
        session.model = self._safe_model(session.model)
        message = update.message.text.strip()

        try:
            if len(session.history) // 2 >= MAX_TURNS_PER_CHAT:
                session.sid = None
                session.history.clear()
                await update.message.reply_text(
                    "🧵 Chat limit reached, I started a fresh conversation automatically."
                )
            if not session.sid:
                session.sid = self.flip.new_session(model=session.model)

            answer, used_model = self._chat_with_retry(session, message)
            if used_model != session.model:
                old_model = session.model
                session.model = used_model
                await update.message.reply_text(
                    f"⚠️ `{old_model}` failed right now. I switched to `{used_model}` automatically.",
                    parse_mode="Markdown",
                )
            session.history.append({"role": "user", "content": message})
            session.history.append({"role": "assistant", "content": answer})
            await self._reply_long(update, answer)
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Chat failed: {exc}")

    def _chat_with_retry(self, session: ChatSession, message: str) -> Tuple[str, str]:
        models_to_try = [session.model] + [
            model for model in self._get_valid_models() if model != session.model
        ]
        last_error: Optional[Exception] = None

        for model in models_to_try:
            sid = session.sid if model == session.model else None
            for _ in range(2):
                try:
                    if not sid:
                        sid = self.flip.new_session(model=model)
                    reply = self.flip.chat(model, sid, message)
                    session.sid = sid
                    return reply, model
                except Exception as exc:  # noqa: BLE001
                    sid = None
                    last_error = exc

        if last_error is not None:
            raise RuntimeError(f"All models failed for this prompt. Last error: {last_error}")
        raise RuntimeError("All models failed for this prompt.")

    def _get_valid_models(self, force_refresh: bool = False) -> List[str]:
        if force_refresh or not self.valid_models:
            self.valid_models = self.flip.models()
        return self.valid_models

    def _safe_model(self, candidate: str) -> str:
        valid = self._get_valid_models()
        if candidate in valid:
            return candidate
        if DEFAULT_MODEL in valid:
            return DEFAULT_MODEL
        return valid[0]

    @staticmethod
    def _model_keyboard(models: List[str], columns: int = 2) -> InlineKeyboardMarkup:
        rows: List[List[InlineKeyboardButton]] = []
        for idx in range(0, len(models), columns):
            chunk = models[idx : idx + columns]
            rows.append(
                [
                    InlineKeyboardButton(label, callback_data=f"{CALLBACK_MODEL_PREFIX}{label}")
                    for label in chunk
                ]
            )
        return InlineKeyboardMarkup(rows)

    async def _reply_long(self, update: Update, text: str, limit: int = 3900) -> None:
        for i in range(0, len(text), limit):
            await update.message.reply_text(text[i : i + limit])

    def run(self) -> None:
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable before starting.")

    bot = SuperTelegramBot(token)
    bot.run()


if __name__ == "__main__":
    main()
