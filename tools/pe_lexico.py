#!/usr/bin/env python3
"""Search without a model: read a CV by words, keep a "ficha" of the candidate, and score postings
by words ("rank léxico"), so the review page works for someone with no AI subscription.

    python tools/pe_lexico.py leer-cv "mi cv.pdf"     # what the CV says, as shell assignments (empezar.sh)
    python tools/pe_lexico.py rank                    # score every posting still waiting for /rank
    python tools/pe_lexico.py rank --todas            # score again the ones this tool scored before

The ficha (reports/busqueda/ficha.json, written by `pe_pipeline.py consultas`) holds careers,
university, stage, cycle, merit, experience, English, tools, certifications and sectors. A posting
scores up to 100 by words in its title and description: its area (35), the tools it asks for (25),
the level against the stage and experience (20), English (10) and the sector (10). Formal
requirements the ficha does not meet (cycle, merit, graduate status, English level) are not hidden
in the score: they set eligibility_gate or language_gate to FLAG, which the page shows as
"Confirmar antes". It writes the same fields /rank writes (status ranked, rank_score, rank_verdict,
strengths, gaps, warnings, gates) plus rank_method "lexico", so /rank can score them again later.
It is a word match, not understanding: it says so in each posting's warnings.
"""

import argparse
import json
import re
import shlex
import sys
import unicodedata
from datetime import date
from pathlib import Path

STATE = Path("job_scraper/seen_jobs.json")
FICHA = Path("reports/busqueda/ficha.json")

# What a text says that points to each area (accents and case folded before matching).
AREA_HINTS = {
    "economia": ["economia", "economista", "econometr", "macroeconom", "microeconom", "politica economica", "stata", "eviews"],
    "finanzas": ["finanzas", "financier", "valorizacion", "valuacion", "banca", "inversion", "riesgo", "tesoreria",
                 "credito", "mercado de capitales", "cfa", "bloomberg", "modelamiento financiero"],
    "contabilidad": ["contabilidad", "contable", "contador", "auditoria", "niif", "nic ", "tributacion", "tributari"],
    "administracion": ["administracion", "gestion empresarial", "negocios", "business", "emprendimiento"],
    "industrial": ["ingenieria industrial", "procesos", "logistica", "operaciones", "cadena de suministro", "supply chain", "lean"],
    "datos": ["python", "sql", "power bi", "tableau", "analisis de datos", "data analyst", "machine learning", "ciencia de datos", " r "],
    "marketing": ["marketing", "mercadeo", "ventas", "comercial", "branding", "publicidad"],
    "derecho": ["derecho", "abogad", "juridic", "legal"],
    "rrhh": ["recursos humanos", "psicologia", "reclutamiento", "seleccion de personal", "gestion del talento"],
    "ingenieria": ["ingenieria civil", "ingenieria de minas", "ingenieria de sistemas", "ingenieria mecanica", "software"],
    "comunicaciones": ["comunicacion", "periodismo", "audiovisual", "relaciones publicas"],
}
TOOL_HINTS = {
    "excel": ["excel"], "powerbi": ["power bi", "powerbi"], "tableau": ["tableau"], "sql": [" sql"],
    "python": ["python"], "r": [" r ", " r,", " r/", "lenguaje r", "rstudio"], "stata": ["stata"],
    "eviews": ["eviews", "e-views"], "bloomberg": ["bloomberg"], "spss": ["spss"], "sap": [" sap "],
    "ia": ["inteligencia artificial", "chatgpt", "ia generativa", "copilot", "prompt", " ia "],
}
TOOL_NAMES = {"excel": "Excel", "powerbi": "Power BI", "tableau": "Tableau", "sql": "SQL", "python": "Python",
              "r": "R", "stata": "Stata", "eviews": "EViews", "bloomberg": "Bloomberg", "spss": "SPSS",
              "sap": "SAP", "ia": "herramientas de IA"}
