This program is **discord bot client for VATSIM ATC status**.

- it polls periodically from vatsim stat json
- and check ATC connection status by checking differences between old and new one.

# Slash Commands

| Command | Description |
|---------|-------------|
| `/online` | Show currently online controllers in Japanese airspace |
| `/nickname add <cid> <name>` | Register a nickname for a VATSIM CID |
| `/nickname remove <cid>` | Remove a registered nickname |
| `/nickname list` | Show all registered nicknames |

# Features

- **Login/Logout notifications** - Automatic embed messages when controllers connect/disconnect
- **Connection duration** - Shows how long a controller has been online (on both login and logout notifications)
- **CID nicknames** - Map VATSIM CIDs to friendly names, displayed in notifications and `/online`

# How to use
- install python module discord.py (pip install discord.py)
- generate vatsim_stat_notify_to_discord.py file. (or git clone)
- edit settings.ini file (edit ATC callsign prefix, discord bot token, discord channel id)
- set DISCORD_BOT_TOKEN environment variable (or add to settings.ini)
- run in foreground: python vatsim_stat_notify_to_discord.py
- run in background: nohup python vatsim_stat_notify_to_discord.py &

# Contact
- If you wanna modify or add some features, plz contact me or send git pull request.
