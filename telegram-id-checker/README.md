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

The bot creates a unique MongoDB index on `id_records.id` at startup. Each ID
has exactly one `id_records` document containing only `id`, `user`,
`date`, and `occurrence_count`. Duplicate checks atomically increment the
existing record and replace its current user/date; no occurrence-history
documents are created. Group messages that contain an ID are also claimed in
the separate `processed_messages` collection using a unique
`(chat_id, message_id)` index. This prevents a redelivered Telegram update
from incrementing an ID twice.

Run the tests:

```bash
pytest -q
```

## Admin commands

Only Telegram user IDs listed in `ADMIN_IDS` can use these commands:

- `/start` — show the welcome message and an “Add me to your chat!” button
- `/checkid 12345` — read-only lookup
- `/stats` — current-date record counts plus all-time totals
- `/recent` — recently updated ID records
- `/duplicates` — recently updated records with multiple occurrences
- `/panel` — open the private admin control panel
- `/status` — show bot and database status
- `/userlists` — show users attached to current ID records
- `/grouplists` — show registered groups
- `/broadcast` — open broadcast options
- `/clear_ids` — privately choose and confirm clearing either ID records or
  processed message IDs

The bot never stores an ID because `/checkid` was used.
The recent and duplicate commands read the current `id_records` documents;
previous-user history is intentionally not retained.

`/clear_ids` is restricted to admins in private chats. It first offers two
primary options:

- `Clear ID Records` — deletes every document in `id_records` and preserves
  `processed_messages`
- `Processed Message IDs` — deletes every document in `processed_messages` and
  preserves `id_records`

After either option is selected, a separate `Confirm` button is required.
`Cancel` returns to the control panel. Neither option deletes registered
groups or saved message templates.

## Admin control panel

Only users listed in `ADMIN_IDS` can open the control panel, and it works only
in their private chat with the bot. `/start` also opens the panel for these
users.

The panel includes:

- `Status` — bot, ID, user, and registered-group counts
- `User Lists` — current users and their current ID counts
- `Group Lists` — groups that have delivered messages to the bot
- `Welcome Message` — replace the private `/start` welcome message
- `Duplicate Warning` — replace the duplicate warning template
- `Control Panel Message` — replace the control panel heading and instructions
- `Clear IDs` — open the two-option data-clearing flow
- `Broadcast All` — preview and send a message to all registered groups
- `Broadcast Single` — preview and send to one registered group

The panel uses Telegram's native inline-button styles: `primary` (blue),
`success` (green), and `danger` (red). Button labels intentionally do not use
emoji. Clients released before February 9, 2026 may display these buttons
without styling. Group registry data is stored separately in `group_records`
with only `chat_id`, `chat_title`, and `last_seen`; it does not add fields to
`id_records`.

Admins can send normal text or Telegram custom/animated emoji directly after
selecting `Welcome Message`, `Duplicate Warning`, or `Control Panel Message`.
The bot stores Telegram message entities with the text, so an emoji ID does not
need to be entered manually. Duplicate warning templates support these
placeholders:
`{id}`, `{first_user}`, `{first_date}`, `{current_user}`, `{current_date}`,
and `{occurrence_count}`.

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