CERT_HINTS = {"cfa1": ["cfa level i ", "cfa nivel i ", "cfa level 1", "cfa nivel 1"], "cfa2": ["cfa level ii", "cfa nivel ii", "cfa level 2"],
              "cfa3": ["cfa level iii", "cfa nivel iii", "cfa level 3", "cfa charterholder"], "frm": [" frm"],
              "pmp": [" pmp"], "bmc": ["bloomberg market concepts", " bmc"]}
UNIVERSITIES = [  # (words to find, name to show)
    ("universidad del pacifico", "Universidad del Pacífico"), ("pontificia universidad catolica", "PUCP"),
    ("pucp", "PUCP"), ("universidad nacional de ingenieria", "UNI"), ("universidad nacional mayor de san marcos", "UNMSM"),
    ("san marcos", "UNMSM"), ("universidad de lima", "Universidad de Lima"), ("upc", "UPC"),
    ("universidad peruana de ciencias aplicadas", "UPC"), ("usil", "USIL"), ("san ignacio de loyola", "USIL"),
    ("esan", "ESAN"), ("universidad nacional agraria", "UNALM"), ("san martin de porres", "USMP"),
    ("universidad de piura", "Universidad de Piura"), ("utec", "UTEC"), ("universidad cientifica del sur", "UCSUR"),
    ("universidad ricardo palma", "URP"), ("universidad nacional federico villarreal", "UNFV"),
    ("universidad de san andres", "Universidad de San Andrés"), ("universidad cesar vallejo", "UCV"),
    ("universidad privada del norte", "UPN"), ("universidad continental", "Universidad Continental"),
    ("universidad catolica san pablo", "UCSP"), ("universidad nacional de san agustin", "UNSA"),
]
ENGLISH_ORDER = {"basico": 1, "intermedio": 2, "avanzado": 3, "nativo": 4}
ENGLISH_WORDS = {"basico": 1, "elemental": 1, "a1": 1, "a2": 1, "intermedio": 2, "b1": 2, "b2": 3, "avanzado": 3,
                 "fluido": 3, "c1": 3, "c2": 4, "nativo": 4, "bilingue": 4}
MERIT_ORDER = {"decimo": 1, "quinto": 2, "tercio": 3, "medio": 4, "ninguno": 9}
CYCLE_TOP = {"1-4": 4, "5-6": 6, "7-8": 8, "9+": 10}
EXPERIENCE_YEARS = {"ninguna": 0, "practicas": 0.5, "menos1": 0.5, "1-3": 2, "3-5": 4, "5+": 6}


def fold(text: str) -> str:
    """Lower case without accents, single spaces, padded with spaces for whole-word hints."""
    folded = unicodedata.normalize("NFKD", (text or "").lower())
    return " " + re.sub(r"\s+", " ", "".join(c for c in folded if not unicodedata.combining(c))) + " "


