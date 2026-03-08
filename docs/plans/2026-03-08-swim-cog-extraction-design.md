# SWIM機能のCog分離設計書

## 概要

SWIM非公式API関連の全機能（コマンド・自動通知ループ・ヘルパー）を discord.py の Cog として分離し、SWIM環境変数未設定時にはコマンドも含めて完全に無効化する。

## アーキテクチャ

```
vatsim_stat_notify_to_discord.py  (メイン, ~920行)
├── 設定・初期化・DB (init_db, get_db)
├── VATJPNBot クラス (ポーリングループのみ)
├── VATSIM APIヘルパー (get_new, get_controllers, キャッシュ)
├── フォーマッター (format_duration, format_online_entry 等)
├── OJT/Rating検証
├── /online, /sup, /traffic, /stats, /mystats, /nickname
└── setup_hook で SwimCog を条件付きロード

cogs/
└── swim.py  (SwimCog, ~1020行)
    ├── SWIM API共通ヘルパー (_swim_request, _get_swim_headers)
    ├── APCH DB関数 (apch_set_channel, apch_add_watch 等)
    ├── 時間帯判定 (parse_time_range, is_in_time_range)
    ├── NOTAM/ATIS/METAR取得・フォーマット
    ├── PIREP取得・フォーマット・マップ生成
    ├── /atis, /metar, /notam コマンド
    ├── /apch コマンドグループ
    ├── PIREPループ (ENABLE_PIREP_NOTIFICATIONS で制御)
    └── APCHループ (ENABLE_APCH_NOTIFICATIONS で制御)
```

## Cogロード条件

```python
# VATJPNBot.setup_hook() 内
if swim_api_url and swim_api_token:
    await bot.add_cog(SwimCog(bot))
```

SWIM環境変数未設定時:
- SwimCog がロードされない
- `/atis`, `/metar`, `/notam`, `/apch` コマンドが Discord 上に表示されない
- PIREP・APCHループも起動しない

## 環境変数

| 変数 | デフォルト | 制御対象 |
|------|----------|---------|
| `SWIM_API_URL` + `SWIM_API_TOKEN` | なし | SwimCog 自体のロード（コマンドの表示/非表示） |
| `ENABLE_NOTIFICATIONS` | `true` | ログイン/ログアウト通知ループ |
| `ENABLE_PIREP_NOTIFICATIONS` | `true` | PIREP タービュランス通知ループ |
| `ENABLE_APCH_NOTIFICATIONS` | `true`（新規） | APCH TYPE 変更通知ループ |

## メインファイルとCogの境界

### メインファイルに残るもの

- 設定読み込み・バリデーション (settings.ini, 環境変数)
- DB初期化 (`init_db()`, `get_db()`) — APCHテーブル定義含む
- `VATJPNBot` クラス (ポーリングループ、VATSIMキャッシュ)
- VATSIM系コマンド (/online, /sup, /traffic, /stats, /mystats, /nickname)
- フォーマッター (format_duration, format_online_entry 等)
- OJT/Rating検証 (check_position_rating, fetch_solo_list 等)
- on_ready イベント

### SwimCogに移動するもの

- `_get_swim_headers()`, `_swim_request()`
- `fetch_atis()`, `fetch_all_atis()`, `fetch_metar()`, `fetch_runway_info()`, `fetch_all_runway_info()`
- `fetch_notams()`, `format_notam_page()`, `NotamPaginationView`
- `fetch_active_pireps()`, PIREP関連全関数 (turbulence_level, format_pirep_altitude, generate_pirep_map, build_pirep_embed 等)
- APCH DB関数 (apch_set_channel, apch_add_watch, apch_remove_watch, apch_list_watches, apch_get_all_watches)
- `parse_time_range()`, `is_in_time_range()`
- 全SWIMコマンド (/atis, /metar, /notam, /apch)
- PIREPループ、APCHループ

### Cogからメインを参照するもの

- `get_db()` — DB接続の取得
- `pirep_channel_id` — PIREP通知先チャンネルID

## データベース

変更なし。テーブル構造・ファイルパス・接続方法は全て現状維持。
`init_db()` はメインファイルに残り、起動時にAPCHテーブル含む全テーブルを作成。
SwimCog は `get_db()` で既存の永続接続を共有する。

## テストへの影響

既存58テストのロジック変更は不要。SWIM系関数のインポートパスを `vatsim_stat_notify_to_discord` から `cogs.swim` に書き換えるのみ。

## デプロイ変更

### Docker Compose

```yaml
volumes:
  - ./vatsim_stat_notify_to_discord.py:/app/vatsim_stat_notify_to_discord.py:ro
  - ./cogs/:/app/cogs/:ro  # 追加
```

### デプロイ手順

```bash
scp vatsim_stat_notify_to_discord.py nas:/path/bot1/
scp -r cogs/ nas:/path/bot1/cogs/
# bot2 も同様
docker compose restart
```

初回のみ NAS 側に `cogs/` ディレクトリを作成する必要がある。
