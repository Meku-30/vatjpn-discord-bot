# SWIM Cog 分離 実装計画

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** SWIM非公式API関連の全機能を discord.py Cog (`cogs/swim.py`) に分離し、SWIM環境変数未設定時にコマンド・ループが完全に無効化されるようにする

**Architecture:** メインファイルからSWIM関連のコード（コマンド、ループ、ヘルパー、DB関数）を `cogs/swim.py` の `SwimCog` クラスに移動。`setup_hook()` で `SWIM_API_URL` と `SWIM_API_TOKEN` の存在を条件にCogをロード。個別のループは `ENABLE_PIREP_NOTIFICATIONS` / `ENABLE_APCH_NOTIFICATIONS` 環境変数で制御。

**Tech Stack:** Python, discord.py (Cog/commands.Cog), SQLite, aiohttp

**Design Doc:** `docs/plans/2026-03-08-swim-cog-extraction-design.md`

---

### Task 1: `cogs/swim.py` の骨格を作成

**Files:**
- Create: `cogs/__init__.py`
- Create: `cogs/swim.py`

**Step 1: ディレクトリとファイルを作成**

```bash
mkdir -p cogs
touch cogs/__init__.py
```

**Step 2: SwimCog の骨格を作成**

`cogs/swim.py` に以下を記述:

```python
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

logger = logging.getLogger("vatjpn-bot")

# 環境変数（メインから参照）
swim_api_url = os.environ.get("SWIM_API_URL")
swim_api_token = os.environ.get("SWIM_API_TOKEN")
enable_pirep_notifications = os.environ.get("ENABLE_PIREP_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
enable_apch_notifications = os.environ.get("ENABLE_APCH_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")


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
```

**Step 3: 構文チェック**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
```

Expected: 出力なし（成功）

**Step 4: コミット**

```bash
git add cogs/
git commit -m "SwimCog の骨格を作成"
```

---

### Task 2: SWIM共通ヘルパーをCogに移動

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: 以下の関数・定数を `vatsim_stat_notify_to_discord.py` から `cogs/swim.py` の `SwimCog` クラス定義の **前** （モジュールレベル）にコピー**

移動対象（メインファイルの行番号）:
- `_swim_headers` 変数と `_get_swim_headers()` (L901-907)
- `_swim_request()` (L909-935)

これらはCog内のコマンドとループから使用されるモジュールレベル関数。

**Step 2: メインファイルから上記の関数を削除**

L901-935 を削除。

**Step 3: 構文チェック（両ファイル）**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 4: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "SWIM共通ヘルパーをcogs/swim.pyに移動"
```

---

### Task 3: APCH DB関数・時間帯ユーティリティをCogに移動

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: 以下の関数を `cogs/swim.py` のモジュールレベルにコピー**

移動対象（メインファイルの行番号）:
- `apch_set_channel()` (L156-160)
- `apch_get_channel()` (L162-167)
- `apch_add_watch()` (L169-178)
- `apch_remove_watch()` (L180-196)
- `apch_list_watches()` (L198-204)
- `apch_get_all_watches()` (L206-215)
- `parse_time_range()` (L217-226)
- `is_in_time_range()` (L228-240)

これらは `get_db()` をメインファイルからインポートする必要がある:

```python
from vatsim_stat_notify_to_discord import get_db
```

**Step 2: メインファイルから上記の関数を削除**

L156-240 を削除。

**Step 3: 構文チェック（両ファイル）**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 4: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "APCH DB関数・時間帯ユーティリティをcogs/swim.pyに移動"
```

---

### Task 4: NOTAM/ATIS/METARヘルパーをCogに移動

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: 以下の関数・クラスを `cogs/swim.py` に移動**

移動対象（メインファイルの行番号）:
- `NOTAM_PER_PAGE` 定数 (L939)
- `fetch_notams()` (L941-946)
- `format_notam_page()` (L948-976)
- `NotamPaginationView` クラス (L978-1005)
- `fetch_atis()` (L1009-1011)
- `fetch_all_atis()` (L1013-1020)
- `fetch_metar()` (L1022-1030)
- `fetch_runway_info()` (L1032-1034)
- `fetch_all_runway_info()` (L1036-1058)

**Step 2: メインファイルから上記を削除**

**Step 3: 構文チェック（両ファイル）**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 4: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "NOTAM/ATIS/METARヘルパーをcogs/swim.pyに移動"
```

