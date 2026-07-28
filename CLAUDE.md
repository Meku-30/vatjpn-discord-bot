# CLAUDE.md — VATJPN Discord Bot

VATSIM 日本空域の管制官オンライン/オフライン状況を Discord に通知する Bot。

このリポジトリの親ワークスペース (ローカル環境) の `CLAUDE.md` に定義された作業ルールを継承する。

## 公開設定

⚠️ **このリポジトリは Public** (`Meku-30/vatjpn-discord-bot`, GPL-3.0)

以下を**絶対にコミットしない**。

- Discord Bot トークン、Webhook URL
- 自宅の内部 IP (192.168.x.x)、NAS のパス、ホスト名

トークンは `.env` の `DISCORD_BOT_TOKEN` で管理する。コミット前に差分を必ず確認すること。

## デプロイ

自宅 NAS 上の Docker で稼働 (コンテナ 2 台構成)。

**具体的なデプロイパス・ホスト情報はこの Public リポジトリには書かない。**
プライベートな `homelab/NAS-SERVICES.md` を参照すること。

## 機能

- ログイン / ログアウト通知、接続時間表示、CID ニックネーム管理
- **スラッシュコマンド**: `/online`, `/nickname add/remove/list`

## 由来

元リポジトリ: [lancard/vatsim_stat_notify_to_discord](https://github.com/lancard/vatsim_stat_notify_to_discord)
