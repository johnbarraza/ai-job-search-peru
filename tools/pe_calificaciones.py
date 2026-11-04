#!/usr/bin/env python3
"""Employer ratings from Computrabajo for the review page, without a model.

Ratings are public facts about employers, so they live in one file the repo shares,
data/calificaciones.json, each with the date it was read: anyone who clones the repo starts with
them, and a rating is fetched again only after 30 days. tools/pe_revision.py shows them when
company_research/<company>.json (what /rank writes) has no Computrabajo rating of its own.

Which Computrabajo page belongs to a company:
  1. the slug from a Computrabajo posting's companyUrl (exact);
  2. else slugs guessed from the name ("Grupo Ripley" -> grupo-ripley), kept only when the
     page's company name matches the posting's;
  3. else nothing: pass --slug for that one ("Banco Agropecuario" is "agrobanco" there).

Bumeran also rates employers, but only for signed-in users: not read.

Usage:
    python tools/pe_calificaciones.py                       # every evaluated company in seen_jobs.json
    python tools/pe_calificaciones.py --empresa "Banco Agropecuario" --slug agrobanco
    python tools/pe_calificaciones.py --refresh             # ignore the 30-day cache
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / ".agents" / "skills" / "computrabajo-search" / "cli" / "src" / "cli.ts"
SHARED = ROOT / "data" / "calificaciones.json"
TTL = timedelta(days=30)
LEGAL = {"sa", "saa", "sac", "srl", "eirl"}
FILLER = LEGAL | {"s", "a", "c", "peru", "del", "de", "la", "el", "y"}


def words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", (text or "").lower())
    return re.findall(r"[a-z0-9]+", "".join(c for c in folded if not unicodedata.combining(c)))


def company_key(name: str) -> str:
    """The shared file's key: the name's words, accents and legal suffixes dropped."""
    return " ".join(x for x in words(name) if x not in LEGAL and len(x) > 1)


def same_company(a: str, b: str) -> bool:
    """Both names share their meaningful words (legal suffixes and fillers aside)."""
    x, y = set(words(a)) - FILLER, set(words(b)) - FILLER
    return bool(x and y) and (x <= y or y <= x or len(x & y) / min(len(x), len(y)) >= 0.75)


def guesses(name: str) -> list[str]:
    """Computrabajo slugs worth trying for a company name, most specific first."""
    w = words(re.sub(r"\(.*?\)", " ", name))
    core = [x for x in w if x not in FILLER]
    acronyms = [x.lower() for x in re.findall(r"\(([A-Za-z]{2,10})\)", name)]
    return list(dict.fromkeys(s for s in ["-".join(w), "-".join(core), *acronyms] if s))


def slug_from_url(url: str | None) -> str | None:
    m = re.match(r"https?://pe\.computrabajo\.com/([a-z0-9-]+)(?:/|$)", url or "")
    return m.group(1) if m and m.group(1) not in ("ofertas-de-trabajo", "empleos") else None


def fetch(slug: str) -> dict | None:
    bun = shutil.which("bun")
    if not bun:
        sys.exit("falta Bun: instálalo (bash instalar_full.sh) para leer Computrabajo")
    proc = subprocess.run([bun, "run", str(CLI), "company", slug, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=90)
    if proc.returncode != 0:
        return None
    try:
        doc = json.loads(proc.stdout)
    except ValueError:
        return None
    return doc if isinstance(doc.get("rating"), (int, float)) else None


def entry(doc: dict, company: str, today: date) -> dict:
    months = sorted(s.get("date") for s in doc.get("sample") or [] if s.get("date"))
    return {"empresa": company, "source": "Computrabajo", "nombre_en_fuente": doc.get("company"),
            "slug": doc.get("slug"), "rating": doc["rating"], "n": doc.get("reviews"),
            "latest": months[-1] if months else None, "url": doc.get("url"), "fecha": today.isoformat()}


def load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return (doc.get("empresas") or {}) if isinstance(doc, dict) else {}


def write(path: Path, items: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fuente": "Computrabajo (páginas públicas de evaluaciones)",
                                "nota": "Una nota por empresa, con la fecha en que se leyó; "
                                        "tools/pe_calificaciones.py la renueva a los 30 días.",
                                "empresas": dict(sorted(items.items()))}, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def companies(state: Path) -> dict[str, str | None]:
    """Evaluated postings' companies -> a Computrabajo slug when one of their postings has it."""
    seen = json.loads(state.read_text(encoding="utf-8")).get("seen") or {}
    out: dict[str, str | None] = {}
    for e in seen.values():
        name = (e or {}).get("company") or ""
        if e.get("status") != "ranked" or not name.strip() or re.search(r"(?i)confidencial|reservad", name):
            continue
        slug = None
        detail = e.get("detail")
        if detail and Path(detail).is_file():
            try:
                slug = slug_from_url(json.loads(Path(detail).read_text(encoding="utf-8")).get("companyUrl"))
            except (OSError, ValueError):
                pass
        out[name] = out.get(name) or slug
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", type=Path, default=Path("job_scraper/seen_jobs.json"))
    ap.add_argument("--file", type=Path, default=SHARED, help="the shared ratings file (default data/calificaciones.json)")
    ap.add_argument("--empresa", help="only this company (name as the postings write it)")
    ap.add_argument("--slug", help="its Computrabajo slug, when the name does not lead there")
    ap.add_argument("--refresh", action="store_true", help="fetch again even if read in the last 30 days")
    ap.add_argument("--pausa", type=float, default=1.0, help="seconds between requests (default 1)")
    args = ap.parse_args(argv)
    today = date.today()
    items = load(args.file)
    targets = {args.empresa: args.slug} if args.empresa else companies(args.state)
    found = missing = skipped = 0
    for company, slug in sorted(targets.items()):
        key = company_key(company)
        old = items.get(key) or {}
        if not (args.refresh or args.slug) and old.get("fecha") and today - date.fromisoformat(old["fecha"]) < TTL:
            skipped += 1
            continue
        known = slug or old.get("slug")
        doc = None
        for candidate in ([known] if known else guesses(company)):
            doc = fetch(candidate)
            time.sleep(args.pausa)
            if doc and (known or same_company(company, doc.get("company") or "")):
                break
            doc = None
        if doc:
            items[key] = entry(doc, company, today)
            write(args.file, items)
            found += 1
            print(f"  {company}: {doc['rating']} ({doc.get('reviews')} opiniones) · {doc.get('url')}")
        else:
            missing += 1
            print(f'  {company}: sin página en Computrabajo con ese nombre (prueba --empresa "{company}" --slug <slug>)')
    print(f"calificaciones: {found} nuevas, {skipped} ya estaban (menos de 30 días), {missing} sin encontrar · {args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
