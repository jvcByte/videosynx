"""The waitlist: what is promised, and who is waiting.

Two properties matter. The count is real -- it is the only number on that page
a visitor can change, and a made-up one is the same lie as an invented user
count on the landing page. And joining is idempotent, because a list that
counts one person twice is not a measurement of anything.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from videosynx_api.app import create_app
from videosynx_api.features import FEATURES

DSN = "postgresql:///videosynx_test"
pytestmark = pytest.mark.usefixtures("clean_interest")


@pytest.fixture
def client():
    with TestClient(create_app(DSN)) as c:
        yield c


@pytest.fixture
def clean_interest():
    import psycopg

    try:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute("create table if not exists feature_interest ("
                         "user_id text not null, feature text not null, "
                         "created_at timestamptz not null default now(), "
                         "primary key (user_id, feature))")
            conn.execute("delete from feature_interest")
    except Exception:  # noqa: BLE001 -- no database, and the tests that need
        pytest.skip("no test database")  # one are the ones that skip


def auth(user: str = "tester") -> dict[str, str]:
    return {"X-Dev-User": f"{user}:free"}


def test_the_list_says_what_was_chosen():
    with TestClient(create_app(DSN)) as client:
        body = client.get("/features", headers=auth()).json()
    assert [f["id"] for f in body] == [f.id for f in FEATURES]
    for entry in body:
        assert entry["blurb"] and entry["blocker"]


def test_nobody_waiting_is_no_number_rather_than_zero(client):
    """Zero is a true count and a reason to let somebody else go first."""
    body = client.get("/features", headers=auth()).json()
    assert all(entry["waiting"] is None for entry in body)
    assert all(entry["joined"] is False for entry in body)


def test_joining_puts_you_on_it_and_counts_you_once(client):
    first = client.post("/features/any-topic/interest", headers=auth("a")).json()
    assert first["joined"] is True
    assert first["waiting"] == 1

    # The same person again. The table's primary key is what stops this being
    # two, and saying "you are on it" beats counting you twice.
    again = client.post("/features/any-topic/interest", headers=auth("a")).json()
    assert again["waiting"] == 1

    other = client.post("/features/any-topic/interest", headers=auth("b")).json()
    assert other["waiting"] == 2
    # One feature's queue is not another's.
    assert client.get("/features", headers=auth("a")).json()[1]["waiting"] is None


def test_it_remembers_which_ones_are_yours(client):
    client.post("/features/publish/interest", headers=auth("a"))
    mine = {f["id"]: f["joined"] for f in client.get("/features", headers=auth("a")).json()}
    assert mine["publish"] is True
    assert mine["any-topic"] is False

    theirs = {f["id"]: f["joined"] for f in client.get("/features", headers=auth("b")).json()}
    assert theirs["publish"] is False


def test_a_feature_nobody_offers_is_404(client):
    assert client.post("/features/teleportation/interest", headers=auth()).status_code == 404


def test_the_waitlist_needs_an_account(client):
    assert client.get("/features").status_code == 401
    assert client.post("/features/any-topic/interest").status_code == 401
