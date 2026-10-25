"""Python dependencies are pinned to an exact version: a new release (or a hijacked one)
reaches the tools only when someone bumps the line on purpose."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class Pinned(unittest.TestCase):
    def test_every_requirement_is_an_exact_pin(self):
        files = sorted(REPO.glob("**/requirements*.txt"))
        files = [f for f in files if "node_modules" not in f.parts and "perfiles" not in f.parts]
        self.assertTrue(files, "no requirements file found")
        for f in files:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                with self.subTest(file=f.relative_to(REPO).as_posix(), line=line):
                    self.assertRegex(line, r"^[A-Za-z0-9_.\-\[\]]+==[0-9][A-Za-z0-9.\-+]*$")


if __name__ == "__main__":
    unittest.main()
