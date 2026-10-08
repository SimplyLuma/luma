import tempfile
import unittest
from pathlib import Path

from luma_tide.credentials import MemoryCredentialStore
from luma_tide.model import LibraryStore
from luma_tide.remote import RemoteSources
from luma_tide.subsonic import Unreachable


class ArtistArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LibraryStore(self.root / "library.db")
        self.source = self.store.add_source("Server", "subsonic", "https://music.example", local=False)
        self.remote = RemoteSources(self.store, MemoryCredentialStore,
                                    artwork_root=self.root / "art", offline_root=self.root / "offline")
        self.calls = []
        owner = self

        class Client:
            def request(self, endpoint):
                owner.calls.append(endpoint)
                return {"artists": {"index": [{"artist": owner.artists}]}}

            def cover_art(self, cover, path, *, size):
                owner.calls.append(cover)
                if owner.fail:
                    raise Unreachable("offline")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture image")

        self.fail = False
        self.artists = [{"name": "Artist", "coverArt": "portrait-1",
                         "artistImageUrl": "https://unrelated.example/ignored"}]
        self.remote._clients[self.source.id] = Client()

    def tearDown(self):
        self.remote.close()
        self.store.close()
        self.temp.cleanup()

    def test_exact_server_portrait_is_cached_and_available_offline(self):
        delivered = []
        images = self.remote.artist_artwork(["Artist", "Different artist"], delivered.append).result(5)
        self.assertEqual(delivered, [images])
        self.assertEqual(set(images), {"Artist"})
        self.assertEqual(self.calls, ["getArtists", "portrait-1"])
        self.remote._clients.clear()
        self.assertEqual(self.remote.artist_artwork(["Artist"]).result(5), images)
        self.assertEqual(len(self.calls), 2)

    def test_ambiguous_names_are_not_assigned_a_portrait(self):
        self.artists.append({"name": "ARTIST", "coverArt": "portrait-2"})
        self.assertEqual(self.remote.artist_artwork(["Artist"]).result(5), {})
        self.assertEqual(self.calls, ["getArtists"])

    def test_unavailable_server_stops_after_two_artwork_failures(self):
        self.fail = True
        self.artists = [{"name": str(i), "coverArt": str(i)} for i in range(5)]
        self.assertEqual(self.remote.artist_artwork([str(i) for i in range(5)]).result(5), {})
        self.assertEqual(self.calls, ["getArtists", "0", "1"])