def cv_text(path: Path) -> str:
    """The folded text of a CV in PDF (pypdf), Word (.docx) or plain text."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader  # tools/requirements-pdf.txt
        raw = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    elif suffix == ".docx":
        import zipfile
        with zipfile.ZipFile(path) as z:
            raw = re.sub(r"<[^>]+>", " ", z.read("word/document.xml").decode("utf-8", "replace"))
    else:
        raw = path.read_text(encoding="utf-8", errors="replace")
    return fold(raw)


def english_level(text: str) -> str | None:
    """The English level a text states ("inglés avanzado", "English B2"), or None."""
    m = re.search(r"\b(?:ingles|english)\b[^.;\n]{0,40}?\b(basico|elemental|intermedio|avanzado|fluido|nativo|bilingue|"
                  r"a1|a2|b1|b2|c1|c2)\b", text)
    if not m:
        return None
    n = ENGLISH_WORDS[m.group(1)]
    return next(k for k, v in ENGLISH_ORDER.items() if v == n)


def read_cv(text: str) -> dict:
    """What a CV says, read by words: areas (two or more hints, top three), stage, cycle, merit,
    university, English, tools and certifications. Anything unclear is left empty."""
    hits = {a: sum(text.count(h) for h in hints) for a, hints in AREA_HINTS.items()}
    areas = [a for a, n in sorted(hits.items(), key=lambda kv: -kv[1]) if n >= 2][:3]
    merit = next((k for k, words in (("decimo", "decimo superior"), ("quinto", "quinto superior"),
                                     ("tercio", "tercio superior"), ("medio", "medio superior")) if words in text), None)
    m = re.search(r"\b(\d{1,2})\s*\.?\s*(?:°|º|o|vo|mo|to|ro|do|er)?\.?\s*ciclo", text)  # 8.º, 8vo, 8 ciclo
    cycle = None
    if m and 1 <= int(m.group(1)) <= 14:
        n = int(m.group(1))
        cycle = "1-4" if n <= 4 else "5-6" if n <= 6 else "7-8" if n <= 8 else "9+"
    stage = ("egresado" if re.search(r"\begresad|\bbachiller\b|\btitulad", text) else
             "estudiante" if cycle or re.search(r"\bestudiante\b|\b(cursando|en curso)\b", text) else None)
    university = next((name for words, name in UNIVERSITIES if f" {words} " in text or f" {words}," in text), "")
    tools = [k for k, hints in TOOL_HINTS.items() if any(h in text for h in hints)]
    certs = [k for k, hints in CERT_HINTS.items() if any(h in text for h in hints)]
    return {"area": ",".join(areas), "etapa": stage or "", "ciclo": (cycle or "") if stage == "estudiante" else "",
            "merito": merit or "", "universidad": university, "ingles": english_level(text) or "",
            "herramientas": ",".join(tools), "certificaciones": ",".join(certs)}


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def posting_text(entry: dict) -> str:
    """Title plus the saved detail's description, when the search saved one."""
    parts = [entry.get("title") or "", entry.get("level") or ""]
    detail = entry.get("detail")
    if detail and Path(detail).is_file():
        doc = load_json(Path(detail), {})
        parts += [str(doc.get(k) or "") for k in ("description", "requirements", "requisitos", "perfil", "seniority")]
    return fold(" ".join(parts))


def required_cycle(text: str) -> int | None:
    m = re.search(r"(?:a partir del?|desde(?: el)?|minimo|cursando(?: el)?|al menos(?: el)?)\s*(\d{1,2})\s*\.?\s*"
                  r"(?:°|º|o|vo|mo|to|ro|do|er)?\.?\s*ciclo", text)
    if m:
        return int(m.group(1))
    if re.search(r"ultimo(?:s)? (?:ciclo|ano)", text):
        return 9
    return None


def required_merit(text: str) -> str | None:
    return next((k for k in ("decimo", "quinto", "tercio", "medio") if f"{k} superior" in text), None)


def required_years(text: str) -> float | None:
    m = re.search(r"(\d{1,2})\s*(?:a\s*\d{1,2}\s*)?(?:\+\s*)?anos? de experiencia", text)
    return float(m.group(1)) if m else None


