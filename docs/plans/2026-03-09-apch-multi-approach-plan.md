# APCH TYPE 複数進入方式対応 実装計画

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** SWIM APIで複数行の進入方式を配列として返し、Discord Botのbaseline判定・通知表示を改善する

**Architecture:** SWIM APIのパーサーを修正して `approach_types` 配列を返す。DBカラム追加、レスポンスモデル拡張。Bot側は配列に対してbaseline判定し、通知Embedに全進入方式をまとめて表示する。

**Tech Stack:** Python, SQLAlchemy (async, SQLite), FastAPI, Pydantic v2, discord.py

---

## Phase 1: SWIM API側 (swim-apiリポジトリ)

作業ディレクトリ: `<HOME>/claude/swim-api`

### Task 1: パーサーのテスト追加

**Files:**
- Modify: `swim-api/tests/test_runway_info.py`

**Step 1: 複数進入方式のテストを追加**

`tests/test_runway_info.py` の `TestParseRwyInfo` クラスに追加:

```python
def test_multi_approach_rjtt(self):
    """RJTT形式: 複数進入方式の継続行"""
    plain = (
        "APCH 090000\n"
        "RJTT (APCH) ILS X RWY34L\n"
        "            HIGHWAY VISUAL RWY34R\n"
        "     LDG RWY 34L/34R\n"
        "     DEP RWY 05/34R"
    )
    apch, apch_list, rwy = self.scraper._parse_rwy_info(plain)
    assert apch == "ILS X RWY34L"
    assert apch_list == ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
    assert rwy == "34L/34R"

def test_single_approach_returns_list(self):
    """単一進入方式でもリストで返す"""
    plain = "APCH 071300\nRJFR (APCH) ILS Z RWY18\n     USING RWY 18\n"
    apch, apch_list, rwy = self.scraper._parse_rwy_info(plain)
    assert apch == "ILS Z RWY18"
    assert apch_list == ["ILS Z RWY18"]
    assert rwy == "18"

def test_no_match_returns_empty_list(self):
    """マッチなしで空リスト"""
    apch, apch_list, rwy = self.scraper._parse_rwy_info("some random text")
    assert apch is None
    assert apch_list == []
    assert rwy is None
```

**Step 2: 既存テストの戻り値を3値タプルに更新**

既存の全テストを `apch, rwy` → `apch, _, rwy` に変更。
例:
```python
def test_ils_approach(self):
    plain = "APCH 071300\nRJFR (APCH) ILS Z RWY18\n     USING RWY 18\n"
    apch, _, rwy = self.scraper._parse_rwy_info(plain)
    assert apch == "ILS Z RWY18"
    assert rwy == "18"
```

**Step 3: テスト実行 → FAILを確認**

```bash
cd <HOME>/claude/swim-api && python3 -m pytest tests/test_runway_info.py -v
```
Expected: FAIL (戻り値が2値タプルのため)

---

### Task 2: パーサー修正

**Files:**
- Modify: `swim-api/src/scraper/airspace.py:476-507`

**Step 1: `_parse_rwy_info` を修正**

```python
def _parse_rwy_info(self, plain_data: str) -> tuple[str | None, list[str], str | None]:
    """RWY-INFO plain_DATAからAPCH TYPEとUSING RWYを抽出する

    例:
        "APCH 071300\\nRJFR (APCH) ILS Z RWY18\\n     USING RWY 18\\n"
        → ("ILS Z RWY18", ["ILS Z RWY18"], "18")

        "...RJTT (APCH) ILS X RWY34L\\n            HIGHWAY VISUAL RWY34R\\n     LDG RWY 34L/34R\\n..."
        → ("ILS X RWY34L", ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"], "34L/34R")

    Returns:
        (approach_type, approach_types, runway_in_use) のタプル
    """
    approach_type = None
    approach_types = []
    runway_in_use = None

    # APCH TYPE: "RJXX (APCH) <approach_type>" パターン
    m = re.search(r"\(APCH\)\s+(.+?)(?:\n|$)", plain_data)
    if m:
        approach_type = m.group(1).strip()
        approach_types.append(approach_type)
        # 継続行: (APCH)行より後、LDG/DEP/USING行以外のインデント行
        rest = plain_data[m.end():]
        for line in rest.split("\n"):
            stripped = line.strip()
            if not stripped or stripped.startswith(("LDG ", "DEP ", "USING ")):
                break
            approach_types.append(stripped)

    # USING RWY: "USING RWY <number>"
    m = re.search(r"USING\s+RWY\s+(.+?)(?:\n|$)", plain_data)
    if m:
        runway_in_use = m.group(1).strip()
    else:
        # LDG RWY パターン（着陸滑走路）
        m = re.search(r"LDG\s+RWY\s+(.+?)(?:\n|$)", plain_data)
        if m:
            runway_in_use = m.group(1).strip()

    return approach_type, approach_types, runway_in_use
```

