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
from vatsim_stat_notify_to_discord import get_db, pirep_channel_id, swim_api_url, swim_api_token

logger = logging.getLogger("vatjpn-bot")

# 環境変数（SWIM API設定はメインから参照、通知フラグはCog固有）
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

def apch_add_watch(guild_id, icao, baseline, time_start, time_end, registered_by, rwy=None):
    conn = get_db()
    with conn:
        # 同じguild+icao+baseline+時間帯+rwyの既存レコードを削除してから挿入（重複防止、異なるbaselineは共存）
        conn.execute(
            "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ? AND baseline = ? AND time_start IS ? AND time_end IS ? AND rwy IS ?",
            (str(guild_id), icao.upper(), baseline, time_start, time_end, rwy))
        conn.execute(
            "INSERT INTO apch_watches (guild_id, icao, baseline, time_start, time_end, registered_by, rwy) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(guild_id), icao.upper(), baseline, time_start, time_end, str(registered_by), rwy))

def apch_remove_watch(guild_id, icao, baseline=None, time_start=None, time_end=None, rwy=None):
    """登録を削除。baseline/time/rwy指定で絞り込み可。削除件数を返す。"""
    conn = get_db()
    with conn:
        if baseline is not None:
            c = conn.execute(
                "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ? AND baseline = ? AND time_start IS ? AND time_end IS ? AND rwy IS ?",
                (str(guild_id), icao.upper(), baseline, time_start, time_end, rwy))
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
            "SELECT icao, baseline, time_start, time_end, rwy FROM apch_watches WHERE guild_id = ? ORDER BY icao, time_start",
            (str(guild_id),)).fetchall()
    return rows

