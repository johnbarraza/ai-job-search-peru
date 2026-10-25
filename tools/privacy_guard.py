#!/usr/bin/env python3
"""Stop a push that would publish a candidate's personal data.

The pre-push hook (installed by tools/perfiles.py) runs this on every push. It reads the
identity lines of every local profile - each perfiles/<nombre>/CLAUDE.md and a personalized
root CLAUDE.md - and looks for them in what the push would publish: the added lines and the
messages of every outgoing commit. The terms never leave the machine: they come from
gitignored files and are only compared, never written anywhere.

What counts as a term: the full name and its two-word forms (first name + first surname,
both surnames), every e-mail address, the phone number (digits only, with or without
its country code), and the LinkedIn handle and personal site.
Placeholders such as [YOUR_NAME] are skipped.

Usage:
    (the hook)  git push ...                       -> runs `privacy_guard.py --pre-push`
    python tools/privacy_guard.py --range origin/master..master    # check a range by hand
    python tools/privacy_guard.py --terms                          # how many terms it knows (never the terms)

Bypass, for a push you have read yourself: AI_JOB_SEARCH_SKIP_PRIVACY=1 git push ...
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SHARED_DOMAIN = re.compile(r"(?:www\.)?(?:linkedin|gmail|github|google|outlook|hotmail|yahoo)\.com|(?:[a-z0-9-]+\.)*(?:edu|com|gob)\.pe")
FIELD = re.compile(r"^\s*-\s*\*\*(Name|Phone|Email|LinkedIn|Site)\:\*\*\s*(.+)$", re.M)


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def profile_files(root: Path) -> list[Path]:
    files = [root / "CLAUDE.md"] + sorted((root / "perfiles").glob("*/CLAUDE.md"))
    return [f for f in files if f.is_file()]


def terms_from(text: str) -> dict[str, set[str]]:
    """{'text': lowercase phrases, 'digits': phone numbers} from a profile's identity lines."""
    words, digits = set(), set()
    for field, raw in FIELD.findall(text):
        value = re.sub(r"<!--.*?-->", "", raw).strip()
        if not value or "[" in value and "]" in value and value.lstrip().startswith("["):
            continue  # a template placeholder
        if field == "Name":
            name = " ".join(value.split())
            parts = name.split()
            if len(parts) >= 2:
                words.add(name.lower())
                words.add(f"{parts[0]} {parts[-2] if len(parts) >= 3 else parts[-1]}".lower())
                words.add(" ".join(parts[-2:]).lower())
        elif field == "Phone":
            number = re.sub(r"\D", "", value)
            if len(number) >= 7:
                digits.add(number[-9:])  # the local number, with or without the country code
        elif field == "Email":
            words.update(e.lower() for e in EMAIL.findall(value))
        else:  # LinkedIn and Site: the handle and the personal site, never a shared domain
            handle = re.search(r"linkedin\.com/in/([A-Za-z0-9_-]{3,})", value)
            if handle:
                words.add(handle.group(1).lower())
            for site in re.findall(r"(?<![\w.@-])((?:[a-z0-9-]+\.)+(?:github\.io|com|pe|me|dev|io|net|org))(?![\w.-])", value.lower()):
                if not SHARED_DOMAIN.fullmatch(site):
                    words.add(site)
    return {"text": {w for w in words if len(w) >= 6}, "digits": digits}


def collect_terms(root: Path) -> dict[str, set[str]]:
    out = {"text": set(), "digits": set()}
    for f in profile_files(root):
        found = terms_from(f.read_text(encoding="utf-8", errors="replace"))
        out["text"] |= found["text"]
        out["digits"] |= found["digits"]
    return out


def outgoing_commits(root: Path, local_sha: str, remote_sha: str) -> list[str]:
    if local_sha == ZERO:
        return []  # a branch deletion publishes nothing
    if remote_sha == ZERO:
        spec = [local_sha, "--not", "--remotes"]  # a new branch: what no remote has yet
    else:
        spec = [f"{remote_sha}..{local_sha}"]
    return [c for c in git("rev-list", *spec, cwd=root).split() if c]


def scan_commit(root: Path, commit: str, terms: dict[str, set[str]]) -> list[str]:
    """Findings as 'commit file: term kind' lines; the term itself is never printed."""
    # A merge is checked against its first parent: what it brings onto the branch being pushed.
    show = git("show", "--format=%B", "--unified=0", "--no-color", "--no-ext-diff", "-m", "--first-parent", commit, cwd=root)
    findings, current = [], "(mensaje del commit)"
    for line in show.splitlines():
        if line.startswith("diff "):
            current = line.split(" b/", 1)[-1] if " b/" in line else line[5:]
            continue
        if line.startswith(("+++", "---", "@@", "index ", "new file", "deleted file", "similarity", "rename ")):
            continue
        if current != "(mensaje del commit)" and not line.startswith("+"):
            continue
        low = line.lower()
        hits = [t for t in terms["text"] if t in low]
        flat = re.sub(r"\D", "", line)
        if any(d in flat for d in terms["digits"]):
            hits.append("teléfono")
        for hit in hits:
            kind = "teléfono" if hit == "teléfono" else "correo" if "@" in hit else "dato de identidad"
            findings.append(f"{commit[:9]} {current}: {kind}")
    return findings


def check(root: Path, ranges: list[tuple[str, str]]) -> list[str]:
    terms = collect_terms(root)
    if not terms["text"] and not terms["digits"]:
        return []
    findings = []
    for local_sha, remote_sha in ranges:
        for commit in outgoing_commits(root, local_sha, remote_sha):
            findings += scan_commit(root, commit, terms)
    return sorted(set(findings))


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pre-push", action="store_true", help="read git's pre-push lines on stdin")
    ap.add_argument("--range", help="check one range by hand, e.g. origin/master..master")
    ap.add_argument("--terms", action="store_true", help="say how many terms are known (never print them)")
    ap.add_argument("--root", type=Path, default=None)
    args = ap.parse_args(argv)
    root = args.root or Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=Path.cwd()).strip()).parent
    if args.terms:
        t = collect_terms(root)
        print(f"{len(t['text'])} términos de texto y {len(t['digits'])} teléfonos, de {len(profile_files(root))} perfiles")
        return 0
    if os.environ.get("AI_JOB_SEARCH_SKIP_PRIVACY") == "1":
        print("privacy_guard: omitido por AI_JOB_SEARCH_SKIP_PRIVACY=1", file=sys.stderr)
        return 0
    ranges = []
    if args.range:
        base, _, tip = args.range.partition("..")
        ranges.append((git("rev-parse", tip or "HEAD", cwd=root).strip(), git("rev-parse", base, cwd=root).strip()))
    if args.pre_push:
        for line in sys.stdin:
            parts = line.split()
            if len(parts) == 4:
                ranges.append((parts[1], parts[3]))
    findings = check(root, ranges)
    if findings:
        print("privacy_guard: el push publicaría datos personales de un perfil. Nada se subió.", file=sys.stderr)
        for f in findings[:20]:
            print(f"  {f}", file=sys.stderr)
        print("Quita esos datos del commit (o del mensaje) y vuelve a intentar. Si ya lo revisaste y es intencional: "
              "AI_JOB_SEARCH_SKIP_PRIVACY=1 git push ...", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
