"""純粋なヘルパー関数のユニットテスト。"""
from unittest.mock import patch
from datetime import datetime, timezone

from vatsim_stat_notify_to_discord import (
    format_duration_seconds,
    get_rating_str,
    check_position_rating,
)
from cogs.swim import (
    parse_time_range,
    is_in_time_range,
    turbulence_level,
    _fl_to_display,
    format_pirep_altitude,
    format_pirep_location,
    parse_pirep_coords,
    SwimCog,
)


# ── parse_time_range ─────────────────────────────────────────────

class TestParseTimeRange:
    def test_valid(self):
        assert parse_time_range("22:00-06:00") == ("22:00", "06:00")

    def test_midnight(self):
        assert parse_time_range("00:00-23:59") == ("00:00", "23:59")

    def test_invalid_format(self):
        assert parse_time_range("2200-0600") is None
        assert parse_time_range("22:00") is None
        assert parse_time_range("") is None
        assert parse_time_range("abc") is None

    def test_invalid_time(self):
        assert parse_time_range("25:00-06:00") is None
        assert parse_time_range("22:00-06:60") is None


# ── is_in_time_range ─────────────────────────────────────────────

def _mock_utc(hour, minute=0):
    """指定したUTC時刻を返すモックを作る。"""
    return datetime(2026, 3, 8, hour, minute, tzinfo=timezone.utc)