**Step 2: 呼び出し元を修正**

`src/scraper/airspace.py:653-655`:
```python
# 変更前
approach_type, runway_in_use = self._parse_rwy_info(plain_data)

# 変更後
approach_type, approach_types, runway_in_use = self._parse_rwy_info(plain_data)
```

`RunwayInfo` コンストラクタ (L667-675) に `approach_types` 追加:
```python
session.add(
    RunwayInfo(
        icao_code=icao,
        runway_number=item.get("runway_NUMBER"),
        approach_type=approach_type,
        approach_types=json.dumps(approach_types) if approach_types else None,
        runway_in_use=runway_in_use,
        plain_data=plain_data.strip(),
        observed_at=observed_at,
    )
)
```

ファイル先頭のimportに `import json` 追加（未importの場合）。

**Step 3: テスト実行 → PASSを確認**

```bash
cd <HOME>/claude/swim-api && python3 -m pytest tests/test_runway_info.py -v
```
Expected: PASS

**Step 4: tests/test_airspace_scraper.py の既存テストも修正**

`TestParseRwyInfo` クラス (L185-214) の戻り値を同様に3値タプルに更新。
`test_saves_all_types` (L478-505) で `approach_types` の検証を追加。

```bash
cd <HOME>/claude/swim-api && python3 -m pytest tests/test_airspace_scraper.py -v
```
Expected: PASS

**Step 5: コミット**

```bash
cd <HOME>/claude/swim-api
git add src/scraper/airspace.py tests/test_runway_info.py tests/test_airspace_scraper.py
git commit -m "パーサー修正: 複数行進入方式をリストで返す"
```

---

### Task 3: DBモデル・レスポンスモデル拡張

**Files:**
- Modify: `swim-api/src/db/models.py:115-136`
- Modify: `swim-api/src/api/models.py:107-118`

**Step 1: DBモデルにカラム追加**

`src/db/models.py` の `RunwayInfo` クラスに追加:
```python
approach_types: Mapped[str | None] = mapped_column(Text, nullable=True)
# JSON文字列: '["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]'
```
`approach_type` (L123) の直後に配置。

**Step 2: レスポンスモデルにフィールド追加**

`src/api/models.py` の `RunwayInfoResponse` に追加:
```python
approach_types: list[str] | None = None

@field_validator("approach_types", mode="before")
@classmethod
def parse_approach_types(cls, v):
    if isinstance(v, str):
        import json
        return json.loads(v)
    return v
```

`from pydantic import field_validator` がimportされていなければ追加。

**Step 3: DBマイグレーション実行**

```bash
cd <HOME>/claude/swim-api
ssh nas 'HOME=/tmp DOCKER_CONFIG=/tmp/.docker <NAS_CONTAINER_STATION>/bin/docker exec swim-api python3 -c "
import sqlite3
conn = sqlite3.connect(\"/app/data/swim.db\")
try:
    conn.execute(\"ALTER TABLE runway_info ADD COLUMN approach_types TEXT\")
    conn.commit()
    print(\"OK: approach_types column added\")
except Exception as e:
    print(\"Already exists or error:\", e)
conn.close()
"'
```

**Step 4: 全テスト実行**

```bash
cd <HOME>/claude/swim-api && python3 -m pytest -v
```
Expected: ALL PASS

**Step 5: コミット**

```bash
cd <HOME>/claude/swim-api
git add src/db/models.py src/api/models.py
git commit -m "DBモデル・レスポンスにapproach_types配列フィールド追加"
```

---

### Task 4: SWIM APIデプロイ

**Step 1: NASにコード転送**

