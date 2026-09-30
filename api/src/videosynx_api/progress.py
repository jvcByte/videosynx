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

# Milestones, in the order they are earned. Each is a fact about the account
# rather than a reward for using the page.
ACHIEVEMENTS: tuple[tuple[str, str, str], ...] = (
    # id, name, what it takes
    ("first", "First Tutorial", "rendered a tutorial"),
    ("runner", "Code Runner", "five tutorials rendered"),
    ("marathon", "Marathon", "an hour of video produced"),
    ("steady", "Steady", "seven days in a row"),
    ("prolific", "Prolific", "twenty-five tutorials rendered"),
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
                      earned_on: dict[str, date | None]) -> list[dict]:
    """Which badges this account has, and which it has not.

    The locked ones are returned too, with what they take -- a wall of badges
    with no explanation of the greyed-out ones is a puzzle, not encouragement.
    """
    earned = {
        "first": rendered >= 1,
        "runner": rendered >= 5,
        "marathon": minutes >= 60,
        "steady": streak >= 7,
        "prolific": rendered >= 25,
    }
    return [
        {
            "id": key,
            "name": name,
            "requires": requires,
            "earned": earned[key],
            # Only ever a real date. A badge that appeared today says today;
            # one from before this was recorded says nothing rather than a
            # guess.
            "earned_on": earned_on.get(key) if earned[key] else None,
        }
        for key, name, requires in ACHIEVEMENTS
    ]


def progress_for(*, rendered: int, minutes: float, streak: int,
                 earned_on: dict[str, date | None]) -> Progress:
    xp = rendered * XP_PER_TUTORIAL + int(minutes) * XP_PER_MINUTE
    level, into, needed = level_for(xp)
    return Progress(
        xp=xp,
        level=level,
        xp_into_level=into,
        xp_for_level=needed,
        streak=streak,
        achievements=achievements_for(rendered=rendered, minutes=minutes,
                                      streak=streak, earned_on=earned_on),
    )
