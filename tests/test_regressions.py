import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from swingmusic.api.colors import get_album_color
from swingmusic.lib.home import find_mix


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


if __name__ == "__main__":
    unittest.main()