```bash
cd <HOME>/claude/swim-api
scp src/scraper/airspace.py nas:<NAS_DATA_PATH>/swim-api/src/scraper/
scp src/db/models.py nas:<NAS_DATA_PATH>/swim-api/src/db/
scp src/api/models.py nas:<NAS_DATA_PATH>/swim-api/src/api/
```

注: swim-apiのデプロイパスは実際のパスを確認すること。

**Step 2: コンテナ再起動**

```bash
ssh nas "HOME=/tmp DOCKER_CONFIG=/tmp/.docker <NAS_CONTAINER_STATION>/bin/docker compose -f <swim-api-compose-path> restart swim-api"
```

**Step 3: データ確認**

次回のスクレイピング後にRJTTの `approach_types` が配列で返るか確認:
```bash
ssh nas '..docker exec swim-api python3 -c "..."'  # /api/runway-info/RJTT
```

**Step 4: git push**

```bash
cd <HOME>/claude/swim-api && git push
```

---

## Phase 2: Discord Bot側 (vatjpn-discord-botリポジトリ)

作業ディレクトリ: `<HOME>/claude/vatjpn-discord-bot`

### Task 5: Bot側テスト追加

**Files:**
- Modify: `vatjpn-discord-bot/tests/test_helpers.py`

**Step 1: 複数approach_typesに対するbaseline判定テスト追加**

`TestApchMatchesBaseline` クラスに追加:
```python
def test_multi_approach_any_match(self):
    """approach_types配列のいずれかにマッチすればTrue"""
    approach_types = ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
    assert any(SwimCog._apch_matches_baseline(a, "ILS") for a in approach_types) is True
    assert any(SwimCog._apch_matches_baseline(a, "VISUAL") for a in approach_types) is True
    assert any(SwimCog._apch_matches_baseline(a, "RNAV") for a in approach_types) is False
```

**Step 2: テスト実行 → PASS確認（既存ロジックで対応可能なため）**

```bash
cd <HOME>/claude/vatjpn-discord-bot && python3 -m pytest tests/test_helpers.py -v
```
Expected: PASS

**Step 3: コミット**

```bash
git add tests/test_helpers.py
git commit -m "テスト追加: 複数approach_typesに対するbaseline判定"
```

---

### Task 6: apch_loop のbaseline判定を approach_types 対応に修正

**Files:**
- Modify: `vatjpn-discord-bot/cogs/swim.py`

**Step 1: approach_types 取得ヘルパー追加**

`cogs/swim.py` の `SwimCog` クラス内、`_apch_matches_baseline` の近くに:
```python
@staticmethod
def _get_approach_types(rwy):
    """RWY-INFOからapproach_typesを取得。フォールバック付き。"""
    types = rwy.get("approach_types")
    if types:
        return types
    # approach_types未対応 (SWIM API更新前) のフォールバック
    apch = rwy.get("approach_type", "")
    return [apch] if apch else []
```

**Step 2: apch_loop 内のbaseline判定を修正**

`apch_loop` 内で `apch = rwy.get("approach_type", "")` を使っている箇所を修正。

変更前 (L916-917):
```python
apch = rwy.get("approach_type", "")
if not apch:
    continue
```

変更後:
```python
approach_types = self._get_approach_types(rwy)
if not approach_types:
    continue
apch = rwy.get("approach_type", "")  # 表示用（1行目）
```

baseline判定部分 (L939):
```python
# 変更前
if any(self._apch_matches_baseline(apch, bl) for bl, _, _ in applicable):

# 変更後
if any(
    self._apch_matches_baseline(a, bl)
    for bl, _, _ in applicable
    for a in approach_types
):
```

グローバルwatch変化検知 (L976) のキー:
```python
# 変更前: 単一approach_typeで変化検知
if self.apch_last_notified.get(key) == apch:

# 変更後: approach_types全体をタプルとして比較
apch_tuple = tuple(approach_types)
if self.apch_last_notified.get(key) == apch_tuple:
    continue
prev = self.apch_last_notified.get(key)
self.apch_last_notified[key] = apch_tuple
```

baseline登録のキャッシュ (L943, L946) も同様に `apch_tuple` へ変更。

初回キャッシュ (L906) も修正:
```python
# 変更前
self.apch_last_notified[(gid, icao)] = apch

# 変更後
self.apch_last_notified[(gid, icao)] = tuple(approach_types)
```

**Step 3: テスト実行**

```bash
cd <HOME>/claude/vatjpn-discord-bot && python3 -m pytest tests/ -v
```
Expected: ALL PASS

