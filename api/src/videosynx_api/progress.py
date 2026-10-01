"""What an account has done, as numbers it can feel good about.

Every figure here is computed from rows that already exist -- jobs it
completed, minutes of video it produced, the days it turned up. Nothing is
stored, nothing is awarded, and there is no way for a number to go up without
the work having happened.

That is the whole design constraint. A dashboard of XP is either a measurement
or a lie, and the moment one of these can be incremented by anything other
than a finished render, every other number on the page stops meaning anything.
The mockup this was built from showed 1,280 XP, a five-day streak, and a
leaderboard of other people; the first two are things an account can actually
have, and the third needs users who do not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# What a completed tutorial is worth. Round numbers on purpose: an account
# should be able to look at its own dashboard and work out where the figure
# came from without opening the source.
XP_PER_TUTORIAL = 100
XP_PER_MINUTE = 10
XP_PER_LEVEL = 500

# How hard a badge is to get. This is the only thing its colour says, so it
# has to be a fact about the badge rather than decoration -- which is why it
# is here, next to the thing that decides it, and not in the stylesheet.
#
# Four metals, because a medal has a metal and this app already said so: what
# separates a badge from a grey disc is the metal it is struck in, not a
# different glyph. Bronze is the first afternoon, legend is a month.
BRONZE, SILVER, GOLD, LEGEND = "bronze", "silver", "gold", "legend"

# Milestones, roughly in the order they are earned. Each is a fact about the
# account rather than a reward for using the page -- and each is a *different*
# fact, because twelve ways of counting tutorials is one badge printed twelve
# times. Between them they cover how much, how long, how big, how often, which
# of the things you have used, and whether you came back after it broke.
ACHIEVEMENTS: tuple[tuple[str, str, str, str], ...] = (
    # id, name, what it takes, how hard that is
    ("first", "First Tutorial", "rendered a tutorial", BRONZE),
    ("scripted", "Own Script", "rendered a script you wrote", BRONZE),
    ("local", "Local Mode", "rendered without calling a model", BRONZE),
    ("voiced", "Voice-over", "made a speech clip", BRONZE),
    ("runner", "Code Runner", "five tutorials rendered", SILVER),
    ("epic", "Long-form", "a single video over ten minutes", SILVER),
    ("undeterred", "Undeterred", "a render that failed, and then worked", SILVER),
    ("steady", "Steady", "seven days in a row", SILVER),
    ("marathon", "Marathon", "an hour of video produced", GOLD),
    ("prolific", "Prolific", "twenty-five tutorials rendered", GOLD),
    ("series", "Series", "five hours of video produced", LEGEND),
    ("devoted", "Devoted", "thirty days in a row", LEGEND),
)


@dataclass(frozen=True)
class Progress:
    xp: int
    level: int
    xp_into_level: int
    xp_for_level: int
    streak: int
    achievements: list[dict]

    def as_dict(self) -> dict:
        return {
            "xp": self.xp,
            "level": self.level,
            # What the bar shows: progress through the current level, not
            # toward some total that never arrives.
            "xp_into_level": self.xp_into_level,
            "xp_for_level": self.xp_for_level,
            "streak": self.streak,
            "achievements": self.achievements,
        }


def level_for(xp: int) -> tuple[int, int, int]:
    """(level, xp into it, xp the level takes)."""
    level = xp // XP_PER_LEVEL + 1
    return level, xp % XP_PER_LEVEL, XP_PER_LEVEL


def streak_from(days: set[date], today: date) -> int:
    """Consecutive days ending today or yesterday.

    Yesterday counts as live: someone who worked last night and opens the page
    this morning has a streak, not a broken one, and resetting it at midnight
    would punish them for sleeping.

    Anything older is a streak that has ended, and reports zero.
    """
    if not days:
        return 0
    start = today if today in days else today - timedelta(days=1)
    if start not in days:
        return 0

    length = 0
    day = start
    while day in days:
        length += 1
        day -= timedelta(days=1)
    return length


def achievements_for(*, rendered: int, minutes: float, streak: int,
                      earned_on: dict[str, date | None],
                      clips: int = 0, longest_s: float = 0.0,
                      used_script: bool = False, local_only: bool = False,
                      recovered: bool = False) -> list[dict]:
    """Which badges this account has, and which it has not.

    The locked ones are returned too, with what they take -- a wall of badges
    with no explanation of the greyed-out ones is a puzzle, not encouragement.

    Everything past `earned_on` is a fact the caller has to go and look up,
    and each defaults to "not yet". They are keyword-only and defaulted rather
    than grouped into a record so that every one is named at the call site:
    `achievements_for(5, 60, 7, ...)` is a call nobody can read.
    """
    earned = {
        "first": rendered >= 1,
        "runner": rendered >= 5,
        "prolific": rendered >= 25,
        "marathon": minutes >= 60,
        "series": minutes >= 300,
        "epic": longest_s >= 600,
        "steady": streak >= 7,
        "devoted": streak >= 30,
        "scripted": used_script,
        "local": local_only,
        "voiced": clips >= 1,
        "undeterred": recovered,
    }
    return [
        {
            "id": key,
            "name": name,
            "requires": requires,
            # How hard it is, which is what the metal means. Sent so the page
            # does not keep its own list of which badge is which -- two lists
            # is one of them being wrong eventually.
            "rarity": rarity,
            "earned": earned[key],
            # Only ever a real date. A badge that appeared today says today;
            # one from before this was recorded says nothing rather than a
            # guess.
            "earned_on": earned_on.get(key) if earned[key] else None,
        }
        for key, name, requires, rarity in ACHIEVEMENTS
    ]


def progress_for(*, rendered: int, minutes: float, streak: int,
                 earned_on: dict[str, date | None],
                 clips: int = 0, longest_s: float = 0.0,
                 used_script: bool = False, local_only: bool = False,
                 recovered: bool = False) -> Progress:
    xp = rendered * XP_PER_TUTORIAL + int(minutes) * XP_PER_MINUTE
    level, into, needed = level_for(xp)
    return Progress(
        xp=xp,
        level=level,
        xp_into_level=into,
        xp_for_level=needed,
        streak=streak,
        achievements=achievements_for(
            rendered=rendered, minutes=minutes, streak=streak,
            earned_on=earned_on, clips=clips, longest_s=longest_s,
            used_script=used_script, local_only=local_only,
            recovered=recovered,
        ),
    )
