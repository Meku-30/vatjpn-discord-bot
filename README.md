This program is **discord bot client for VATSIM ATC status**.

- it polls periodically from vatsim stat json
- and check ATC connection status by checking differences between old and new one.

# How to use
- install python module discord.py (pip install discord.py)
- generate vatsim_stat_notify_to_discord.py file. (or git clone)
- edit settings.ini file (edit ATC callsign prefix, discord bot token, discord channel id)
- run in foreground: python vatsim_stat_notify_to_discord.py
- run in background: nohup python vatsim_stat_notify_to_discord.py &

# Contact
- If you wanna modify or add some features, plz contact me or send git pull request.