def score(entry: dict, ficha: dict, employer_type=None) -> dict:
    """The /rank-shaped result for one posting, from words only."""
    text = posting_text(entry)
    title = fold(entry.get("title") or "")
    strengths, gaps, warnings = [], [], ["Puntaje sin IA (por palabras): revisa el aviso antes de decidir."]
    eligibility, notes = "PASS", []
    total = 0

    # Area (35): the candidate's careers in the title weigh most.
    areas = [a for a in (ficha.get("carreras") or []) if a in AREA_HINTS]
    in_title = [a for a in areas if any(h in title for h in AREA_HINTS[a])]
    in_text = {a: sum(text.count(h) for h in AREA_HINTS[a]) for a in areas}
    if in_title:
        total += 35
        strengths.append(f"El puesto es de tu área ({', '.join(in_title)})")
    elif any(n >= 2 for n in in_text.values()):
        total += 25
        strengths.append(f"El aviso menciona tu área ({', '.join(a for a, n in in_text.items() if n >= 2)})")
    elif any(in_text.values()):
        total += 12
    elif areas:
        gaps.append("El aviso no menciona tu área")
    else:
        total += 15  # no careers in the ficha: neutral

    # Tools (25): what the posting asks for against what the candidate has.
    asked = [k for k, hints in TOOL_HINTS.items() if any(h in text for h in hints)]
    have = set(ficha.get("herramientas") or [])
    if asked:
        matched = [k for k in asked if k in have]
        total += round(25 * len(matched) / len(asked))
        if matched:
            strengths.append("Tienes lo que pide: " + ", ".join(TOOL_NAMES[k] for k in matched))
        missing = [k for k in asked if k not in have]
        if missing:
            gaps.append("Pide " + ", ".join(TOOL_NAMES[k] for k in missing) + " (no está en tu ficha)")
    else:
        total += 18

    # Level (20): stage, cycle, graduate status, experience.
    stage, level_points = ficha.get("etapa"), 20
    intern_post = bool(re.search(r"practica|practicante|pasant|trainee|intern", text))
    grad_only = bool(re.search(r"\begresad|\bbachiller\b|\btitulad", text)) and " estudiante" not in text
    if stage == "estudiante" and grad_only:
        eligibility, level_points = "FLAG", 5
        notes.append("Pide egresado o bachiller y tu ficha dice estudiante")
    if stage == "profesional" and intern_post:
        level_points = 6
        gaps.append("Es una práctica y tu ficha dice que ya trabajas")
    need_cycle = required_cycle(text)
    if need_cycle and stage == "estudiante":
        top = CYCLE_TOP.get(ficha.get("ciclo") or "")
        if top is None:
            eligibility = "FLAG"
            notes.append(f"Pide desde {need_cycle}.º ciclo; confirma el tuyo")
        elif top < need_cycle:
            eligibility, level_points = "FLAG", min(level_points, 5)
            notes.append(f"Pide desde {need_cycle}.º ciclo y tu ficha dice {ficha.get('ciclo')}")
    need_years = required_years(text)
    have_years = EXPERIENCE_YEARS.get(ficha.get("experiencia") or "")
    if need_years:
        if have_years is None:
            warnings.append(f"Pide {need_years:g} años de experiencia; confirma la tuya")
        elif have_years < need_years:
            level_points = min(level_points, 4 if need_years - have_years >= 2 else 10)
            gaps.append(f"Pide {need_years:g} años de experiencia")
        else:
            strengths.append(f"Cumples la experiencia que pide ({need_years:g} años)")
    if re.search(r"\b(senior|jefe|gerente|director|head of)\b", title) and stage in ("estudiante", "egresado"):
        level_points = 0
        gaps.append("Es un puesto senior")
    total += level_points

    # Merit (gate only): a stated décimo/quinto/tercio superior the ficha does not meet.
    need_merit = required_merit(text)
    if need_merit:
        mine = ficha.get("merito")
        if not mine:
            eligibility = "FLAG"
            notes.append(f"Pide {need_merit} superior; confirma tu mérito")
        elif MERIT_ORDER[mine] > MERIT_ORDER[need_merit]:
            eligibility = "FLAG"
            notes.append(f"Pide {need_merit} superior y tu ficha dice {mine}")
        else:
            strengths.append(f"Cumples el mérito que pide ({need_merit} superior)")

    # English (10).
    need_en, language_gate, language_note = english_level(text), "PASS", None
    mine_en = ficha.get("ingles")
    if not need_en:
        total += 10
    elif mine_en and ENGLISH_ORDER.get(mine_en, 0) >= ENGLISH_ORDER[need_en]:
        total += 10
        strengths.append(f"Tu inglés ({mine_en}) cubre el que pide ({need_en})")
    else:
        language_gate = "FLAG"
        language_note = f"Pide inglés {need_en}" + (f"; tu ficha dice {mine_en}" if mine_en else "; tu ficha no lo dice")
        gaps.append(language_note)

    # Certifications: a stated CFA/FRM the candidate has is a strength (no points taken away).
    certs = set(ficha.get("certificaciones") or [])
    if "cfa" in text and certs & {"cfa1", "cfa2", "cfa3"}:
        strengths.append("Menciona CFA y tienes avance en CFA")
        total += 3

    # Sector (10).
    sectors = set(ficha.get("sectores") or [])
    kind = employer_type(entry) if employer_type else None
    if not sectors or not kind or kind in sectors or (kind == "otros" and "otros" in sectors):
        total += 10
    else:
        gaps.append("No es del sector que elegiste")

    total = max(0, min(100, total))
    verdict = ("Strong Fit" if total >= 75 else "Good Fit" if total >= 60 else "Moderate Fit" if total >= 45
               else "Weak Fit" if total >= 30 else "Poor Fit")
    out = {"status": "ranked", "rank_score": total, "rank_verdict": verdict, "rank_date": date.today().isoformat(),
           "rank_method": "lexico", "location_verdict": "PASS", "language_gate": language_gate,
           "eligibility_gate": eligibility, "strengths": strengths[:3], "gaps": gaps[:3], "warnings": warnings}
    if language_note:
        out["language_note"] = language_note
    if notes:
        out["eligibility_note"] = "; ".join(notes)
    return out


