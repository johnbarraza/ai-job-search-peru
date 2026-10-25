"""tools/privacy_guard.py: a push never publishes a local profile's identity."""
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("privacy_guard", REPO / "tools" / "privacy_guard.py")
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)

PROFILE = """# Job Application Assistant for Ana Pérez Rojas
### Identity
- **Name:** Ana María Pérez Rojas
- **Phone:** +51 987 654 321
- **Email:** ana.perez.r@example.org (university: a.perez@alumnos.example.edu)
- **LinkedIn:** linkedin.com/in/ana-perez-rojas · **Site:** anaperez.github.io
"""
TEMPLATE = """- **Name:** [YOUR_NAME]
- **Phone:** [YOUR_PHONE]
- **Email:** [YOUR_EMAIL]
"""


def git(*args, cwd):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", env=env)


class Terms(unittest.TestCase):
    def test_identity_lines_become_terms_and_placeholders_do_not(self):
        t = pg.terms_from(PROFILE)
        for term in ("ana maría pérez rojas", "ana pérez", "pérez rojas", "ana.perez.r@example.org",
                     "a.perez@alumnos.example.edu", "ana-perez-rojas", "anaperez.github.io"):
            self.assertIn(term, t["text"])
        self.assertEqual(t["digits"], {"987654321"})
        empty = pg.terms_from(TEMPLATE)
        self.assertEqual((empty["text"], empty["digits"]), (set(), set()))


class Push(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        git("init", "-q", "-b", "master", cwd=self.root)
        (self.root / ".gitignore").write_text("perfiles/\n", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text(TEMPLATE, encoding="utf-8")
        git("add", "-A", cwd=self.root)
        git("commit", "-qm", "base", cwd=self.root)
        self.base = git("rev-parse", "HEAD", cwd=self.root).stdout.strip()
        (self.root / "perfiles" / "ana").mkdir(parents=True)
        (self.root / "perfiles" / "ana" / "CLAUDE.md").write_text(PROFILE, encoding="utf-8")

    def commit(self, name, text, message="change"):
        (self.root / name).write_text(text, encoding="utf-8")
        git("add", name, cwd=self.root)
        git("commit", "-qm", message, cwd=self.root)
        return git("rev-parse", "HEAD", cwd=self.root).stdout.strip()

    def test_a_clean_push_passes(self):
        tip = self.commit("tools.py", "def f():\n    return 'Lima, Perú'\n")
        self.assertEqual(pg.check(self.root, [(tip, self.base)]), [])

    def test_name_email_and_spaced_phone_are_caught_without_printing_them(self):
        tip = self.commit("docs.md", "Ejemplo: escribir a ana.perez.r@example.org\nLlamar al 987-654-321\n", "fix for Pérez Rojas")
        found = pg.check(self.root, [(tip, self.base)])
        text = "\n".join(found)
        self.assertIn("docs.md: correo", text)
        self.assertIn("docs.md: teléfono", text)
        self.assertIn("(mensaje del commit): dato de identidad", text)
        self.assertNotIn("example.org", text)

    def test_removed_lines_and_branch_deletions_are_not_findings(self):
        tip = self.commit("x.md", "Pérez Rojas\n")
        clean = self.commit("x.md", "nada\n")
        self.assertEqual(pg.check(self.root, [(clean, tip)]), [])
        self.assertEqual(pg.check(self.root, [(pg.ZERO, tip)]), [])

    def test_the_hook_entry_point_blocks_and_can_be_bypassed(self):
        tip = self.commit("docs.md", "contacto: ana-perez-rojas\n")
        stdin = f"refs/heads/master {tip} refs/heads/master {self.base}\n"
        proc = subprocess.run(["python", str(REPO / "tools" / "privacy_guard.py"), "--pre-push", "--root", str(self.root)],
                              input=stdin, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("datos personales", proc.stderr)
        proc = subprocess.run(["python", str(REPO / "tools" / "privacy_guard.py"), "--pre-push", "--root", str(self.root)],
                              input=stdin, capture_output=True, text=True, encoding="utf-8",
                              env=dict(os.environ, AI_JOB_SEARCH_SKIP_PRIVACY="1"))
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