---

### Task 5: PIREPヘルパーをCogに移動

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: 以下の関数・定数を `cogs/swim.py` に移動**

移動対象（メインファイルの行番号）:
- `TURBULENCE_TEXT_TO_LEVEL` 定数 (L1055-1058)
- `turbulence_level()` (L1060-1066)
- `fetch_active_pireps()` (L1068-1073)
- `JAPAN_TRANSITION_FL` 定数 (L1075)
- `_fl_to_display()` (L1077-1082)
- `format_pirep_altitude()` (L1084-1112)
- `format_pirep_location()` (L1114-1124)
- `parse_pirep_coords()` (L1126-1136)
- `JAPAN_BBOX`, `JAPAN_REF` 定数 (L1138-1140)
- `generate_pirep_map()` (L1142-1158)
- `build_pirep_embed()` (L1160-1200)

**Step 2: メインファイルから上記を削除**

**Step 3: 構文チェック（両ファイル）**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 4: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "PIREPヘルパーをcogs/swim.pyに移動"
```

---

### Task 6: SWIMコマンドをCogメソッドに変換

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: `/atis`, `/metar`, `/notam` コマンドを SwimCog のメソッドに変換**

discord.py の Cog ではコマンドの書き方が変わる:

```python
# Before (メインファイル):
@bot.tree.command(name="atis", description="...")
async def atis_command(interaction: discord.Interaction, icao: str):
    ...

# After (Cog内):
@app_commands.command(name="atis", description="...")
@app_commands.describe(icao="...")
async def atis_command(self, interaction: discord.Interaction, icao: str):
    ...
```

移動対象（メインファイルの行番号）:
- `/notam` コマンド (L1312-1366) — `JAPAN_MAJOR_AIRPORTS` 定数もCogに移動
- `/atis` コマンド (L1368-1484) — `AIRPORT_ORDER` 定数もCogに移動
- `/metar` コマンド (L1486-1514)

コマンド内の `bot.http_session` を `self.bot.http_session` に変更。

**Step 2: メインファイルから上記コマンドと `JAPAN_MAJOR_AIRPORTS`, `AIRPORT_ORDER` 定数を削除**

**Step 3: 構文チェック**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 4: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "/atis, /metar, /notamコマンドをSwimCogに移動"
```

---

### Task 7: `/apch` コマンドグループをCogメソッドに変換

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: `/apch` コマンドグループを SwimCog 内に変換**

Cog内の `app_commands.Group` は以下の形式になる:

```python
class SwimCog(commands.Cog):
    apch_group = app_commands.Group(name="apch", description="APCH TYPE変更監視", guild_only=True)

    @apch_group.command(name="setchannel", description="...")
    async def apch_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        ...
```

移動対象（メインファイルの行番号）:
- `apch_group` 定義 (L1755)
- `apch_setchannel` (L1757-1761)
- `apch_watch` (L1763-1777)
- `apch_unwatch` (L1779-1791)
- `apch_set` (L1793-1819)
- `apch_remove` (L1821-1839)
- `apch_list` (L1841-1871)
- `bot.tree.add_command(apch_group)` (L1873) — Cogでは不要（自動登録）

**Step 2: メインファイルから上記を削除**

`bot.tree.add_command(apch_group)` 行も削除。

