import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from swingmusic.api.colors import get_album_color
from swingmusic.lib.home import find_mix
from swingmusic.plugins.mixes import MixesPlugin


class TestAlbumColorApi(unittest.TestCase):
    def test_get_album_color_returns_404_when_album_has_no_color(self):
        with patch(
            "swingmusic.api.colors.Store.get_album_by_hash",
            return_value=SimpleNamespace(),
        ):
            result = get_album_color(SimpleNamespace(albumhash="albumhash"))

        self.assertEqual(result, ({"color": ""}, 404))

    def test_get_album_color_returns_album_color(self):
        with patch(
            "swingmusic.api.colors.Store.get_album_by_hash",
            return_value=SimpleNamespace(color="#112233"),
        ):
            result = get_album_color(SimpleNamespace(albumhash="albumhash"))

        self.assertEqual(result, {"color": "#112233"})


class TestFindMix(unittest.TestCase):
    def test_find_mix_falls_back_to_source_plugin_when_premium_module_missing(self):
        fake_plugin_module = types.ModuleType("swingmusic.plugins.mixes")

        custom_mix = SimpleNamespace(
            to_dict=lambda: {"type": "mix", "sourcehash": "sourcehash"}
        )

        class FakeMixesPlugin:
            @staticmethod
            def get_track_mix(mix):
                return custom_mix

        fake_plugin_module.MixesPlugin = FakeMixesPlugin

        original_premium = sys.modules.pop("swingmusic.premium", None)

        try:
            with patch.dict(
                sys.modules,
                {"swingmusic.plugins.mixes": fake_plugin_module},
                clear=False,
            ):
                with patch(
                    "swingmusic.store.homepage.HomepageStore.get_mix",
                    return_value=None,
                ), patch(
                    "swingmusic.lib.home.MixTable.get_by_sourcehash",
                    return_value=SimpleNamespace(),
                ):
                    result = find_mix("t123", "sourcehash")
        finally:
            if original_premium is not None:
                sys.modules["swingmusic.premium"] = original_premium

        self.assertEqual(result, {"type": "mix", "sourcehash": "sourcehash"})

    def test_find_mix_returns_store_match_before_db_lookup(self):
        store_mix = {"id": "a1", "sourcehash": "sourcehash"}

        with patch(
            "swingmusic.store.homepage.HomepageStore.get_mix",
            return_value=store_mix,
        ), patch("swingmusic.lib.home.MixTable.get_by_sourcehash") as db_lookup:
            result = find_mix("a1", "sourcehash")

        db_lookup.assert_not_called()
        self.assertEqual(result, store_mix)


class TestLocalMixRecommendations(unittest.TestCase):
    @staticmethod
    def make_track(
        trackhash: str,
        weakhash: str,
        albumhash: str,
        artist_name: str,
        artisthash: str,
        genres: list[str],
        *,
        playcount: int = 0,
        playduration: int = 0,
        bitrate: int = 320,
    ):
        return SimpleNamespace(
            trackhash=trackhash,
            weakhash=weakhash,
            albumhash=albumhash,
            title=trackhash,
            artists=[{"name": artist_name, "artisthash": artisthash}],
            artisthashes=[artisthash],
            genrehashes=genres,
            playcount=playcount,
            playduration=playduration,
            bitrate=bitrate,
        )

    def test_local_mix_prefers_similar_artists_and_shared_genres(self):
        seed = self.make_track(
            "seed-track",
            "seed-weak",
            "seed-album",
            "Seed Artist",
            "seed-artist",
            ["alt", "rock"],
            playcount=10,
            playduration=600,
        )
        similar_hit = self.make_track(
            "recommended-1",
            "recommended-weak-1",
            "album-1",
            "Similar Artist",
            "similar-artist",
            ["rock", "indie"],
            playcount=40,
            playduration=2400,
        )
        genre_hit = self.make_track(
            "recommended-2",
            "recommended-weak-2",
            "album-2",
            "Genre Artist",
            "genre-artist",
            ["alt", "electro"],
            playcount=20,
            playduration=1200,
        )
        unrelated = self.make_track(
            "recommended-3",
            "recommended-weak-3",
            "album-3",
            "Other Artist",
            "other-artist",
            ["jazz"],
            playcount=1,
            playduration=60,
        )

        track_groups = {
            track.trackhash: SimpleNamespace(get_best=lambda track=track: track)
            for track in [seed, similar_hit, genre_hit, unrelated]
        }

        with patch.object(MixesPlugin, "MAX_TRACKS_PER_ARTIST", 5), patch(
            "swingmusic.plugins.mixes.TrackStore.trackhashmap",
            track_groups,
        ), patch(
            "swingmusic.plugins.mixes.ArtistStore.artistmap",
            {
                "similar-artist": SimpleNamespace(),
                "genre-artist": SimpleNamespace(),
                "other-artist": SimpleNamespace(),
            },
        ), patch(
            "swingmusic.plugins.mixes.SimilarArtistTable.get_by_hash",
            return_value=SimpleNamespace(
                similar_artists=[
                    {"artisthash": "similar-artist", "weight": 98},
                ]
            ),
        ):
            tracks, albums, artists = MixesPlugin.get_local_track_mix_data([seed])

        self.assertEqual([track.trackhash for track in tracks[:2]], [
            "recommended-1",
            "recommended-2",
        ])
        self.assertIn("similar-artist", artists)
        self.assertIn("album-1", albums)
        self.assertNotIn("seed-track", [track.trackhash for track in tracks])

    def test_get_track_mix_data_uses_local_logic_without_remote_call(self):
        seed = self.make_track(
            "seed-track",
            "seed-weak",
            "seed-album",
            "Seed Artist",
            "seed-artist",
            ["rock"],
        )

        with patch.object(
            MixesPlugin,
            "get_local_track_mix_data",
            return_value=([seed], ["album-1"], ["artist-1"]),
        ) as local_logic, patch.object(
            MixesPlugin,
            "fallback_create_artist_mix",
            return_value=[],
        ):
            tracks, albums, artists = MixesPlugin().get_track_mix_data([seed])

        local_logic.assert_called_once()
        self.assertEqual(tracks, [seed])
        self.assertEqual(albums, ["album-1"])
        self.assertEqual(artists, ["artist-1"])


if __name__ == "__main__":
    unittest.main()