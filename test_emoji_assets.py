import unittest
from pathlib import Path

from PIL import Image

from emojis import ACTIVITY_EMOJI_KEYS, DEFINITIONS, text_emoji


class EmojiAssetTests(unittest.TestCase):
    def test_every_catalog_emoji_has_a_discord_ready_png(self):
        emoji_dir = Path(__file__).resolve().parent / "assets" / "discord" / "emojis"
        for definition in DEFINITIONS.values():
            path = emoji_dir / f"{definition.name}.png"
            self.assertTrue(path.exists(), path.name)
            with Image.open(path) as image:
                self.assertEqual(image.size, (128, 128))
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getchannel("A").getextrema(), (0, 255))

    def test_all_discord_ids_are_unique_and_active(self):
        ids = [definition.default_id for definition in DEFINITIONS.values()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(value > 0 for value in ids))
        for key, definition in DEFINITIONS.items():
            expected = f"<:{definition.name}:{definition.default_id}>"
            self.assertEqual(text_emoji(key), expected)

    def test_each_activity_has_its_own_emoji(self):
        self.assertEqual(
            set(ACTIVITY_EMOJI_KEYS),
            {
                "kill_scout",
                "kill_pelea",
                "limpieza_aspecto",
                "scouteo",
                "mapeo",
            },
        )
        self.assertEqual(
            len(ACTIVITY_EMOJI_KEYS),
            len(set(ACTIVITY_EMOJI_KEYS.values())),
        )


if __name__ == "__main__":
    unittest.main()
