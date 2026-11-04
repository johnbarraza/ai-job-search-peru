#!/usr/bin/env python3
"""The search's mechanical steps in one command, with no model in the loop.

Everything here is a script the candidate can run alone; each step writes a file the model
can read later instead of redoing the work (the judgement steps, /rank's fit and the CVs,
stay with the model):

  buscar      run the saved queries through the portal CLIs (bun), keep each result in
              reports/busqueda/<fecha>/ (never over an earlier run), list the postings not yet
              in seen_jobs.json in reports/busqueda/nuevas.json, and record per portal what ran,
              what failed and why in reports/busqueda/cobertura.json. --detalle also saves each
              new posting's detail; --registrar adds them to seen_jobs.json as status "new" for
              /rank to score.
  portales    the portal registry and the last run's coverage, with the links to search by hand
              the portals that have no CLI (no network).
  actualizar  coverage -> duplicates -> freshness -> place and sector -> (logos) -> the review
              page, its JSON twin (reports/revision-ofertas.json) and, with --artifact, the
              claude.ai copy.
  estado      what closes soon and what to do next, from reports/revision-ofertas.json;
              --json prints it as data.
  diario      buscar --detalle --registrar, then (with --ia agy|claude) /rank run headless by
              that CLI, then actualizar; every line also goes to reports/busqueda/diario.log,
              stamped DD/MM/AAAA and 12-hour Peru time. A run cut short resumes that day from the
              step that did not finish (reports/busqueda/diario-estado.json).
  programar   register `diario` as a daily task (Windows Task Scheduler, macOS launchd; on
              Linux it prints the crontab line). --hora takes Peru time (UTC-5) in 12- or
              24-hour form ("8:00 am", "7:30 p. m.", "20:30"); --utc reads it as UTC instead.
              --quitar removes the task.

Abroad: the search is in Lima, Peru. A query placed abroad runs only when the profile's
`Abroad:` line (CLAUDE.md) asks for it: "remote only" runs it with `--remote remote`, and
relocating or travelling runs it as is; without the line it is skipped.

The queries live in reports/busqueda/consultas.json (see docs/PIPELINE.md):
    {"jobage": 14, "excluir_titulo": ["senior", "jefe"],
     "consultas": [{"portal": "linkedin", "q": "practicante finanzas", "l": "Lima, Peru"},
                   {"portal": "linkedin", "q": "research assistant", "l": "Chile"}],
     "consultas_manuales": ["practicante finanzas", "asistente de investigación"]}

Usage (from the profile folder, e.g. perfiles/<nombre>):
    python tools/pe_pipeline.py buscar --detalle
    python tools/pe_pipeline.py actualizar --titulo "Tu Nombre"
    python tools/pe_pipeline.py estado
    python tools/pe_pipeline.py portales
    python tools/pe_pipeline.py programar --hora "8:00 am" --ia agy
"""

import argparse
import base64
import glob
import importlib.util
import json
import os
import plistlib
import re
import shlex
import unicodedata
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pe_frescura = _load("pe_frescura")
pe_revision = _load("pe_revision")
job_key = _load("job_key")
pe_lexico = _load("pe_lexico")

STATE = Path("job_scraper/seen_jobs.json")
PROFILE = Path("CLAUDE.md")
SEARCH_DIR = Path("reports/busqueda")
DETAIL_DIR = Path("reports/detalles")
COVERAGE = SEARCH_DIR / "cobertura.json"
REVIEW_JSON = Path("reports/revision-ofertas.json")
# CLI portal -> (file prefix for searches, file prefix for details), as the earlier runs named them.
PORTALS = {"linkedin": ("li", "li"), "bumeran": ("bu", "bm"), "computrabajo": ("ct", "ct"),
           "talento-peru": ("sv", "sv"), "practicas-pe": ("pr", "pr"),
           "bolsa-universidad": ("up", "up")}
# LinkedIn answers bursts with 429 and then blocks the IP for hours. reports/busqueda/
# linkedin-control.json keeps, across runs, when it may be asked again and which query is next.
LINKEDIN_PAUSE = 10  # seconds between requests within one run
LINKEDIN_SEARCHES = 8  # searches per run; the rest wait for the next run, in rotation
LINKEDIN_DETAILS = 12  # posting details per run
LINKEDIN_REFRESH = timedelta(hours=20)  # one run a day, even if today's starts a bit earlier
LINKEDIN_429_BACKOFF = timedelta(hours=48)
PAGED = {"linkedin", "bumeran", "computrabajo", "bolsa-universidad"}  # CLIs that take --page
MAX_PAGES = 10
# Peru keeps UTC-5 all year (no daylight saving since 1994), so a fixed offset is exact and needs
# no tz database (Windows Python ships none).
PERU = timezone(timedelta(hours=-5), "UTC-5")
DAILY_LOG = SEARCH_DIR / "diario.log"
DAILY_STATE = SEARCH_DIR / "diario-estado.json"
TASK_NAME = "AI Job Search Peru"
# `diario --ia`: /rank run headless by the agent CLI. No flag here widens what the agent may do:
# each CLI applies the user's own permission rules, and what they do not allow is skipped and logged.
RANK_PROMPT = ("Ejecuta el flujo /rank definido en .claude/commands/rank.md sobre este perfil, sin argumentos. "
               "Corres sin supervisión: no hagas preguntas, no postules ni envíes nada; si un paso necesita "
               "confirmación o un permiso que no tienes, sáltalo y dilo en el resumen final. Si no puedes "
               "lanzar subagentes, puntúa tú mismo las ofertas con el mismo procedimiento.")
IA = {
    "agy": ["agy", "-p", RANK_PROMPT, "--output-format", "json", "--print-timeout", "60m"],
    "claude": ["claude", "-p", "/rank"],
    # No model: scores by words against reports/busqueda/ficha.json (tools/pe_lexico.py).
    "lexico": [sys.executable, "-X", "utf8", str(TOOLS / "pe_lexico.py"), "rank"],
}
IA_TIMEOUT = 65 * 60

