import unittest
from pathlib import Path
import tempfile

import yaml

from bot import load_config


class ConfigTests(unittest.TestCase):
    def test_paper_mode_required(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.yaml"
            path.write_text(yaml.safe_dump({"paper": {"enabled": False}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(str(path))

    def test_valid_config_example(self):
        with open("config.example.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.yaml"
            path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
            self.assertEqual(load_config(str(path))["scanner"]["symbols"], ["BTCUSD", "ETHUSD"])


if __name__ == "__main__":
    unittest.main()
