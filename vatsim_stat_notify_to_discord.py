import discord
from discord import app_commands
import asyncio
import requests
import json
import configparser
import re
import traceback
import os
import sqlite3
from datetime import datetime, timezone, timedelta

# load config
config = configparser.ConfigParser()
config.read("settings.ini")

vatsim_stat_json_url = config["VATSIM_CONFIG"]["vatsim_stat_json_url"]
vatsim_stat_retrieve_period = float(config["VATSIM_CONFIG"]["vatsim_stat_retrieve_period"])
vatsim_controller_callsign_filter_regex = config["VATSIM_CONFIG"]["vatsim_controller_callsign_filter_regex"]
pattern = re.compile(vatsim_controller_callsign_filter_regex)

discord_bot_client_token = os.environ.get("DISCORD_BOT_TOKEN") or config["DISCORD_CONFIG"]["discord_bot_client_token"]
discord_channel_id = int(config["DISCORD_CONFIG"]["discord_channel_id"])

data_filename = config["DATAFILE_CONFIG"]["data_filename"]
nickname_filename = config.get("DATAFILE_CONFIG", "nickname_filename", fallback="nicknames.json")
stats_db_filename = config.get("DATAFILE_CONFIG", "stats_db_filename", fallback="stats.db")




def init_db():
    conn = sqlite3.connect(stats_db_filename)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cid INTEGER,
        callsign TEXT,
        rating INTEGER,
        logon_time TEXT,
        logoff_time TEXT,
        duration_seconds INTEGER
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_logoff_time ON sessions(logoff_time)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_callsign ON sessions(callsign)")
    conn.commit()
    conn.close()

def log_session(atc_info):
    logon_time_str = atc_info.get("logon_time", "")
    logoff_time = datetime.now(timezone.utc)
    duration_seconds = 0
    if logon_time_str:
        try:
            logon_time = datetime.fromisoformat(logon_time_str.replace("Z", "+00:00"))
            duration_seconds = int((logoff_time - logon_time).total_seconds())
        except (ValueError, TypeError):
            pass
    conn = sqlite3.connect(stats_db_filename)
    c = conn.cursor()
    c.execute(
        "INSERT INTO sessions (cid, callsign, rating, logon_time, logoff_time, duration_seconds) VALUES (?, ?, ?, ?, ?, ?)",
        (atc_info["cid"], atc_info["callsign"], atc_info["rating"],
         logon_time_str, logoff_time.isoformat(), duration_seconds)
    )
    conn.commit()
    conn.close()

init_db()

