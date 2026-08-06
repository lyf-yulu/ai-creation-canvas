import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class UpstreamSnapshotTest(unittest.TestCase):
    def test_pinned_source_and_license(self):
        text = (ROOT / "UPSTREAM.md").read_text("utf-8")
        self.assertIn("9bccd0ff1a7057a835708a731644ab05371fea3b", text)
        self.assertIn("https://github.com/basketikun/infinite-canvas", text)
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", (ROOT / "LICENSE").read_text("utf-8"))
        self.assertFalse((ROOT / "web" / ".git").exists())
