# APCH TYPE 変更監視・通知機能 実装計画

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** RWY-INFOのAPCH TYPEが基準と異なる場合にDiscordチャンネルへ自動通知する機能を追加する

**Architecture:** 既存の `vatsim_stat_notify_to_discord.py` に、SQLiteテーブル2つ（`apch_config`, `apch_watches`）、`/apch` コマンドグループ（set/remove/list/setchannel）、5分間隔ポーリングループ（`apch_loop`）を追加。PIREPループと同じパターンを踏襲する。

**Tech Stack:** Python, discord.py (app_commands), SQLite, aiohttp (SWIM API)

**Design Doc:** `docs/plans/2026-03-08-apch-type-monitor-design.md`

---

### Task 1: データベーステーブル追加

**Files:**
- Modify: `vatsim_stat_notify_to_discord.py:111-132` (`init_db()`)

**Step 1: `init_db()` に `apch_config` と `apch_watches` テーブル作成を追加**

L131 (`conn.commit()`) の直前に以下を追加:

```python
    c.execute("""CREATE TABLE IF NOT EXISTS apch_config (
        guild_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS apch_watches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT NOT NULL,
        icao TEXT NOT NULL,
        baseline TEXT NOT NULL,
        time_start TEXT,
        time_end TEXT,
        registered_by TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_apch_watches_guild_icao ON apch_watches(guild_id, icao)")
```

**Step 2: コミット**

```bash
git add vatsim_stat_notify_to_discord.py
git commit -m "APCH監視用のDBテーブル定義を init_db() に追加"
```

---

### Task 2: APCH設定のCRUDヘルパー関数

**Files:**
- Modify: `vatsim_stat_notify_to_discord.py` — `init_db()` の直後（L133付近）に追加

**Step 1: CRUD関数を追加**

```python
# ── APCH TYPE monitoring helpers ──────────────────────────────────

def apch_set_channel(guild_id, channel_id):
    with sqlite3.connect(stats_db_filename) as conn:
        conn.execute("INSERT OR REPLACE INTO apch_config (guild_id, channel_id) VALUES (?, ?)",
                     (str(guild_id), str(channel_id)))

def apch_get_channel(guild_id):
    with sqlite3.connect(stats_db_filename) as conn:
        row = conn.execute("SELECT channel_id FROM apch_config WHERE guild_id = ?",
                          (str(guild_id),)).fetchone()
    return int(row[0]) if row else None

def apch_add_watch(guild_id, icao, baseline, time_start, time_end, registered_by):
    with sqlite3.connect(stats_db_filename) as conn:
        conn.execute(
            "INSERT INTO apch_watches (guild_id, icao, baseline, time_start, time_end, registered_by) VALUES (?, ?, ?, ?, ?, ?)",
            (str(guild_id), icao.upper(), baseline, time_start, time_end, str(registered_by)))

def apch_remove_watch(guild_id, icao, time_start=None, time_end=None):
    """登録を削除。time_start/time_end指定時はその時間帯のみ、未指定時は全削除。削除件数を返す。"""
    with sqlite3.connect(stats_db_filename) as conn:
        if time_start is not None and time_end is not None:
            c = conn.execute(
                "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ? AND time_start = ? AND time_end = ?",
                (str(guild_id), icao.upper(), time_start, time_end))
        else:
            c = conn.execute(
                "DELETE FROM apch_watches WHERE guild_id = ? AND icao = ?",
                (str(guild_id), icao.upper()))
        return c.rowcount

def apch_list_watches(guild_id):
    with sqlite3.connect(stats_db_filename) as conn:
        rows = conn.execute(
            "SELECT icao, baseline, time_start, time_end FROM apch_watches WHERE guild_id = ? ORDER BY icao, time_start",
            (str(guild_id),)).fetchall()
    return rows  # [(icao, baseline, time_start, time_end), ...]

def apch_get_all_watches():
    """全ギルドの監視設定を取得（ポーリング用）。"""
    with sqlite3.connect(stats_db_filename) as conn:
        rows = conn.execute(
            "SELECT guild_id, icao, baseline, time_start, time_end FROM apch_watches ORDER BY guild_id, icao"
        ).fetchall()
    return rows  # [(guild_id, icao, baseline, time_start, time_end), ...]
```

**Step 2: コミット**

```bash
git add vatsim_stat_notify_to_discord.py
git commit -m "APCH監視設定のCRUDヘルパー関数を追加"
```

---

### Task 3: 時間帯判定ユーティリティ

**Files:**
- Modify: `vatsim_stat_notify_to_discord.py` — CRUD関数の直後に追加

**Step 1: 時間帯パース・判定関数を追加**

```python
def parse_time_range(time_range_str):
    """'HH:MM-HH:MM' をパースして (start, end) を返す。不正な場合は None。"""
    m = re.match(r'^(\d{2}:\d{2})-(\d{2}:\d{2})$', time_range_str)
    if not m:
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
```

