import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiohttp
import asyncio
import re
import os
import logging
import io
from datetime import datetime, timezone
from staticmap import StaticMap, CircleMarker
from vatsim_stat_notify_to_discord import get_db

logger = logging.getLogger("vatjpn-bot")

# 環境変数（メインから参照）
swim_api_url = os.environ.get("SWIM_API_URL")
swim_api_token = os.environ.get("SWIM_API_TOKEN")
enable_pirep_notifications = os.environ.get("ENABLE_PIREP_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
enable_apch_notifications = os.environ.get("ENABLE_APCH_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")

# ── SWIM API common helper ────────────────────────────────────────

_swim_headers = None

def _get_swim_headers():
    global _swim_headers
    if _swim_headers is None and swim_api_token:
        _swim_headers = {"Authorization": f"Bearer {swim_api_token}"}
    return _swim_headers

async def _swim_request(http_session, path, label="SWIM", params=None, retries=1):
    """SWIM APIへの共通リクエスト。5xx/タイムアウト時にリトライ。Returns (json_data, error_msg)."""
    if not swim_api_url or not swim_api_token:
        return None, f"{label}機能を使用するにはSWIM_API_URL/SWIM_API_TOKEN環境変数の設定が必要です。"
    url = f"{swim_api_url}{path}"
    last_err = None
    for attempt in range(1 + retries):
        try:
            async with http_session.get(url, headers=_get_swim_headers(), params=params) as resp:
                if resp.status in (401, 403):
                    return None, "SWIM APIの認証に失敗しました。トークンを確認してください。"
                if resp.status >= 500 and attempt < retries:
                    last_err = f"SWIM APIエラー (HTTP {resp.status})"
                    await asyncio.sleep(2)
                    continue
                if resp.status != 200:
                    return None, f"SWIM APIエラー (HTTP {resp.status})"
                return await resp.json(), None
        except asyncio.TimeoutError:
            last_err = f"{label}情報の取得がタイムアウトしました。"
            if attempt < retries:
                await asyncio.sleep(2)
                continue
        except Exception:
            logger.exception("SWIM APIリクエストエラー (%s)", label)
            return None, f"{label}情報の取得に失敗しました。"
    return None, last_err

# ── APCH TYPE monitoring helpers ──────────────────────────────────

def apch_set_channel(guild_id, channel_id):
    conn = get_db()
    with conn:
        conn.execute("INSERT OR REPLACE INTO apch_config (guild_id, channel_id) VALUES (?, ?)",
                     (str(guild_id), str(channel_id)))

def apch_get_channel(guild_id):
    conn = get_db()
    with conn:
        row = conn.execute("SELECT channel_id FROM apch_config WHERE guild_id = ?",
                          (str(guild_id),)).fetchone()
    return int(row[0]) if row else None

def apch_add_watch(guild_id, icao, baseline, time_start, time_end, registered_by):
    conn = get_db()
    with conn:
        # 同じguild+icao+baseline+時間帯の既存レコードを削除してから挿入（重複防止、異なるbaselineは共存）
        conn.execute(
            "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ? AND baseline = ? AND time_start IS ? AND time_end IS ?",
            (str(guild_id), icao.upper(), baseline, time_start, time_end))
        conn.execute(
            "INSERT INTO apch_watches (guild_id, icao, baseline, time_start, time_end, registered_by) VALUES (?, ?, ?, ?, ?, ?)",
            (str(guild_id), icao.upper(), baseline, time_start, time_end, str(registered_by)))

def apch_remove_watch(guild_id, icao, baseline=None, time_start=None, time_end=None):
    """登録を削除。baseline/time指定で絞り込み可。削除件数を返す。"""
    conn = get_db()
    with conn:
        if baseline is not None:
            c = conn.execute(
                "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ? AND baseline = ? AND time_start IS ? AND time_end IS ?",
                (str(guild_id), icao.upper(), baseline, time_start, time_end))
        elif time_start is not None and time_end is not None:
            c = conn.execute(
                "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ? AND time_start IS ? AND time_end IS ?",
                (str(guild_id), icao.upper(), time_start, time_end))
        else:
            c = conn.execute(
                "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ?",
                (str(guild_id), icao.upper()))
        return c.rowcount

def apch_list_watches(guild_id):
    conn = get_db()
    with conn:
        rows = conn.execute(
            "SELECT icao, baseline, time_start, time_end FROM apch_watches WHERE guild_id = ? ORDER BY icao, time_start",
            (str(guild_id),)).fetchall()
    return rows

def apch_get_all_watches():
    """全ギルドの監視設定をchannel_id付きで取得（ポーリング用）。"""
    conn = get_db()
    with conn:
        rows = conn.execute(
            "SELECT w.guild_id, w.icao, w.baseline, w.time_start, w.time_end, c.channel_id "
            "FROM apch_watches w LEFT JOIN apch_config c ON w.guild_id = c.guild_id "
            "ORDER BY w.guild_id, w.icao"
        ).fetchall()
    return rows

def parse_time_range(time_range_str):
    """'HH:MM-HH:MM' をパースして (start, end) を返す。不正な場合は None。"""
    m = re.match(r'^(\d{2}:\d{2})-(\d{2}:\d{2})$', time_range_str)
    if not m:
        return None
    for part in (m.group(1), m.group(2)):
        h, mi = map(int, part.split(":"))
        if h > 23 or mi > 59:
            return None
    return m.group(1), m.group(2)

def is_in_time_range(time_start, time_end):
    """現在UTC時刻が time_start〜time_end の範囲内かを判定する。日跨ぎ対応。"""
    now = datetime.now(timezone.utc)
    now_minutes = now.hour * 60 + now.minute
    sh, sm = map(int, time_start.split(":"))
    eh, em = map(int, time_end.split(":"))
    start_minutes = sh * 60 + sm
    end_minutes = eh * 60 + em
    if start_minutes <= end_minutes:
        return start_minutes <= now_minutes < end_minutes
    else:
        # 日跨ぎ: 22:00-06:00 → 22:00<=now OR now<06:00
        return now_minutes >= start_minutes or now_minutes < end_minutes


class SwimCog(commands.Cog):
    """SWIM非公式API連携機能（ATIS, METAR, NOTAM, PIREP, APCH）"""

    def __init__(self, bot):
        self.bot = bot
        self.pirep_notified = set()
        self._pirep_first_run = True
        self.apch_last_notified = {}
        self._apch_first_run = True

    async def cog_load(self):
        """Cogロード時にループを起動。"""
        if enable_pirep_notifications:
            self.pirep_loop.start()
        if enable_apch_notifications:
            self.apch_loop.start()

    async def cog_unload(self):
        """Cogアンロード時にループを停止。"""
        if self.pirep_loop.is_running():
            self.pirep_loop.cancel()
        if self.apch_loop.is_running():
            self.apch_loop.cancel()

    # ── ループの仮定義（後のタスクで中身を移動） ──

    @tasks.loop(seconds=300)
    async def pirep_loop(self):
        pass

    @pirep_loop.before_loop
    async def before_pirep_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=300)
    async def apch_loop(self):
        pass

    @apch_loop.before_loop
    async def before_apch_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)


async def setup(bot):
    """discord.py の拡張機能ロード用エントリーポイント。"""
    await bot.add_cog(SwimCog(bot))
