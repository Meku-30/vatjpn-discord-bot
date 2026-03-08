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

# ── NOTAM helper ──────────────────────────────────────────────────

NOTAM_PER_PAGE = 5

async def fetch_notams(http_session, icao):
    """SWIM非公式APIから有効なNOTAMを取得。Returns (notams_list, total_count, error_msg)."""
    data, err = await _swim_request(http_session, "/api/notams/active", "NOTAM", params={"icao": icao.upper()})
    if err:
        return [], 0, err
    return data or [], len(data or []), None

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
    return await _swim_request(http_session, f"/api/atis/{icao.upper()}", "ATIS")

async def fetch_all_atis(http_session):
    """SWIM非公式APIから全空港ATISを一括取得。Returns (atis_list, error_msg)."""
    data, err = await _swim_request(http_session, "/api/atis", "ATIS")
    if err:
        return [], err
    return data or [], None

# ── METAR helper ──────────────────────────────────────────────────

async def fetch_metar(http_session, icao):
    """SWIM非公式APIから最新METARを取得。Returns (metar_dict, error_msg)."""
    data, err = await _swim_request(http_session, f"/api/weather/{icao.upper()}", "METAR")
    if err:
        return None, err
    metar = next((w for w in (data or []) if w.get("type") == "METAR"), None)
    return metar, None

# ── RWY-INFO helper ──────────────────────────────────────────────

async def fetch_runway_info(http_session, icao):
    """SWIM非公式APIから最新RWY-INFOを取得。Returns (rwy_dict, error_msg)."""
    return await _swim_request(http_session, f"/api/runway-info/{icao.upper()}", "RWY-INFO")

async def fetch_all_runway_info(http_session):
    """SWIM非公式APIから全空港のRWY-INFOを一括取得。Returns (rwy_list, error_msg)."""
    data, err = await _swim_request(http_session, "/api/runway-info", "RWY-INFO")
    if err:
        return [], err
    # フィールド名を正規化（bulk APIはicao_codeを使用）
    for rwy in (data or []):
        if "icao_code" in rwy and "icao" not in rwy:
            rwy["icao"] = rwy["icao_code"]
    return data or [], None

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
    data, err = await _swim_request(http_session, "/api/pireps/active", "PIREP")
    if err:
        return [], err
    return data or [], None

JAPAN_TRANSITION_FL = 140  # FL140 = 14,000ft

def _fl_to_display(fl_val):
    """FL値(百ft単位)を日本の遷移高度に基づきFL/ftで表示する。"""
    if fl_val >= JAPAN_TRANSITION_FL:
        return f"FL{fl_val:03d}"
    feet = fl_val * 100
    return f"{feet:,}ft"

def format_pirep_altitude(pirep):
    """PIREPの高度を表示用にフォーマットする。
    bodyテキストから高度レンジを抽出し、日本の遷移高度(14,000ft/FL140)に基づきFL/ftを使い分ける。
    """
    body = pirep.get("body", "")

    # bodyテキストから高度レンジを抽出 (例: /F000-050, /F350)
    m = re.search(r'/F(\d{3})(?:-(\d{3}))?(?=[\s/]|$)', body)
    if m:
        low = int(m.group(1))
        high = int(m.group(2)) if m.group(2) else None
        low_str = _fl_to_display(low)
        if high is not None:
            high_str = _fl_to_display(high)
            return f"{low_str} - {high_str}"
        return low_str

    # フォールバック: APIのaltitudeフィールド
    alt = pirep.get("altitude")
    if not alt:
        return "不明"
    indicator = pirep.get("altitude_indicator", "")
    if indicator == "F":
        try:
            fl_val = int(alt)
            return _fl_to_display(fl_val)
        except ValueError:
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

def parse_pirep_coords(pirep):
    """PIREPの緯度経度を十進度に変換する。変換できない場合はNone。"""
    lat_raw, lon_raw = pirep.get("latitude"), pirep.get("longitude")
    if not lat_raw or not lon_raw:
        return None
    try:
        lat = int(lat_raw[:2]) + int(lat_raw[2:]) / 60
        lon = int(lon_raw[:3]) + int(lon_raw[3:]) / 60
        return lat, lon
    except (ValueError, IndexError):
        return None

# 日本の陸地の大まかな範囲
JAPAN_BBOX = {"lat_min": 24, "lat_max": 46, "lon_min": 122, "lon_max": 146}
JAPAN_REF = (138, 36)  # 本州中部

def generate_pirep_map(lat, lon):
    """PIREP位置のスタティックマップを生成し、discord.Fileとして返す。"""
    m = StaticMap(400, 300, url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    m.add_marker(CircleMarker((lon, lat), "red", 12))

    near_japan = (JAPAN_BBOX["lat_min"] <= lat <= JAPAN_BBOX["lat_max"]
                  and JAPAN_BBOX["lon_min"] <= lon <= JAPAN_BBOX["lon_max"])
    if near_japan:
        image = m.render(zoom=7)
    else:
        m.add_marker(CircleMarker(JAPAN_REF, "#00000001", 1))
        image = m.render()

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="pirep_map.png")

async def build_pirep_embed(pirep):
    """MOD以上のPIREP用Embedとマップファイルを作成する。(embed, file_or_none)を返す。"""
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

    # マップ画像生成
    map_file = None
    coords = parse_pirep_coords(pirep)
    if coords:
        try:
            map_file = await asyncio.to_thread(generate_pirep_map, *coords)
            embed.set_image(url="attachment://pirep_map.png")
        except Exception:
            logger.warning("PIREPマップ生成失敗", exc_info=True)

    observed = pirep.get("observed_at", "")
    effective_end = pirep.get("effective_end", "")
    time_str = ""
    if observed:
        time_str += f"観測: {observed[0:10]} {observed[11:16]}Z"
    if effective_end:
        time_str += f"  有効: ~{effective_end[0:10]} {effective_end[11:16]}Z"
    if time_str:
        embed.set_footer(text=time_str)

    return embed, map_file


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
