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

    def test_add_with_rwy(self):
        """rwy条件付きの登録"""
        apch_add_watch("g8", "RJFT", "VISUAL", None, None, "user1", rwy="07")
        watches = apch_list_watches("g8")
        assert len(watches) == 1
        assert watches[0][0] == "RJFT"
        assert watches[0][1] == "VISUAL"
        assert watches[0][4] == "07"  # rwyは5番目 (index 4)

    def test_add_with_rwy_none(self):
        """rwy=Noneの登録（既存互換）"""
        apch_add_watch("g9", "RJFT", "VISUAL", None, None, "user1", rwy=None)
        watches = apch_list_watches("g9")
        assert len(watches) == 1
        assert watches[0][4] is None

    def test_rwy_coexists(self):
        """同じbaseline + 異なるrwyは共存"""
        apch_add_watch("g10", "RJFT", "VISUAL", None, None, "user1", rwy="07")
        apch_add_watch("g10", "RJFT", "VISUAL", None, None, "user1", rwy="25")
        watches = apch_list_watches("g10")
        rjft = [w for w in watches if w[0] == "RJFT"]
        assert len(rjft) == 2

    def test_remove_with_rwy(self):
        """rwy条件付き削除"""
        apch_add_watch("g11", "RJFT", "VISUAL", None, None, "user1", rwy="07")
        apch_add_watch("g11", "RJFT", "VISUAL", None, None, "user1", rwy="25")
        count = apch_remove_watch("g11", "RJFT", baseline="VISUAL", rwy="07")
        assert count == 1
        remaining = [w for w in apch_list_watches("g11") if w[0] == "RJFT"]
        assert len(remaining) == 1
        assert remaining[0][4] == "25"


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

    def test_returns_rwy(self):
        """JOINでrwyが取得される"""
        apch_set_channel("grwy", "888")
        apch_add_watch("grwy", "RJFT", "VISUAL", None, None, "user1", rwy="07")
        rows = apch_get_all_watches()
        grwy_rows = [r for r in rows if r[0] == "grwy"]
        assert len(grwy_rows) >= 1
        # rwyは7番目のカラム (index 6)
        assert grwy_rows[0][6] == "07"


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
