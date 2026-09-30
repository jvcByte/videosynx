"""What is not built yet, and who is waiting for it.

A written list rather than a table, for the same reason the landing page's
examples are: what the service promises next is a decision someone makes, not
a row that accumulates. The count beside each one is the part that is real --
it comes from `feature_interest`, and it is the only thing on the page that a
visitor can change.

Order is the order they appear in, so the one closest to being built goes
first.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    id: str
    title: str
    # One sentence, said to someone who is deciding whether to wait. What it
    # would let them do, not what it is made of.
    blurb: str
    # What stands in the way, in plain terms. A promise with no obstacle
    # named is a promise nobody has costed.
    blocker: str


FEATURES: tuple[Feature, ...] = (
    Feature(
        id="any-topic",
        title="Any-topic tutorials",
        blurb="Make a tutorial about how to build a battery, or a rocket, or "
              "anything else that is not code.",
        blocker="The whole pipeline writes and runs Python today. Teaching "
                "something else means writing the lesson differently, and "
                "nothing can be executed to check it.",
    ),
    Feature(
        id="narration-languages",
        title="Narration in other languages",
        blurb="Narrate a finished tutorial in a language other than English, "
              "with the captions to match.",
        blocker="The voice catalogue already records a language per voice, so "
                "the narration half is close. The slides and captions are "
                "written in English and would have to be translated with it.",
    ),
    Feature(
        id="publish",
        title="Publish to YouTube and TikTok",
        blurb="Send a finished tutorial straight to a channel instead of "
              "downloading the file and uploading it by hand.",
        blocker="The MP4 and the SRT already exist, so this is a grant and an "
                "upload per platform. It needs an account on each, and "
                "whatever each one requires of an app that posts for you.",
    ),
    Feature(
        id="longer-and-sharper",
        title="Longer videos at higher resolution on the free tier",
        blurb="Raise the free tier's limits on length and picture size.",
        blocker="Nothing technical: 1080p and longer runtimes already work, "
                "they are switched off by the plan. This is a decision about "
                "what the free tier costs to run, not a feature to build.",
    ),
)

BY_ID = {feature.id: feature for feature in FEATURES}

# What the API answers when asked to record interest in something not on the
# list -- a typo, or a client that has drifted from this file.
UNKNOWN = "unknown feature"
