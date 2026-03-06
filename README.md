# VATJPN Discord Bot

VATSIM 日本空域の ATC 管制官のオンライン/オフライン状況を Discord チャンネルに通知する Bot。

- VATSIM Data API を15秒間隔で非同期ポーリング (aiohttp)
- 日本空域 (RJ*, ROAH, OKA, FUK, KOJ, TYO, HDK, SRK, VATJ 等) のコントローラーを監視
- ログイン/ログアウト時に Discord Embed で通知
- 起動時の設定ファイルバリデーション

## スラッシュコマンド / Slash Commands

| コマンド | 説明 |
|---------|------|
| `/online` | 日本空域のオンライン管制官を一覧表示 |
| `/sup` | オンラインの Supervisor (SUP/ADM) 一覧を表示 |
| `/notam <icao> [keyword]` | 空港の有効な NOTAM を表示（`japan` で主要空港一括サマリー、`keyword` でフィルター可能、ページネーション対応） |
| `/atis <icao>` | 空港の ATIS 情報を表示（`japan` で主要空港一括表示） |
| `/metar <icao>` | 空港の METAR 情報を表示 |
| `/traffic <icao>` | 指定空港の発着予定トラフィック一覧（出発・到着・プリファイル） |
| `/stats [days:<日数>] [position:<ポジション>]` | 管制統計を表示（日数: 0=全期間、デフォルト7日、ポジション: 部分一致フィルター） |
| `/mystats link <cid>` | Discord ID と VATSIM CID を紐付け |
| `/mystats unlink` | CID の紐付けを解除 |
| `/mystats show` | 自分の管制統計を表示（要 link） |
| `/mystats user <cid>` | 指定 CID の管制統計を表示 |
| `/nickname add <cid> <name>` | VATSIM CID にニックネームを登録 |
| `/nickname remove <cid>` | ニックネームを削除 |
| `/nickname list` | 登録済みニックネーム一覧 |

## 機能 / Features

- **ログイン/ログアウト通知** - 管制官の接続・切断時に Embed メッセージで自動通知
- **接続時間表示** - ログイン/ログアウト通知に管制官の接続時間を表示
- **CID ニックネーム** - VATSIM CID にフレンドリーな名前を紐付け、通知や `/online` で表示
- **管制統計** - ログアウト時にセッションを SQLite に記録し、`/stats` で期間別・ポジション別の統計を表示
- **Supervisor 一覧** - VATSIM ネットワーク全体のオンライン SUP/ADM を表示
- **NOTAM 表示** - SWIM非公式APIから日本空域の有効な NOTAM を取得・表示。`/notam japan` で主要6空港の一括サマリー、`keyword` オプションでキーワード絞り込み（例: RWY, ILS, TWY）、5件ずつのページネーションボタン対応
- **ATIS 表示** - SWIM非公式APIから空港の最新 ATIS 情報を取得・表示。`/atis japan` で全ATIS発行空港（23空港）を一括表示
- **METAR 表示** - SWIM非公式APIから空港の最新 METAR を取得・表示
- **PIREP タービュランス通知** - SWIM非公式APIからPIREPを5分間隔で自動取得し、MOD（Moderate）以上のタービュランスを検知した場合に自動通知。`ENABLE_PIREP_NOTIFICATIONS` 環境変数でオン/オフ切り替え可能
- **空港トラフィック** - ICAO コードで空港を指定し、出発・到着・プリファイル済みフライトを一覧表示
- **個人管制統計** - Discord ID と VATSIM CID を紐付けて個人の管制統計を表示。VATSIM API から総管制時間・レーティング情報も取得

## セットアップ / Setup

### 1. Bot Token の設定

[Discord Developer Portal](https://discord.com/developers/applications) で Bot を作成し、Token を取得する。

**環境変数で設定（推奨）:**
```bash
export DISCORD_BOT_TOKEN="your-bot-token-here"
```

**Docker (docker-compose) の場合:**
`.env` ファイルに記載する（docker-compose.yml と同じディレクトリに配置）:
```
DISCORD_BOT_TOKEN=your-bot-token-here

# NOTAM/ATIS/METAR/PIREP機能（オプション: SWIM非公式APIが必要）
SWIM_API_URL=http://swim-api:8000
SWIM_API_TOKEN=your-swim-api-token

# PIREP タービュランス自動通知（デフォルト: true）
ENABLE_PIREP_NOTIFICATIONS=true
```

### 2. settings.ini の設定

`settings.ini.example` をコピーして `settings.ini` を作成:
```bash
cp settings.ini.example settings.ini
```

`discord_channel_id` に通知先の Discord チャンネル ID を設定する。

### 3. 起動

```bash
# ローカル実行
pip install -r requirements.txt
python vatsim_stat_notify_to_discord.py

# バックグラウンド実行
nohup python vatsim_stat_notify_to_discord.py &
```

### Docker Compose

```yaml
services:
  discord-bot:
    build: .
    volumes:
      - ./settings.ini:/app/settings.ini:ro
      - ./data.json:/app/data.json
      - ./nicknames.json:/app/nicknames.json
      - ./stats.db:/app/stats.db
    environment:
      - DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN}
      - SWIM_API_URL=${SWIM_API_URL}
      - SWIM_API_TOKEN=${SWIM_API_TOKEN}
    restart: always
```

## Credits

Based on [lancard/vatsim_stat_notify_to_discord](https://github.com/lancard/vatsim_stat_notify_to_discord) (GPL-3.0).

## License

This project is licensed under the GNU General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
