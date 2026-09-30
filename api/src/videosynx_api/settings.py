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
    # "local" promises never to call a model. Rendering already is local and
    # free -- Pillow and ffmpeg, and Kokoro for the voice -- so the only thing
    # that costs anything is *writing* the content, and local mode simply
    # refuses to. It cannot invent a tutorial from a topic; what it can do is
    # render one you already have.
    generation: Literal["auto", "local"] = "auto"
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
            "generation": self.generation,
            "style": self.style,
            "audience": self.audience,
            "focus": self.focus,
            "voice": self.voice,
            "captions": self.captions,
            "length": self.length,
        }


# Kokoro v1.0's voices, as shipped in voices-v1.0.bin. A released artifact, so
# the list is stable -- but it is still a copy, and api/tests compares it
# against the voices the engine actually loads.
KOKORO_VOICES = (
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica", "af_kore",
    "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_echo", "am_eric", "am_fenrir", "am_liam", "am_michael",
    "am_onyx", "am_puck", "am_santa",
    "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
    "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
    "ef_dora", "em_alex", "em_santa",
    "ff_siwis",
    "hf_alpha", "hf_beta", "hm_omega", "hm_psi",
    "if_sara", "im_nicola",
    "jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo",
    "pf_dora", "pm_alex", "pm_santa",
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
)

# A voice name is <language><gender>_<name>, so the label is derived from the
# name rather than written out fifty-four times alongside it.
VOICE_LANGUAGES = {
    "af": ("English (US)", "female"), "am": ("English (US)", "male"),
    "bf": ("English (UK)", "female"), "bm": ("English (UK)", "male"),
    "ef": ("Spanish", "female"), "em": ("Spanish", "male"),
    "ff": ("French", "female"),
    "hf": ("Hindi", "female"), "hm": ("Hindi", "male"),
    "if": ("Italian", "female"), "im": ("Italian", "male"),
    "jf": ("Japanese", "female"), "jm": ("Japanese", "male"),
    "pf": ("Portuguese", "female"), "pm": ("Portuguese", "male"),
    "zf": ("Chinese", "female"), "zm": ("Chinese", "male"),
}


def voice_options() -> list[dict]:
    """Every voice the tier's engine can speak with.

    Sorted with English first and the default voice at the top, because that
    is what almost every caller wants and a wall of fifty-four entries in
    alphabetical order buries it.
    """
    options = []
    for name in KOKORO_VOICES:
        language, gender = VOICE_LANGUAGES.get(name[:2], ("Other", ""))
        options.append({"id": name, "language": language, "gender": gender})
    options.sort(key=lambda v: (not v["language"].startswith("English"),
                                v["language"], v["id"]))
    return options


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