def cmd_rank(args) -> int:
    state = load_json(args.state, None)
    if not isinstance(state, dict):
        print(f"no puedo leer {args.state}: corre primero una búsqueda (pe_pipeline.py buscar --registrar)", file=sys.stderr)
        return 1
    ficha = load_json(args.ficha, {})
    if not ficha:
        print(f"falta {args.ficha}: corre empezar.sh o pe_pipeline.py consultas para armar tu ficha", file=sys.stderr)
        return 1
    import importlib.util
    spec = importlib.util.spec_from_file_location("pe_revision", Path(__file__).resolve().parent / "pe_revision.py")
    pe_revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pe_revision)
    kinds = {"privado": "privado", "estado": "publico", "investigacion": "investigacion", "otros": "otros"}
    employer = lambda e: kinds.get(pe_revision.employer_type(e))
    done = 0
    for key, e in (state.get("seen") or {}).items():
        if not isinstance(e, dict):
            continue
        waiting = e.get("status") in (None, "new") and e.get("rank_score") is None
        again = args.todas and e.get("rank_method") == "lexico" and e.get("status") == "ranked"
        if waiting or again:
            e.update(score(e, ficha, employer))
            done += 1
    if done:
        args.state.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{done} ofertas puntuadas sin IA (por palabras). Para verlas: python tools/pe_pipeline.py actualizar")
    return 0


def cmd_leer_cv(args) -> int:
    try:
        found = read_cv(cv_text(args.cv))
    except Exception as exc:  # an unreadable file is an answer too: nothing found
        print(f"no pude leer {args.cv}: {exc}", file=sys.stderr)
        return 1
    for key, value in found.items():
        print(f"cv_{key}={shlex.quote(value)}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    lc = sub.add_parser("leer-cv", help="what a CV says, as shell assignments")
    lc.add_argument("cv", type=Path)
    rk = sub.add_parser("rank", help="score the postings still waiting for /rank, by words")
    rk.add_argument("--todas", action="store_true", help="also score again the ones scored before by this tool")
    rk.add_argument("--state", type=Path, default=STATE)
    rk.add_argument("--ficha", type=Path, default=FICHA)
    args = ap.parse_args(argv)
    return cmd_leer_cv(args) if args.cmd == "leer-cv" else cmd_rank(args)


if __name__ == "__main__":
    sys.exit(main())
