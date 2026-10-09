"""Who may use the bot: listed users, and/or the members of one Slack channel.

    SLACK_ALLOWED_USERS    Slack user IDs, comma-separated (U0123ABCD,...)
    SLACK_MEMBERS_CHANNEL  one channel ID (C0123ABCD); everyone in that channel may use the bot.
                           Make it a PRIVATE channel: anyone can join a public one on their own.

A person is allowed if they are in either one. With neither set, everyone is allowed.

The channel is the easier one to keep up to date: add a consultant to the channel and they can use
the bot; remove them and they can't. It applies to /agent and to DMs alike. A DM is not sent in any
channel, so this is the only way a channel can decide who may DM the bot.

The member list is fetched from Slack (conversations.members) and kept for CACHE_SECONDS, so Slack
is not asked on every message. A change to the channel therefore takes up to that long to apply.
If Slack can't be reached, the last list fetched is kept; if there is none yet, nobody from the
channel is let in. Failing closed is deliberate: the bot shows customer data.
"""

import logging
import threading
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

CACHE_SECONDS = 300


class Access:
    """Decides whether a Slack user may use the bot. See the module docstring for the rules."""

    def __init__(
        self,
        allowed_users: Iterable[str] = (),
        members_channel: Optional[str] = None,
        client=None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`client` is a Slack WebClient, needed only with `members_channel`. `clock` is for tests."""
        self.allowed_users = frozenset(allowed_users)
        self.members_channel = members_channel
        self.client = client
        self.clock = clock
        self._members: Optional[frozenset[str]] = None  # None until the first fetch succeeds
        self._fetched_at = 0.0
        self._lock = threading.Lock()  # handlers run in several threads

    @property
    def is_open(self) -> bool:
        """True when no limit is set, so everyone may use the bot."""
        return not self.allowed_users and not self.members_channel

    def allows(self, user_id: Optional[str]) -> bool:
        """True if `user_id` may use the bot."""
        if self.is_open:
            return True
        if user_id in self.allowed_users:
            return True
        if self.members_channel:
            return user_id in self.channel_members()
        return False

    def channel_members(self) -> frozenset[str]:
        """The members of `members_channel`, fetched again once the cached list is too old."""
        with self._lock:
            if self._members is None or self.clock() - self._fetched_at >= CACHE_SECONDS:
                try:
                    self._members = self.fetch_members()
                    self._fetched_at = self.clock()
                except Exception:
                    logger.exception("Could not read the members of %s; keeping the last list", self.members_channel)
            return self._members or frozenset()

    def fetch_members(self) -> frozenset[str]:
        """Ask Slack for every member of `members_channel` (the list comes in pages). Raises on error.

        Needs the groups:read scope for a private channel (channels:read for a public one), and the
        bot must be in a private channel to see its members.
        """
        members: set[str] = set()
        cursor = None
        while True:
            response = self.client.conversations_members(channel=self.members_channel, cursor=cursor, limit=1000)
            members.update(response["members"])
            cursor = (response.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return frozenset(members)
