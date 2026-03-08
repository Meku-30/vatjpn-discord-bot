"""APCH監視関連のデータベースヘルパーのテスト。

conftest.py が一時ディレクトリに stats.db を作成するため、
テストごとに独立したDB状態で検証できる。
"""
from vatsim_stat_notify_to_discord import (
    log_session,
    get_controller_stats,
)
from cogs.swim import (
    apch_set_channel,
    apch_get_channel,
    apch_add_watch,
    apch_remove_watch,
    apch_list_watches,
    apch_get_all_watches,
)


class TestApchChannel:
    def test_set_and_get(self):
        apch_set_channel("guild1", "123456")
        assert apch_get_channel("guild1") == 123456

    def test_overwrite(self):
        apch_set_channel("guild2", "100")
        apch_set_channel("guild2", "200")
        assert apch_get_channel("guild2") == 200

    def test_not_found(self):
        assert apch_get_channel("nonexistent") is None


class TestApchWatch:
    def test_add_and_list(self):
        apch_add_watch("g1", "RJTT", "ILS", None, None, "user1")
        watches = apch_list_watches("g1")
        assert len(watches) >= 1
        found = [w for w in watches if w[0] == "RJTT" and w[1] == "ILS"]
        assert len(found) == 1

    def test_icao_uppercased(self):
        apch_add_watch("g2", "rjaa", "ILS", None, None, "user1")
        watches = apch_list_watches("g2")
        icaos = [w[0] for w in watches]
        assert "RJAA" in icaos
        assert "rjaa" not in icaos

    def test_multiple_baselines(self):
        """同一空港に複数baseline登録（OR条件）"""
        apch_add_watch("g3", "RJBB", "ILS Y", None, None, "user1")
        apch_add_watch("g3", "RJBB", "ILS Z", None, None, "user1")
        watches = apch_list_watches("g3")
        rjbb = [w for w in watches if w[0] == "RJBB"]
        assert len(rjbb) == 2

    def test_time_range(self):
        apch_add_watch("g4", "RJOO", "ILS", "22:00", "06:00", "user1")
        watches = apch_list_watches("g4")
        rjoo = [w for w in watches if w[0] == "RJOO"]
        assert rjoo[0][2] == "22:00"  # time_start
        assert rjoo[0][3] == "06:00"  # time_end

    def test_remove_all(self):
        apch_add_watch("g5", "RJFF", "ILS", None, None, "user1")
        apch_add_watch("g5", "RJFF", "RNAV", None, None, "user1")
        count = apch_remove_watch("g5", "RJFF")
        assert count == 2
        assert apch_list_watches("g5") == [] or all(w[0] != "RJFF" for w in apch_list_watches("g5"))

    def test_remove_by_baseline(self):
        apch_add_watch("g6", "RJCC", "ILS", None, None, "user1")
        apch_add_watch("g6", "RJCC", "RNAV", None, None, "user1")
        count = apch_remove_watch("g6", "RJCC", baseline="ILS")
        assert count == 1
        remaining = [w for w in apch_list_watches("g6") if w[0] == "RJCC"]
        assert len(remaining) == 1
        assert remaining[0][1] == "RNAV"

    def test_remove_nonexistent(self):
        count = apch_remove_watch("g_none", "XXXX")
        assert count == 0

    def test_global_watch(self):
        """グローバルwatch: icao='*', baseline='*'"""
        apch_add_watch("g7", "*", "*", None, None, "user1")
        watches = apch_list_watches("g7")
        assert any(w[0] == "*" and w[1] == "*" for w in watches)


class TestApchGetAllWatches:
    def test_returns_channel_id(self):
        """JOINでchannel_idが取得される"""
        apch_set_channel("gall", "999")
        apch_add_watch("gall", "RJTT", "ILS", None, None, "user1")
        rows = apch_get_all_watches()
        gall_rows = [r for r in rows if r[0] == "gall"]
        assert len(gall_rows) >= 1
        # channel_id は6番目のカラム (index 5)
        assert gall_rows[0][5] == "999"


class TestSessionLogging:
    def test_log_and_retrieve(self):
        atc_info = {
            "cid": 9999999,
            "callsign": "RJTT_TWR",
            "rating": 5,
            "logon_time": "2026-03-08T10:00:00Z",
        }
        log_session(atc_info)
        stats = get_controller_stats(9999999)
        assert stats is not None
        assert stats["total_sessions"] >= 1
