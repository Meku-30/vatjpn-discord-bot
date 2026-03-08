"""pytest conftest: テスト用の一時環境を構築してからモジュールをインポート可能にする。"""
import os
import sys
import tempfile
import shutil

# テスト用の一時ディレクトリを作成し、settings.ini と必要ファイルを配置
_test_dir = tempfile.mkdtemp(prefix="vatjpn_test_")

_settings_ini = os.path.join(_test_dir, "settings.ini")
with open(_settings_ini, "w") as f:
    f.write("""[VATSIM_CONFIG]
vatsim_stat_json_url=https://data.vatsim.net/v3/vatsim-data.json
vatsim_stat_retrieve_period=15
vatsim_controller_callsign_filter_regex=(^RJ|^ROAH)[A-Za-z_]+$

[DISCORD_CONFIG]
discord_channel_id=123456789

[DATAFILE_CONFIG]
data_filename=data.json
nickname_filename=nicknames.json
stats_db_filename=stats.db
""")

# data.json (空の管制官マップ)
with open(os.path.join(_test_dir, "data.json"), "w") as f:
    f.write("{}")

# 環境変数を設定
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-for-pytest")
os.environ["ENABLE_NOTIFICATIONS"] = "false"
os.environ["ENABLE_PIREP_NOTIFICATIONS"] = "false"

# モジュールが settings.ini を CWD から読むため、一時ディレクトリに移動
_original_cwd = os.getcwd()
os.chdir(_test_dir)

# 元のプロジェクトディレクトリをPythonパスに追加（モジュールインポート用）
if _original_cwd not in sys.path:
    sys.path.insert(0, _original_cwd)


def pytest_sessionfinish(session, exitstatus):
    """テスト終了後にCWD復帰と一時ディレクトリ削除。"""
    os.chdir(_original_cwd)
    shutil.rmtree(_test_dir, ignore_errors=True)
