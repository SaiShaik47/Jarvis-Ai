import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import requests
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BASE_URL = "https://llm-flip.vercel.app"
DEFAULT_MODEL = "mistral-large"


@dataclass
class ChatSession:
    model: str = DEFAULT_MODEL
    sid: Optional[str] = None
    history: List[dict] = field(default_factory=list)


class FlipClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url.rstrip("/")

    def models(self) -> List[str]:
        response = requests.get(f"{self.base_url}/models", timeout=30)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("models", "data", "result"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        raise ValueError("Unexpected /models response format")

    def new_session(self, model: str, system_prompt: Optional[str] = None) -> str:
        params = {"model": model}
        if system_prompt:
            params["system_prompt"] = system_prompt
        response = requests.get(f"{self.base_url}/session/new", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        sid = payload.get("sid")
        if not sid:
            raise ValueError("Session ID missing in /session/new response")
        return sid

    def chat(self, model: str, sid: str, text: str) -> str:
        response = requests.get(
            f"{self.base_url}/{model}/chat",
            params={"text": text, "sid": sid},
            timeout=60,
        )
        response.raise_for_status()
        return response.text

    def session_info(self, sid: str) -> str:
        response = requests.get(f"{self.base_url}/session/{sid}", timeout=30)
        response.raise_for_status()
        return response.text


class SuperTelegramBot:
    def __init__(self, token: str):
        self.flip = FlipClient()
        self.sessions: Dict[int, ChatSession] = {}
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self) -> None:
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_cmd))
        self.app.add_handler(CommandHandler("model", self.model_cmd))
        self.app.add_handler(CommandHandler("models", self.models_cmd))
        self.app.add_handler(CommandHandler("history", self.history_cmd))
        self.app.add_handler(CommandHandler("reset", self.reset_cmd))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.chat_msg))

    def _chat_session(self, chat_id: int) -> ChatSession:
        if chat_id not in self.sessions:
            self.sessions[chat_id] = ChatSession()
        return self.sessions[chat_id]

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.message.reply_text(
            "🤖 Super Bot is online!\n"
            "Commands:\n"
            "/model <name> - switch model\n"
            "/models - list available models\n"
            "/history - show current session history\n"
            "/reset - reset your session\n"
            "Send any message to chat."
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
            all_models = self.flip.models()
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
            models = self.flip.models()
            pretty = "\n".join(f"• {name}" for name in models)
            await update.message.reply_text(f"Available models:\n{pretty}")
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Failed to fetch models: {exc}")

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
        await update.message.reply_text("♻️ Session reset. Model is back to mistral-large.")

    async def chat_msg(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        chat_id = update.effective_chat.id
        session = self._chat_session(chat_id)
        message = update.message.text.strip()

        try:
            if not session.sid:
                session.sid = self.flip.new_session(model=session.model)

            answer = self.flip.chat(session.model, session.sid, message)
            session.history.append({"role": "user", "content": message})
            session.history.append({"role": "assistant", "content": answer})
            await update.message.reply_text(answer[:3900])
        except Exception as exc:  # noqa: BLE001
            await update.message.reply_text(f"Chat failed: {exc}")

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