rating_list = ["Unknown", "OBS", "S1", "S2", "S3", "C1", "C2", "C3", "I1", "I2", "I3", "SUP", "ADM"]

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def load_nicknames():
    try:
        with open(nickname_filename, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_nicknames(nicknames):
    with open(nickname_filename, "w") as f:
        json.dump(nicknames, f, ensure_ascii=False, indent=2)

def get_display_name(cid):
    nicknames = load_nicknames()
    cid_str = str(cid)
    if cid_str in nicknames:
        return f"{nicknames[cid_str]} ({cid_str})"
    return str(cid)

def get_old():
    try:
        with open(data_filename, "r") as old_file:
            return json.loads(old_file.read())
    except:
        return {}

def get_new():
    vatsim_info = requests.get(vatsim_stat_json_url).json()
    controllers = vatsim_info["controllers"]
    controllers_map = { controllers[i]["callsign"]: controllers[i] for i in range(0, len(controllers)) }

    # print(vatsim_info["general"])

    return controllers_map

def get_controllers():
    old_stat = get_old()
    new_stat = get_new()

    # save current
    with open(data_filename, "w") as a_file:
        json.dump(new_stat, a_file)

    connected_controllers = { k : new_stat[k] for k in set(new_stat) - set(old_stat) }
    disconnected_controllers = { k : old_stat[k] for k in set(old_stat) - set(new_stat) }

    # filter
    connected_controllers = { d: connected_controllers[d] for d in connected_controllers if pattern.match(connected_controllers[d]['callsign']) is not None and connected_controllers[d]["rating"]>1 }
    disconnected_controllers = { d: disconnected_controllers[d] for d in disconnected_controllers if pattern.match(disconnected_controllers[d]['callsign']) is not None and disconnected_controllers[d]["rating"]>1 }
    all_controllers = { d: new_stat[d] for d in new_stat if pattern.match(new_stat[d]['callsign']) is not None and new_stat[d]["rating"]>1 }


    return all_controllers, connected_controllers, disconnected_controllers



def format_duration(logon_time_str):
    try:
        logon_time = datetime.fromisoformat(logon_time_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - logon_time
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, _ = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}時間{minutes}分"
        return f"{minutes}分"
    except (ValueError, TypeError):
        return "不明"

def format_online_entry(atc_info):
    callsign = atc_info["callsign"]
    freq = f"({atc_info['frequency']})" if atc_info["frequency"] != "199.998" else ""
    name = get_display_name(atc_info["cid"])
    duration = format_duration(atc_info.get("logon_time", "")) if atc_info.get("logon_time") else ""
    duration_str = f" [{duration}]" if duration else ""
    return f"{callsign}{freq} - {name}{duration_str}"

def get_discord_embed(connect_type, atc_info, current_list):
    online_entries = [format_online_entry(current_list[d]) for d in current_list]
    display_name = get_display_name(atc_info["cid"])

    if connect_type == "connect":
        embed = discord.Embed(title=atc_info['callsign'] + ' - ' + connect_type, color=0x00ff00, description='< online list >\n' + '\n'.join(online_entries))
        embed.add_field(name='Rating', value=rating_list[atc_info["rating"]])
        embed.add_field(name='CID', value=display_name)
        embed.add_field(name='Server', value=atc_info["server"])
        return embed

    if connect_type == "disconnect":
        embed = discord.Embed(title=atc_info['callsign'] + ' - ' + connect_type, color=0xff0000, description='< online list >\n' + '\n'.join(online_entries))
        embed.add_field(name='Rating', value=rating_list[atc_info["rating"]])
        embed.add_field(name='CID', value=display_name)
        embed.add_field(name='Server', value=atc_info["server"])
        if atc_info.get("logon_time"):
            embed.add_field(name='接続時間', value=format_duration(atc_info["logon_time"]))
        return embed


async def run():
    await client.wait_until_ready()

    while not client.is_closed():
        try:
            all_controllers, connected_controllers, disconnected_controllers = get_controllers()

            channel = client.get_channel(discord_channel_id)

            for a in connected_controllers:
                await channel.send(embed = get_discord_embed('connect', connected_controllers[a], all_controllers))
            for a in disconnected_controllers:
                await channel.send(embed = get_discord_embed('disconnect', disconnected_controllers[a], all_controllers))
                log_session(disconnected_controllers[a])

        except Exception as e:
                traceback.print_exc()

        finally:
            await asyncio.sleep(vatsim_stat_retrieve_period)

@tree.command(name="online", description="日本空域のオンライン管制官を表示")
async def online_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        vatsim_info = requests.get(vatsim_stat_json_url).json()
        controllers = vatsim_info["controllers"]
        jp_controllers = [c for c in controllers if pattern.match(c["callsign"]) and c["rating"] > 1]

        if not jp_controllers:
            await interaction.followup.send("現在、日本空域にオンラインの管制官はいません。")
            return

        jp_controllers.sort(key=lambda c: c["callsign"])
        lines = []
        for c in jp_controllers:
            freq = f" ({c['frequency']})" if c["frequency"] != "199.998" else ""
            name = get_display_name(c["cid"])
            duration = format_duration(c.get("logon_time", ""))
            lines.append(f"**{c['callsign']}**{freq}\n  {rating_list[c['rating']]} | {name} | {duration}")

        embed = discord.Embed(
            title="VATJPN Online Controllers",
            color=0x0099ff,
            description='\n'.join(lines)
        )
        embed.set_footer(text=f"Total: {len(jp_controllers)} controller(s)")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.followup.send(f"エラーが発生しました: {e}")

nickname_group = app_commands.Group(name="nickname", description="CIDニックネーム管理")

@nickname_group.command(name="add", description="CIDにニックネームを登録")
@app_commands.describe(cid="VATSIM CID", name="ニックネーム")
async def nickname_add(interaction: discord.Interaction, cid: int, name: str):
    nicknames = load_nicknames()
    nicknames[str(cid)] = name
    save_nicknames(nicknames)
    await interaction.response.send_message(f"CID {cid} のニックネームを「{name}」に設定しました。")

@nickname_group.command(name="remove", description="CIDのニックネームを削除")
@app_commands.describe(cid="VATSIM CID")
async def nickname_remove(interaction: discord.Interaction, cid: int):
    nicknames = load_nicknames()
    cid_str = str(cid)
    if cid_str in nicknames:
        removed = nicknames.pop(cid_str)
        save_nicknames(nicknames)
        await interaction.response.send_message(f"CID {cid} のニックネーム「{removed}」を削除しました。")
    else:
        await interaction.response.send_message(f"CID {cid} にニックネームは登録されていません。")

@nickname_group.command(name="list", description="登録済みニックネーム一覧")
async def nickname_list(interaction: discord.Interaction):
    nicknames = load_nicknames()
    if not nicknames:
        await interaction.response.send_message("ニックネームは登録されていません。")
        return
    lines = [f"**{cid}**: {name}" for cid, name in sorted(nicknames.items())]
    embed = discord.Embed(
        title="登録済みニックネーム",
        color=0xffaa00,
        description='\n'.join(lines)
    )
    await interaction.response.send_message(embed=embed)

tree.add_command(nickname_group)

def format_duration_seconds(total_seconds):
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}時間{minutes:02d}分"
    return f"{minutes}分"

