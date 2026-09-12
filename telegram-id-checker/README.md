# Telegram ID Duplicate Checker

A small standalone Telegram bot that watches group messages for a standalone
`ID` field, stores each ID globally in MongoDB, and warns a group when an ID is
seen again. It does not delete messages, use OCR, or require a configured list
of group IDs.

## Requirements

- Python 3.11+
- A Telegram bot token
- A MongoDB database whose URI includes the database name

## Installation

```bash
cd telegram-id-checker
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set the three required environment variables in `.env`:

```dotenv
BOT_TOKEN=
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/id_checker
ADMIN_IDS=123456789,987654321
```

`MONGODB_DATABASE` and `MONITORED_CHAT_IDS` are intentionally not used. The
database name comes from the path in `MONGODB_URI`, and group IDs are taken
from incoming Telegram updates.

## Telegram setup

1. Open [@BotFather](https://t.me/BotFather), create a bot, and copy its token
   into `BOT_TOKEN`.
2. In BotFather, open **Bot Settings → Group Privacy → Turn off**. With
   privacy mode enabled, Telegram generally sends bots only commands and
   replies, so ordinary `ID: ...` messages may not reach this bot.
3. Add the bot to every group it should monitor. No group registration is
   needed.
4. The bot does not need permission to delete messages. If the group platform
   requires it to read ordinary messages, grant the minimum group permission
   needed for message visibility.

Private chats are ignored by the ID detection handler. If an administrator
uses one of the admin commands in a private chat, the command can still return
read-only information.

## ID detection rules

Recognized examples:

```text
ID: 12345
Id - 12345
ID-12345
ID : 000123
ID=ABC123
```

The header must be a standalone field at the start of its line. `Client ID`,
`Telegram ID`, `User ID`, `Order ID`, `Identity`, and `ID Card` do not match.
IDs remain strings, so `000123` and `123` are different values. Empty IDs are
ignored. If a message has more than one standalone `ID` header, it is treated
as ambiguous and ignored instead of guessing.

The handler reads normal text and Telegram media captions. It does not perform
OCR.

## Running locally

```bash
python bot.py
```

The bot creates a unique MongoDB index on `id_records.id` at startup. The
primary record is created atomically; simultaneous submissions cannot create
two primary records. Every occurrence is also recorded in `id_occurrences` so
statistics and recent lists can be calculated.

Run the tests:

```bash
pytest -q
```

## Admin commands

Only Telegram user IDs listed in `ADMIN_IDS` can use these commands:

- `/start` — show the welcome message and usage instructions
- `/checkid 12345` — read-only lookup
- `/stats` — today's new IDs and duplicates plus all-time totals
- `/recent` — recently detected IDs
- `/duplicates` — recent duplicate occurrences

The bot never stores an ID because `/checkid` was used.

## Render deployment

`render.yaml` defines a Python web service. The bot uses long polling for
Telegram and runs a small `/healthz` endpoint on Render's `PORT`, so the
service can be monitored externally.

1. Push this repository to GitHub.
2. In Render, create a new Blueprint from the repository. The root
   `render.yaml` points Render at the `telegram-id-checker` directory.
   Alternatively, create a Python web service with:
   - Root directory: `telegram-id-checker`
   - Build command: `pip install -r requirements.txt`
   - Start command: `python bot.py`
3. Add `BOT_TOKEN`, `MONGODB_URI`, and `ADMIN_IDS` as Render environment
   variables. Keep secrets in Render's environment settings.
4. Deploy and confirm the service logs contain `Telegram bot is ready`.
5. Open `https://<your-render-service>.onrender.com/healthz` and confirm it
   returns JSON with `"status": "ok"`.

For a continuously running bot, use a Render service plan that stays
available for your account. Free services may sleep or have platform limits;
an uptime monitor cannot guarantee a platform will keep a sleeping service
awake.

## UptimeRobot

After Render provides the public service URL, create an HTTP(s) monitor in
UptimeRobot for:

```text
https://<your-render-service>.onrender.com/healthz
```

Expect an HTTP 200 response. Do not use a local or Replit development URL.

## Troubleshooting

- **No group messages are detected:** turn off BotFather privacy mode, ensure
  the bot is still in the group, and check that Telegram is delivering normal
  messages to it.
- **MongoDB startup failure:** verify the URI includes a database name, the
  deployment IP is allowed by MongoDB network access rules, and the credentials
  are stored correctly in the hosting provider.
- **A duplicate warning is not sent:** inspect the bot logs for a Telegram API
  error and verify the bot can send messages in the group.
- **Render health check fails:** confirm the service is a web service, the
  process binds `PORT`, and the `/healthz` path returns HTTP 200.

Sensitive values are never logged. The bot logs startup, database readiness,
new IDs, duplicates, ambiguity, and errors without logging full message text.