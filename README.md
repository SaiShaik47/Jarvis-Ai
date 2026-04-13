# Telegram Super Bot (Flip LLM)

A Telegram bot that connects to the Flip LLM API and supports:

- Multi-model switching (`/model`, `/models`)
- Model toggle buttons (inline keyboard in Telegram)
- Reachability checks for every model (`/checkmodels`)
- Session-based memory (`sid` per Telegram chat)
- Automatic chat rollover to a new session after a turn limit
- Chat history lookup (`/history`)
- Session reset (`/reset`)

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configure token

Set your Telegram bot token through an environment variable:

```bash
export TELEGRAM_BOT_TOKEN="your_token_here"
```

> Security note: never commit real bot tokens to git. If a token has been shared publicly, revoke it immediately in BotFather and generate a new one.

## Run

```bash
python main.py
```

## Railpack deployment

The project includes a `railpack.json` file with a valid `start` command:

```json
{
  "$schema": "https://railpack.com/schema.json",
  "start": "python main.py"
}
```

If you rename the entry script, update the `start` value to match the new filename.

## Commands

- `/start` or `/help` — show help
- `/model <model_name>` — switch active model and reset session
- `/models` — open toggle buttons for available models
- `/checkmodels` — test each model by creating a session and show health
- `/newchat` — force a fresh chat thread with current model
- `/history` — print active remote session info/history
- `/reset` — reset local state to default model

## Flip LLM API endpoints used

- `GET /models`
- `GET /session/new?model=<model>&system_prompt=<optional>`
- `GET /<model>/chat?text=<message>&sid=<sid>`
- `GET /session/<sid>`
