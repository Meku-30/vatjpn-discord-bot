# APCH TYPE 変更監視・通知機能 設計書

## 概要

空港ごとに「基準となるAPCH TYPE」を登録しておき、RWY-INFOのAPCH TYPEが基準と異なる場合にDiscordチャンネルへ通知する機能。

## 要件

- サーバー（ギルド）単位で空港ごとの基準APCH TYPEを登録
- Discordコマンドで登録・削除・一覧表示・通知チャンネル設定
- 5分間隔のポーリングでRWY-INFOを監視
- 基準との比較は部分一致（「ILS」登録→ILS以外で通知）またはフルテキスト完全一致
- 時間帯別に異なる基準を設定可能（例: 深夜帯はILS Z、日中はILS Y）
- 時間帯未設定の登録は全時間帯に適用
- 変化時のみ1回通知（同じ状態が続く間は再通知しない、基準復帰時の通知なし）

## データベース設計

`stats.db` に2テーブル追加:

```sql
-- ギルドごとの通知チャンネル設定
CREATE TABLE IF NOT EXISTS apch_config (
    guild_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL
);

-- 監視対象の空港・基準APCH TYPE登録
CREATE TABLE IF NOT EXISTS apch_watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    icao TEXT NOT NULL,
    baseline TEXT NOT NULL,
    time_start TEXT,
    time_end TEXT,
    registered_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_apch_watches_guild_icao
    ON apch_watches(guild_id, icao);
```

### フィールド説明

| フィールド | 説明 | 例 |
|-----------|------|-----|
| `baseline` | 基準APCH TYPE文字列 | `"ILS"`, `"ILS Y RWY34L"` |
| `time_start` | UTC開始時刻 (NULL=全時間帯) | `"22:00"` |
| `time_end` | UTC終了時刻 (NULL=全時間帯) | `"06:00"` |
| `registered_by` | 登録したDiscordユーザーID | `"123456789"` |

- `baseline` が短い文字列（例: `"ILS"`）→ `approach_type` に含まれるかで判定（部分一致）
- `baseline` がフルテキスト（例: `"ILS Y RWY34L"`）→ 同様に部分一致だが事実上完全一致に近い
- 同じ `guild_id + icao` に複数レコード可（時間帯別の基準登録）

## コマンド設計

コマンドグループ: `/apch`

### `/apch set <icao> <baseline> [time_range]`

基準APCH TYPEを登録。

```
/apch set RJTT ILS              → 全時間帯でILSを基準に設定
/apch set RJTT "ILS Z" 22:00-06:00  → 22:00-06:00 UTCのみILS Zを基準に設定
```

- `time_range` はオプション、`HH:MM-HH:MM` 形式（UTC）
- `setchannel` 未設定の場合はエラー「先に /apch setchannel で通知先を設定してください」

### `/apch remove <icao> [time_range]`

登録を削除。

```
/apch remove RJTT               → RJTTの全登録を削除
/apch remove RJTT 22:00-06:00   → 該当時間帯の登録のみ削除
```

### `/apch list`

そのギルドの全登録一覧を表示。

```
RJTT: "ILS" (全時間帯)
RJTT: "ILS Z" (22:00-06:00 UTC)
RJAA: "ILS" (全時間帯)
通知先: #apch-alerts
```

### `/apch setchannel <channel>`

通知先チャンネルを設定。

```
/apch setchannel #apch-alerts
```

## 監視ロジック

PIREP通知と同じ `@tasks.loop(seconds=300)` パターン。

### ポーリングフロー

```
apch_loop (5分間隔):
  1. apch_watches から全登録を取得
  2. guild_id × icao でグルーピング（APIコール数を最小化）
  3. 各空港の RWY-INFO を fetch_runway_info() で取得
  4. 各登録について:
     a. 時間帯チェック: 現在UTC時刻が time_start〜time_end 内か判定
        - 日跨ぎ対応: 22:00-06:00 → (22:00<=now OR now<06:00)
        - 時間帯設定ありで範囲外 → スキップ
     b. approach_type と baseline を比較:
        - baseline.upper() in approach_type.upper() → 基準通り、何もしない
        - 含まれない → 通知対象
     c. 前回と同じ approach_type を既に通知済み → スキップ
  5. 通知対象があればチャンネルに Embed を送信
```

### 重複通知の防止

メモリ上に辞書を保持:

```python
self.apch_last_notified: dict[tuple[str, str], str] = {}
# key: (guild_id, icao) → value: 前回通知した approach_type
```

- 同じ `approach_type` が続く間は再通知しない
- 基準に戻ったら辞書からクリア（次回の変化で再通知可能）

### 初回起動

`_apch_first_run = True` フラグで初回は現在状態をキャッシュするのみ（通知しない）。

## 通知Embed

```
⚠️ APCH TYPE 変更 — RJTT
━━━━━━━━━━━━━━━━━━
現在: RNAV RWY22
基準: ILS (22:00-06:00 UTC)
使用滑走路: 22
━━━━━━━━━━━━━━━━━━
観測: 2026-03-08 13:02Z
```

- 色: オレンジ (`0xFF9900`)
- 時間帯が全時間帯の場合は「基準: ILS」のみ（時間帯表記なし）
- RWY-INFO取得失敗時は通知せず `logger.warning` のみ

## データソース

- SWIM非公式API `/api/runway-info/{icao}` から取得
- RWY-INFOはATIS発行空港を含む約49空港をカバー
- SWIM側は毎時 :02, :32 に更新（Bot側は5分間隔ポーリング）

## ストレージ方式

SQLite（`stats.db`）— 既存の `user_links` テーブルと同じパターン。`init_db()` にテーブル作成を追加。
