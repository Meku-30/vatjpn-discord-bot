# CLAUDE.md — VATJPN Discord Bot

VATSIM 日本空域の管制官オンライン/オフライン状況を Discord に通知する Bot。

親ワークスペース `<HOME>/claude/CLAUDE.md` の作業ルールを継承する。

## 公開設定

⚠️ **このリポジトリは Public** (`Meku-30/vatjpn-discord-bot`, GPL-3.0)

以下を**絶対にコミットしない**。

- Discord Bot トークン、Webhook URL
- 自宅の内部 IP (192.168.x.x)、NAS のパス、ホスト名

トークンは `.env` の `DISCORD_BOT_TOKEN` で管理する。コミット前に差分を必ず確認すること。

## デプロイ

- **デプロイ先**: NAS `<NAS_DATA_PATH>/discord-bot/bot1`
- **Docker Compose**: `<NAS_CONTAINER_STATION>/data/application/phase6-bots/`
- **稼働コンテナ**: `discord_bot_1`, `discord_bot_2`

## 機能

- ログイン / ログアウト通知、接続時間表示、CID ニックネーム管理
- **スラッシュコマンド**: `/online`, `/nickname add/remove/list`

## 由来

元リポジトリ: [lancard/vatsim_stat_notify_to_discord](https://github.com/lancard/vatsim_stat_notify_to_discord)