class TestIsInTimeRange:
    def test_normal_range_inside(self):
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(10, 30)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("09:00", "12:00") is True

    def test_normal_range_outside(self):
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(13, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("09:00", "12:00") is False

    def test_overnight_range_late_night(self):
        """22:00-06:00 の範囲、23:00 → 範囲内"""
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(23, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("22:00", "06:00") is True

    def test_overnight_range_early_morning(self):
        """22:00-06:00 の範囲、03:00 → 範囲内"""
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(3, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("22:00", "06:00") is True

    def test_overnight_range_daytime(self):
        """22:00-06:00 の範囲、12:00 → 範囲外"""
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("22:00", "06:00") is False

    def test_boundary_start(self):
        """境界: ちょうどstart時刻 → 範囲内"""
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(9, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("09:00", "12:00") is True

    def test_boundary_end(self):
        """境界: ちょうどend時刻 → 範囲外 (end exclusive)"""
        with patch("cogs.swim.datetime") as mock_dt:
            mock_dt.now.return_value = _mock_utc(12, 0)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            assert is_in_time_range("09:00", "12:00") is False


# ── format_duration_seconds ──────────────────────────────────────

class TestFormatDurationSeconds:
    def test_hours_and_minutes(self):
        assert format_duration_seconds(3661) == "1時間01分"

    def test_minutes_only(self):
        assert format_duration_seconds(300) == "5分"

    def test_zero(self):
        assert format_duration_seconds(0) == "0分"

    def test_exact_hour(self):
        assert format_duration_seconds(7200) == "2時間00分"


# ── get_rating_str ───────────────────────────────────────────────

class TestGetRatingStr:
    def test_known_ratings(self):
        assert get_rating_str(1) == "OBS"
        assert get_rating_str(5) == "C1"
        assert get_rating_str(11) == "SUP"
        assert get_rating_str(12) == "ADM"

    def test_unknown(self):
        assert get_rating_str(99) == "Unknown(99)"
        assert get_rating_str(-1) == "Unknown(-1)"


# ── check_position_rating ───────────────────────────────────────

class TestCheckPositionRating:
    def test_sufficient_rating(self):
        assert check_position_rating("RJTT_TWR", 3) is None  # S2 for TWR = OK

    def test_insufficient_rating(self):
        result = check_position_rating("RJTT_CTR", 3)  # S2 for CTR (needs C1=5)
        assert result is not None
        assert "Rating不足" in result

    def test_ojt_skips_check(self):
        """OJT (_T_) は Rating チェックをスキップ"""
        assert check_position_rating("RJTT_T_TWR", 2) is None

    def test_unknown_position(self):
        assert check_position_rating("RJTT_OBS", 1) is None


# ── turbulence_level ─────────────────────────────────────────────

class TestTurbulenceLevel:
    def test_numeric(self):
        assert turbulence_level("4") == 4
        assert turbulence_level("6") == 6

    def test_text(self):
        assert turbulence_level("MODERATE") == 4
        assert turbulence_level("SEVERE") == 6

    def test_case_insensitive(self):
        assert turbulence_level("moderate") == 4

    def test_none_and_empty(self):
        assert turbulence_level(None) is None
        assert turbulence_level("") is None

    def test_unknown_text(self):
        assert turbulence_level("LIGHT") is None


# ── _fl_to_display ───────────────────────────────────────────────

class TestFlToDisplay:
    def test_above_transition(self):
        assert _fl_to_display(350) == "FL350"
        assert _fl_to_display(140) == "FL140"

    def test_below_transition(self):
        assert _fl_to_display(100) == "10,000ft"
        assert _fl_to_display(50) == "5,000ft"

    def test_zero(self):
        assert _fl_to_display(0) == "0ft"


# ── format_pirep_altitude ────────────────────────────────────────

class TestFormatPirepAltitude:
    def test_body_single_fl(self):
        pirep = {"body": "UA /OV RJTT /F350 /TP B777"}
        assert format_pirep_altitude(pirep) == "FL350"

    def test_body_range(self):
        pirep = {"body": "UA /OV RJTT /F000-050 /TP C172"}
        assert format_pirep_altitude(pirep) == "0ft - 5,000ft"

    def test_body_range_high(self):
        pirep = {"body": "UA /OV RJTT /F300-400 /TP B787"}
        assert format_pirep_altitude(pirep) == "FL300 - FL400"

    def test_fallback_to_altitude_field(self):
        pirep = {"body": "UA no altitude info", "altitude": "350", "altitude_indicator": "F"}
        assert format_pirep_altitude(pirep) == "FL350"

    def test_fallback_below_transition(self):
        pirep = {"body": "UA no altitude info", "altitude": "50", "altitude_indicator": "F"}
        assert format_pirep_altitude(pirep) == "5,000ft"

    def test_no_altitude(self):
        pirep = {"body": "UA no info"}
        assert format_pirep_altitude(pirep) == "不明"


# ── format_pirep_location ────────────────────────────────────────

class TestFormatPirepLocation:
    def test_valid(self):
        pirep = {"latitude": "3530", "longitude": "13945"}
        assert format_pirep_location(pirep) == "N35°30' E139°45'"

    def test_missing(self):
        assert format_pirep_location({}) == "不明"
        assert format_pirep_location({"latitude": "3530"}) == "不明"


# ── parse_pirep_coords ───────────────────────────────────────────

class TestParsePirepCoords:
    def test_valid(self):
        pirep = {"latitude": "3530", "longitude": "13945"}
        lat, lon = parse_pirep_coords(pirep)
        assert abs(lat - 35.5) < 0.01
        assert abs(lon - 139.75) < 0.01

    def test_missing(self):
        assert parse_pirep_coords({}) is None

    def test_invalid(self):
        assert parse_pirep_coords({"latitude": "abc", "longitude": "def"}) is None


# ── _apch_matches_baseline ──────────────────────────────────────

class TestApchMatchesBaseline:
    def test_partial_match(self):
        assert SwimCog._apch_matches_baseline("ILS Y RWY34L", "ILS") is True

    def test_no_match(self):
        assert SwimCog._apch_matches_baseline("RNAV RWY22", "ILS") is False

    def test_case_insensitive(self):
        assert SwimCog._apch_matches_baseline("ils y rwy34l", "ILS") is True

    def test_exact_match(self):
        assert SwimCog._apch_matches_baseline("ILS Y RWY34L", "ILS Y RWY34L") is True

    def test_wildcard_always_false(self):
        """watchモード ('*') は常にFalse（全変化を通知）"""
        assert SwimCog._apch_matches_baseline("ILS Y RWY34L", "*") is False
        assert SwimCog._apch_matches_baseline("RNAV RWY22", "*") is False

    def test_multi_approach_any_match(self):
        """approach_types配列のいずれかにマッチすればTrue"""
        approach_types = ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
        assert any(SwimCog._apch_matches_baseline(a, "ILS") for a in approach_types) is True
        assert any(SwimCog._apch_matches_baseline(a, "VISUAL") for a in approach_types) is True
        assert any(SwimCog._apch_matches_baseline(a, "RNAV") for a in approach_types) is False


# ── _baseline_matches_approaches ────────────────────────────────

class TestBaselineMatchesApproaches:
    def test_single_baseline_match(self):
        """単一baselineがapproach_typesのいずれかにマッチ"""
        assert SwimCog._baseline_matches_approaches("ILS", ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]) is True

    def test_single_baseline_no_match(self):
        assert SwimCog._baseline_matches_approaches("RNAV", ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]) is False

    def test_set_baseline_all_match(self):
        """セット条件: 全サブ条件がそれぞれマッチ"""
        assert SwimCog._baseline_matches_approaches(
            "ILS X RWY34L + HIGHWAY VISUAL RWY34R",
            ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
        ) is True

    def test_set_baseline_partial_match(self):
        """セット条件: 片方のみマッチ → False"""
        assert SwimCog._baseline_matches_approaches(
            "ILS X RWY34L + HIGHWAY VISUAL RWY34R",
            ["ILS X RWY34L"]
        ) is False

    def test_set_baseline_partial_string(self):
        """セット条件: 部分一致でもOK"""
        assert SwimCog._baseline_matches_approaches(
            "ILS + HIGHWAY",
            ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
        ) is True

    def test_set_baseline_wildcard(self):
        """ワイルドカードは常にFalse"""
        assert SwimCog._baseline_matches_approaches("*", ["ILS X RWY34L"]) is False

    def test_set_baseline_case_insensitive(self):
        assert SwimCog._baseline_matches_approaches(
            "ils x rwy34l + highway visual rwy34r",
            ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
        ) is True

    def test_or_between_baselines(self):
        """複数baseline(OR): セットと単一の混在"""
        baselines = ["ILS X RWY34L + HIGHWAY VISUAL RWY34R", "RNP RWY16L"]
        approaches_set = ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]
        approaches_single = ["RNP RWY16L"]
        approaches_none = ["RNAV RWY22"]
        # セット条件にマッチ
        assert any(SwimCog._baseline_matches_approaches(bl, approaches_set) for bl in baselines) is True
        # 単一条件にマッチ
        assert any(SwimCog._baseline_matches_approaches(bl, approaches_single) for bl in baselines) is True
        # どちらにもマッチしない
        assert any(SwimCog._baseline_matches_approaches(bl, approaches_none) for bl in baselines) is False

    def test_rwy_match(self):
        """rwy条件: approach_types一致 + runway_in_use一致"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy="07", runway_in_use="RWY 07") is True

    def test_rwy_no_match(self):
        """rwy条件: approach_types一致 + runway_in_use不一致 → False"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy="07", runway_in_use="RWY 25") is False

    def test_rwy_multi_runway(self):
        """rwy条件: 複数滑走路の一つに一致"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy="16L", runway_in_use="RWY 16L/16R") is True

    def test_rwy_multi_runway_no_match(self):
        """rwy条件: 複数滑走路のどれにも不一致"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy="34R", runway_in_use="RWY 16L/16R") is False

    def test_rwy_strict_match(self):
        """rwy条件: 16と16Lは別物（厳密一致）"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy="16", runway_in_use="RWY 16L") is False

    def test_rwy_none_ignores_runway(self):
        """rwy=None: runway_in_useを無視（既存動作）"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy=None, runway_in_use="RWY 25") is True

    def test_rwy_approach_no_match(self):
        """rwy条件: approach_types不一致なら rwy一致でもFalse"""
        assert SwimCog._baseline_matches_approaches(
            "ILS", ["VISUAL"], rwy="07", runway_in_use="RWY 07") is False

    def test_rwy_case_insensitive(self):
        """rwy条件: 大文字小文字は無関係"""
        assert SwimCog._baseline_matches_approaches(
            "VISUAL", ["VISUAL"], rwy="34r", runway_in_use="RWY 34R") is True

    def test_rwy_with_set_baseline(self):
        """rwy条件 + セット条件の組み合わせ"""
        assert SwimCog._baseline_matches_approaches(
            "ILS + VISUAL", ["ILS RWY07", "VISUAL"], rwy="07", runway_in_use="RWY 07") is True
        assert SwimCog._baseline_matches_approaches(
            "ILS + VISUAL", ["ILS RWY07", "VISUAL"], rwy="07", runway_in_use="RWY 25") is False


# ── _parse_runway_in_use ──────────────────────────────────────

class TestParseRunwayInUse:
    def test_single_runway(self):
        assert SwimCog._parse_runway_in_use("RWY 07") == ["07"]

    def test_single_runway_with_side(self):
        assert SwimCog._parse_runway_in_use("RWY 34R") == ["34R"]

    def test_dual_runway(self):
        assert SwimCog._parse_runway_in_use("RWY 16L/16R") == ["16L", "16R"]

    def test_mixed_runway(self):
        assert SwimCog._parse_runway_in_use("RWY 34L/34R") == ["34L", "34R"]

    def test_empty(self):
        assert SwimCog._parse_runway_in_use("") == []

    def test_none(self):
        assert SwimCog._parse_runway_in_use(None) == []


# ── _get_approach_types ─────────────────────────────────────────

class TestGetApproachTypes:
    def test_approach_types_present(self):
        """approach_types配列がある場合はそのまま返す"""
        rwy = {"approach_types": ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]}
        assert SwimCog._get_approach_types(rwy) == ["ILS X RWY34L", "HIGHWAY VISUAL RWY34R"]

    def test_approach_types_none(self):
        """approach_typesがNoneの場合は空リスト"""
        rwy = {"approach_types": None}
        assert SwimCog._get_approach_types(rwy) == []

    def test_approach_types_empty_list(self):
        """approach_typesが空リストの場合は空リスト"""
        rwy = {"approach_types": []}
        assert SwimCog._get_approach_types(rwy) == []

    def test_no_fields(self):
        """フィールドがない場合は空リスト"""
        assert SwimCog._get_approach_types({}) == []
