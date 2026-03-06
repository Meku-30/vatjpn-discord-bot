import discord
from discord import app_commands
from discord.ext import tasks
import aiohttp
import asyncio
import json
import configparser
import re
import logging
import os
import sys
import sqlite3
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("vatjpn-bot")

# ── Feature flags ────────────────────────────────────────────────
enable_notifications = os.environ.get("ENABLE_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
enable_pirep_notifications = os.environ.get("ENABLE_PIREP_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")

# ── Config validation ──────────────────────────────────────────────

config = configparser.ConfigParser()
if not config.read("settings.ini"):
    logger.error("settings.ini が見つかりません。settings.ini.example をコピーして設定してください。")
    sys.exit(1)

REQUIRED_CONFIG = {
    "VATSIM_CONFIG": ["vatsim_stat_json_url", "vatsim_stat_retrieve_period", "vatsim_controller_callsign_filter_regex"],
    "DISCORD_CONFIG": ["discord_channel_id"],
    "DATAFILE_CONFIG": ["data_filename"],
}
for section, keys in REQUIRED_CONFIG.items():
    if not config.has_section(section):
        logger.error("settings.ini にセクション [%s] がありません。", section)
        sys.exit(1)
    for key in keys:
        if not config.has_option(section, key):
            logger.error("settings.ini の [%s] にキー '%s' がありません。", section, key)
            sys.exit(1)

vatsim_stat_json_url = config["VATSIM_CONFIG"]["vatsim_stat_json_url"]
vatsim_stat_retrieve_period = float(config["VATSIM_CONFIG"]["vatsim_stat_retrieve_period"])
vatsim_controller_callsign_filter_regex = config["VATSIM_CONFIG"]["vatsim_controller_callsign_filter_regex"]
pattern = re.compile(vatsim_controller_callsign_filter_regex)

discord_bot_client_token = os.environ.get("DISCORD_BOT_TOKEN") or config.get("DISCORD_CONFIG", "discord_bot_client_token", fallback=None)
if not discord_bot_client_token:
    logger.error("DISCORD_BOT_TOKEN 環境変数または settings.ini の discord_bot_client_token を設定してください。")
    sys.exit(1)
discord_channel_id_str = config["DISCORD_CONFIG"]["discord_channel_id"]
if enable_notifications and not discord_channel_id_str:
    logger.error("ENABLE_NOTIFICATIONS=true の場合、discord_channel_id の設定が必要です。")
    sys.exit(1)
discord_channel_id = int(discord_channel_id_str) if discord_channel_id_str else 0

solo_validation_url = config.get("VATSIM_CONFIG", "solo_validation_url", fallback=None)

# ── SWIM API (NOTAM) ─────────────────────────────────────────────
swim_api_url = os.environ.get("SWIM_API_URL")
swim_api_token = os.environ.get("SWIM_API_TOKEN")
pirep_channel_id = int(os.environ.get("PIREP_CHANNEL_ID", 0)) or discord_channel_id

JAPAN_MAJOR_AIRPORTS = {
    "RJTT": "羽田",
    "RJAA": "成田",
    "RJBB": "関西",
    "RJOO": "伊丹",
    "RJFF": "福岡",
    "RJCC": "新千歳",
}

# 日本空港の表示順（北→南、AIS Japan準拠）
AIRPORT_ORDER = [
    "RJCR", "RJCW", "RJER", "RJEB", "RJCM", "RJEC", "RJCA", "RJCN",
    "RJCK", "RJCT", "RJCB", "RJCO", "RJCJ", "RJCC", "RJEO", "RJCH",
    "RJSO", "RJSA", "RJSM", "RJSH", "RJSR", "RJSK", "RJSI", "RJSY",
    "RJSC", "RJST", "RJSU", "RJSS", "RJSF",
    "RJSD", "RJSN", "RJAF", "RJTU", "RJAH", "RJAK", "RJTL", "RJAA",
    "RJTJ", "RJTY", "RJTC", "RJTF", "RJTT", "RJTA", "RJTK", "RJTE",
    "RJTO", "RJAN", "RJAZ", "RJTQ", "RJTH", "RJAW", "RJAM",
    "RJNW", "RJNT", "RJNK", "RJNF", "RJNG", "RJNA", "RJNY", "RJNS",
    "RJNH", "RJGG", "RJOE",
    "RJBT", "RJOO", "RJOY", "RJBE", "RJBB", "RJBD",
    "RJOR", "RJNO", "RJOH", "RJOC", "RJOW", "RJOB", "RJBK", "RJOA",
    "RJOI", "RJOF", "RJDC", "RJOZ",
    "RJOS", "RJOT", "RJOM", "RJOK",
    "RJDT", "RJDB", "RJFA", "RJFR", "RJFF", "RJFZ", "RJFO", "RJDO",
    "RJDK",
    "RJFE", "RJFS", "RJDM", "RJDU", "RJFU", "RJFT", "RJDA", "RJFN",
    "RJFM", "RJFK", "RJFY",
    "RJFG", "RJFC", "RJKA", "RJKI", "RJKN", "RJKB", "RORY", "RORE",
    "RORA", "ROKJ", "RODN", "ROTM", "ROAH", "ROKR", "RORK", "ROMD",
    "RORS", "ROMY", "RORT", "ROIG", "RORH", "ROYN",
]
AIRPORT_ORDER_MAP = {code: i for i, code in enumerate(AIRPORT_ORDER)}

data_filename = config["DATAFILE_CONFIG"]["data_filename"]
nickname_filename = config.get("DATAFILE_CONFIG", "nickname_filename", fallback="nicknames.json")
stats_db_filename = config.get("DATAFILE_CONFIG", "stats_db_filename", fallback="stats.db")

# ── SQLite ─────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(stats_db_filename)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL")
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
    c.execute("""CREATE TABLE IF NOT EXISTS user_links (
        discord_id TEXT PRIMARY KEY,
        cid INTEGER NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_cid ON sessions(cid)")
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
    with sqlite3.connect(stats_db_filename) as conn:
        conn.execute(
            "INSERT INTO sessions (cid, callsign, rating, logon_time, logoff_time, duration_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (atc_info["cid"], atc_info["callsign"], atc_info["rating"],
             logon_time_str, logoff_time.isoformat(), duration_seconds)
        )

def link_user(discord_id, cid):
    with sqlite3.connect(stats_db_filename) as conn:
        conn.execute("INSERT OR REPLACE INTO user_links (discord_id, cid) VALUES (?, ?)", (str(discord_id), cid))

def unlink_user(discord_id):
    with sqlite3.connect(stats_db_filename) as conn:
        cursor = conn.execute("DELETE FROM user_links WHERE discord_id = ?", (str(discord_id),))
        return cursor.rowcount > 0

def get_linked_cid(discord_id):
    with sqlite3.connect(stats_db_filename) as conn:
        row = conn.execute("SELECT cid FROM user_links WHERE discord_id = ?", (str(discord_id),)).fetchone()
        return row[0] if row else None

def get_controller_stats(cid):
    """Get statistics for a given CID from local DB. Returns dict or None."""
    with sqlite3.connect(stats_db_filename) as conn:
        c = conn.cursor()

        c.execute("SELECT COUNT(*), SUM(duration_seconds) FROM sessions WHERE cid = ?", (cid,))
        row = c.fetchone()
        total_sessions = row[0] or 0
        total_duration = row[1] or 0

        if total_sessions == 0:
            return None

        # Position breakdown (top 5)
        c.execute("""SELECT callsign, SUM(duration_seconds) as total, COUNT(*) as cnt
                     FROM sessions WHERE cid = ?
                     GROUP BY callsign ORDER BY total DESC LIMIT 5""", (cid,))
        positions = [{"callsign": r[0], "duration": r[1], "count": r[2]} for r in c.fetchall()]

        # Longest session
        c.execute("""SELECT callsign, duration_seconds, logon_time FROM sessions
                     WHERE cid = ? ORDER BY duration_seconds DESC LIMIT 1""", (cid,))
        longest_row = c.fetchone()
        longest = {"callsign": longest_row[0], "duration": longest_row[1]} if longest_row else None

        # Last activity
        c.execute("SELECT logoff_time FROM sessions WHERE cid = ? ORDER BY logoff_time DESC LIMIT 1", (cid,))
        last_row = c.fetchone()
        last_logoff = last_row[0] if last_row else None

        return {
            "total_sessions": total_sessions,
            "total_duration": total_duration,
            "positions": positions,
            "longest": longest,
            "last_logoff": last_logoff,
        }

async def fetch_vatsim_member(http_session, cid):
    """Fetch member info and stats from VATSIM API. Returns dict or None."""
    try:
        async with http_session.get(f"https://api.vatsim.net/v2/members/{cid}") as resp:
            if resp.status != 200:
                return None
            info = await resp.json()
        async with http_session.get(f"https://api.vatsim.net/v2/members/{cid}/stats") as resp:
            if resp.status == 200:
                info["stats"] = await resp.json()
        return info
    except Exception:
        return None

def build_stats_embed(cid, stats, vatsim_info):
    """Build Discord embed for controller stats."""
    name = get_display_name(cid)

    rating_str = ""
    reg_date_str = ""
    if vatsim_info:
        r = vatsim_info.get("rating", 0)
        rating_str = get_rating_str(r)
        reg = vatsim_info.get("reg_date", "")
        if reg:
            reg_date_str = reg[:10]

    vatsim_atc_hours = None
    if vatsim_info and "stats" in vatsim_info:
        vatsim_atc_hours = vatsim_info["stats"].get("atc")

    desc_lines = []
    if rating_str:
        desc_lines.append(f"Rating: **{rating_str}**")
    if reg_date_str:
        desc_lines.append(f"登録日: {reg_date_str}")
    if vatsim_atc_hours is not None:
        h = int(vatsim_atc_hours)
        m = int((vatsim_atc_hours - h) * 60)
        desc_lines.append(f"VATSIM総管制時間: **{h}時間{m:02d}分**")
    desc_lines.append("")
    desc_lines.append(f"__日本空域 (Bot記録)__")
    desc_lines.append(f"総セッション: **{stats['total_sessions']}**回")
    desc_lines.append(f"総管制時間: **{format_duration_seconds(stats['total_duration'])}**")
    if stats["longest"]:
        desc_lines.append(f"最長セッション: **{format_duration_seconds(stats['longest']['duration'])}** ({stats['longest']['callsign']})")
    if stats["last_logoff"]:
        desc_lines.append(f"最終ログオフ: {stats['last_logoff'][:16]}Z")

    embed = discord.Embed(
        title=f"Controller Stats - {name}",
        color=0x00bfff,
        description="\n".join(desc_lines),
    )

    if stats["positions"]:
        pos_lines = []
        rank_medal = ["🥇", "🥈", "🥉"]
        for i, p in enumerate(stats["positions"]):
            prefix = rank_medal[i] if i < 3 else f"`{i+1}.`"
            pos_lines.append(f"{prefix} `{p['callsign']}` - {format_duration_seconds(p['duration'])} ({p['count']}回)")
        embed.add_field(name="ポジション別 TOP5", value="\n".join(pos_lines), inline=False)

    return embed

init_db()

# ── Constants ──────────────────────────────────────────────────────

rating_list = ["Unknown", "OBS", "S1", "S2", "S3", "C1", "C2", "C3", "I1", "I2", "I3", "SUP", "ADM"]

def get_rating_str(rating):
    if 0 <= rating < len(rating_list):
        return rating_list[rating]
    return f"Unknown({rating})"

# ── OJT/Rating validation ────────────────────────────────────────

_solo_cache = []
_solo_cache_time = None
SOLO_CACHE_TTL = 43200  # 12時間

async def fetch_solo_list(http_session):
    """solo.txtを取得・パースしてキャッシュを更新"""
    global _solo_cache, _solo_cache_time
    if not solo_validation_url:
        return []
    now = datetime.now(timezone.utc)
    if _solo_cache_time and (now - _solo_cache_time).total_seconds() < SOLO_CACHE_TTL:
        return _solo_cache
    try:
        async with http_session.get(solo_validation_url) as resp:
            text = await resp.text()
        entries = []
        for line in text.strip().splitlines():
            parts = line.strip().split(";")
            if len(parts) >= 4:
                try:
                    entries.append({
                        "cid": int(parts[0]),
                        "callsign": parts[1],
                        "start": parts[2],
                        "end": parts[3],
                    })
                except ValueError:
                    continue
        _solo_cache = entries
        _solo_cache_time = now
    except Exception:
        pass  # キャッシュが古くてもそのまま使う
    return _solo_cache

POSITION_MIN_RATING = {
    "DEL": 3, "GND": 3,  # S2
    "TWR": 3,              # S2
    "APP": 4, "DEP": 4,   # S3
    "CTR": 5,              # C1
}

def check_position_rating(callsign, rating):
    """Ratingがポジションに対して不足していないかチェック。
    Returns: 警告文字列 or None"""
    is_ojt = "_T_" in callsign
    suffix = callsign.rsplit("_", 1)[-1] if "_" in callsign else None
    if suffix not in POSITION_MIN_RATING:
        return None
    min_rating = POSITION_MIN_RATING[suffix]
    if not is_ojt and rating < min_rating:
        return f"⚠️ Rating不足: {get_rating_str(rating)} が {suffix} を開局（最低 {get_rating_str(min_rating)} 必要）"
    return None

async def check_solo_registration(http_session, callsign, cid, current_list=None):
    """_T_付きコールサインがsolo.txtに登録されているかチェック。
    日本空域に_I_付きコールサイン（監督者）がオンラインなら監督付きOJTとみなし警告しない。
    Returns: 警告文字列 or None"""
    if "_T_" not in callsign:
        return None
    # 日本空域に_I_付き（Instructor/Mentor）がオンラインなら監督付きOJTとみなす
    if current_list:
        for info in current_list.values():
            if "_I_" in info["callsign"]:
                return None
    if not solo_validation_url:
        return None
    solo_list = await fetch_solo_list(http_session)
    if not solo_list:
        return None  # solo.txt取得不可時は警告しない
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for entry in solo_list:
        if entry["cid"] == cid and entry["callsign"] == callsign:
            if entry["start"] <= today <= entry["end"]:
                return None  # 登録あり・有効期間内
    return f"⚠️ solo.txt未登録のOJT（{callsign}）"

# ── Bot class ──────────────────────────────────────────────────────

class VATJPNBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.http_session = None
        self.pirep_notified = set()  # 通知済みPIREPのcontrol_number
        self._pirep_first_run = True

    async def setup_hook(self):
        timeout = aiohttp.ClientTimeout(total=10)
        self.http_session = aiohttp.ClientSession(timeout=timeout)
        if enable_notifications:
            self.polling_loop.start()
            if enable_pirep_notifications and swim_api_url and swim_api_token:
                self.pirep_loop.start()

    async def close(self):
        if enable_notifications:
            self.polling_loop.cancel()
            if enable_pirep_notifications and self.pirep_loop.is_running():
                self.pirep_loop.cancel()
        if self.http_session:
            await self.http_session.close()
        await super().close()

    @tasks.loop(seconds=vatsim_stat_retrieve_period)
    async def polling_loop(self):
        try:
            all_controllers, connected, disconnected = await get_controllers(self.http_session)
            channel = self.get_channel(discord_channel_id)
            if channel is None:
                return
            for a in connected:
                await channel.send(embed=await get_discord_embed('connect', connected[a], all_controllers, self.http_session))
            for a in disconnected:
                await channel.send(embed=await get_discord_embed('disconnect', disconnected[a], all_controllers))
                log_session(disconnected[a])
        except Exception:
            logger.exception("エラーが発生しました")

    @polling_loop.before_loop
    async def before_polling(self):
        await self.wait_until_ready()

    @tasks.loop(seconds=300)
    async def pirep_loop(self):
        try:
            pireps, err = await fetch_active_pireps(self.http_session)
            if err:
                logger.warning("PIREP取得エラー: %s", err)
                return
            channel = self.get_channel(pirep_channel_id)
            if channel is None:
                return

            # MOD以上 (strength >= 4) を抽出（数値・テキスト両対応）
            mod_plus = [
                p for p in pireps
                if (turbulence_level(p.get("turbulence_strength", "")) or 0) >= 4
            ]

            # 有効なPIREPのcontrol_number一覧（期限切れを自動クリア用）
            active_ids = {p["control_number"] for p in pireps}
            self.pirep_notified &= active_ids

            if self._pirep_first_run:
                # 初回: 既存PIREPを全て通知済みに登録（通知はスキップ）
                self._pirep_first_run = False
                for p in mod_plus:
                    self.pirep_notified.add(p["control_number"])
                logger.info("PIREP監視開始（既存MOD+: %d件をスキップ）", len(mod_plus))
                return

            # 通常: 新規PIREPのみ通知
            for p in mod_plus:
                cn = p["control_number"]
                if cn in self.pirep_notified:
                    continue
                self.pirep_notified.add(cn)
                await channel.send(embed=build_pirep_embed(p))

        except Exception:
            logger.exception("PIREPポーリングエラー")

    @pirep_loop.before_loop
    async def before_pirep_loop(self):
        await self.wait_until_ready()

bot = VATJPNBot()

# ── Helper functions ───────────────────────────────────────────────

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
    except Exception:
        return {}

async def get_new(http_session):
    async with http_session.get(vatsim_stat_json_url) as resp:
        vatsim_info = await resp.json()
    controllers = vatsim_info.get("controllers", [])
    controllers_map = {c["callsign"]: c for c in controllers}
    return controllers_map

async def get_controllers(http_session):
    old_stat = get_old()
    new_stat = await get_new(http_session)

    with open(data_filename, "w") as a_file:
        json.dump(new_stat, a_file)

    connected_controllers = { k : new_stat[k] for k in set(new_stat) - set(old_stat) }
    disconnected_controllers = { k : old_stat[k] for k in set(old_stat) - set(new_stat) }

    connected_controllers = { d: connected_controllers[d] for d in connected_controllers if pattern.match(connected_controllers[d]['callsign']) is not None and connected_controllers[d]["rating"]>1 }
    disconnected_controllers = { d: disconnected_controllers[d] for d in disconnected_controllers if pattern.match(disconnected_controllers[d]['callsign']) is not None and disconnected_controllers[d]["rating"]>1 }
    all_controllers = { d: new_stat[d] for d in new_stat if pattern.match(new_stat[d]['callsign']) is not None and new_stat[d]["rating"]>1 }

    return all_controllers, connected_controllers, disconnected_controllers

# ── Format helpers ─────────────────────────────────────────────────

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

def format_duration_seconds(total_seconds):
    hours, remainder = divmod(total_seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}時間{minutes:02d}分"
    return f"{minutes}分"

async def get_discord_embed(connect_type, atc_info, current_list, http_session=None):
    online_entries = [format_online_entry(current_list[d]) for d in current_list]
    display_name = get_display_name(atc_info["cid"])

    if connect_type == "connect":
        color = 0x00ff00
        warnings = []
        if http_session:
            rating_warn = check_position_rating(atc_info["callsign"], atc_info["rating"])
            if rating_warn:
                warnings.append(rating_warn)
            solo_warn = await check_solo_registration(http_session, atc_info["callsign"], atc_info["cid"], current_list)
            if solo_warn:
                warnings.append(solo_warn)
        if warnings:
            color = 0xffa500

        embed = discord.Embed(title=atc_info['callsign'] + ' - ' + connect_type, color=color, description='< online list >\n' + '\n'.join(online_entries))
        embed.add_field(name='Rating', value=get_rating_str(atc_info["rating"]))
        embed.add_field(name='CID', value=display_name)
        embed.add_field(name='Server', value=atc_info["server"])
        if warnings:
            embed.add_field(name='警告', value='\n'.join(warnings), inline=False)
        return embed

    if connect_type == "disconnect":
        embed = discord.Embed(title=atc_info['callsign'] + ' - ' + connect_type, color=0xff0000, description='< online list >\n' + '\n'.join(online_entries))
        embed.add_field(name='Rating', value=get_rating_str(atc_info["rating"]))
        embed.add_field(name='CID', value=display_name)
        embed.add_field(name='Server', value=atc_info["server"])
        if atc_info.get("logon_time"):
            embed.add_field(name='接続時間', value=format_duration(atc_info["logon_time"]))
        return embed

# ── NOTAM helper ──────────────────────────────────────────────────

NOTAM_PER_PAGE = 5

async def fetch_notams(http_session, icao):
    """SWIM非公式APIから有効なNOTAMを取得。Returns (notams_list, total_count, error_msg)."""
    if not swim_api_url or not swim_api_token:
        return [], 0, "NOTAM機能を使用するにはSWIM_API_URL/SWIM_API_TOKEN環境変数の設定が必要です。"
    headers = {"Authorization": f"Bearer {swim_api_token}"}
    url = f"{swim_api_url}/api/notams/active"
    params = {"icao": icao.upper()}
    try:
        async with http_session.get(url, headers=headers, params=params) as resp:
            if resp.status == 401 or resp.status == 403:
                return [], 0, "SWIM APIの認証に失敗しました。トークンを確認してください。"
            if resp.status != 200:
                return [], 0, f"SWIM APIエラー (HTTP {resp.status})"
            notams = await resp.json()
    except asyncio.TimeoutError:
        return [], 0, "NOTAM情報の取得がタイムアウトしました。"
    except Exception:
        logger.exception("エラーが発生しました")
        return [], 0, "NOTAM情報の取得に失敗しました。"
    return notams, len(notams), None

def format_notam_page(notams, page, icao, total_count, keyword=None):
    """NOTAMリストの指定ページをEmbed形式で生成。"""
    total_pages = max(1, (len(notams) + NOTAM_PER_PAGE - 1) // NOTAM_PER_PAGE)
    start = page * NOTAM_PER_PAGE
    end = start + NOTAM_PER_PAGE
    page_notams = notams[start:end]

    lines = []
    for n in page_notams:
        notam_id = n.get("notam_id", "?")
        body = n.get("body", "")
        if len(body) > 200:
            body = body[:197] + "..."
        valid_from = (n.get("valid_from") or "")[:16]
        valid_to = (n.get("valid_to") or "")[:16]
        period = ""
        if valid_from or valid_to:
            period = f"\n  {valid_from} ~ {valid_to}"
        lines.append(f"**{notam_id}**\n{body}{period}")

    description = "\n\n".join(lines)
    if len(description) > 4096:
        description = description[:4093] + "..."

    filter_text = f" (filter: {keyword})" if keyword else ""
    title = f"{icao} NOTAM ({len(notams)}/{total_count}件){filter_text}"
    embed = discord.Embed(title=title, color=0xff9900, description=description)
    embed.set_footer(text=f"Page {page + 1}/{total_pages}")
    return embed, total_pages

class NotamPaginationView(discord.ui.View):
    def __init__(self, notams, icao, total_count, keyword=None):
        super().__init__(timeout=300)
        self.notams = notams
        self.icao = icao
        self.total_count = total_count
        self.keyword = keyword
        self.page = 0
        self.total_pages = max(1, (len(notams) + NOTAM_PER_PAGE - 1) // NOTAM_PER_PAGE)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages - 1

    @discord.ui.button(label="<", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._update_buttons()
        embed, _ = format_notam_page(self.notams, self.page, self.icao, self.total_count, self.keyword)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._update_buttons()
        embed, _ = format_notam_page(self.notams, self.page, self.icao, self.total_count, self.keyword)
        await interaction.response.edit_message(embed=embed, view=self)

# ── ATIS helper ───────────────────────────────────────────────────

async def fetch_atis(http_session, icao):
    """SWIM非公式APIから最新ATISを取得。Returns (atis_dict, error_msg)."""
    if not swim_api_url or not swim_api_token:
        return None, "ATIS機能を使用するにはSWIM_API_URL/SWIM_API_TOKEN環境変数の設定が必要です。"
    headers = {"Authorization": f"Bearer {swim_api_token}"}
    url = f"{swim_api_url}/api/atis/{icao.upper()}"
    try:
        async with http_session.get(url, headers=headers) as resp:
            if resp.status == 401 or resp.status == 403:
                return None, "SWIM APIの認証に失敗しました。トークンを確認してください。"
            if resp.status != 200:
                return None, f"SWIM APIエラー (HTTP {resp.status})"
            atis = await resp.json()
    except asyncio.TimeoutError:
        return None, "ATIS情報の取得がタイムアウトしました。"
    except Exception:
        logger.exception("エラーが発生しました")
        return None, "ATIS情報の取得に失敗しました。"
    return atis, None

async def fetch_all_atis(http_session):
    """SWIM非公式APIから全空港ATISを一括取得。Returns (atis_list, error_msg)."""
    if not swim_api_url or not swim_api_token:
        return [], "ATIS機能を使用するにはSWIM_API_URL/SWIM_API_TOKEN環境変数の設定が必要です。"
    headers = {"Authorization": f"Bearer {swim_api_token}"}
    url = f"{swim_api_url}/api/atis"
    try:
        async with http_session.get(url, headers=headers) as resp:
            if resp.status == 401 or resp.status == 403:
                return [], "SWIM APIの認証に失敗しました。トークンを確認してください。"
            if resp.status != 200:
                return [], f"SWIM APIエラー (HTTP {resp.status})"
            atis_list = await resp.json()
    except asyncio.TimeoutError:
        return [], "ATIS情報の取得がタイムアウトしました。"
    except Exception:
        logger.exception("エラーが発生しました")
        return [], "ATIS情報の取得に失敗しました。"
    return atis_list or [], None

# ── METAR helper ──────────────────────────────────────────────────

async def fetch_metar(http_session, icao):
    """SWIM非公式APIから最新METARを取得。Returns (metar_dict, error_msg)."""
    if not swim_api_url or not swim_api_token:
        return None, "METAR機能を使用するにはSWIM_API_URL/SWIM_API_TOKEN環境変数の設定が必要です。"
    headers = {"Authorization": f"Bearer {swim_api_token}"}
    url = f"{swim_api_url}/api/weather/{icao.upper()}"
    try:
        async with http_session.get(url, headers=headers) as resp:
            if resp.status == 401 or resp.status == 403:
                return None, "SWIM APIの認証に失敗しました。トークンを確認してください。"
            if resp.status != 200:
                return None, f"SWIM APIエラー (HTTP {resp.status})"
            weather_list = await resp.json()
    except asyncio.TimeoutError:
        return None, "METAR情報の取得がタイムアウトしました。"
    except Exception:
        logger.exception("エラーが発生しました")
        return None, "METAR情報の取得に失敗しました。"
    metar = next((w for w in weather_list if w.get("type") == "METAR"), None)
    return metar, None

# ── PIREP helper ─────────────────────────────────────────────────

TURBULENCE_MAP = {
    "0": "SMTH", "1": "LGTM", "2": "LGT", "3": "LGTP",
    "4": "MOD", "5": "MODP", "6": "SEV", "7": "EXT",
}

# AIREP Specialのテキスト形式 → 数値レベル
TURBULENCE_TEXT_TO_LEVEL = {
    "MODERATE": 4,
    "SEVERE": 6,
}

def turbulence_level(strength):
    """turbulence_strengthを数値レベルに変換する（数値・テキスト両対応）。"""
    if not strength:
        return None
    if strength.isdigit():
        return int(strength)
    return TURBULENCE_TEXT_TO_LEVEL.get(strength.upper())

async def fetch_active_pireps(http_session):
    """SWIM非公式APIから有効なPIREPを取得。Returns (pirep_list, error_msg)."""
    if not swim_api_url or not swim_api_token:
        return [], "PIREP機能を使用するにはSWIM_API_URL/SWIM_API_TOKEN環境変数の設定が必要です。"
    headers = {"Authorization": f"Bearer {swim_api_token}"}
    url = f"{swim_api_url}/api/pireps/active"
    try:
        async with http_session.get(url, headers=headers) as resp:
            if resp.status == 401 or resp.status == 403:
                return [], "SWIM APIの認証に失敗しました。トークンを確認してください。"
            if resp.status != 200:
                return [], f"SWIM APIエラー (HTTP {resp.status})"
            pireps = await resp.json()
    except asyncio.TimeoutError:
        return [], "PIREP情報の取得がタイムアウトしました。"
    except Exception:
        logger.exception("エラーが発生しました")
        return [], "PIREP情報の取得に失敗しました。"
    return pireps or [], None

def format_pirep_altitude(pirep):
    """PIREPの高度を表示用にフォーマットする。"""
    alt = pirep.get("altitude")
    if not alt:
        return "不明"
    indicator = pirep.get("altitude_indicator", "")
    if indicator == "F":
        return f"FL{alt}"
    return f"{alt}ft"

def format_pirep_location(pirep):
    """PIREPの緯度経度を表示用にフォーマットする。"""
    lat, lon = pirep.get("latitude"), pirep.get("longitude")
    if not lat or not lon:
        return "不明"
    try:
        lat_deg, lat_min = int(lat[:2]), int(lat[2:])
        lon_deg, lon_min = int(lon[:3]), int(lon[3:])
        return f"N{lat_deg}°{lat_min:02d}' E{lon_deg}°{lon_min:02d}'"
    except (ValueError, IndexError):
        return f"{lat}/{lon}"

def build_pirep_embed(pirep):
    """MOD以上のPIREP用Embedを作成する。"""
    strength_code = pirep.get("turbulence_strength", "")
    strength_label = TURBULENCE_MAP.get(strength_code, strength_code)
    level = turbulence_level(strength_code)
    is_severe = level is not None and level >= 6

    if is_severe:
        title = f"🔴 PIREP - {strength_label} Turbulence"
        color = 0xFF0000
    else:
        title = f"⚠️ PIREP - {strength_label} Turbulence"
        color = 0xFF9900

    body = pirep.get("body", "").strip()
    embed = discord.Embed(title=title, color=color, description=f"```\n{body}\n```")
    embed.add_field(name="強度", value=strength_label, inline=True)
    embed.add_field(name="高度", value=format_pirep_altitude(pirep), inline=True)
    embed.add_field(name="位置", value=format_pirep_location(pirep), inline=True)

    observed = pirep.get("observed_at", "")
    effective_end = pirep.get("effective_end", "")
    time_str = ""
    if observed:
        time_str += f"観測: {observed[0:10]} {observed[11:16]}Z"
    if effective_end:
        time_str += f"  有効: ~{effective_end[0:10]} {effective_end[11:16]}Z"
    if time_str:
        embed.set_footer(text=time_str)

    return embed

# ── Slash commands ─────────────────────────────────────────────────

@bot.tree.command(name="online", description="日本空域のオンライン管制官を表示")
async def online_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with bot.http_session.get(vatsim_stat_json_url) as resp:
            vatsim_info = await resp.json()
        controllers = vatsim_info.get("controllers", [])
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
            lines.append(f"**{c['callsign']}**{freq}\n  {get_rating_str(c['rating'])} | {name} | {duration}")

        description = '\n'.join(lines)
        if len(description) > 4096:
            description = description[:4093] + "..."
        embed = discord.Embed(
            title="VATJPN Online Controllers",
            color=0x0099ff,
            description=description
        )
        embed.set_footer(text=f"Total: {len(jp_controllers)} controller(s)")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

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

bot.tree.add_command(nickname_group)

@bot.tree.command(name="sup", description="オンラインのSupervisor一覧を表示")
async def sup_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        async with bot.http_session.get(vatsim_stat_json_url) as resp:
            vatsim_info = await resp.json()
        controllers = vatsim_info.get("controllers", [])
        sups = [c for c in controllers if c["rating"] >= 11]

        if not sups:
            await interaction.followup.send("現在、オンラインのSupervisorはいません。")
            return

        sups.sort(key=lambda c: c["callsign"])
        lines = []
        for c in sups:
            freq = f" ({c['frequency']})" if c["frequency"] != "199.998" else ""
            duration = format_duration(c.get("logon_time", ""))
            lines.append(f"**{c['callsign']}**{freq}\n  {get_rating_str(c['rating'])} | {c['name']} | {duration}")

        embed = discord.Embed(
            title="Online Supervisors",
            color=0xff6600,
            description='\n'.join(lines)
        )
        embed.set_footer(text=f"Total: {len(sups)} supervisor(s)")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

@bot.tree.command(name="notam", description="空港のNOTAMを表示")
@app_commands.describe(
    icao="空港のICAOコード（例: RJTT）または 'japan' で主要空港一括表示",
    keyword="キーワードでNOTAMを絞り込み（例: RWY, ILS, TWY）",
)
async def notam_command(interaction: discord.Interaction, icao: str, keyword: str = None):
    await interaction.response.defer()
    try:
        icao_input = icao.strip().upper()

        if icao_input == "JAPAN":
            # 主要空港一括サマリー
            tasks_list = [fetch_notams(bot.http_session, code) for code in JAPAN_MAJOR_AIRPORTS]
            results = await asyncio.gather(*tasks_list, return_exceptions=True)
            lines = []
            for (code, name), result in zip(JAPAN_MAJOR_AIRPORTS.items(), results):
                if isinstance(result, Exception):
                    lines.append(f"**{code}** ({name}): エラー")
                else:
                    _, total_count, error = result
                    if error:
                        lines.append(f"**{code}** ({name}): {error}")
                    else:
                        lines.append(f"**{code}** ({name}): {total_count}件")
            embed = discord.Embed(
                title="Japan NOTAM Summary",
                color=0xff9900,
                description="\n".join(lines),
            )
            await interaction.followup.send(embed=embed)
            return

        # 単一空港
        notams, total_count, error = await fetch_notams(bot.http_session, icao_input)
        if error:
            await interaction.followup.send(error)
            return
        if not notams:
            await interaction.followup.send(f"**{icao_input}** の有効なNOTAMはありません。")
            return

        # キーワードフィルター
        if keyword:
            kw = keyword.strip().upper()
            notams = [n for n in notams if kw in (n.get("body", "") + " " + n.get("notam_id", "")).upper()]
            if not notams:
                await interaction.followup.send(f"**{icao_input}** のNOTAMに「{keyword}」に一致するものはありません。({total_count}件中)")
                return

        embed, total_pages = format_notam_page(notams, 0, icao_input, total_count, keyword)
        view = NotamPaginationView(notams, icao_input, total_count, keyword) if total_pages > 1 else None
        await interaction.followup.send(embed=embed, view=view)
    except Exception:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

@bot.tree.command(name="atis", description="空港のATIS情報を表示")
@app_commands.describe(icao="空港のICAOコード（例: RJTT）または 'japan' で主要空港一括表示")
async def atis_command(interaction: discord.Interaction, icao: str):
    await interaction.response.defer()
    try:
        icao_input = icao.strip().upper()

        if icao_input == "JAPAN":
            atis_list, error = await fetch_all_atis(bot.http_session)
            if error:
                await interaction.followup.send(error)
                return
            if not atis_list:
                await interaction.followup.send("ATISデータがありません。")
                return

            # 北→南の順にソート（AIS Japan準拠）
            atis_list.sort(key=lambda a: AIRPORT_ORDER_MAP.get(a.get("icao_code", ""), 999))

            # エリア別にEmbed分割
            atis_regions = [
                ("RJCC", "RJSN"),
                ("RJAA", "RJGG"),
                ("RJOO", "RJOK"),
                ("RJFF", "RJFK"),
                ("ROAH", "ROIG"),
            ]
            region_bounds = []
            for start, end in atis_regions:
                s = AIRPORT_ORDER_MAP.get(start, 0)
                e = AIRPORT_ORDER_MAP.get(end, 999)
                region_bounds.append((s, e))

            embeds = []
            for s, e in region_bounds:
                lines = []
                for atis in atis_list:
                    idx = AIRPORT_ORDER_MAP.get(atis.get("icao_code", ""), -1)
                    if s <= idx <= e:
                        icao = atis.get("icao_code", "?")
                        letter = atis.get("atis_letter", "")
                        content = atis.get("content", "")
                        header = f"**{icao}**"
                        if letter:
                            header += f" - **{letter}**"
                        lines.append(f"{header}\n{content}")
                if lines:
                    description = "\n\n".join(lines)
                    if len(description) > 4096:
                        description = description[:4093] + "..."
                    embeds.append(discord.Embed(color=0x00bfff, description=description))

            if not embeds:
                await interaction.followup.send("ATISデータがありません。")
                return
            embeds[0].title = f"Japan ATIS ({len(atis_list)}空港)"
            await interaction.followup.send(embeds=embeds[:10])
            return

        atis, error = await fetch_atis(bot.http_session, icao_input)
        if error:
            await interaction.followup.send(error)
            return
        if not atis:
            await interaction.followup.send(f"**{icao_input}** のATISデータがありません。")
            return

        atis_letter = atis.get("atis_letter")
        content = atis.get("content", "")
        issued_at = atis.get("issued_at")

        title = f"{icao_input} ATIS"
        if atis_letter:
            title += f" - {atis_letter}"

        if len(content) > 4096:
            content = content[:4093] + "..."

        embed = discord.Embed(
            title=title,
            color=0x00bfff,
            description=content,
        )
        await interaction.followup.send(embed=embed)
    except Exception:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

@bot.tree.command(name="metar", description="空港のMETAR情報を表示")
@app_commands.describe(icao="空港のICAOコード（例: RJTT）")
async def metar_command(interaction: discord.Interaction, icao: str):
    await interaction.response.defer()
    try:
        icao_input = icao.strip().upper()
        metar, error = await fetch_metar(bot.http_session, icao_input)
        if error:
            await interaction.followup.send(error)
            return
        if not metar:
            await interaction.followup.send(f"**{icao_input}** のMETARデータがありません。")
            return

        raw_text = metar.get("raw_text", "")
        observed_at = metar.get("observed_at")

        if len(raw_text) > 4096:
            raw_text = raw_text[:4093] + "..."

        embed = discord.Embed(
            title=f"{icao_input} METAR",
            color=0x00bfff,
            description=raw_text,
        )
        await interaction.followup.send(embed=embed)
    except Exception:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

@bot.tree.command(name="traffic", description="指定空港の発着予定トラフィック一覧")
@app_commands.describe(icao="空港のICAOコード（例: RJTT）")
async def traffic_command(interaction: discord.Interaction, icao: str):
    await interaction.response.defer()
    try:
        icao = icao.upper()
        async with bot.http_session.get(vatsim_stat_json_url) as resp:
            vatsim_info = await resp.json()

        pilots = vatsim_info.get("pilots", [])
        prefiles = vatsim_info.get("prefiles", [])

        departures = []
        arrivals = []
        for p in pilots:
            fp = p.get("flight_plan")
            if not fp:
                continue
            if fp.get("departure") == icao:
                departures.append(p)
            if fp.get("arrival") == icao:
                arrivals.append(p)

        prefiled = []
        for p in prefiles:
            fp = p.get("flight_plan")
            if not fp:
                continue
            if fp.get("departure") == icao or fp.get("arrival") == icao:
                prefiled.append(p)

        if not departures and not arrivals and not prefiled:
            await interaction.followup.send(f"**{icao}** に関連するトラフィックはありません。")
            return

        embed = discord.Embed(
            title=f"{icao} Traffic",
            color=0x0099ff,
        )

        def format_altitude(p):
            alt = p.get("altitude", 0)
            try:
                alt = int(alt)
            except (ValueError, TypeError):
                return "?ft"
            return f"FL{alt // 100}" if alt > 10000 else f"{alt}ft"

        def truncate_field(text, limit=1024):
            if len(text) <= limit:
                return text
            return text[:limit - 3] + "..."

        dep_lines = []
        for p in sorted(departures, key=lambda x: x["callsign"]):
            fp = p["flight_plan"]
            gs = p.get("groundspeed", 0)
            status = f"{format_altitude(p)} / {gs}kt" if gs > 50 else "Gate/Taxi"
            dep_lines.append(f"**{p['callsign']}** ({fp.get('aircraft_short', fp.get('aircraft_faa', '?'))})\n  → {fp.get('arrival', '?')} | {status}")

        arr_lines = []
        for p in sorted(arrivals, key=lambda x: x["callsign"]):
            fp = p["flight_plan"]
            gs = p.get("groundspeed", 0)
            status = f"{format_altitude(p)} / {gs}kt" if gs > 50 else "Arrived/Taxi"
            arr_lines.append(f"**{p['callsign']}** ({fp.get('aircraft_short', fp.get('aircraft_faa', '?'))})\n  {fp.get('departure', '?')} → | {status}")

        embed.add_field(
            name=f"Departures ({len(departures)})",
            value=truncate_field('\n'.join(dep_lines[:15])) if dep_lines else "—",
            inline=True
        )
        embed.add_field(
            name=f"Arrivals ({len(arrivals)})",
            value=truncate_field('\n'.join(arr_lines[:15])) if arr_lines else "—",
            inline=True
        )

        if prefiled:
            pre_lines = []
            for p in sorted(prefiled, key=lambda x: x["callsign"]):
                fp = p["flight_plan"]
                dep = fp.get("departure", "?")
                arr = fp.get("arrival", "?")
                pre_lines.append(f"**{p['callsign']}** ({fp.get('aircraft_short', fp.get('aircraft_faa', '?'))}) {dep} → {arr}")
            embed.add_field(name=f"Prefiled ({len(prefiled)})", value=truncate_field('\n'.join(pre_lines[:10])), inline=False)

        total = len(departures) + len(arrivals) + len(prefiled)
        embed.set_footer(text=f"Total: {total} flight(s)")
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

@bot.tree.command(name="stats", description="日本空域の管制統計を表示")
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
        if position:
            period_label += f" | {position}"

        with sqlite3.connect(stats_db_filename) as conn:
            query = "SELECT cid, callsign, duration_seconds FROM sessions WHERE 1=1"
            params = []
            if start:
                query += " AND logoff_time >= ?"
                params.append(start.isoformat())
            if position:
                query += " AND callsign LIKE ?"
                params.append(f"%{position}%")
            rows = conn.execute(query, params).fetchall()

        if not rows:
            await interaction.followup.send(f"📊 **VATJPN 管制統計 ({period_label})**\n\nデータがありません。")
            return

        total_sessions = len(rows)
        total_duration = sum(r[2] for r in rows)

        pos_stats = {}
        for _, callsign, duration in rows:
            if callsign not in pos_stats:
                pos_stats[callsign] = {"duration": 0, "count": 0}
            pos_stats[callsign]["duration"] += duration
            pos_stats[callsign]["count"] += 1
        pos_ranking = sorted(pos_stats.items(), key=lambda x: x[1]["duration"], reverse=True)[:10]

        ctrl_stats = {}
        for cid, _, duration in rows:
            if cid not in ctrl_stats:
                ctrl_stats[cid] = {"duration": 0, "count": 0}
            ctrl_stats[cid]["duration"] += duration
            ctrl_stats[cid]["count"] += 1
        ctrl_ranking = sorted(ctrl_stats.items(), key=lambda x: x[1]["duration"], reverse=True)[:10]

        rank_medal = ["🥇", "🥈", "🥉"]
        pos_lines = []
        for i, (callsign, data) in enumerate(pos_ranking):
            prefix = rank_medal[i] if i < 3 else f"`{i+1}.`"
            pos_lines.append(f"{prefix} `{callsign}` - {format_duration_seconds(data['duration'])} ({data['count']}回)")

        ctrl_lines = []
        for i, (cid, data) in enumerate(ctrl_ranking):
            prefix = rank_medal[i] if i < 3 else f"`{i+1}.`"
            name = get_display_name(cid)
            ctrl_lines.append(f"{prefix} {name} - {format_duration_seconds(data['duration'])} ({data['count']}回)")

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
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

# ── MyStats commands ──────────────────────────────────────────────

mystats_group = app_commands.Group(name="mystats", description="管制官個人統計")

@mystats_group.command(name="link", description="Discord IDとVATSIM CIDを紐付け")
@app_commands.describe(cid="VATSIM CID")
async def mystats_link(interaction: discord.Interaction, cid: int):
    if cid <= 0:
        await interaction.response.send_message("無効なCIDです。正の整数を指定してください。")
        return
    link_user(interaction.user.id, cid)
    await interaction.response.send_message(f"CID **{cid}** と紐付けました。`/mystats show` で統計を確認できます。")

@mystats_group.command(name="unlink", description="CIDの紐付けを解除")
async def mystats_unlink(interaction: discord.Interaction):
    if unlink_user(interaction.user.id):
        await interaction.response.send_message("CIDの紐付けを解除しました。")
    else:
        await interaction.response.send_message("紐付けされていません。")

@mystats_group.command(name="show", description="自分の管制統計を表示")
async def mystats_show(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        cid = get_linked_cid(interaction.user.id)
        if cid is None:
            await interaction.followup.send("CIDが紐付けられていません。`/mystats link <CID>` で紐付けてください。")
            return

        stats = get_controller_stats(cid)
        if stats is None:
            await interaction.followup.send(f"CID **{cid}** の管制セッションデータがありません。")
            return

        vatsim_info = await fetch_vatsim_member(bot.http_session, cid)
        embed = build_stats_embed(cid, stats, vatsim_info)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

@mystats_group.command(name="user", description="指定CIDの管制統計を表示")
@app_commands.describe(cid="VATSIM CID")
async def mystats_user(interaction: discord.Interaction, cid: int):
    await interaction.response.defer()
    try:
        stats = get_controller_stats(cid)
        if stats is None:
            await interaction.followup.send(f"CID **{cid}** の管制セッションデータがありません。")
            return

        vatsim_info = await fetch_vatsim_member(bot.http_session, cid)
        embed = build_stats_embed(cid, stats, vatsim_info)
        await interaction.followup.send(embed=embed)
    except Exception as e:
        logger.exception("エラーが発生しました")
        await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

bot.tree.add_command(mystats_group)

# ── Events ─────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    mode = "notifications + commands" if enable_notifications else "commands only"
    logger.info('Logged in as %s (mode: %s)', bot.user, mode)
    for attempt in range(3):
        try:
            for guild in bot.guilds:
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                logger.info('Synced %d slash command(s) to %s', len(synced), guild.name)
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            logger.info('Cleared global commands')
            break
        except Exception as e:
            wait = 2 ** (attempt + 1)
            logger.warning('Failed to sync slash commands (attempt %d/3): %s', attempt + 1, e)
            if attempt < 2:
                await asyncio.sleep(wait)

bot.run(discord_bot_client_token)