# Every portal the search covers. `cli` portals run in
# `buscar`; the others get a search link per manual query ({q} is the query). A profile can
# add or override entries with a "portales" list in consultas.json (same keys, matched by id).
REGISTRY = [
    {"id": "linkedin", "nombre": "LinkedIn Jobs", "metodo": "cli",
     "home": "https://www.linkedin.com/jobs/", "url": "https://www.linkedin.com/jobs/search/?keywords={q}&location=Lima%2C%20Peru",
     "nota": "Prácticas y analistas junior en bancos, consultoras y grandes empresas. Limita ráfagas (429)."},
    {"id": "bolsa-universidad", "nombre": "Bolsa UP (Symplicity)", "metodo": "manual",
     "home": "https://up-csm.symplicity.com/students/app/jobs/search?perPage=20&page=1&sort=%21postdate", "url": None,
     "nota": "Con tu cuenta UP. CLI local optativo: login manual en Chrome, sesión temporal fuera de Git."},
    {"id": "talento-peru", "nombre": "Talento Perú (SERVIR)", "metodo": "cli",
     "home": "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/ofertas_laborales.xhtml", "url": None,
      "nota": "Ministerios, reguladores y entidades públicas (CAS y prácticas). Búsqueda por formulario JSF."},
    {"id": "bumeran", "nombre": "Bumeran", "metodo": "cli",
     "home": "https://www.bumeran.com.pe/", "url": "https://www.bumeran.com.pe/empleos-busqueda-{qs}.html",
     "nota": "Prácticas, asistentes, analistas junior y finanzas."},
    {"id": "laborum", "nombre": "Laborum", "metodo": "manual",
     "home": "https://www.laborum.pe/", "url": "https://www.laborum.pe/search-jobs?q={q}",
     "nota": "Empresas privadas: administración, finanzas y análisis. Resultados por JavaScript: sin CLI todavía; o jev-search."},
    {"id": "indeed", "nombre": "Indeed Perú", "metodo": "manual",
     "home": "https://pe.indeed.com/", "url": "https://pe.indeed.com/jobs?q={q}&l=Lima",
     "nota": "Vacantes de muchas empresas y portales. Enlace manual; JobSpy permite una consulta opcional de Indeed Perú (ver docs/PORTALES.md)."},
    {"id": "glassdoor", "nombre": "Glassdoor Perú", "metodo": "manual",
     "home": "https://www.glassdoor.com/", "url": None,
     "nota": "Ofertas, sueldos y reseñas. Busca Lima dentro del portal; JobSpy no declara soporte para Glassdoor Perú."},
    {"id": "computrabajo", "nombre": "Computrabajo", "metodo": "cli",
     "home": "https://pe.computrabajo.com/", "url": "https://pe.computrabajo.com/trabajo-de-{qs}-en-lima",
     "nota": "Mucho volumen; hay que filtrar bastante."},
    {"id": "empleos-peru", "nombre": "Empleos Perú (MTPE)", "metodo": "manual",
     "home": "https://www.empleosperu.gob.pe/portal-mtpe/", "url": None,
     "nota": "Bolsa pública del Ministerio de Trabajo. Búsqueda dentro del portal; o jev-search."},
    {"id": "onpe", "nombre": "Convocatorias ONPE", "metodo": "manual",
     "home": "https://reclutamiento.onpe.gob.pe/convocatorias", "url": None,
     "nota": "Procesos electorales y trabajo de campo. El portal exige iniciar sesión; verifica provincia y plazo en cada convocatoria."},
    {"id": "practicas-pe", "nombre": "Prácticas.pe", "metodo": "cli",
     "home": "https://www.practicas.pe/", "url": "https://www.practicas.pe/resultados-busqueda.php?q={q}&dep=15",
     "nota": "Prácticas públicas y privadas, con ficha y fecha de postulación cuando se publica."},
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", job_key.slugify(text)).strip("-")[:60] or "consulta"


def cli(portal: str) -> list[str]:
    if portal == "bolsa-universidad":
        return [sys.executable, str(TOOLS / "pe_symplicity.py")]
    if portal == "talento-peru":
        return [sys.executable, str(TOOLS / "pe_servir.py")]
    if portal == "practicas-pe":
        return [sys.executable, str(TOOLS / "pe_practicas.py")]
    bun = shutil.which("bun")
    if not bun:
        raise SystemExit("bun no está instalado o no está en el PATH (lo usan las CLIs de los portales)")
    return [bun, "run", str(Path(".agents/skills") / f"{portal}-search/cli/src/cli.ts")]


def run_json(cmd: list[str], out: Path | None = None, timeout: int = 180) -> tuple[object, str | None]:
    """Run a portal CLI and keep its stdout as a file. Returns (parsed JSON, None) or (None, error)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)[:200]
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        try:
            err = json.loads(err).get("error") or err
        except (ValueError, AttributeError):
            pass
        return None, str(err)[:200] or f"código {proc.returncode}"
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None, "la salida no es JSON"
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data, None


def known_ids(state: dict) -> set[str]:
    ids = set()
    for e in (state.get("seen") or {}).values():
        if not isinstance(e, dict):
            continue
        ids.add(pe_frescura.posting_id({"id": e.get("id"), "url": e.get("url")}))
        m = re.search(r"practicas\.pe/oferta-[^/]+-(\d+)\.html", e.get("url") or "")
        if m:
            ids.add(m.group(1))
    return ids


def load_config(path: Path) -> dict:
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def registry(cfg: dict) -> list[dict]:
    """The built-in portals, with the profile's own entries (consultas.json "portales") on top."""
    out = {p["id"]: dict(p) for p in REGISTRY}
    for p in cfg.get("portales") or []:
        if isinstance(p, dict) and p.get("id"):
            out[p["id"]] = {**out.get(p["id"], {}), **p}
    if any(c.get("portal") == "bolsa-universidad" for c in cfg.get("consultas") or []):
        out["bolsa-universidad"]["metodo"] = "cli"
    return list(out.values())


def manual_queries(cfg: dict) -> list[str]:
    qs = cfg.get("consultas_manuales")
    if isinstance(qs, list) and qs:
        return [q for q in qs if isinstance(q, str)][:8]
    seen, out = set(), []
    for c in cfg.get("consultas") or []:
        q = (c.get("q") or "").strip()
        if q and q.lower() not in seen and pe_revision.PERU_PLACES.search(c.get("l") or "Lima"):
            seen.add(q.lower())
            out.append(q)
    return out[:5]


def search_links(portal: dict, queries: list[str]) -> list[dict]:
    url = portal.get("url")
    if not url:
        return [{"q": "abrir", "url": portal["home"]}] if portal.get("home") else []
    return [{"q": q, "url": url.replace("{q}", quote_plus(q)).replace("{qs}", slug(q))} for q in queries]


def history(portal: str) -> dict | None:
    """Coverage of the runs made before this script, read from what they left in reports/busqueda*/:
    one <prefix>_<consulta>.json per query that ran, a non-empty .json.err per query that failed."""
    prefix = PORTALS[portal][0] + "_"
    ok = failed = results = 0
    errors: list[str] = []
    latest = 0.0
    for folder in sorted(Path("reports").glob("busqueda*")):
        for f in folder.rglob(prefix + "*.json*"):
            if f.name.endswith(".json.err"):
                text = f.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                failed += 1
                try:
                    text = json.loads(text).get("error") or text
                except (ValueError, AttributeError):
                    pass
                errors.append(f"{f.name[len(prefix):-9]}: {str(text)[:120]}")
            elif f.name.endswith(".json"):
                try:
                    results += len(json.loads(f.read_text(encoding="utf-8")).get("results") or [])
                except (OSError, ValueError, AttributeError):
                    continue
                ok += 1
            latest = max(latest, f.stat().st_mtime)
    if not ok and not failed:
        return None
    # Repeated errors read once, with how many times they happened.
    counts: dict[str, int] = {}
    for e in errors:
        msg = e.split(": ", 1)[-1]
        counts[msg] = counts.get(msg, 0) + 1
    summary = [f"{msg} ×{n}" if n > 1 else msg for msg, n in counts.items()]
    return {"consultas": ok + failed, "ok": ok, "fallidas": failed, "resultados": results, "errores": summary,
            "fecha": date.fromtimestamp(latest).isoformat() + " (búsquedas anteriores)"}


def write_coverage(cfg: dict, runs: dict | None = None, when: str | None = None) -> dict:
    """reports/busqueda/cobertura.json: each portal with how it is covered, the last run's
    counts and errors (kept from the previous file for portals this run did not touch)."""
    previous = load_config(COVERAGE)
    before = {p.get("id"): p for p in previous.get("portales") or [] if isinstance(p, dict)}
    queries = manual_queries(cfg)
    portals = []
    for p in registry(cfg):
        entry = dict(p, enlaces=search_links(p, queries))
        if p.get("metodo") == "cli":
            run = (runs or {}).get(p["id"]) or ({k: before[p["id"]].get(k) for k in
                                                  ("consultas", "ok", "fallidas", "resultados", "errores", "fecha")}
                                                 if before.get(p["id"], {}).get("consultas") else None)
            run = run or (history(p["id"]) if p["id"] in PORTALS else None) or {}
            entry.update({k: v for k, v in run.items() if v is not None})
        portals.append(entry)
    doc = {"fecha": when or previous.get("fecha") or date.today().isoformat(), "portales": portals}
    COVERAGE.parent.mkdir(parents=True, exist_ok=True)
    COVERAGE.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return doc


def cmd_buscar(args) -> int:
    cfg = load_config(args.consultas)
    if not cfg.get("consultas"):
        print(f"no hay consultas en {args.consultas} (formato en docs/PIPELINE.md)", file=sys.stderr)
        return 1
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {"seen": {}}
    abroad = pe_revision.read_abroad(args.perfil)
    seen_ids = known_ids(state)
    jobage = args.jobage or cfg.get("jobage")
    exclude = [w.lower() for w in cfg.get("excluir_titulo") or []]
    queries = [c for c in cfg["consultas"] if c.get("portal") in PORTALS and (not args.portal or c["portal"] == args.portal)]
    if any(c["portal"] == "bolsa-universidad" for c in queries) and not cfg.get("symplicity_account"):
        print('falta "symplicity_account" en consultas.json para la Bolsa UP (ejemplo: "mi-cuenta")', file=sys.stderr)
        return 1
    today = date.today().isoformat()
    run_dir = SEARCH_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    suffix = 2
    while run_dir.exists():
        run_dir = SEARCH_DIR / (datetime.now().strftime("%Y-%m-%d_%H%M%S") + f"_{suffix}")
        suffix += 1
    runs = {p: {"consultas": 0, "ok": 0, "fallidas": 0, "resultados": 0, "errores": [], "fecha": today}
            for p in {c["portal"] for c in queries}}
    new: dict[str, dict] = {}
    excluded = skipped_abroad = 0
    control_file = SEARCH_DIR / "linkedin-control.json"
    try:
        linkedin_control = json.loads(control_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        linkedin_control = {}
    if not isinstance(linkedin_control, dict):
        linkedin_control = {}
    linkedin_indices = [i for i, c in enumerate(queries) if c["portal"] == "linkedin"]
    cursor = int(linkedin_control.get("cursor") or 0) % max(1, len(linkedin_indices))
    selected = {linkedin_indices[(cursor + n) % len(linkedin_indices)]
                for n in range(min(LINKEDIN_SEARCHES, len(linkedin_indices)))}
    linkedin_control["cursor"] = (cursor + len(selected)) % max(1, len(linkedin_indices))
    last_linkedin, linkedin_searches, linkedin_details, linkedin_429 = 0.0, 0, 0, False

    def save_linkedin(blocked=False):
        delay = LINKEDIN_429_BACKOFF if blocked else LINKEDIN_REFRESH
        linkedin_control["next_at"] = (datetime.now() + delay).isoformat(timespec="seconds")
        linkedin_control["motivo"] = "429 de LinkedIn" if blocked else "última corrida"
        control_file.parent.mkdir(parents=True, exist_ok=True)
        control_file.write_text(json.dumps(linkedin_control, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        linkedin_paused = datetime.now() < datetime.fromisoformat(linkedin_control.get("next_at") or "")
    except (TypeError, ValueError):
        linkedin_paused = False
    for i, c in enumerate(queries, 1):
        portal, run = c["portal"], runs[c["portal"]]
        if portal == "linkedin" and (linkedin_paused or linkedin_429 or i - 1 not in selected):
            run["omitidas"] = run.get("omitidas", 0) + 1
            continue
        place = c.get("l") or ""
        cmd = cli(portal) + ["search"]
        if portal == "bolsa-universidad":
            cmd += ["--account", cfg["symplicity_account"]]
        if c.get("q"):
            cmd += ["-q", c["q"]]
        if place and portal != "bolsa-universidad":
            cmd += ["-l", place]
        if jobage and portal not in ("practicas-pe", "bolsa-universidad"):
            cmd += ["--jobage", str(jobage)]
        if portal == "talento-peru" and c.get("tipo"):
            cmd += ["--tipo", c["tipo"]]
        if portal == "talento-peru" and c.get("campo") in ("perfil", "puesto"):
            cmd += ["--campo", c["campo"]]
        remote = c.get("remote")
        abroad_query = portal == "linkedin" and bool(place) and not pe_revision.PERU_PLACES.search(place)
        if abroad_query and abroad["mode"] == "no":
            skipped_abroad += 1  # the profile searches at home only
            continue
        if abroad_query and abroad["mode"] == "remoto" and not remote:
            remote = "remote"  # abroad: remote only, as the profile says
        if remote:
            cmd += ["--remote", remote]
        print(f"[{i}/{len(queries)}] {portal}: {c.get('q')} · {place}" + (f" · {remote}" if remote else ""))
        run["consultas"] += 1
        stem = f'{PORTALS[portal][0]}_{slug(" ".join(x for x in (c.get("q"), c.get("tipo") if portal == "talento-peru" else "", c.get("campo") if portal == "talento-peru" and c.get("campo") != "perfil" else "", place if portal == "linkedin" and "lima" not in place.lower() else "") if x))}'
        # One page is 20 results on Computrabajo and Bumeran and 10 on LinkedIn: a broad query
        # needs more. LinkedIn has its own default, since every page is another request to it.
        default_pages = cfg.get("paginas_linkedin") if portal == "linkedin" else cfg.get("paginas")
        pages = max(1, min(MAX_PAGES, int(c.get("paginas") or default_pages or 1))) if portal in PAGED else 1
        results, failed, seen_page = [], None, set()
        for page in range(1, pages + 1):
            if portal == "linkedin":
                if page > 1 and linkedin_searches >= LINKEDIN_SEARCHES:
                    break  # further pages draw on the same per-run budget
                time.sleep(max(0, LINKEDIN_PAUSE - (time.monotonic() - last_linkedin)))
                last_linkedin = time.monotonic()
                linkedin_searches += 1
                save_linkedin()
            page_cmd = cmd + (["--page", str(page)] if page > 1 else [])
            data, error = run_json(page_cmd, run_dir / (stem + (f"-p{page}" if page > 1 else "") + ".json"))
            if error:
                if portal == "linkedin" and "429" in error:
                    linkedin_429 = True  # stop asking: every further request lengthens the block
                    save_linkedin(blocked=True)
                if page == 1:
                    failed = error
                else:  # the first pages already came back: keep them, note the gap
                    run["errores"].append(f"{c.get('q')} · {place} · página {page}: {error}")
                break
            rows = [p for p in (data or {}).get("results") or [] if isinstance(p, dict)]
            ids = {pe_frescura.posting_id(p) for p in rows} - seen_page
            if not ids:
                break  # an empty page, or the last one again: the listing ended
            seen_page |= ids
            results += rows
            if page > 1:
                run["paginas_extra"] = run.get("paginas_extra", 0) + 1
        if failed:
            run["fallidas"] += 1
            run["errores"].append(f"{c.get('q')} · {place}: {failed}")
            print(f"  falló: {failed}", file=sys.stderr)
            continue
        run["ok"] += 1
        run["resultados"] += len(results)
        for p in results:
            pid = pe_frescura.posting_id(p)
            if not pid or pid in seen_ids or pid in new:
                continue
            if any(w in (p.get("title") or "").lower() for w in exclude):
                excluded += 1
                continue
            new[pid] = {"id": pid, "portal": portal, "title": p.get("title"), "company": p.get("company"),
                        "location": p.get("location"), "date": p.get("date"), "url": p.get("url"),
                        "search_url": p.get("search_url"),
                        "modality": p.get("modality") or ("remoto" if remote == "remote" else None),
                         "level": p.get("level"), "query": c.get("q"), "detail": None,
                         "deadline": p.get("deadline"), "validThrough": p.get("validThrough"),
                         "ref": p.get("ref"), "type": p.get("type"),
                         "salary": p.get("salary"), "requirements": p.get("requirements")}

    if args.detalle:
        for j, p in enumerate(new.values(), 1):
            if p["portal"] == "bolsa-universidad":
                ident = p["id"].split(":", 1)[-1].lower()
                out = DETAIL_DIR / f"up_{ident}.json"
                try:
                    detail = json.loads(out.read_text(encoding="utf-8"))
                    if not isinstance(detail, dict) or not detail.get("description"):
                        raise ValueError("ficha incompleta")
                except (OSError, ValueError):
                    print(f"detalle {j}/{len(new)}: {p['title']}")
                    detail, error = run_json(cli(p["portal"]) + ["detail", p["id"], "--account", cfg["symplicity_account"]], out)
                    if error:
                        runs[p["portal"]]["errores"].append(f"detalle {p['id']}: {error}")
                        continue
                p["detail"] = str(out).replace("\\", "/")
                p["requirements"] = detail.get("description") or p.get("requirements")
                p["deadline"] = detail.get("deadline") or p.get("deadline")
                p["modality"] = (detail.get("modality") or p.get("modality") or "").lower() or None
                p["level"] = detail.get("job_type") or p.get("level")
                p["academic_level"] = detail.get("academic_level")
                continue
            ident = p["id"].split(":", 1)[-1].lower() if p["portal"] == "talento-peru" else slug(p["id"])
            out = DETAIL_DIR / f'{PORTALS[p["portal"]][1]}_{ident}.json'
            if not out.is_file() or (p["portal"] == "talento-peru" and "description" not in json.loads(out.read_text(encoding="utf-8"))):
                print(f"detalle {j}/{len(new)}: {p['title']}")
                if p["portal"] == "linkedin":
                    if linkedin_429 or linkedin_details >= LINKEDIN_DETAILS:
                        continue  # /rank reads the posting page itself
                    time.sleep(max(0, LINKEDIN_PAUSE - (time.monotonic() - last_linkedin)))
                    last_linkedin = time.monotonic()
                    linkedin_details += 1
                if p["portal"] == "talento-peru":
                    detail, error = run_json(cli(p["portal"]) + ["detail", p["id"], "--titulo", p["title"],
                                               "--tipo", p["type"], "--ref", p.get("ref") or ""], out)
                    if not error and isinstance(detail, dict):
                        p["requirements"] = detail.get("description")
                else:
                    detail, error = run_json(cli(p["portal"]) + ["detail", p["url"] or p["id"]], out)
                    if not error and p["portal"] == "practicas-pe" and isinstance(detail, dict):
                        p["date"] = detail.get("date") or p["date"]
                        p["deadline"] = detail.get("deadline") or p["deadline"]
                if error:
                    if p["portal"] == "linkedin" and "429" in error:
                        linkedin_429 = True
                        save_linkedin(blocked=True)
                    runs[p["portal"]]["errores"].append(f"detalle {p['id']}: {error}")
                    continue
            p["detail"] = str(out).replace("\\", "/")

    applied_ids = set()
    if "bolsa-universidad" in runs:
        applications, error = run_json(cli("bolsa-universidad") + ["applications", "--account", cfg["symplicity_account"]],
                                       run_dir / "up_solicitudes.json")
        if error:
            runs["bolsa-universidad"]["errores"].append("Solicitudes: " + error)
        else:
            applied_ids = {pe_frescura.posting_id(p) for p in applications.get("results") or []}
            for p in new.values():
                if p["portal"] == "bolsa-universidad":
                    p["already_applied"] = p["id"] in applied_ids

    report = {"generated": today, "jobage": jobage, "consultas": len(queries), "extranjero": abroad["mode"],
              "excluidas_por_titulo": excluded,
              "ya_postuladas_bolsa_up": sum(bool(p.get("already_applied")) for p in new.values()),
              "nuevas": sorted(new.values(), key=lambda p: p.get("date") or "", reverse=True)}
    SEARCH_DIR.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    latest = SEARCH_DIR / "nuevas.json"
    if latest.is_file():
        shutil.copyfile(latest, run_dir / "nuevas-anteriores.json")
    (run_dir / "nuevas.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    write_coverage(cfg, runs, today)
    shutil.copyfile(COVERAGE, run_dir / "cobertura.json")

    if args.registrar:
        seen = state.setdefault("seen", {})
        for p in new.values():
            key = ("up_" + p["id"].split(":", 1)[-1].lower() if p["portal"] == "bolsa-universidad" else
                   "servir_" + p["id"].split(":", 1)[-1] if p["portal"] == "talento-peru"
                   else "practicas_" + p["id"] if p["portal"] == "practicas-pe"
                   else job_key.make_key(p.get("company") or "", p.get("title") or "", p.get("url") or ""))
            seen.setdefault(key, {
                "id": p["id"], "title": p.get("title"), "company": p.get("company"), "url": p.get("url"),
                "search_url": p.get("search_url"), "ref": p.get("ref"), "location": p.get("location"),
                "first_seen": today, "posted_date": p.get("date"), "deadline": p.get("deadline"),
                "validThrough": p.get("validThrough"), "salary": p.get("salary"),
                "fit": None, "status": "applied" if p.get("already_applied") else "new",
                "portal": f'{p["portal"]}-search', "source": "cli",
                "modality": p.get("modality"), "level": p.get("level"), "detail": p.get("detail"),
                "academic_level": p.get("academic_level"),
                "how_to_apply": f'Busca la convocatoria {p["ref"]} en SERVIR; abre «Ver más» y comprueba las bases y el plazo en la entidad.'
                if p["portal"] == "talento-peru" else
                'Abre la ficha en la Bolsa UP con tu sesión y comprueba requisitos y plazo antes de postular.'
                if p["portal"] == "bolsa-universidad" else None,
            })
        applied_changed = False
        if applied_ids:
            for entry in seen.values():
                if isinstance(entry, dict) and pe_frescura.posting_id(entry) in applied_ids:
                    if entry.get("status") == "new":
                        entry["status"] = "applied"
                        applied_changed = True
                    if entry.get("application_status") != "applied":
                        entry["application_status"] = "applied"
                        applied_changed = True
                    if entry.get("application_source") != "bolsa-up-solicitudes":
                        entry["application_source"] = "bolsa-up-solicitudes"
                        applied_changed = True
        if new or applied_changed:
            args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    for portal, run in sorted(runs.items()):
        print(f"{portal}: {run['ok']}/{run['consultas']} consultas ok · {run['resultados']} resultados"
              + (f" · {run['fallidas']} fallidas" if run["fallidas"] else ""))
    if skipped_abroad:
        print(f"{skipped_abroad} consultas fuera del Perú omitidas: el perfil no pide buscar en el extranjero (línea Abroad)")
    if runs.get("linkedin", {}).get("omitidas"):
        print(f"linkedin: {runs['linkedin']['omitidas']} consultas en espera · vuelve a consultarse desde "
              f"{linkedin_control.get('next_at')} ({linkedin_control.get('motivo')}; {control_file})")
    print(f"\n{len(new)} detectadas (no estaban en {args.state}) · "
          f"{report['ya_postuladas_bolsa_up']} ya postuladas en Bolsa UP · {excluded} excluidas por título"
          f" → {SEARCH_DIR / 'nuevas.json'}" + (" · registradas como 'new'" if args.registrar and new else ""))
    print(f"Copia de esta corrida: {run_dir}")
    print(f"Cobertura y enlaces de portales: {COVERAGE} (python tools/pe_pipeline.py portales)")
    if new and not args.registrar:
        print("Para evaluarlas: usa /rank con cualquier agente leyendo reports/busqueda/nuevas.json, o vuelve a correr con --registrar.")
    return 0 if all(r["ok"] or not r["consultas"] for r in runs.values()) else 2  # a portal on hold is not a failure


def cmd_portales(args) -> int:
    doc = write_coverage(load_config(args.consultas))
    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    print(f"Portales (registro del {doc['fecha']}):")
    for p in doc["portales"]:
        if p.get("metodo") == "cli":
            state = (f"{p.get('ok', 0)}/{p.get('consultas')} ok, {p.get('resultados', 0)} resultados"
                     if p.get("consultas") else "sin correr")
        else:
            state = "a mano"
        print(f"  {p['nombre']:<24} {'CLI' if p.get('metodo') == 'cli' else 'enlace':<7} {state}")
        for e in (p.get("errores") or [])[:2]:
            print(f"  {'':<42}error: {e}")
        if p.get("metodo") != "cli":
            for x in p.get("enlaces") or []:
                print(f"  {'':<42}{x['q']}: {x['url']}")
    return 0


def step(label: str, cmd: list[str], out: Path | None = None) -> bool:
    print(f"· {label}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        print(f"  falló: {(proc.stderr or proc.stdout).strip()[:400]}", file=sys.stderr)
        return False
    if out:
        out.write_text(proc.stdout, encoding="utf-8")
    elif proc.stdout.strip():
        print("  " + proc.stdout.strip().splitlines()[-1])
    return True


def cmd_actualizar(args) -> int:
    py = [sys.executable, "-X", "utf8"]
    config = load_config(args.consultas)
    write_coverage(config)
    details = sorted(glob.glob(str(DETAIL_DIR / "*.json")))
    today = ["--today", args.today.isoformat()]
    ok = True
    if details:
        ok &= step("Duplicados entre portales", py + [str(TOOLS / "pe_duplicados.py"), *details, "--write"])
        pool = SEARCH_DIR / "pool.json"
        calibrate = [str(pool)] if pool.is_file() else sorted(
            f for f in glob.glob(str(SEARCH_DIR / "**" / "*.json"), recursive=True)
            if Path(f).name not in ("nuevas.json", "cobertura.json", "consultas.json"))
        ok &= step("Frescura", py + [str(TOOLS / "pe_frescura.py"), *details, "--calibrar", *calibrate, "--json", *today],
                   Path("reports/frescura.json"))
        ok &= step("Lugar y rubro", py + [str(TOOLS / "pe_contexto.py"), *details, "--json"], Path("reports/contexto.json"))
        if args.logos:
            # Folders, not files: hundreds of paths would overflow a Windows command line.
            ok &= step("Logos (descarga)", py + [str(TOOLS / "pe_logos.py"), str(DETAIL_DIR), str(SEARCH_DIR)])
        if args.calificaciones:
            ok &= step("Calificaciones de Computrabajo (descarga)", py + [str(TOOLS / "pe_calificaciones.py")])
    else:
        print(f"· sin detalles en {DETAIL_DIR}: salto duplicados, frescura y lugar")
    title = ["--titulo", args.titulo] if args.titulo else []
    initial_focus = args.filtro_inicial or config.get("filtro_inicial")
    focus = ["--filtro-inicial", initial_focus] if initial_focus else []
    ok &= step("Página de revisión", py + [str(TOOLS / "pe_revision.py"), *title, *focus, *today, "--json", str(REVIEW_JSON)])
    if args.artifact:
        ok &= step("Copia para claude.ai", py + [str(TOOLS / "pe_revision.py"), *title, *focus, *today, "--artifact",
                                                 "--out", "reports/revision-ofertas-artifact.html",
                                                 "--cv-manifest", "reports/cv_manifest.json"])
    print()
    estado(REVIEW_JSON, as_json=False)
    return 0 if ok else 1


REMOTE_FLAG = {"remoto": "remote", "hibrido": "hybrid", "presencial": "onsite"}
STAGE_LEVEL = {"estudiante": "práctica preprofesional",
               "egresado": "práctica profesional; trainee; analista junior",
               "profesional": "analista; asociado; junior"}
MODE_TEXT = {"remoto": "remoto", "hibrido": "híbrido", "presencial": "presencial"}
ABROAD_TEXT = {"lima": "no: solo en el Perú", "peru": "no: solo en el Perú",
               "afuera-remoto": "solo remoto fuera del Perú", "afuera": "dispuesto a mudarme o viajar"}
RESEARCH_PHRASES = ["asistente de investigación", "research assistant"]
# empezar.sh's area menu: key -> the word the portals are searched with.
AREAS = {"economia": "economía", "finanzas": "finanzas", "contabilidad": "contabilidad",
         "administracion": "administración", "industrial": "ingeniería industrial", "datos": "datos",
         "marketing": "marketing", "derecho": "derecho", "rrhh": "recursos humanos",
         "ingenieria": "ingeniería", "comunicaciones": "comunicaciones"}
# The role word each stage searches with, before the area ("practicante finanzas").
STAGE_WORDS = {"estudiante": ["practicante"], "egresado": ["practicante", "analista junior"],
               "profesional": ["analista"], None: ["practicante", "analista"]}
CYCLE_TEXT = {"1-4": "1.º a 4.º ciclo", "5-6": "5.º o 6.º ciclo", "7-8": "7.º u 8.º ciclo",
              "9+": "9.º ciclo o más (último año)"}
EXPERIENCE_TEXT = {"ninguna": "sin experiencia laboral", "practicas": "prácticas previas", "menos1": "menos de 1 año",
                   "1-3": "1 a 3 años", "3-5": "3 a 5 años", "5+": "más de 5 años"}
CERT_TEXT = {"cfa1": "CFA nivel I", "cfa2": "CFA nivel II", "cfa3": "CFA nivel III", "frm": "FRM", "pmp": "PMP",
             "bmc": "Bloomberg Market Concepts"}
MERIT_TEXT = {"decimo": "Décimo superior", "quinto": "Quinto superior", "tercio": "Tercio superior",
              "medio": "Medio superior", "ninguno": "ninguno (sin décimo, quinto ni tercio superior)"}


# The CV reader lives in pe_lexico.py, with the word-based ranking it feeds.
AREA_HINTS, cv_text, read_cv = pe_lexico.AREA_HINTS, pe_lexico.cv_text, pe_lexico.read_cv


def cmd_leer_cv(args) -> int:
    """empezar.sh's CV option: print what the CV says as shell assignments (fixed vocabulary only)."""
    return pe_lexico.cmd_leer_cv(args)


def fill_profile(path: Path, values: dict[str, str], lines: dict[str, str] | None = None) -> list[str]:
    """Write the answers into the profile's CLAUDE.md where its template placeholders still are,
    and add `- **Label:** value` lines (ciclo, mérito) after the Target level line when absent;
    what /setup (or the user) already wrote is never touched."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    done = [k for k, v in values.items() if v and k in text]
    for k in done:
        text = text.replace(k, values[k], 1)
    anchor = re.search(r"^- \*\*Target level:\*\*.*$", text, re.M)
    new = {label: value for label, value in (lines or {}).items() if value and f"**{label}:**" not in text}
    if anchor and new:
        text = text[:anchor.end()] + "".join(f"\n- **{k}:** {v}" for k, v in new.items()) + text[anchor.end():]
        done += list(new)
    if done:
        path.write_text(text, encoding="utf-8")
    return done


def cmd_consultas(args) -> int:
    """A first reports/busqueda/consultas.json from empezar.sh's answers, every one optional: the
    sectors (privado, publico, investigacion, otros), the stage (estudiante, egresado, profesional),
    the areas, the work mode and where (Lima, another Peruvian city, also abroad remote or moving).
    No typing needed: the phrases are the stage's role word plus each area ("practicante finanzas");
    --q adds phrases of one's own. Only the chosen sectors are searched: private and other
    employers on Bumeran, Computrabajo and LinkedIn (and Prácticas.pe for interns), the public
    sector on SERVIR, research with research-assistant phrases. The answers, ciclo and mérito
    included, also fill the profile's CLAUDE.md where it is still empty.
    Refuses to overwrite a file of the user's, unless --reemplazar."""
    spec = importlib.util.spec_from_file_location("perfiles", TOOLS / "perfiles.py")
    perfiles = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(perfiles)
    starter = load_config(args.consultas) == perfiles.STARTER_QUERIES  # perfiles.py crear's example
    if args.es_ejemplo:  # for empezar.sh: 0 = nothing of the user's yet (missing or the example)
        return 0 if starter or not args.consultas.exists() else 3  # 3, not 1: a crash must not read as "yours exists"
    if args.consultas.exists() and not starter and not args.reemplazar:
        print(f"ya existe {args.consultas}: no lo toco (edítalo, o usa --reemplazar)")
        return 0
    split = lambda values: [x.strip() for x in ",".join(values).split(",") if x.strip()]
    sectors = set(split(args.sector)) or {"privado", "publico", "investigacion", "otros"}
    modes = [m for m in split(args.modalidad) if m in REMOTE_FLAG]
    # Only "remote only" filters the search: many postings (most internships) declare no mode, and a
    # hybrid or on-site filter would drop them. Those preferences go to the profile for /rank and the page.
    remote = "remote" if modes == ["remoto"] else None
    stage = args.etapa
    intern = stage != "profesional"  # an unanswered stage searches internships too
    areas = [AREAS[a] for a in split(args.area) if a in AREAS]
    words = STAGE_WORDS[stage]
    phrases = split(args.q) or ([f"{w} {a}" for a in areas for w in words] if areas else list(words))
    if "investigacion" in sectors:
        phrases += RESEARCH_PHRASES
    phrases = list(dict.fromkeys(phrases))[:10]
    by_area = areas[:3] or [""]  # Prácticas.pe and SERVIR search a career or field word, or everything
    city = args.ciudad.strip() or "Lima"
    queries: list[dict] = []
    if sectors & {"privado", "otros", "investigacion"}:
        mode = {"remote": remote} if remote else {}
        queries += [{"portal": p, "q": q, "l": city, **mode} for p in ("bumeran", "computrabajo") for q in phrases]
        queries += [{"portal": "linkedin", "q": q, "l": f"{city}, Peru", **mode} for q in phrases]
        if intern:
            queries += [{"portal": "practicas-pe", "q": q, "l": city} for q in by_area]
        if args.lugar in ("afuera-remoto", "afuera"):
            abroad = {"remote": "remote"} if args.lugar == "afuera-remoto" else {}
            queries += [{"portal": "linkedin", "q": q, "l": "Latin America", **abroad} for q in phrases[:3]]
    if "publico" in sectors:
        if intern:
            queries.append({"portal": "talento-peru", "q": "", "l": city, "tipo": "practicas"})
        if stage != "estudiante":
            queries += [{"portal": "talento-peru", "q": q, "l": city, "tipo": "laboral"} for q in by_area]
    exclude = ["senior", "jefe", "gerente", "director"] + (["coordinador", "supervisor"] if intern else [])
    cfg = {"jobage": 14, "excluir_titulo": exclude + args.excluir, "consultas_manuales": phrases[:8], "consultas": queries}
    args.consultas.parent.mkdir(parents=True, exist_ok=True)
    args.consultas.write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    # The ficha: what the word-based ranking (pe_lexico.py) compares each posting with.
    ficha = {"carreras": [a for a in split(args.area) if a in AREAS], "universidad": args.universidad.strip(),
             "etapa": stage or "", "ciclo": args.ciclo or "", "merito": args.merito or "",
             "experiencia": args.experiencia or "", "ingles": args.ingles or "",
             "herramientas": [k for k in split(args.herramientas) if k in pe_lexico.TOOL_HINTS],
             "certificaciones": [k for k in split(args.certificaciones) if k in pe_lexico.CERT_HINTS],
             "sectores": sorted(sectors), "modalidad": modes, "lugar": args.lugar or "lima", "ciudad": city}
    (args.consultas.parent / "ficha.json").write_text(json.dumps(ficha, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    filled = fill_profile(args.perfil, {
        "[YOUR_TARGET_LEVEL]": STAGE_LEVEL.get(stage or "", ""),
        "[YOUR_WORK_MODE_PREFERENCE]": " o ".join(MODE_TEXT[m] for m in modes) if modes else
                                        ("presencial, híbrido o remoto" if split(args.modalidad) else ""),
        "[YOUR_ABROAD]": ABROAD_TEXT.get(args.lugar or "", ""),
        "[YOUR_CITY]": city if args.lugar else "", "[YOUR_COUNTRY]": "Perú" if args.lugar else "",
        "[INSTITUTION]": args.universidad.strip(),
        "[YOUR_PRIMARY_SKILLS]": ", ".join(pe_lexico.TOOL_NAMES[k] for k in ficha["herramientas"])},
        {"Ciclo": CYCLE_TEXT.get(args.ciclo or "", ""), "Mérito académico": MERIT_TEXT.get(args.merito or "", ""),
         "Inglés": args.ingles or "", "Experiencia": EXPERIENCE_TEXT.get(args.experiencia or "", ""),
         "Certificaciones": ", ".join(CERT_TEXT[k] for k in ficha["certificaciones"])})
    where = {"privado": "privado", "publico": "público", "investigacion": "investigación", "otros": "otros"}
    print(f"{len(queries)} búsquedas en {args.consultas} · sector {', '.join(where[s] for s in sorted(sectors) if s in where)}"
          + (f" · {', '.join(phrases[:4])}{'…' if len(phrases) > 4 else ''}" if sectors - {"publico"} else "")
          + f" · {city}" + (" · solo remoto" if remote else "")
          + ". Puedes editarlo cuando quieras (formato en docs/PIPELINE.md).")
    if filled:
        names = {"[YOUR_TARGET_LEVEL]": "nivel", "[YOUR_WORK_MODE_PREFERENCE]": "modalidad", "[YOUR_ABROAD]": "fuera del Perú",
                 "[YOUR_CITY]": "ciudad", "[YOUR_COUNTRY]": None, "Ciclo": "ciclo", "Mérito académico": "mérito",
                 "[INSTITUTION]": "universidad", "[YOUR_PRIMARY_SKILLS]": "herramientas", "Inglés": "inglés",
                 "Experiencia": "experiencia", "Certificaciones": "certificaciones"}
        print(f"  tu ficha para el puntaje sin IA: {args.consultas.parent / 'ficha.json'}")
        print(f"  también en {args.perfil}: {', '.join(n for k in filled if (n := names.get(k, k)))} (solo lo que estaba vacío)")
    return 0


def cmd_descartar(args) -> int:
    """Postings discarded on the review page ("Descartar" -> "Copiar comando"): status "skipped" in
    seen_jobs.json, so they leave the page and later searches skip them. --deshacer restores them."""
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no puedo leer {args.state}: {exc}", file=sys.stderr)
        return 1
    seen = state.get("seen") or {}
    changed, missing = [], []
    for key in args.claves:
        e = seen.get(key)
        if not isinstance(e, dict):
            missing.append(key)
        elif args.deshacer and "discarded_by_user" in e:
            e["status"] = e.pop("status_before_discard", None) or "ranked"
            e.pop("discarded_by_user")
            changed.append(e.get("title") or key)
        elif not args.deshacer and "discarded_by_user" not in e:
            e["status_before_discard"], e["status"] = e.get("status"), "skipped"
            e["discarded_by_user"] = date.today().isoformat()
            changed.append(e.get("title") or key)
    if changed:
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    verb = "vuelven a la lista" if args.deshacer else "descartadas"
    print(f"{len(changed)} {verb}" + "".join(f"\n  · {t}" for t in changed))
    for key in missing:
        print(f"  no está en {args.state}: {key}", file=sys.stderr)
    if changed:
        print("Para ver la página sin ellas: python tools/pe_pipeline.py actualizar")
    return 1 if missing and not changed else 0


def estado(path: Path, as_json: bool, limit: int = 10) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"no puedo leer {path}: {exc} (corre primero: python tools/pe_pipeline.py actualizar)", file=sys.stderr)
        return 1
    rows = data.get("para_decidir") or []
    soon = sorted((r for r in rows if isinstance(r.get("days_to_close"), int) and 0 <= r["days_to_close"] <= 7),
                  key=lambda r: (r["days_to_close"], -(r.get("score") or 0)))
    ready = [r for r in rows if r["action"] in ("ya", "pronto")]
    brief = {
        "generated": data.get("generated"), "counts": data.get("counts"), "preferencias": data.get("preferencias"),
        "cierran_7_dias": [{k: r.get(k) for k in ("title", "company", "closes_label", "score", "how_to_apply", "cv", "url")} for r in soon],
        "postular": [{k: r.get(k) for k in ("title", "company", "score", "freshness", "closes_label", "cv", "url")} for r in ready[:limit]],
        "sin_cv_a_medida": [f'{r["title"]} · {r["company"]}' for r in ready if not r.get("cv")][:limit],
    }
    if as_json:
        print(json.dumps(brief, ensure_ascii=False, indent=1))
        return 0
    c = data.get("counts") or {}
    print(f"Estado al {data.get('generated')}: {c.get('ranked')} evaluadas · postular {c.get('postular')} · "
          f"confirmar {c.get('confirmar')} · si hay tiempo {c.get('si_hay_tiempo')} · cerradas {c.get('cerradas', 0)}")
    if soon:
        print("\nCierran en 7 días:")
        for r in soon:
            cv = "CV listo" if r.get("cv") else "sin CV a medida"
            print(f"  {r['closes_label']:<20} {r.get('score')!s:>3}  {r['title']} · {r['company']}  [{cv}]")
            if r.get("how_to_apply"):
                print(f"  {'':<25}{r['how_to_apply']}")
    if ready:
        print(f"\nPostular (primeras {min(limit, len(ready))} de {len(ready)}):")
        for r in ready[:limit]:
            print(f"  {r.get('score')!s:>3}  {r['title']} · {r['company']}  ({r.get('freshness')})")
    return 0


def parse_hora(text: str) -> tuple[int, int]:
    """'8', '8:30', '08:30', '20:30', '8am', '7:30 p. m.' -> (hour, minute) on the 24-hour clock."""
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?([ap]m)?", re.sub(r"[\s.]", "", text.lower()))
    hour, minute, half = (int(m[1]), int(m[2] or 0), m[3]) if m else (99, 0, None)
    if hour > 23 or minute > 59 or (half and not 1 <= hour <= 12):
        raise argparse.ArgumentTypeError(f"hora no válida: {text!r} (ejemplos: 8:00 am, 7:30 p. m., 20:30)")
    return (hour % 12 + (12 if half == "pm" else 0) if half else hour), minute


def hora_12(t) -> str:
    """A datetime or time -> '8:05 a. m.', the way Peru writes the 12-hour clock."""
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'a. m.' if t.hour < 12 else 'p. m.'}"


def fecha_hora(dt: datetime) -> str:
    """'04/11/2026 8:05 a. m.' (DD/MM/AAAA) in Peru time, whatever the PC's clock zone."""
    dt = dt.astimezone(PERU)
    return f"{dt:%d/%m/%Y} {hora_12(dt)}"


def next_run(hour: int, minute: int, utc: bool, now: datetime) -> datetime:
    """The next moment the clock in Peru (or UTC) reads hour:minute, as an aware datetime."""
    at = now.astimezone(timezone.utc if utc else PERU).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at if at > now else at + timedelta(days=1)


def run_step(key: str, cmd: list[str], out) -> bool:
    """Run one step of `diario` or `evaluar`, echo its output through out(), and say whether it worked."""
    exe = shutil.which(cmd[0])
    if not exe:
        out(f"  falló: no encuentro «{cmd[0]}» en el PATH")
        return False
    try:
        proc = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=IA_TIMEOUT if key == "rank" else None)
    except (OSError, subprocess.TimeoutExpired) as exc:
        out(f"  falló: {exc}")
        return False
    text, step_ok = proc.stdout, proc.returncode == 0
    if key == "buscar":
        step_ok |= proc.returncode == 2  # it ran; a portal that failed is in cobertura.json
    if cmd[0] == "agy":  # --output-format json: {"status": "SUCCESS", "response": "...", ...}
        try:
            reply = json.loads(proc.stdout)
            text = f'{reply.get("status")}\n{reply.get("response") or ""}'
            step_ok &= reply.get("status") == "SUCCESS"
        except ValueError:
            pass
    for line in (text + "\n" + proc.stderr).splitlines():
        if line.strip():
            out("  " + line)
    if cmd[0] == "agy" and proc.stderr.strip():
        out("  (si arriba agy nombra una herramienta sin permiso, la saltó y terminó igual: ver docs/PIPELINE.md, «diario»)")
    return step_ok


def cmd_evaluar(args) -> int:
    """/rank on the postings not yet evaluated, by an agent CLI, then actualizar: what the review
    page's "Evaluar las nuevas" button copies. Logged in diario.log like the daily run."""
    py = [sys.executable, "-X", "utf8", str(Path(__file__).resolve())]
    steps = [("rank", f"Evaluación /rank con {args.ia} (puede tardar varios minutos)", IA[args.ia]),
             ("actualizar", "Página y estado", py + ["actualizar"] + (["--titulo", args.titulo] if args.titulo else []))]
    DAILY_LOG.parent.mkdir(parents=True, exist_ok=True)
    ok = True
    with DAILY_LOG.open("a", encoding="utf-8") as log:
        def out(line: str) -> None:
            print(line)
            log.write(line + "\n")
            log.flush()

        out(f"== {fecha_hora(datetime.now(PERU))} (hora de Perú, UTC-5) · {Path.cwd().name} · evaluar ==")
        for key, label, cmd in steps:
            out(f"· {label}")
            ok &= run_step(key, cmd, out)
        out("== terminado sin errores ==\n" if ok else "== hubo errores: revisa arriba y vuelve a correr ==\n")
    return 0 if ok else 1


def cmd_diario(args) -> int:
    """One day's run. reports/busqueda/diario-estado.json records which steps finished, so a run
    cut short (window closed, PC turned off, a step failed) resumes that same day from the step
    that did not finish. With --programado HH:MM (what the scheduled task passes) it also runs
    at login: it resumes an unfinished run, does today's run if that hour has passed and it has
    not run yet, and otherwise does nothing."""
    now = datetime.now(PERU)
    today = now.date().isoformat()
    try:
        state = json.loads(DAILY_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    resume = state.get("fecha") == today and not state.get("terminado")
    if args.programado and not resume:
        if state.get("fecha") == today:
            print(f"La corrida de hoy ya terminó ({state.get('fin', '')}); nada que hacer.")
            return 0
        if (now.hour, now.minute) < args.programado:
            print(f"Todavía no es la hora programada ({hora_12(now.replace(hour=args.programado[0], minute=args.programado[1]))}).")
            return 0
    py = [sys.executable, "-X", "utf8", str(Path(__file__).resolve())]
    steps = [("buscar", "Búsqueda", py + ["buscar", "--detalle", "--registrar"])]
    if args.ia:
        steps.append(("rank", f"Evaluación /rank con {args.ia}", IA[args.ia]))
    steps.append(("actualizar", "Página y estado", py + ["actualizar"] + (["--titulo", args.titulo] if args.titulo else [])))
    done = set(state.get("hechos") or []) if resume else set()
    if not resume:
        state = {"fecha": today, "inicio": fecha_hora(now)}

    def save(**changes) -> None:
        state.update(changes, hechos=sorted(done))
        DAILY_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    DAILY_LOG.parent.mkdir(parents=True, exist_ok=True)
    save(terminado=False)
    with DAILY_LOG.open("a", encoding="utf-8") as log:
        def out(line: str) -> None:
            print(line)
            log.write(line + "\n")
            log.flush()

        out(f"== {fecha_hora(now)} (hora de Perú, UTC-5) · {Path.cwd().name}"
            + (f" · retoma la corrida de las {state['inicio'].split(' ', 1)[-1]}" if resume else "") + " ==")
        for key, label, cmd in steps:
            if key in done:
                out(f"· {label}: ya hecho en esta corrida")
                continue
            out(f"· {label}")
            step_ok = run_step(key, cmd, out)
            if step_ok:
                done.add(key)
                save()
        ok = all(key in done for key, _, _ in steps)
        save(terminado=ok, fin=fecha_hora(datetime.now(PERU)))
        out("== terminado sin errores ==\n" if ok else
            "== quedó a medias: se retoma hoy al volver a iniciar sesión o con `python tools/pe_pipeline.py diario` ==\n")
    return 0 if ok else 1


def cmd_programar(args) -> int:
    cwd = Path.cwd()
    name = f"{TASK_NAME} {slug(cwd.name)}"
    label = "pe.aijobsearch." + slug(cwd.name)
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    if args.quitar:
        if sys.platform == "win32":
            proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                   f"Unregister-ScheduledTask -TaskName '{name}' -Confirm:$false"], capture_output=True, text=True)
            if proc.returncode:
                print(f"no pude quitar «{name}»: {proc.stderr.strip()[:300]}", file=sys.stderr)
                return 1
        elif sys.platform == "darwin":
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
        else:
            print("En Linux quita la línea de `crontab -e`.")
            return 0
        print(f"Tarea diaria quitada: {name}")
        return 0
    if not args.hora:
        print("falta --hora (ejemplos: --hora \"8:00 am\", --hora 20:30)", file=sys.stderr)
        return 2
    if not (cwd / SEARCH_DIR / "consultas.json").is_file():
        print(f"no hay {SEARCH_DIR / 'consultas.json'} aquí: corre programar desde la carpeta del perfil", file=sys.stderr)
        return 2
    if args.ia and not shutil.which(args.ia):
        print(f"no encuentro «{args.ia}» en el PATH", file=sys.stderr)
        return 2
    run = next_run(*args.hora, args.utc, datetime.now(timezone.utc))
    local = run.astimezone()  # the PC's own clock, which is what the scheduler reads
    peru = run.astimezone(PERU)
    cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "diario", "--programado", f"{peru:%H:%M}"]
    cmd += (["--ia", args.ia] if args.ia else []) + (["--titulo", args.titulo] if args.titulo else [])
    if sys.platform == "win32":
        q = lambda s: "'" + str(s).replace("'", "''") + "'"  # PowerShell single-quoted string
        script = (
            "$ProgressPreference = 'SilentlyContinue'; "  # else stderr carries CLIXML progress, not the error
            f"$a = New-ScheduledTaskAction -Execute {q(cmd[0])} -Argument {q(subprocess.list2cmdline(cmd[1:]))}"
            f" -WorkingDirectory {q(cwd)}; "
            # Daily at that hour, and at login: `diario --programado` then resumes a run that was
            # cut short, or does today's if the PC was off at that hour; otherwise it does nothing.
            f"$t = @((New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.AddHours({local.hour}).AddMinutes({local.minute}))), "
            "(New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name))); "
            "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries"
            " -ExecutionTimeLimit (New-TimeSpan -Hours 3); "
            f"Register-ScheduledTask -TaskName {q(name)} -Action $a -Trigger $t -Settings $s -Force"
            f" -Description {q('python tools/pe_pipeline.py diario en ' + str(cwd))} | Out-Null")
        # -EncodedCommand (UTF-16LE base64): no quoting surprises with spaces or accents in paths.
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                               base64.b64encode(script.encode("utf-16-le")).decode()], capture_output=True, text=True)
        if proc.returncode:
            print(f"no pude crear la tarea: {proc.stderr.strip()[:400]}", file=sys.stderr)
            return 1
        where = f"Programador de tareas de Windows: «{name}»"
    elif sys.platform == "darwin":
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as f:
            plistlib.dump({"Label": label, "ProgramArguments": cmd, "WorkingDirectory": str(cwd),
                           "StartCalendarInterval": {"Hour": local.hour, "Minute": local.minute},
                           "RunAtLoad": True,  # at login: resume or catch up, as on Windows
                           # launchd starts with a bare PATH: keep this shell's so bun and agy are found.
                           "EnvironmentVariables": {"PATH": os.environ.get("PATH", "")},
                           "StandardErrorPath": str(cwd / SEARCH_DIR / "diario-launchd.err")}, f)
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        proc = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True)
        if proc.returncode:
            print(f"no pude cargar {plist}: {proc.stderr.strip()[:400]}", file=sys.stderr)
            return 1
        where = f"launchd: {plist}"
    else:
        print("Agrega esta línea con `crontab -e`:")
        print(f"{local.minute} {local.hour} * * * cd {shlex.quote(str(cwd))} && {shlex.join(cmd)}")
        where = "cron"
    print(f"Búsqueda diaria: {hora_12(peru)} hora de Perú (UTC-5) = {run.astimezone(timezone.utc):%H:%M} UTC"
          + (f" · luego /rank con {args.ia}" if args.ia else ""))
    print(f"Primera corrida: {fecha_hora(run)} · {where}")
    print("Si la PC estaba apagada a esa hora o la corrida se cortó, se retoma al iniciar sesión."
          " Para cambiar la hora, vuelve a correr programar.")
    if local.utcoffset() != PERU.utcoffset(None):
        print(f"Ojo: el reloj de este equipo no está en hora de Perú; la tarea queda a las {local:%H:%M} de su reloj"
              " y, si su zona cambia de horario de verano, se correrá una hora.")
    print(f"Registro: {cwd / DAILY_LOG}")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("buscar", help="run the saved queries and list the new postings")
    b.add_argument("--consultas", type=Path, default=SEARCH_DIR / "consultas.json")
    b.add_argument("--state", type=Path, default=STATE)
    b.add_argument("--perfil", type=Path, default=PROFILE, help="profile with the `Abroad:` line")
    b.add_argument("--portal", choices=sorted(PORTALS), help="only this portal's queries")
    b.add_argument("--jobage", type=int, help="override the file's jobage (days)")
    b.add_argument("--detalle", action="store_true", help="also save each new posting's detail")
    b.add_argument("--registrar", action="store_true", help="add the new postings to seen_jobs.json as status new")
    po = sub.add_parser("portales", help="portal registry, last run's coverage and manual search links")
    po.add_argument("--consultas", type=Path, default=SEARCH_DIR / "consultas.json")
    po.add_argument("--json", action="store_true")
    a = sub.add_parser("actualizar", help="coverage, duplicates, freshness, place and the review page")
    a.add_argument("--consultas", type=Path, default=SEARCH_DIR / "consultas.json")
    a.add_argument("--titulo", help="name in the page heading")
    a.add_argument("--filtro-inicial", choices=("campo", "rrhh", "practicas", "estado", "otros"),
                   help="role shortcut selected when the page first opens")
    a.add_argument("--logos", action="store_true", help="also download missing employer logos (network)")
    a.add_argument("--calificaciones", action="store_true",
                   help="also fetch employer ratings from Computrabajo for evaluated postings (network)")
    a.add_argument("--artifact", action="store_true", help="also write the claude.ai copy and its CV manifest")
    a.add_argument("--today", type=date.fromisoformat, default=date.today())
    e = sub.add_parser("estado", help="what closes soon and what to do next")
    e.add_argument("--json", action="store_true", help="print it as JSON (for a model or another script)")
    e.add_argument("--file", type=Path, default=REVIEW_JSON)
    d = sub.add_parser("diario", help="buscar, then optional /rank by an agent CLI, then actualizar; logged")
    d.add_argument("--ia", choices=sorted(IA), help="agent CLI that runs /rank headless between the two")
    d.add_argument("--titulo", help="name in the page heading")
    d.add_argument("--programado", type=parse_hora, metavar="HH:MM",
                   help="scheduled Peru time (the task passes it): at login, run only to resume or catch up")
    pg = sub.add_parser("programar", help="run `diario` every day (Windows Task Scheduler, macOS launchd)")
    pg.add_argument("--hora", type=parse_hora, help='Peru time: "8:00 am", "7:30 p. m.", "20:30"')
    pg.add_argument("--utc", action="store_true", help="read --hora as UTC instead of Peru time")
    pg.add_argument("--ia", choices=sorted(IA), help="also run /rank with this agent CLI")
    pg.add_argument("--titulo", help="name in the page heading")
    pg.add_argument("--quitar", action="store_true", help="remove the daily task")
    lc = sub.add_parser("leer-cv", help="areas, stage, cycle and merit read from a CV, without a model (for empezar.sh)")
    lc.add_argument("cv", type=Path, help="the CV: .pdf, .docx or .txt")
    ds = sub.add_parser("descartar", help="drop postings discarded on the review page (seen_jobs.json status skipped)")
    ds.add_argument("claves", nargs="+", help="posting keys, as the page's 'Copiar comando' writes them")
    ds.add_argument("--deshacer", action="store_true", help="put them back as they were")
    ds.add_argument("--state", type=Path, default=STATE)
    ev = sub.add_parser("evaluar", help="/rank the postings not yet evaluated with an agent CLI, then actualizar")
    ev.add_argument("--ia", choices=sorted(IA), default="claude", help="agent CLI that runs /rank (default claude)")
    ev.add_argument("--titulo", help="name in the page heading")
    cq = sub.add_parser("consultas", help="write a first consultas.json from a few search phrases")
    cq.add_argument("--q", action="append", default=[], help='phrases, comma-separated: "practicante finanzas, analista"')
    cq.add_argument("--ciudad", default="Lima", help="city when --lugar peru (default Lima)")
    cq.add_argument("--sector", action="append", default=[], help="privado,publico,investigacion,otros (default all)")
    cq.add_argument("--modalidad", action="append", default=[], help="remoto,presencial,hibrido or cualquiera")
    cq.add_argument("--lugar", choices=sorted(ABROAD_TEXT), help="lima, peru (another city), afuera-remoto, afuera")
    cq.add_argument("--etapa", choices=sorted(STAGE_LEVEL), help="estudiante, egresado or profesional")
    cq.add_argument("--area", action="append", default=[], help=f"areas, comma-separated: {', '.join(AREAS)}")
    cq.add_argument("--ciclo", choices=sorted(CYCLE_TEXT), help="a student's cycle: 1-4, 5-6, 7-8, 9+")
    cq.add_argument("--merito", choices=sorted(MERIT_TEXT), help="decimo, quinto, tercio, medio or ninguno")
    cq.add_argument("--universidad", default="", help="university, for the ficha and the profile")
    cq.add_argument("--experiencia", choices=sorted(pe_lexico.EXPERIENCE_YEARS),
                    help="ninguna, practicas, menos1, 1-3, 3-5, 5+")
    cq.add_argument("--ingles", choices=sorted(pe_lexico.ENGLISH_ORDER), help="basico, intermedio, avanzado, nativo")
    cq.add_argument("--herramientas", action="append", default=[],
                    help=f"tools, comma-separated: {', '.join(pe_lexico.TOOL_HINTS)}")
    cq.add_argument("--certificaciones", action="append", default=[],
                    help=f"comma-separated: {', '.join(pe_lexico.CERT_HINTS)}")
    cq.add_argument("--perfil", type=Path, default=Path("CLAUDE.md"), help="profile CLAUDE.md to fill in")
    cq.add_argument("--excluir", action="append", default=[], help="extra title words to leave out")
    cq.add_argument("--reemplazar", action="store_true", help="overwrite an existing consultas.json")
    cq.add_argument("--es-ejemplo", action="store_true", help="exit 0 when there is none yet or only perfiles.py's example")
    cq.add_argument("--consultas", type=Path, default=SEARCH_DIR / "consultas.json")
    args = ap.parse_args(argv)
    if args.cmd == "leer-cv":
        return cmd_leer_cv(args)
    if args.cmd == "consultas":
        return cmd_consultas(args)
    if args.cmd == "evaluar":
        return cmd_evaluar(args)
    if args.cmd == "descartar":
        return cmd_descartar(args)
    if args.cmd == "diario":
        return cmd_diario(args)
    if args.cmd == "programar":
        return cmd_programar(args)
    if args.cmd == "buscar":
        return cmd_buscar(args)
    if args.cmd == "portales":
        return cmd_portales(args)
    if args.cmd == "actualizar":
        return cmd_actualizar(args)
    return estado(args.file, args.json)


if __name__ == "__main__":
    sys.exit(main())
