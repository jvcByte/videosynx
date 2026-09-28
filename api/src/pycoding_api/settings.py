"""What a user can choose, as opposed to what their tier allows.

Two layers decide how a video is made. The tier owns whatever costs money or
is promised by the plan -- which voice engine, what resolution, whether there
is a watermark, how many snippets. The user owns the rest. Tier keys are
merged over the top at submit time, so nothing saved here can grant more than
the tier gives.
"""

from __future__ import annotations

from typing import Literal

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from pydantic import BaseModel, Field, ValidationError

# The engine owns what these mean; this is the wire contract, and
# api/tests checks the two lists still agree. The API deliberately does not
# import the engine -- that would pull PIL and numpy into a process that never
# renders anything.
Mode = Literal["coding", "illustration", "both"]


class Settings(BaseModel):
    mode: Mode = "both"
    style: Literal["sketch", "clean"] = "sketch"
    audience: Literal["beginner", "intermediate", "expert"] = "beginner"
    focus: str = Field(default="", max_length=300,
                       description="A free-text steer, like a director's note: "
                                   "'dark and minimal', 'focus on the failure case'.")
    voice: str = Field(default="af_heart", max_length=40,
                       description="Voice within the tier's engine. An unknown "
                                   "name falls back to the default rather than "
                                   "failing the render.")
    captions: bool = True
    length: Literal["short", "medium", "long"] = "medium"

    def as_options(self) -> dict:
        """The subset that belongs on a job, in the worker's vocabulary."""
        return {
            "mode": self.mode,
            "style": self.style,
            "audience": self.audience,
            "focus": self.focus,
            "voice": self.voice,
            "captions": self.captions,
            "length": self.length,
        }


def load_settings(dsn: str, user_id: str) -> Settings:
    """Saved settings, or the defaults for someone who has never saved any."""
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        row = conn.execute(
            "select settings from user_settings where user_id = %s", (user_id,)
        ).fetchone()
    if row is None or not row["settings"]:
        return Settings()
    # Validated on the way out too: a row written before a field existed, or
    # edited by hand, must not take the endpoint down.
    try:
        return Settings(**{**Settings().model_dump(), **row["settings"]})
    except ValidationError:
        return Settings()


def save_settings(dsn: str, user_id: str, settings: dict) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            insert into user_settings (user_id, settings, updated_at)
            values (%s, %s, now())
            on conflict (user_id) do update
               set settings = excluded.settings, updated_at = now()
            """,
            (user_id, Json(settings)),
        )
