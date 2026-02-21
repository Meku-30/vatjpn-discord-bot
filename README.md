# VATJPN Discord Bot

VATSIM 日本空域の ATC 管制官のオンライン/オフライン状況を Discord チャンネルに通知する Bot。

- VATSIM Data API を15秒間隔でポーリング
- 日本空域 (RJ*, ROAH, OKA, FUK, KOJ, TYO, HDK, SRK, VATJ 等) のコントローラーを監視
- ログイン/ログアウト時に Discord Embed で通知

## How to use

1. `settings.ini.example` を `settings.ini` にコピーし、Discord Channel ID とフィルター設定を編集
2. 環境変数 `DISCORD_BOT_TOKEN` に Discord Bot Token を設定
3. `pip install -r requirements.txt`
4. `python vatsim_stat_notify_to_discord.py`

### Docker Compose

```yaml
services:
  discord-bot:
    image: python:3.11-slim
    working_dir: /app
    volumes:
      - ./:/app
    environment:
      - DISCORD_BOT_TOKEN=${DISCORD_BOT_TOKEN}
    command: sh -c "pip install -r requirements.txt && python vatsim_stat_notify_to_discord.py"
    restart: always
```

## Credits

Based on [lancard/vatsim_stat_notify_to_discord](https://github.com/lancard/vatsim_stat_notify_to_discord) (GPL-3.0).
