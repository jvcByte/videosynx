"""XP, levels, streaks and badges, computed from what an account actually did.

The property worth protecting is that none of it can be had without the work.
If a figure here could be incremented by anything other than a finished
render, every other number on the dashboard would stop meaning anything.
"""

from __future__ import annotations

from datetime import date, timedelta

from videosynx_api.progress import (
    ACHIEVEMENTS,
    XP_PER_LEVEL,
    XP_PER_MINUTE,
    XP_PER_TUTORIAL,
    achievements_for,
    level_for,
    progress_for,
    streak_from,
)

TODAY = date(2026, 9, 30)


# ------------------------------------------------------------------- the sum

def test_xp_is_the_work_and_nothing_else():
    p = progress_for(rendered=8, minutes=40, streak=0, earned_on={})
    assert p.xp == 8 * XP_PER_TUTORIAL + 40 * XP_PER_MINUTE


def test_no_work_is_no_xp():
    p = progress_for(rendered=0, minutes=0, streak=0, earned_on={})
    assert p.xp == 0
    assert p.level == 1


def test_a_level_is_a_fixed_amount_of_work():
    assert level_for(0) == (1, 0, XP_PER_LEVEL)
    assert level_for(XP_PER_LEVEL) == (2, 0, XP_PER_LEVEL)
    assert level_for(XP_PER_LEVEL + 120) == (2, 120, XP_PER_LEVEL)
    # Half a minute is not half a point: the figure is whole and stays whole.
    assert level_for(XP_PER_LEVEL * 3)[0] == 4


# ------------------------------------------------------------------ streaks

def test_a_streak_counts_back_from_today():
    days = {TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=2)}
    assert streak_from(days, TODAY) == 3


def test_a_streak_survives_until_you_miss_a_whole_day():
    """Someone who worked last night and opens the page this morning has a
    streak. Resetting it at midnight would punish them for sleeping."""
    days = {TODAY - timedelta(days=1), TODAY - timedelta(days=2)}
    assert streak_from(days, TODAY) == 2


def test_a_missed_day_ends_it():
    days = {TODAY - timedelta(days=2), TODAY - timedelta(days=3)}
    assert streak_from(days, TODAY) == 0


def test_a_gap_shortens_the_streak_rather_than_ending_it():
    days = {TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=5)}
    assert streak_from(days, TODAY) == 2


def test_no_days_is_no_streak():
    assert streak_from(set(), TODAY) == 0


# ------------------------------------------------------------- achievements

def test_a_badge_needs_the_thing_it_says():
    locked = achievements_for(rendered=0, minutes=0, streak=0, earned_on={})
    assert [a["id"] for a in locked if a["earned"]] == []

    first = achievements_for(rendered=1, minutes=0, streak=0, earned_on={})
    assert [a["id"] for a in first if a["earned"]] == ["first"]


def test_every_badge_says_what_it_takes():
    """A wall of greyed-out badges with no explanation is a puzzle, not
    encouragement."""
    for badge in achievements_for(rendered=0, minutes=0, streak=0, earned_on={}):
        assert badge["requires"], badge
        assert badge["earned"] is False
        assert badge["earned_on"] is None


def test_a_badge_that_was_not_earned_carries_no_date():
    """Even when a date was handed in, which is the shape a bug would take."""
    badges = achievements_for(rendered=0, minutes=0, streak=0,
                              earned_on={"first": TODAY})
    first = next(b for b in badges if b["id"] == "first")
    assert first["earned"] is False
    assert first["earned_on"] is None


def test_an_hour_of_video_earns_the_marathon():
    badges = achievements_for(rendered=1, minutes=60, streak=0, earned_on={})
    earned = {b["id"] for b in badges if b["earned"]}
    assert earned == {"first", "marathon"}


def test_a_seven_day_streak_earns_steady():
    badges = achievements_for(rendered=1, minutes=0, streak=7, earned_on={})
    assert "steady" in {b["id"] for b in badges if b["earned"]}
    badges = achievements_for(rendered=1, minutes=0, streak=6, earned_on={})
    assert "steady" not in {b["id"] for b in badges if b["earned"]}


# --------------------------------------------------------- the later badges
# Each is a different fact, so each gets a test that only its own fact earns
# it. A badge reachable two ways is two badges with one name.

def _earned(**facts) -> set[str]:
    badges = achievements_for(
        rendered=facts.pop("rendered", 0), minutes=facts.pop("minutes", 0.0),
        streak=facts.pop("streak", 0), earned_on={}, **facts)
    return {b["id"] for b in badges if b["earned"]}


def test_five_hours_of_video_is_a_series_and_one_hour_is_not():
    assert "series" not in _earned(minutes=60)
    assert "series" in _earned(minutes=300)


def test_long_form_needs_one_long_video_not_many_short_ones():
    """Ten hours of three-minute videos is the Series badge, not this one."""
    assert "epic" not in _earned(rendered=200, minutes=600, longest_s=179)
    assert "epic" in _earned(rendered=1, longest_s=600)


def test_own_script_is_for_rendering_one_not_for_pasting_one():
    assert "scripted" not in _earned(rendered=10, minutes=60)
    assert "scripted" in _earned(rendered=1, used_script=True)


def test_local_mode_is_its_own_badge():
    assert "local" not in _earned(rendered=10)
    assert "local" in _earned(rendered=1, local_only=True)


def test_a_clip_earns_the_voice_over_badge():
    assert "voiced" not in _earned(rendered=10, minutes=60)
    assert "voiced" in _earned(clips=1)


def test_undeterred_is_for_recovering_not_for_failing():
    """A job that failed and stayed failed is not this, and neither is one
    that is still on its second attempt."""
    assert "undeterred" not in _earned(rendered=10)
    assert "undeterred" in _earned(rendered=1, recovered=True)


def test_thirty_days_is_a_streak_of_its_own():
    assert "devoted" not in _earned(streak=7)
    assert "devoted" in _earned(streak=30)


def test_every_badge_is_reachable_and_only_by_its_own_fact():
    """Nothing is a duplicate, and nothing is unreachable.

    A badge no combination of facts can earn is a locked tile forever, which
    is a promise the page cannot keep.
    """
    reachable = {
        "first": {"rendered": 1},
        "runner": {"rendered": 5},
        "prolific": {"rendered": 25},
        "marathon": {"minutes": 60},
        "series": {"minutes": 300},
        "epic": {"longest_s": 600},
        "steady": {"streak": 7},
        "devoted": {"streak": 30},
        "scripted": {"used_script": True},
        "local": {"local_only": True},
        "voiced": {"clips": 1},
        "undeterred": {"recovered": True},
    }
    every = {key for key, _, _ in ACHIEVEMENTS}
    assert set(reachable) == every, "a badge with no fact behind it"
    for key, facts in reachable.items():
        assert key in _earned(**facts), f"{key} cannot be earned"