def apch_get_all_watches():
    """全ギルドの監視設定をchannel_id付きで取得（ポーリング用）。"""
    conn = get_db()
    with conn:
        rows = conn.execute(
            "SELECT w.guild_id, w.icao, w.baseline, w.time_start, w.time_end, c.channel_id, w.rwy "
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

    # ── SWIM コマンド ──

    @app_commands.command(name="notam", description="空港のNOTAMを表示")
    @app_commands.describe(
        icao="空港のICAOコード（例: RJTT）または 'japan' で主要空港一括表示",
        keyword="キーワードでNOTAMを絞り込み（例: RWY, ILS, TWY）",
    )
    async def notam_command(self, interaction: discord.Interaction, icao: str, keyword: str = None):
        await interaction.response.defer()
        try:
            icao_input = icao.strip().upper()

            if icao_input == "JAPAN":
                # 主要空港一括サマリー
                tasks_list = [fetch_notams(self.bot.http_session, code) for code in JAPAN_MAJOR_AIRPORTS]
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
            notams, total_count, error = await fetch_notams(self.bot.http_session, icao_input)
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
            logger.exception("/notamコマンドエラー")
            await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

    @app_commands.command(name="atis", description="空港のATIS情報を表示")
    @app_commands.describe(icao="空港のICAOコード（例: RJTT）または 'japan' で主要空港一括表示")
    async def atis_command(self, interaction: discord.Interaction, icao: str):
        await interaction.response.defer()
        try:
            icao_input = icao.strip().upper()

            if icao_input == "JAPAN":
                atis_list, error = await fetch_all_atis(self.bot.http_session)
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

            atis, error = await fetch_atis(self.bot.http_session, icao_input)
            if error:
                await interaction.followup.send(error)
                return
            if not atis:
                await interaction.followup.send(f"**{icao_input}** のATISデータがありません。RWY-INFOは `/rwy {icao_input}` で確認できます。")
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
            logger.exception("/atisコマンドエラー")
            await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

    @app_commands.command(name="metar", description="空港のMETAR情報を表示")
    @app_commands.describe(icao="空港のICAOコード（例: RJTT）")
    async def metar_command(self, interaction: discord.Interaction, icao: str):
        await interaction.response.defer()
        try:
            icao_input = icao.strip().upper()
            metar, error = await fetch_metar(self.bot.http_session, icao_input)
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
            logger.exception("/metarコマンドエラー")
            await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

    @app_commands.command(name="rwy", description="空港のRWY-INFO（APCH TYPE・DEP/LDG RWY）とMETARを表示")
    @app_commands.describe(icao="空港のICAOコード（例: RJTT）")
    async def rwy_command(self, interaction: discord.Interaction, icao: str):
        await interaction.response.defer()
        try:
            icao_input = icao.strip().upper()
            metar, rwy = await asyncio.gather(
                fetch_metar(self.bot.http_session, icao_input),
                fetch_runway_info(self.bot.http_session, icao_input),
            )
            metar_data, metar_err = metar
            rwy_data, rwy_err = rwy

            parts = []
            if metar_data:
                parts.append(f"**METAR:**\n{metar_data.get('raw_text', '')}")
            if rwy_data:
                approach_types = self._get_approach_types(rwy_data)
                dep_rwy = rwy_data.get("dep_rwy")
                ldg_rwy = rwy_data.get("ldg_rwy")
                rwy_in_use = rwy_data.get("runway_in_use")
                if approach_types:
                    parts.append(f"**APCH TYPE:** {approach_types[0]}" if len(approach_types) == 1
                                 else "**APCH TYPE:**\n" + "\n".join(approach_types))
                if dep_rwy:
                    parts.append(f"**DEP RWY:** {dep_rwy}")
                if ldg_rwy:
                    parts.append(f"**LDG RWY:** {ldg_rwy}")
                if rwy_in_use:
                    parts.append(f"**USING RWY:** {rwy_in_use}")

            if not parts:
                await interaction.followup.send(f"**{icao_input}** のRWY-INFO・METARデータがありません。")
                return

            description = "\n\n".join(parts)
            if len(description) > 4096:
                description = description[:4093] + "..."
            embed = discord.Embed(
                title=f"{icao_input} RWY-INFO",
                color=0x00bfff,
                description=description,
            )
            await interaction.followup.send(embed=embed)
        except Exception:
            logger.exception("/rwyコマンドエラー")
            await interaction.followup.send("エラーが発生しました。しばらくしてから再度お試しください。")

    # ── APCH コマンドグループ ──

    apch_group = app_commands.Group(name="apch", description="APCH TYPE変更監視", guild_only=True)

    @apch_group.command(name="setchannel", description="APCH TYPE変更通知の送信先チャンネルを設定")
    @app_commands.describe(channel="通知先チャンネル")
    async def apch_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        apch_set_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"APCH TYPE変更通知の送信先を {channel.mention} に設定しました。")

    @apch_group.command(name="watch", description="全空港のAPCH TYPE変化を監視開始")
    async def apch_watch(self, interaction: discord.Interaction):
        if not apch_get_channel(interaction.guild_id):
            await interaction.response.send_message(
                "先に `/apch setchannel` で通知先チャンネルを設定してください。", ephemeral=True)
            return
        # 既にグローバルwatchが登録されているかチェック
        existing = [r for r in apch_list_watches(interaction.guild_id) if r[0] == "*"]
        if existing:
            await interaction.response.send_message("全空港監視は既に有効です。", ephemeral=True)
            return
        apch_add_watch(interaction.guild_id, "*", "*", None, None, interaction.user.id)
        await interaction.response.send_message(
            "全空港のAPCH TYPE変化監視を開始しました。変更があるたびに通知します。\n"
            "個別の基準を登録するには `/apch set <ICAO> <baseline>` を使用してください。")

    @apch_group.command(name="unwatch", description="全空港のAPCH TYPE変化監視を停止")
    async def apch_unwatch(self, interaction: discord.Interaction):
        count = apch_remove_watch(interaction.guild_id, "*")
        if count > 0:
            await interaction.response.send_message("全空港のAPCH TYPE変化監視を停止しました。")
        else:
            await interaction.response.send_message("全空港監視は設定されていません。", ephemeral=True)

    @apch_group.command(name="set", description="空港の基準APCH TYPEを登録")
    @app_commands.describe(
        icao="空港のICAOコード（例: RJTT）",
        baseline="基準APCH TYPE（例: ILS, ILS Y RWY34L）",
        time_range="適用時間帯 HH:MM-HH:MM UTC（省略時は全時間帯）",
        rwy="使用滑走路条件（例: 07, 34R）— VISUAL等の滑走路名なしAPCH TYPEで滑走路を区別する場合に指定",
    )
    async def apch_set(self, interaction: discord.Interaction, icao: str, baseline: str, time_range: str = None, rwy: str = None):
        if not re.match(r'^[A-Z]{4}$', icao.upper()):
            await interaction.response.send_message(
                "ICAOコードは4文字の英字で指定してください（例: RJTT）。", ephemeral=True)
            return
        if not apch_get_channel(interaction.guild_id):
            await interaction.response.send_message(
                "先に `/apch setchannel` で通知先チャンネルを設定してください。", ephemeral=True)
            return
        time_start, time_end = None, None
        if time_range:
            parsed = parse_time_range(time_range)
            if not parsed:
                await interaction.response.send_message(
                    "時間帯の形式が不正です。`HH:MM-HH:MM`（UTC）で指定してください。例: `22:00-06:00`", ephemeral=True)
                return
            time_start, time_end = parsed
        rwy_normalized = rwy.strip().upper() if rwy else None
        apch_add_watch(interaction.guild_id, icao, baseline, time_start, time_end, interaction.user.id, rwy=rwy_normalized)
        time_desc = f" ({time_start}-{time_end} UTC)" if time_start else " (全時間帯)"
        rwy_desc = f" (RWY {rwy_normalized})" if rwy_normalized else ""
        await interaction.response.send_message(
            f"**{icao.upper()}** の基準APCH TYPEを「{baseline}」に設定しました。{rwy_desc}{time_desc}")

    @apch_group.command(name="remove", description="空港のAPCH TYPE監視登録を削除")
    @app_commands.describe(
        icao="空港のICAOコード（例: RJTT）",
        baseline="削除する基準APCH TYPE（省略時はその空港の全登録を削除）",
        time_range="削除する時間帯 HH:MM-HH:MM UTC（省略時は全時間帯）",
        rwy="削除する使用滑走路条件（例: 07, 34R）",
    )
    async def apch_remove(self, interaction: discord.Interaction, icao: str, baseline: str = None, time_range: str = None, rwy: str = None):
        if not re.match(r'^[A-Z]{4}$', icao.upper()):
            await interaction.response.send_message(
                "ICAOコードは4文字の英字で指定してください（例: RJTT）。", ephemeral=True)
            return
        time_start, time_end = None, None
        if time_range:
            parsed = parse_time_range(time_range)
            if not parsed:
                await interaction.response.send_message(
                    "時間帯の形式が不正です。`HH:MM-HH:MM`（UTC）で指定してください。", ephemeral=True)
                return
            time_start, time_end = parsed
        rwy_normalized = rwy.strip().upper() if rwy else None
        count = apch_remove_watch(interaction.guild_id, icao, baseline=baseline, time_start=time_start, time_end=time_end, rwy=rwy_normalized)
        if count > 0:
            await interaction.response.send_message(f"**{icao.upper()}** の監視登録を{count}件削除しました。")
        else:
            await interaction.response.send_message(f"**{icao.upper()}** の該当する監視登録が見つかりません。")

    @apch_group.command(name="list", description="APCH TYPE監視の登録一覧を表示")
    async def apch_list(self, interaction: discord.Interaction):
        watches = apch_list_watches(interaction.guild_id)
        ch_id = apch_get_channel(interaction.guild_id)
        if not watches and not ch_id:
            await interaction.response.send_message("APCH TYPE監視は設定されていません。")
            return
        lines = []
        has_global = False
        # 空港別にグルーピングして表示
        airport_baselines = {}
        for icao, baseline, ts, te, rwy in watches:
            if icao == "*":
                has_global = True
                continue
            airport_baselines.setdefault(icao, []).append((baseline, ts, te, rwy))
        if has_global:
            lines.append("🌐 **全空港変化監視: ON**")
        for icao in sorted(airport_baselines.keys()):
            bl_parts = []
            for baseline, ts, te, rwy in airport_baselines[icao]:
                time_desc = f" ({ts}-{te} UTC)" if ts else ""
                rwy_desc = f" (RWY {rwy})" if rwy else ""
                bl_parts.append(f"\"{baseline}\"{rwy_desc}{time_desc}")
            lines.append(f"**{icao}**: {' / '.join(bl_parts)}")
        if ch_id:
            lines.append(f"\n通知先: <#{ch_id}>")
        embed = discord.Embed(
            title="APCH TYPE 監視一覧",
            color=0xFF9900,
            description="\n".join(lines) if lines else "登録なし"
        )
        await interaction.response.send_message(embed=embed)

    # ── ループ ──

    @tasks.loop(seconds=300)
    async def pirep_loop(self):
        try:
            pireps, err = await fetch_active_pireps(self.bot.http_session)
            if err:
                logger.warning("PIREP取得エラー: %s", err)
                return
            channel = self.bot.get_channel(pirep_channel_id)
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
                embed, map_file = await build_pirep_embed(p)
                await channel.send(embed=embed, file=map_file) if map_file else await channel.send(embed=embed)

        except Exception:
            logger.exception("PIREPポーリングエラー")

    @pirep_loop.before_loop
    async def before_pirep_loop(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=300)
    async def apch_loop(self):
        try:
            all_watches = apch_get_all_watches()
            if not all_watches:
                return

            # グローバルwatch (icao="*") の有無と、空港別baseline登録を分類
            guilds_with_global = set()
            guild_watches = {}  # guild_id → {icao: [(baseline, ts, te, rwy), ...]}
            guild_channels = {}  # guild_id → channel_id
            for guild_id, icao, baseline, ts, te, channel_id, rwy in all_watches:
                if channel_id:
                    guild_channels[guild_id] = int(channel_id)
                if icao == "*":
                    guilds_with_global.add(guild_id)
                else:
                    guild_watches.setdefault(guild_id, {}).setdefault(icao, []).append((baseline, ts, te, rwy))

            # キャッシュクリーンアップ
            active_specific_keys = set()
            for gid, airports in guild_watches.items():
                for icao in airports:
                    active_specific_keys.add((gid, icao))
            self.apch_last_notified = {
                k: v for k, v in self.apch_last_notified.items()
                if k in active_specific_keys or k[0] in guilds_with_global
            }

            # RWY-INFO取得: グローバルwatchがあれば一括取得、なければ個別取得
            specific_icaos = set()
            for airports in guild_watches.values():
                specific_icaos.update(airports.keys())

            rwy_cache = {}
            if guilds_with_global:
                rwy_list, err = await fetch_all_runway_info(self.bot.http_session)
                if err:
                    logger.warning("RWY-INFO一括取得エラー: %s", err)
                    return
                for rwy in rwy_list:
                    icao = rwy.get("icao", "")
                    if icao:
                        rwy_cache[icao] = rwy
            else:
                for icao in specific_icaos:
                    rwy_data, err = await fetch_runway_info(self.bot.http_session, icao)
                    if err:
                        logger.warning("RWY-INFO取得エラー (%s): %s", icao, err)
                        continue
                    if rwy_data:
                        rwy_cache[icao] = rwy_data

            if self._apch_first_run:
                self._apch_first_run = False
                for icao, rwy_data in rwy_cache.items():
                    approach_types = self._get_approach_types(rwy_data)
                    if not approach_types:
                        continue
                    rwy_in_use = rwy_data.get("runway_in_use", "")
                    cache_key_val = (tuple(approach_types), rwy_in_use)
                    for gid in guilds_with_global:
                        self.apch_last_notified[(gid, icao)] = cache_key_val
                    for gid, airports in guild_watches.items():
                        if icao in airports:
                            self.apch_last_notified[(gid, icao)] = cache_key_val
                logger.info("APCH TYPE監視開始（%d空港）", len(rwy_cache))
                return

            # 通常ポーリング: 各空港 × 各ギルド を処理
            all_guild_ids = set(guild_watches.keys()) | guilds_with_global
            for icao, rwy_data in rwy_cache.items():
                approach_types = self._get_approach_types(rwy_data)
                if not approach_types:
                    continue
                rwy_in_use = rwy_data.get("runway_in_use", "")
                cache_key_val = (tuple(approach_types), rwy_in_use)
                observed = rwy_data.get("observed_at", "")

                for guild_id in all_guild_ids:
                    has_specific = guild_id in guild_watches and icao in guild_watches[guild_id]
                    has_global = guild_id in guilds_with_global
                    if not has_specific and not has_global:
                        continue

                    key = (guild_id, icao)

                    if has_specific:
                        # 基準登録あり: 適用時間帯のbaselineをOR判定
                        applicable = []
                        for bl, ts, te, bl_rwy in guild_watches[guild_id][icao]:
                            if ts and te and not is_in_time_range(ts, te):
                                continue
                            applicable.append((bl, ts, te, bl_rwy))

                        if applicable:
                            if any(
                                self._baseline_matches_approaches(bl, approach_types, rwy=bl_rwy, runway_in_use=rwy_in_use)
                                for bl, _, _, bl_rwy in applicable
                            ):
                                self.apch_last_notified.pop(key, None)
                                continue
                            # どのbaselineにも一致しない → 通知
                            if self.apch_last_notified.get(key) == cache_key_val:
                                continue
                            self.apch_last_notified[key] = cache_key_val

                            ch_id = guild_channels.get(guild_id)
                            if not ch_id:
                                continue
                            channel = self.bot.get_channel(ch_id)
                            if not channel:
                                continue

                            bl_strs = []
                            for bl, ts, te, bl_rwy in applicable:
                                td = f" ({ts}-{te} UTC)" if ts else ""
                                rd = f" (RWY {bl_rwy})" if bl_rwy else ""
                                bl_strs.append(f"{bl}{rd}{td}")
                            embed = discord.Embed(
                                title=f"⚠️ APCH TYPE 変更 — {icao}",
                                color=0xFF9900,
                            )
                            apch_display = "\n".join(approach_types)
                            if rwy_in_use:
                                apch_display += f"\nRWY: {rwy_in_use}"
                            embed.add_field(name="現在", value=apch_display, inline=True)
                            embed.add_field(name="基準", value=" / ".join(bl_strs), inline=True)
                            if observed:
                                embed.set_footer(text=f"観測: {observed[:10]} {observed[11:16]}Z")
                            await channel.send(embed=embed)
                            continue  # baseline処理済み
                        # 全baselineが時間帯外 → グローバルwatchにフォールバック
                        if not has_global:
                            continue
                    if has_global:
                        # グローバルwatch: 変化検知のみ
                        if self.apch_last_notified.get(key) == cache_key_val:
                            continue
                        prev = self.apch_last_notified.get(key)
                        self.apch_last_notified[key] = cache_key_val
                        if not prev:
                            continue  # 初回観測はキャッシュのみ

                        ch_id = guild_channels.get(guild_id)
                        if not ch_id:
                            continue
                        channel = self.bot.get_channel(ch_id)
                        if not channel:
                            continue

                        embed = discord.Embed(
                            title=f"APCH TYPE 更新 — {icao}",
                            color=0x3498DB,
                        )
                        apch_display = "\n".join(approach_types)
                        if rwy_in_use:
                            apch_display += f"\nRWY: {rwy_in_use}"
                        embed.add_field(name="現在", value=apch_display, inline=True)
                        if prev:
                            prev_apch, prev_rwy = prev if isinstance(prev, tuple) and len(prev) == 2 and isinstance(prev[0], tuple) else (prev, "")
                            prev_display = "\n".join(prev_apch) if isinstance(prev_apch, tuple) else str(prev_apch)
                            if prev_rwy:
                                prev_display += f"\nRWY: {prev_rwy}"
                            embed.add_field(name="前回", value=prev_display, inline=True)
                        if observed:
                            embed.set_footer(text=f"観測: {observed[:10]} {observed[11:16]}Z")
                        await channel.send(embed=embed)

        except Exception:
            logger.exception("APCHポーリングエラー")

    @staticmethod
    def _get_approach_types(rwy):
        """RWY-INFOからapproach_typesを取得する。"""
        return rwy.get("approach_types") or []

    @staticmethod
    def _apch_matches_baseline(approach_type, baseline):
        """approach_typeがbaselineに合致するかを完全一致で判定する。'*'は全変化監視（常に不一致）。"""
        if baseline == "*":
            return False
        return baseline.upper() == approach_type.upper()

    @staticmethod
    def _parse_runway_in_use(runway_in_use):
        """runway_in_useフィールドを個別滑走路のリストに分解する。
        例: 'RWY 16L/16R' → ['16L', '16R'], 'RWY 07' → ['07']
        """
        if not runway_in_use:
            return []
        stripped = re.sub(r'^RWY\s*', '', runway_in_use.strip(), flags=re.IGNORECASE)
        return [r.strip().upper() for r in stripped.split("/") if r.strip()]

    @staticmethod
    def _baseline_matches_approaches(baseline, approach_types, rwy=None, runway_in_use=None):
        """baselineがapproach_typesリスト全体に合致するか判定する。
        '+' 区切りのセット条件: 全サブ条件がそれぞれapproach_types内のいずれかに完全一致すればTrue。
        単一条件: approach_types内のいずれかに完全一致すればTrue。
        '*'は常にFalse（全変化監視）。
        rwy指定時: approach_types一致に加え、runway_in_use内の個別滑走路と完全一致も必要。
        """
        if baseline == "*":
            return False
        if "+" in baseline:
            sub_baselines = [s.strip() for s in baseline.split("+")]
            apch_match = all(
                any(sb.upper() == a.upper() for a in approach_types)
                for sb in sub_baselines
            )
        else:
            apch_match = any(baseline.upper() == a.upper() for a in approach_types)
        if not apch_match:
            return False
        if rwy:
            runways = SwimCog._parse_runway_in_use(runway_in_use)
            return rwy.upper() in runways
        return True

    @apch_loop.before_loop
    async def before_apch_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)  # PIREP loopとのAPI同時アクセスを避ける


async def setup(bot):
    """discord.py の拡張機能ロード用エントリーポイント。"""
    await bot.add_cog(SwimCog(bot))