**Step 2: コミット**

```bash
git add vatsim_stat_notify_to_discord.py
git commit -m "APCH監視の時間帯パース・判定ユーティリティを追加"
```

---

### Task 4: `/apch` コマンドグループ

**Files:**
- Modify: `vatsim_stat_notify_to_discord.py` — `bot.tree.add_command(mystats_group)` (L1447) の直後に追加

**Step 1: `/apch` コマンドグループを実装**

```python
# ── /apch command group ───────────────────────────────────────────

apch_group = app_commands.Group(name="apch", description="APCH TYPE変更監視")

@apch_group.command(name="setchannel", description="APCH TYPE変更通知の送信先チャンネルを設定")
@app_commands.describe(channel="通知先チャンネル")
async def apch_setchannel(interaction: discord.Interaction, channel: discord.TextChannel):
    apch_set_channel(interaction.guild_id, channel.id)
    await interaction.response.send_message(f"APCH TYPE変更通知の送信先を {channel.mention} に設定しました。")

@apch_group.command(name="set", description="空港の基準APCH TYPEを登録")
@app_commands.describe(
    icao="空港のICAOコード（例: RJTT）",
    baseline="基準APCH TYPE（例: ILS, ILS Y RWY34L）",
    time_range="適用時間帯 HH:MM-HH:MM UTC（省略時は全時間帯）"
)
async def apch_set(interaction: discord.Interaction, icao: str, baseline: str, time_range: str = None):
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
    apch_add_watch(interaction.guild_id, icao, baseline, time_start, time_end, interaction.user.id)
    time_desc = f" ({time_start}-{time_end} UTC)" if time_start else " (全時間帯)"
    await interaction.response.send_message(
        f"**{icao.upper()}** の基準APCH TYPEを「{baseline}」に設定しました。{time_desc}")

@apch_group.command(name="remove", description="空港のAPCH TYPE監視登録を削除")
@app_commands.describe(
    icao="空港のICAOコード（例: RJTT）",
    time_range="削除する時間帯 HH:MM-HH:MM UTC（省略時は全削除）"
)
async def apch_remove(interaction: discord.Interaction, icao: str, time_range: str = None):
    time_start, time_end = None, None
    if time_range:
        parsed = parse_time_range(time_range)
        if not parsed:
            await interaction.response.send_message(
                "時間帯の形式が不正です。`HH:MM-HH:MM`（UTC）で指定してください。", ephemeral=True)
            return
        time_start, time_end = parsed
    count = apch_remove_watch(interaction.guild_id, icao, time_start, time_end)
    if count > 0:
        await interaction.response.send_message(f"**{icao.upper()}** の監視登録を{count}件削除しました。")
    else:
        await interaction.response.send_message(f"**{icao.upper()}** の該当する監視登録が見つかりません。")

@apch_group.command(name="list", description="APCH TYPE監視の登録一覧を表示")
async def apch_list(interaction: discord.Interaction):
    watches = apch_list_watches(interaction.guild_id)
    ch_id = apch_get_channel(interaction.guild_id)
    if not watches and not ch_id:
        await interaction.response.send_message("APCH TYPE監視は設定されていません。")
        return
    lines = []
    for icao, baseline, ts, te in watches:
        time_desc = f"({ts}-{te} UTC)" if ts else "(全時間帯)"
        lines.append(f"**{icao}**: \"{baseline}\" {time_desc}")
    if ch_id:
        lines.append(f"\n通知先: <#{ch_id}>")
    embed = discord.Embed(
        title="APCH TYPE 監視一覧",
        color=0xFF9900,
        description="\n".join(lines) if lines else "登録なし"
    )
    await interaction.response.send_message(embed=embed)

bot.tree.add_command(apch_group)
```

**Step 2: コミット**

```bash
git add vatsim_stat_notify_to_discord.py
git commit -m "/apch コマンドグループ (set/remove/list/setchannel) を追加"
```

---

### Task 5: APCH TYPE監視ループ

**Files:**
- Modify: `vatsim_stat_notify_to_discord.py`
  - `VATJPNBot.__init__()` (L359-366): 状態変数追加
  - `VATJPNBot.setup_hook()` (L368-374): ループ起動追加
  - `VATJPNBot.close()` (L376-383): ループ停止追加
  - `pirep_loop.before_loop` (L445-447) の直後: `apch_loop` メソッド追加

**Step 1: `__init__` に状態変数を追加**

L366 (`self._pirep_first_run = True`) の直後に追加:

```python
        self.apch_last_notified = {}  # (guild_id, icao) → 前回通知した approach_type
        self._apch_first_run = True
```

**Step 2: `setup_hook` にループ起動を追加**

