# SPDX-License-Identifier: Apache-2.0
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from luma_streamy import chat  # noqa: E402
from luma_streamy.chat import Feed, LinkError, Source  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "chat"


def frames(name):
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


class LinkTest(unittest.TestCase):
    def test_links(self):
        self.assertEqual(chat.parse_link("https://www.twitch.tv/NovaPlays"), Source("twitch", "novaplays"))
        self.assertEqual(chat.parse_link("twitch.tv/popout/novaplays/chat?popout="), Source("twitch", "novaplays"))
        self.assertEqual(chat.parse_link("https://kick.com/orbit"), Source("kick", "orbit"))
        for bad, words in (("https://www.youtube.com/watch?v=x", "sign in"), ("tiktok.com/@a", "TikTok"),
                           ("https://example.org/x", "Twitch and Kick"), ("twitch.tv/directory", "channel"), ("", "Paste")):
            with self.assertRaises(LinkError) as caught:
                chat.parse_link(bad)
            self.assertIn(words, str(caught.exception))


class TwitchTest(unittest.TestCase):
    def test_hello(self):
        lines = chat.twitch_hello("novaplays", "justinfan1")
        self.assertEqual(lines[-2:], ["NICK justinfan1", "JOIN #novaplays"])

    def test_recorded_session(self):
        feed, replies = Feed(), []
        for frame in frames("twitch.jsonl"):
            messages, answer = chat.twitch_frame(frame)
            replies += answer
            for message in messages:
                feed.add(message)
        self.assertEqual(replies, ["PONG :tmi.twitch.tv"])
        texts = [(m.author, m.text, m.kind) for m in feed.messages]
        self.assertEqual(texts, [("NovaPlays", "Welcome in, starting soon", "message"),
                                 ("Pixel_Fox", "hype; the new overlay looks great", "message"),
                                 ("quiet one", "waves", "action"),
                                 ("", "12 raiders from Orbit have joined!", "notice")])  # the spam was cleared
        self.assertEqual(feed.messages[0].badges, ["broadcaster"])
        self.assertEqual(feed.messages[1].badges, ["moderator", "subscriber"])
        self.assertEqual(feed.messages[1].color, "#1E90FF")
        self.assertAlmostEqual(feed.messages[0].at, 1763660000.0)


class KickTest(unittest.TestCase):
    def test_chatroom(self):
        self.assertEqual(chat.kick_chatroom(json.loads((FIXTURES / "kick-channel.json").read_text())), 668)
        with self.assertRaises(LinkError):
            chat.kick_chatroom({"chatroom": None})
        self.assertIn("chatrooms.668.v2", chat.kick_subscribe(668))

    def test_recorded_session(self):
        feed, replies = Feed(), []
        for frame in frames("kick.jsonl"):
            messages, answer = chat.kick_frame(frame, "orbit")
            replies += answer
            for message in messages:
                feed.add(message)
        self.assertEqual(replies, ['{"event": "pusher:pong", "data": {}}'])
        self.assertEqual([(m.author, m.text, m.badges) for m in feed.messages], [("Orbit", "gg KEKW", ["moderator"])])
        self.assertEqual(chat.kick_frame("not json", "x"), ([], []))


class FeedTest(unittest.TestCase):
    def test_keeps_the_newest(self):
        feed, heard = Feed(), []
        feed.listeners.append(heard.append)
        for i in range(chat.KEEP + 10):
            feed.add(chat.ChatMessage("twitch", "c", "a", str(i)))
        self.assertEqual(len(feed.messages), chat.KEEP)
        self.assertEqual(feed.messages[0].text, "10")
        self.assertEqual(len(heard), chat.KEEP + 10)

    def test_send_needs_sign_in(self):
        self.assertIn("Sign in to Twitch", chat.send(Source("twitch", "x"), "hi"))
        self.assertIn("read-only", chat.send(Source("kick", "x"), "hi"))


if __name__ == "__main__":
    unittest.main()