@tree.command(name="stats", description="日本空域の管制統計を表示")
@app_commands.describe(
    days="集計日数（0=全期間、1=過去1日、7=過去7日 等）",
    position="ポジションフィルター（部分一致、例: RJTT）"
)
async def stats_command(interaction: discord.Interaction, days: int = 7, position: str = None):
    await interaction.response.defer()
    try:
        now = datetime.now(timezone.utc)
        if days > 0:
            start = (now - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
            period_label = f"過去{days}日間"
        else:
            start = None
            period_label = "全期間"

        conn = sqlite3.connect(stats_db_filename)
        c = conn.cursor()

        query = "SELECT cid, callsign, duration_seconds FROM sessions WHERE 1=1"
        params = []
        if start:
            query += " AND logoff_time >= ?"
            params.append(start.isoformat())
        if position:
            query += " AND callsign LIKE ?"
            params.append(f"%{position}%")

        c.execute(query, params)
        rows = c.fetchall()
        conn.close()

        if not rows:
            await interaction.followup.send(f"📊 **VATJPN 管制統計 ({period_label})**\n\nデータがありません。")
            return

        total_sessions = len(rows)
        total_duration = sum(r[2] for r in rows)

        # ポジション別集計
        pos_stats = {}
        for _, callsign, duration in rows:
            if callsign not in pos_stats:
                pos_stats[callsign] = {"duration": 0, "count": 0}
            pos_stats[callsign]["duration"] += duration
            pos_stats[callsign]["count"] += 1
        pos_ranking = sorted(pos_stats.items(), key=lambda x: x[1]["duration"], reverse=True)[:10]

        # 管制官別集計
        ctrl_stats = {}
        for cid, _, duration in rows:
            if cid not in ctrl_stats:
                ctrl_stats[cid] = {"duration": 0, "count": 0}
            ctrl_stats[cid]["duration"] += duration
            ctrl_stats[cid]["count"] += 1
        ctrl_ranking = sorted(ctrl_stats.items(), key=lambda x: x[1]["duration"], reverse=True)[:10]

        pos_lines = []
        for callsign, data in pos_ranking:
            pos_lines.append(f"`{callsign}` - {format_duration_seconds(data['duration'])} ({data['count']}回)")

        ctrl_lines = []
        for cid, data in ctrl_ranking:
            name = get_display_name(cid)
            ctrl_lines.append(f"{name} - {format_duration_seconds(data['duration'])} ({data['count']}回)")

        description = f"セッション数: **{total_sessions}**\n合計管制時間: **{format_duration_seconds(total_duration)}**"

        embed = discord.Embed(
            title=f"📊 VATJPN 管制統計 ({period_label})",
            color=0x00bfff,
            description=description
        )
        if pos_lines:
            embed.add_field(name="【ポジション別】", value='\n'.join(pos_lines), inline=False)
        if ctrl_lines:
            embed.add_field(name="【管制官別】", value='\n'.join(ctrl_lines), inline=False)

        await interaction.followup.send(embed=embed)
    except Exception as e:
        traceback.print_exc()
        await interaction.followup.send(f"エラーが発生しました: {e}")

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    try:
        # ギルドごとに同期
        for guild in client.guilds:
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
            print(f'Synced {len(synced)} slash command(s) to {guild.name}')
        # 古いグローバルコマンドをクリア
        tree.clear_commands(guild=None)
        await tree.sync()
        print('Cleared global commands')
    except Exception as e:
        print(f'Failed to sync slash commands: {e}')
    # 二重起動防止：すでにループが走っていないか確認
    if not hasattr(client, 'loop_started'):
        client.loop.create_task(run())
        client.loop_started = True

client.run(discord_bot_client_token)