L374 (`self.pirep_loop.start()`) の直後に追加:

```python
            if swim_api_url and swim_api_token:
                self.apch_loop.start()

```

**Step 3: `close` にループ停止を追加**

L380 (`self.pirep_loop.cancel()`) の直後に追加:

```python
            if self.apch_loop.is_running():
                self.apch_loop.cancel()
```

**Step 4: `apch_loop` メソッドを追加**

`before_pirep_loop` (L445-447) の直後に追加:

```python
    @tasks.loop(seconds=300)
    async def apch_loop(self):
        try:
            all_watches = apch_get_all_watches()
            if not all_watches:
                return

            # guild_id × icao でグルーピング（APIコール数を最小化）
            icao_set = {row[1] for row in all_watches}
            rwy_cache = {}
            for icao in icao_set:
                rwy_data, err = await fetch_runway_info(self.http_session, icao)
                if err:
                    logger.warning("RWY-INFO取得エラー (%s): %s", icao, err)
                    continue
                if rwy_data:
                    rwy_cache[icao] = rwy_data

            if self._apch_first_run:
                # 初回: 現在のAPCH TYPEをキャッシュ（通知しない）
                self._apch_first_run = False
                for guild_id, icao, baseline, ts, te in all_watches:
                    if icao in rwy_cache:
                        apch = rwy_cache[icao].get("approach_type", "")
                        key = (guild_id, icao)
                        if apch and not self._apch_matches_baseline(apch, baseline):
                            self.apch_last_notified[key] = apch
                logger.info("APCH TYPE監視開始（%d空港）", len(icao_set))
                return

            # 通常ポーリング
            for guild_id, icao, baseline, ts, te in all_watches:
                if icao not in rwy_cache:
                    continue
                # 時間帯チェック
                if ts and te and not is_in_time_range(ts, te):
                    continue
                apch = rwy_cache[icao].get("approach_type", "")
                if not apch:
                    continue
                key = (guild_id, icao)
                if self._apch_matches_baseline(apch, baseline):
                    # 基準に戻った → 通知済みをクリア
                    self.apch_last_notified.pop(key, None)
                    continue
                # 基準と異なる
                if self.apch_last_notified.get(key) == apch:
                    continue  # 既に通知済み
                self.apch_last_notified[key] = apch

                ch_id = apch_get_channel(guild_id)
                if not ch_id:
                    continue
                channel = self.get_channel(ch_id)
                if not channel:
                    continue

                rwy_in_use = rwy_cache[icao].get("runway_in_use", "")
                observed = rwy_cache[icao].get("observed_at", "")
                time_desc = f" ({ts}-{te} UTC)" if ts else ""
                embed = discord.Embed(
                    title=f"⚠️ APCH TYPE 変更 — {icao}",
                    color=0xFF9900,
                )
                embed.add_field(name="現在", value=apch, inline=True)
                embed.add_field(name="基準", value=f"{baseline}{time_desc}", inline=True)
                if rwy_in_use:
                    embed.add_field(name="使用滑走路", value=rwy_in_use, inline=True)
                if observed:
                    embed.set_footer(text=f"観測: {observed[:10]} {observed[11:16]}Z")
                await channel.send(embed=embed)

        except Exception:
            logger.exception("APCHポーリングエラー")

    @staticmethod
    def _apch_matches_baseline(approach_type, baseline):
        """approach_typeがbaselineに合致するかを部分一致で判定する。"""
        return baseline.upper() in approach_type.upper()

    @apch_loop.before_loop
    async def before_apch_loop(self):
        await self.wait_until_ready()
```

**Step 5: コミット**

```bash
git add vatsim_stat_notify_to_discord.py
git commit -m "APCH TYPE監視ループ (5分間隔ポーリング) を追加"
```

---

### Task 6: 最終確認・デプロイ

**Step 1: 構文チェック**

```bash
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

Expected: 出力なし（成功）

**Step 2: コミット＆プッシュ**

```bash
git push
```

**Step 3: NASへデプロイ**

```bash
scp vatsim_stat_notify_to_discord.py nas:/share/ZFS18_DATA/ContainerData/discord-bot/bot1/
ssh nas "cd /share/ZFS530_DATA/.qpkg/container-station/data/application/phase6-bots && HOME=/tmp DOCKER_CONFIG=/tmp/.docker /share/ZFS530_DATA/.qpkg/container-station/bin/docker compose restart bot1"
```

**Step 4: ログ確認**

```bash
ssh nas "cd /share/ZFS530_DATA/.qpkg/container-station/data/application/phase6-bots && HOME=/tmp DOCKER_CONFIG=/tmp/.docker /share/ZFS530_DATA/.qpkg/container-station/bin/docker compose logs --tail=30 bot1"
```

Expected: `APCH TYPE監視開始` のログが出力される
