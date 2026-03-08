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
