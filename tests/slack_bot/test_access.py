"""Tests for slack_bot/access.py: who may use the bot, with a fake Slack client and a fake clock."""

from slack_bot.access import CACHE_SECONDS, Access


class FakeSlack:
    """Stands in for the Slack WebClient: serves a channel's members in pages, or raises."""

    def __init__(self, pages: list[list[str]], fail: bool = False):
        """`pages` is the member list split the way Slack would page it."""
        self.pages = pages
        self.fail = fail
        self.calls = 0

    def conversations_members(self, channel, cursor=None, limit=None):
        """Return one page, with a cursor to the next one (empty on the last page)."""
        self.calls += 1
        if self.fail:
            raise RuntimeError("channel_not_found")
        page = int(cursor or 0)
        next_cursor = str(page + 1) if page + 1 < len(self.pages) else ""
        return {"members": self.pages[page], "response_metadata": {"next_cursor": next_cursor}}


class FakeClock:
    """A clock the test moves forward by hand."""

    def __init__(self):
        """Start at time 0."""
        self.now = 0.0

    def __call__(self) -> float:
        """The current fake time."""
        return self.now


def test_with_no_limits_everyone_is_allowed():
    """Neither setting set: the bot is open to the whole workspace."""
    assert Access().allows("U9")


def test_listed_users_only():
    """With only SLACK_ALLOWED_USERS, exactly those users are allowed."""
    access = Access(allowed_users=["U1"])
    assert access.allows("U1")
    assert not access.allows("U9")


def test_channel_members_are_allowed_and_others_are_not():
    """With SLACK_MEMBERS_CHANNEL, being in the channel is what counts."""
    access = Access(members_channel="C1", client=FakeSlack([["U1", "U2"]]))
    assert access.allows("U2")
    assert not access.allows("U9")


def test_a_listed_user_outside_the_channel_is_allowed():
    """The two settings add up: listed users, plus the channel's members."""
    access = Access(allowed_users=["U7"], members_channel="C1", client=FakeSlack([["U1"]]))
    assert access.allows("U7")
    assert access.allows("U1")


def test_every_page_of_members_is_read():
    """Slack returns big channels in pages; a member on the last page still counts."""
    access = Access(members_channel="C1", client=FakeSlack([["U1"], ["U2"], ["U3"]]))
    assert access.allows("U3")


def test_members_are_cached_then_fetched_again():
    """Slack is asked once per CACHE_SECONDS, not on every message."""
    slack, clock = FakeSlack([["U1"]]), FakeClock()
    access = Access(members_channel="C1", client=slack, clock=clock)

    access.allows("U1")
    access.allows("U1")
    assert slack.calls == 1

    slack.pages = [["U2"]]  # U1 left the channel, U2 joined
    clock.now = CACHE_SECONDS
    assert access.allows("U2")
    assert not access.allows("U1")
    assert slack.calls == 2


def test_if_slack_fails_first_nobody_from_the_channel_is_allowed():
    """Fail closed: with no member list yet, the channel lets nobody in."""
    access = Access(members_channel="C1", client=FakeSlack([["U1"]], fail=True))
    assert not access.allows("U1")


def test_if_slack_fails_later_the_last_list_is_kept():
    """A Slack error after a good fetch keeps the last list instead of locking everyone out."""
    slack, clock = FakeSlack([["U1"]]), FakeClock()
    access = Access(members_channel="C1", client=slack, clock=clock)
    assert access.allows("U1")

    slack.fail = True
    clock.now = CACHE_SECONDS
    assert access.allows("U1")
