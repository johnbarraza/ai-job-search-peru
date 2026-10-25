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
"""

import argparse
import glob
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date, datetime
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

STATE = Path("job_scraper/seen_jobs.json")
PROFILE = Path("CLAUDE.md")
SEARCH_DIR = Path("reports/busqueda")
DETAIL_DIR = Path("reports/detalles")
COVERAGE = SEARCH_DIR / "cobertura.json"
REVIEW_JSON = Path("reports/revision-ofertas.json")
# CLI portal -> (file prefix for searches, file prefix for details), as the earlier runs named them.
PORTALS = {"linkedin": ("li", "li"), "bumeran": ("bu", "bm"), "computrabajo": ("ct", "ct"),
           "talento-peru": ("sv", "sv"), "practicas-pe": ("pr", "pr")}
LINKEDIN_PAUSE = 6  # seconds between LinkedIn calls: it answers bursts with 429
LINKEDIN_MAX_429 = 2  # consecutive 429s before the run stops asking LinkedIn
PAGED = {"linkedin", "bumeran", "computrabajo"}  # CLIs that take --page
MAX_PAGES = 10

# Every portal the search covers. `cli` portals run in
# `buscar`; the others get a search link per manual query ({q} is the query). A profile can
# add or override entries with a "portales" list in consultas.json (same keys, matched by id).
REGISTRY = [
    {"id": "linkedin", "nombre": "LinkedIn Jobs", "metodo": "cli",
     "home": "https://www.linkedin.com/jobs/", "url": "https://www.linkedin.com/jobs/search/?keywords={q}&location=Lima%2C%20Peru",
     "nota": "Prácticas y analistas junior en bancos, consultoras y grandes empresas. Limita ráfagas (429)."},
    {"id": "bolsa-universidad", "nombre": "Bolsa de tu universidad", "metodo": "manual", "home": None, "url": None,
     "nota": "Con tu cuenta de estudiante; exige iniciar sesión. Pon su URL en consultas.json → portales."},
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
     "nota": "Vacantes de muchas empresas y portales. Solo enlace (sus términos no permiten extraer); o jev-search."},
    {"id": "computrabajo", "nombre": "Computrabajo", "metodo": "cli",
     "home": "https://pe.computrabajo.com/", "url": "https://pe.computrabajo.com/trabajo-de-{qs}-en-lima",
     "nota": "Mucho volumen; hay que filtrar bastante."},
    {"id": "empleos-peru", "nombre": "Empleos Perú (MTPE)", "metodo": "manual",
     "home": "https://www.empleosperu.gob.pe/portal-mtpe/", "url": None,
     "nota": "Bolsa pública del Ministerio de Trabajo. Búsqueda dentro del portal; o jev-search."},
    {"id": "practicas-pe", "nombre": "Prácticas.pe", "metodo": "cli",
     "home": "https://www.practicas.pe/", "url": "https://www.practicas.pe/resultados-busqueda.php?q={q}&dep=15",
     "nota": "Prácticas públicas y privadas, con ficha y fecha de postulación cuando se publica."},
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", job_key.slugify(text)).strip("-")[:60] or "consulta"


def cli(portal: str) -> list[str]:
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
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
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
    last_linkedin, linkedin_429 = 0.0, 0
    for i, c in enumerate(queries, 1):
        portal, run = c["portal"], runs[c["portal"]]
        place = c.get("l") or ""
        cmd = cli(portal) + ["search"]
        if c.get("q"):
            cmd += ["-q", c["q"]]
        if place:
            cmd += ["-l", place]
        if jobage and portal != "practicas-pe":
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
                if linkedin_429 >= LINKEDIN_MAX_429:
                    failed = "omitida (LinkedIn limitó las consultas)" if page == 1 else None
                    break
                wait = LINKEDIN_PAUSE - (time.monotonic() - last_linkedin)
                if wait > 0:
                    time.sleep(wait)
                last_linkedin = time.monotonic()
            page_cmd = cmd + (["--page", str(page)] if page > 1 else [])
            data, error = run_json(page_cmd, run_dir / (stem + (f"-p{page}" if page > 1 else "") + ".json"))
            if error:
                linkedin_429 = linkedin_429 + 1 if portal == "linkedin" and "429" in error else 0
                if page == 1:
                    failed = error
                else:  # the first pages already came back: keep them, note the gap
                    run["errores"].append(f"{c.get('q')} · {place} · página {page}: {error}")
                break
            linkedin_429 = 0
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
                        "modality": p.get("modality") or ("remoto" if remote == "remote" else None),
                         "level": p.get("level"), "query": c.get("q"), "detail": None,
                         "deadline": p.get("deadline"), "validThrough": p.get("validThrough"),
                         "ref": p.get("ref"), "type": p.get("type"),
                         "salary": p.get("salary"), "requirements": p.get("requirements")}

    if args.detalle:
        for j, p in enumerate(new.values(), 1):
            out = DETAIL_DIR / f'{PORTALS[p["portal"]][1]}_{slug(p["id"])}.json'
            if not out.is_file():
                print(f"detalle {j}/{len(new)}: {p['title']}")
                if p["portal"] == "linkedin":
                    time.sleep(LINKEDIN_PAUSE)
                if p["portal"] == "talento-peru":
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
                    error = None
                else:
                    detail, error = run_json(cli(p["portal"]) + ["detail", p["url"] or p["id"]], out)
                    if not error and p["portal"] == "practicas-pe" and isinstance(detail, dict):
                        p["date"] = detail.get("date") or p["date"]
                        p["deadline"] = detail.get("deadline") or p["deadline"]
                if error:
                    runs[p["portal"]]["errores"].append(f"detalle {p['id']}: {error}")
                    continue
            p["detail"] = str(out).replace("\\", "/")

    report = {"generated": today, "jobage": jobage, "consultas": len(queries), "extranjero": abroad["mode"],
              "excluidas_por_titulo": excluded,
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

    if args.registrar and new:
        seen = state.setdefault("seen", {})
        for p in new.values():
            key = ("servir_" + p["id"].split(":", 1)[-1] if p["portal"] == "talento-peru"
                   else "practicas_" + p["id"] if p["portal"] == "practicas-pe"
                   else job_key.make_key(p.get("company") or "", p.get("title") or "", p.get("url") or ""))
            seen.setdefault(key, {
                "id": p["id"], "title": p.get("title"), "company": p.get("company"), "url": p.get("url"), "location": p.get("location"),
                "first_seen": today, "posted_date": p.get("date"), "deadline": p.get("deadline"),
                "validThrough": p.get("validThrough"),
                "fit": None, "status": "new", "portal": f'{p["portal"]}-search', "source": "cli",
                "modality": p.get("modality"), "level": p.get("level"), "detail": p.get("detail"),
                "how_to_apply": f'Busca la convocatoria {p["ref"]} en SERVIR y descarga el Word para verificar bases y plazo.'
                if p["portal"] == "talento-peru" else None,
            })
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    for portal, run in sorted(runs.items()):
        print(f"{portal}: {run['ok']}/{run['consultas']} consultas ok · {run['resultados']} resultados"
              + (f" · {run['fallidas']} fallidas" if run["fallidas"] else ""))
    if skipped_abroad:
        print(f"{skipped_abroad} consultas fuera del Perú omitidas: el perfil no pide buscar en el extranjero (línea Abroad)")
    print(f"\n{len(new)} nuevas (no estaban en {args.state}) · {excluded} excluidas por título"
          f" → {SEARCH_DIR / 'nuevas.json'}" + (" · registradas como 'new'" if args.registrar and new else ""))
    print(f"Copia de esta corrida: {run_dir}")
    print(f"Cobertura y enlaces de portales: {COVERAGE} (python tools/pe_pipeline.py portales)")
    if new and not args.registrar:
        print("Para evaluarlas: usa /rank con cualquier agente leyendo reports/busqueda/nuevas.json, o vuelve a correr con --registrar.")
    return 0 if all(r["ok"] for r in runs.values()) or not runs else 2


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
    write_coverage(load_config(args.consultas))
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
            searches = sorted(glob.glob(str(SEARCH_DIR / "**" / "*.json"), recursive=True))
            ok &= step("Logos (descarga)", py + [str(TOOLS / "pe_logos.py"), *details, *searches])
    else:
        print(f"· sin detalles en {DETAIL_DIR}: salto duplicados, frescura y lugar")
    title = ["--titulo", args.titulo] if args.titulo else []
    ok &= step("Página de revisión", py + [str(TOOLS / "pe_revision.py"), *title, *today, "--json", str(REVIEW_JSON)])
    if args.artifact:
        ok &= step("Copia para claude.ai", py + [str(TOOLS / "pe_revision.py"), *title, *today, "--artifact",
                                                 "--out", "reports/revision-ofertas-artifact.html",
                                                 "--cv-manifest", "reports/cv_manifest.json"])
    print()
    estado(REVIEW_JSON, as_json=False)
    return 0 if ok else 1


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
    a.add_argument("--logos", action="store_true", help="also download missing employer logos (network)")
    a.add_argument("--artifact", action="store_true", help="also write the claude.ai copy and its CV manifest")
    a.add_argument("--today", type=date.fromisoformat, default=date.today())
    e = sub.add_parser("estado", help="what closes soon and what to do next")
    e.add_argument("--json", action="store_true", help="print it as JSON (for a model or another script)")
    e.add_argument("--file", type=Path, default=REVIEW_JSON)
    args = ap.parse_args(argv)
    if args.cmd == "buscar":
        return cmd_buscar(args)
    if args.cmd == "portales":
        return cmd_portales(args)
    if args.cmd == "actualizar":
        return cmd_actualizar(args)
    return estado(args.file, args.json)


if __name__ == "__main__":
    sys.exit(main())