**Step 4: コミット**

```bash
git add cogs/swim.py
git commit -m "APCH baseline判定をapproach_types配列対応に修正"
```

---

### Task 7: 通知Embed表示を改善

**Files:**
- Modify: `vatjpn-discord-bot/cogs/swim.py`

**Step 1: baseline通知Embed修正 (L959-969)**

変更前:
```python
embed = discord.Embed(title=f"⚠️ APCH TYPE 変更 — {icao}", color=0xFF9900)
embed.add_field(name="現在", value=apch, inline=True)
embed.add_field(name="基準", value=" / ".join(bl_strs), inline=True)
if rwy_in_use:
    embed.add_field(name="使用滑走路", value=rwy_in_use, inline=True)
```

変更後:
```python
embed = discord.Embed(title=f"⚠️ APCH TYPE 変更 — {icao}", color=0xFF9900)
# 進入方式 + 使用滑走路をまとめて表示
apch_display = "\n".join(approach_types)
if rwy_in_use:
    apch_display += f"\nRWY: {rwy_in_use}"
embed.add_field(name="現在", value=apch_display, inline=True)
embed.add_field(name="基準", value=" / ".join(bl_strs), inline=True)
```

**Step 2: グローバルwatch通知Embed修正 (L990-1001)**

変更前:
```python
embed = discord.Embed(title=f"APCH TYPE 更新 — {icao}", color=0x3498DB)
embed.add_field(name="現在", value=apch, inline=True)
if prev_apch:
    embed.add_field(name="前回", value=prev_apch, inline=True)
if rwy_in_use:
    embed.add_field(name="使用滑走路", value=rwy_in_use, inline=True)
```

変更後:
```python
embed = discord.Embed(title=f"APCH TYPE 更新 — {icao}", color=0x3498DB)
apch_display = "\n".join(approach_types)
if rwy_in_use:
    apch_display += f"\nRWY: {rwy_in_use}"
embed.add_field(name="現在", value=apch_display, inline=True)
if prev:
    prev_display = "\n".join(prev) if isinstance(prev, tuple) else str(prev)
    embed.add_field(name="前回", value=prev_display, inline=True)
```

**Step 3: テスト実行**

```bash
cd <HOME>/claude/vatjpn-discord-bot && python3 -m pytest tests/ -v
```
Expected: ALL PASS

**Step 4: コミット**

```bash
git add cogs/swim.py
git commit -m "APCH通知Embed: 全進入方式+使用滑走路をまとめて表示"
```

---

### Task 8: デプロイ・動作確認

**Step 1: git push**

```bash
cd <HOME>/claude/vatjpn-discord-bot && git push
```

**Step 2: NASに転送・再起動**

```bash
scp <HOME>/claude/vatjpn-discord-bot/cogs/swim.py nas:<NAS_DATA_PATH>/discord-bot/bot1/cogs/
ssh nas "HOME=/tmp DOCKER_CONFIG=/tmp/.docker <NAS_CONTAINER_STATION>/bin/docker compose -f <NAS_CONTAINER_STATION>/data/application/phase6-bots/docker-compose.yml restart discord-bot-1"
```

**Step 3: ログ確認**

```bash
ssh nas "HOME=/tmp DOCKER_CONFIG=/tmp/.docker <NAS_CONTAINER_STATION>/bin/docker compose -f <NAS_CONTAINER_STATION>/data/application/phase6-bots/docker-compose.yml logs discord-bot-1 --tail 20"
```
Expected: `APCH TYPE監視開始` ログが出力される

---

## 実装順序まとめ

| Phase | Task | リポジトリ | 内容 |
|-------|------|-----------|------|
| 1 | 1 | swim-api | パーサーテスト追加（FAIL確認） |
| 1 | 2 | swim-api | パーサー修正 + 呼び出し元修正 |
| 1 | 3 | swim-api | DBモデル・レスポンスモデル拡張 |
| 1 | 4 | swim-api | デプロイ + DBマイグレーション |
| 2 | 5 | discord-bot | baseline判定テスト追加 |
| 2 | 6 | discord-bot | apch_loop を approach_types 対応に修正 |
| 2 | 7 | discord-bot | 通知Embed表示改善 |
| 2 | 8 | discord-bot | デプロイ・動作確認 |