**Step 3: 構文チェック**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 4: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "/apchコマンドグループをSwimCogに移動"
```

---

### Task 8: PIREP・APCHループをCogメソッドに移動

**Files:**
- Modify: `cogs/swim.py`
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: PIREPループをCogに移動**

メインファイルの `pirep_loop` メソッド (L531-570) と `before_pirep_loop` (L572-573) の中身を、Task 1 で作成した仮定義に上書き。

`self.http_session` → `self.bot.http_session` に変更。
`pirep_channel_id` はメインからインポート:

```python
from vatsim_stat_notify_to_discord import pirep_channel_id
```

**Step 2: APCHループをCogに移動**

メインファイルの `apch_loop` メソッド (L575-734) と `_apch_matches_baseline` (L736-741)、`before_apch_loop` (L743-746) の中身をCogに移動。

`self.http_session` → `self.bot.http_session` に変更。

**Step 3: メインファイルから PIREP・APCHループ関連を全て削除**

削除対象:
- `__init__` 内の `self.pirep_notified`, `self._pirep_first_run`, `self.apch_last_notified`, `self._apch_first_run` (L482-485)
- `setup_hook` 内の PIREP・APCH ループ起動コード (L496-499)
- `close` 内の PIREP・APCH ループ停止コード (L504-507)
- `pirep_loop` メソッド全体 (L531-573)
- `apch_loop` メソッド全体 (L575-746)

**Step 4: 構文チェック**

```bash
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 5: コミット**

```bash
git add cogs/swim.py vatsim_stat_notify_to_discord.py
git commit -m "PIREP・APCHループをSwimCogに移動"
```

---

### Task 9: メインファイルの setup_hook を修正して Cog をロード

**Files:**
- Modify: `vatsim_stat_notify_to_discord.py`

**Step 1: `ENABLE_APCH_NOTIFICATIONS` 環境変数を追加**

L26 (`enable_pirep_notifications = ...`) の直後に追加:

```python
enable_apch_notifications = os.environ.get("ENABLE_APCH_NOTIFICATIONS", "true").lower() in ("true", "1", "yes")
```

**Step 2: `setup_hook` を修正**

```python
async def setup_hook(self):
    timeout = aiohttp.ClientTimeout(total=10)
    self.http_session = aiohttp.ClientSession(timeout=timeout)
    if enable_notifications:
        self.polling_loop.start()
    # SWIM機能はCogとして条件付きロード
    if swim_api_url and swim_api_token:
        from cogs.swim import SwimCog
        await self.add_cog(SwimCog(self))
```

**Step 3: `close` からSWIM関連のループ停止コードが削除済みであることを確認**

`close` は以下のみ残る:

```python
async def close(self):
    if enable_notifications:
        self.polling_loop.cancel()
    if self.http_session:
        await self.http_session.close()
    await super().close()
```

**Step 4: `__init__` からSWIM関連の状態変数が削除済みであることを確認**

**Step 5: メインファイルから不要になった `from discord.ext import tasks` の `tasks` が polling_loop でまだ使われているか確認（使われている場合は残す）**

**Step 6: 構文チェック**

```bash
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
```

**Step 7: コミット**

```bash
git add vatsim_stat_notify_to_discord.py
git commit -m "setup_hookでSwimCogを条件付きロード、ENABLE_APCH_NOTIFICATIONS追加"
```

---

### Task 10: テストのインポートパスを更新

**Files:**
- Modify: `tests/test_helpers.py`
- Modify: `tests/test_db.py`

**Step 1: `tests/test_helpers.py` のインポートを修正**

メインに残る関数のインポートはそのまま。Cogに移動した関数のインポート先を `cogs.swim` に変更:

```python
# メインから（変更なし）
from vatsim_stat_notify_to_discord import (
    format_duration_seconds,
    get_rating_str,
    check_position_rating,
)

# Cogから（変更）
from cogs.swim import (
    parse_time_range,
    is_in_time_range,
    turbulence_level,
    _fl_to_display,
    format_pirep_altitude,
    format_pirep_location,
    parse_pirep_coords,
    SwimCog,
)
```

`VATJPNBot._apch_matches_baseline` → `SwimCog._apch_matches_baseline` に変更。

**Step 2: `tests/test_db.py` のインポートを修正**

```python
# Cogから（変更）
from cogs.swim import (
    apch_set_channel,
    apch_get_channel,
    apch_add_watch,
    apch_remove_watch,
    apch_list_watches,
    apch_get_all_watches,
)

# メインから（変更なし）
from vatsim_stat_notify_to_discord import (
    log_session,
    get_controller_stats,
)
```

**Step 3: テスト実行**

```bash
python3 -m pytest tests/ -v
```

Expected: 58テスト全通過

**Step 4: コミット**

```bash
git add tests/
git commit -m "テストのインポートパスをCog分離に合わせて更新"
```

---

### Task 11: ドキュメント・設定ファイル更新

**Files:**
- Modify: `README.md`
- Modify: `settings.ini.example`

**Step 1: README.md に `ENABLE_APCH_NOTIFICATIONS` を追加**

`.env` の例に追加:

```
# APCH TYPE 変更自動通知（デフォルト: true）
ENABLE_APCH_NOTIFICATIONS=true
```

Docker Compose の environment にも追加:

```yaml
- ENABLE_APCH_NOTIFICATIONS=${ENABLE_APCH_NOTIFICATIONS:-true}
```

**Step 2: README.md の Docker Compose 例に `cogs/` マウントを追加**

```yaml
volumes:
  - ./vatsim_stat_notify_to_discord.py:/app/vatsim_stat_notify_to_discord.py:ro
  - ./cogs/:/app/cogs/:ro
```

**Step 3: `settings.ini.example` に `ENABLE_APCH_NOTIFICATIONS` を追記**

```
#   ENABLE_APCH_NOTIFICATIONS=true   (APCH TYPE change auto-notification, default: true)
```

**Step 4: コミット**

```bash
git add README.md settings.ini.example
git commit -m "ドキュメント更新: ENABLE_APCH_NOTIFICATIONS追加、cogs/マウント追加"
```

---

### Task 12: 最終テスト・デプロイ

**Step 1: 全テスト実行**

```bash
python3 -m pytest tests/ -v
```

Expected: 58テスト全通過

**Step 2: 構文チェック（全ファイル）**

```bash
python3 -c "import py_compile; py_compile.compile('vatsim_stat_notify_to_discord.py', doraise=True)"
python3 -c "import py_compile; py_compile.compile('cogs/swim.py', doraise=True)"
```

**Step 3: git push**

```bash
git push
```

**Step 4: NASにデプロイ**

```bash
# bot1
scp vatsim_stat_notify_to_discord.py nas:<NAS_DATA_PATH>/discord-bot/bot1/
ssh nas "mkdir -p <NAS_DATA_PATH>/discord-bot/bot1/cogs"
scp -r cogs/ nas:<NAS_DATA_PATH>/discord-bot/bot1/cogs/

# bot2
scp vatsim_stat_notify_to_discord.py nas:<NAS_DATA_PATH>/discord-bot/bot2/
ssh nas "mkdir -p <NAS_DATA_PATH>/discord-bot/bot2/cogs"
scp -r cogs/ nas:<NAS_DATA_PATH>/discord-bot/bot2/cogs/
```

**Step 5: Docker Compose に `cogs/` マウントを追加**

NAS上の docker-compose.yml (`<NAS_CONTAINER_STATION>/data/application/phase6-bots/docker-compose.yml`) の各サービスの volumes に追加:

```yaml
- ./cogs/:/app/cogs/:ro
```

**注意:** docker-compose.yml はNAS上で直接編集する。正確なパスは bot1/bot2 のマウントパターンに合わせる。

**Step 6: 再起動**

```bash
ssh nas "HOME=/tmp DOCKER_CONFIG=/tmp/.docker <NAS_CONTAINER_STATION>/bin/docker compose -f <NAS_CONTAINER_STATION>/data/application/phase6-bots/docker-compose.yml restart discord-bot-1 discord-bot-2"
```

**Step 7: ログ確認**

```bash
ssh nas "HOME=/tmp DOCKER_CONFIG=/tmp/.docker <NAS_CONTAINER_STATION>/bin/docker compose -f <NAS_CONTAINER_STATION>/data/application/phase6-bots/docker-compose.yml logs --tail=20 discord-bot-1 discord-bot-2"
```

Expected:
- bot1: `Logged in as VATJPN Notify#3425 (mode: notifications + commands)`, `PIREP監視開始`, コマンド数10
- bot2: `Logged in as UNSHU Bot#0760 (mode: commands only)`, `APCH TYPE監視開始（49空港）`, コマンド数10
