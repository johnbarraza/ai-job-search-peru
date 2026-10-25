#!/usr/bin/env python3
"""Review page for a search: one self-contained HTML file that follows the workflow.

It reads only what the workflow already wrote, so the page never says more than the
evaluation did:

  1. Búsqueda    job_scraper/seen_jobs.json (every posting /scrape saw, per portal)
  2. Encaje      the same file's /rank fields (score, verdict, strengths, gaps)
  3. Frescura    `tools/pe_frescura.py --json` output (/rank rule 8), when given
  4. Reputación  company_research/<company>.json `ratings` (04-job-evaluation.md)
  5. Acción      derived: postular ya / postular y confirmar / confirmar antes / ...

The postings to decide are cards in a carousel (arrows, keys or swipe, a strip of ticks
coloured by action to jump) with a list view; a stated deadline that passed moves a posting
to "Cerradas". `--json` writes the same content as data, for a script or a model to read.

Offline and dependency-free: inline CSS and JS, no CDN, so the file opens the same from disk,
a phone, any static host or claude.ai. The tailored CVs are embedded in the local page (a
download that needs nothing else); the Artifact copy links its published PDFs instead. Every
value from the state files is HTML-escaped, and only http(s) URLs become links. The "revisada"
checkboxes are a per-browser convenience (localStorage) and never write back to the state.

Usage:
    python tools/pe_revision.py --json reports/revision-ofertas.json
    python tools/pe_revision.py --frescura reports/frescura.json --out reports/revision.html
    python tools/pe_revision.py --today 2026-10-18 --titulo "Tu Nombre"
"""

import argparse
import base64
import html
import importlib.util
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pe_frescura", ROOT / "tools" / "pe_frescura.py")
pe_frescura = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pe_frescura)

SHORTLIST = ("Strong Fit", "Good Fit")
ACTIONS = [  # (key, label, css class) in priority order
    ("ya", "Postular ya", "go"),
    ("pronto", "Postular pronto", "go"),
    ("confirmar", "Postular y confirmar", "warn"),
    ("antes", "Confirmar antes", "stop"),
    ("tiempo", "Si hay tiempo", "muted"),
    ("postulada", "Postulada", "muted"),
    ("cerrada", "Cerró", "out"),
    ("descartar", "Descartar", "out"),
]
ACTION_ORDER = {key: i for i, (key, _, _) in enumerate(ACTIONS)}
SMALL_N = 30  # below this a rating is only referential (10-peru-market.md section 11)


def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def link(url, text) -> str:
    if isinstance(url, str) and re.match(r"^https?://", url):
        return f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(text)}</a>'
    return esc(text)


URGENT_DAYS = 2  # a stated deadline this close makes a shortlisted posting "postular ya"


def deadline_info(entry: dict, today: date) -> dict:
    """The stated closing date (never the portal's expiry) as a label, a filter bucket and a sort key."""
    try:
        closes = date.fromisoformat(str(entry.get("deadline")))
    except ValueError:
        return {"days": None, "label": "—", "bucket": "sin", "css": "", "sort": 99999}
    days = (closes - today).days
    when = closes.strftime("%d/%m")
    if days < 0:
        return {"days": days, "label": f"venció el {when}", "bucket": "vencida", "css": "past", "sort": 99998}
    if days == 0:
        label, bucket, css = "HOY", "hoy", "today"
    elif days == 1:
        label, bucket, css = f"mañana · {when}", "3d", "soon"
    elif days <= 3:
        label, bucket, css = f"en {days} días · {when}", "3d", "soon"
    elif days <= 7:
        label, bucket, css = f"en {days} días · {when}", "7d", "week"
    else:
        label, bucket, css = when, "mas", ""
    return {"days": days, "label": label, "bucket": bucket, "css": css, "sort": days}


def action_for(entry: dict, fresh: dict | None, today: date | None = None) -> str:
    """The next step for a posting, from its /rank verdict, its freshness risk and a close deadline."""
    if entry.get("application_status") == "applied":
        return "postulada"
    if entry.get("location_verdict") == "FAIL" or entry.get("language_gate") == "FAIL":
        return "descartar"
    verdict = entry.get("rank_verdict")
    days = deadline_info(entry, today).get("days") if today else None
    if days is not None and days < 0:
        return "cerrada"  # its stated deadline passed: nothing left to decide
    if verdict in SHORTLIST:
        if days is not None and 0 <= days <= URGENT_DAYS:
            return "ya"  # a stated deadline is a live process: no time to confirm first
        risk = (fresh or {}).get("risk")
        if risk == "alto":
            return "antes"
        if risk == "medio":
            return "confirmar"
        return "ya" if (fresh or {}).get("window") == "fresca" else "pronto"
    if verdict == "Moderate Fit":
        return "tiempo"
    return "descartar"


def company_file(name: str) -> str:
    """04-job-evaluation.md's cache filename rule: lowercase, trim, spaces to hyphens."""
    return (name or "").strip().lower().replace(" ", "-") + ".json"


def load_logos(research_dir: Path) -> dict:
    """tools/pe_logos.py's index: company key -> PNG file in research_dir/logos."""
    try:
        index = json.loads((research_dir / "logos" / "index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return index if isinstance(index, dict) else {}


def logo_uri(research_dir: Path, logos: dict, company: str) -> str | None:
    """The employer's logo as a data: URI, so the page stays one offline file."""
    entry = logos.get(re.sub(r"\s+", "-", (company or "").strip().lower()))
    file = entry.get("file") if isinstance(entry, dict) else None
    if not isinstance(file, str) or not re.fullmatch(r"[a-z0-9_-]+\.png", file):
        return None  # the index may be hand-edited: only plain PNG names in the logos folder
    try:
        data = (research_dir / "logos" / file).read_bytes()
    except OSError:
        return None
    if not data.startswith(b"\x89PNG"):
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


# Words that name the position level, not the team: what is left of a title is the area.
LEVEL_WORDS = re.compile(
    r"\b(practicantes?|pr[aá]cticas?|pre[- ]?profesional(?:es)?|profesional(?:es)?|preprofesional(?:es)?|programa|"
    r"trainee|asistente|analista|auxiliar|jr|junior|senior|sr|intern(?:ship)?|associates?|summer|off-cycle|"
    r"subgerencia|gerencia|departamento|dpto|grupo funcional|[aá]rea|per[uú]|lima|h[ií]brido|presencial|remoto|\d{4})\b",
    re.I,
)
ANONYMOUS = re.compile(r"confidencial|importante empresa|empleador no divulgado", re.I)


def area_of(title: str) -> str:
    """The team a posting belongs to, from its title: "Practicante - Dpto. de Investigación
    Financiera" -> "Investigación Financiera". Empty when the title names only a level."""
    text = re.sub(r"\(.*?\)", " ", title or "")
    text = LEVEL_WORDS.sub(" ", text)
    text = re.sub(r"[-–/|·,:.]", " ", text)
    stop = ("de", "del", "en", "y", "la", "el", "para", "e")
    words = text.split()
    while words and words[0].lower() in stop:
        words.pop(0)
    words = words[:5]
    while words and words[-1].lower() in stop:
        words.pop()
    return " ".join(words)


LEGAL_SUFFIX = re.compile(r"\b(s\.?\s?a\.?\s?[ac]\.?|s\.?\s?a\.?|s\.?\s?r\.?\s?l\.?|e\.?\s?i\.?\s?r\.?\s?l\.?|ltd\.?|llc|inc\.?|sac|saa)(?=\s|$)", re.I)


def company_query(company: str) -> str:
    """The company as people search it: its acronym when the name carries one ("Banco Central de
    Reserva del Perú (BCRP)" -> "BCRP"), without legal suffixes ("Credicorp Ltd." -> "Credicorp")."""
    acronym = re.search(r"\(([A-ZÁÉÍÓÚÑ]{2,6})\)", company or "")
    if acronym:
        return acronym.group(1)
    name = re.sub(r"\(.*?\)", " ", company or "")
    name = LEGAL_SUFFIX.sub(" ", name)
    return " ".join(name.replace(",", " ").split())


def people_search(keywords: str) -> str:
    return "https://www.linkedin.com/search/results/people/?keywords=" + quote_plus(keywords)


def contacts_html(r: dict) -> str:
    """Links to find the team's leads, the recruiters and the named contact on LinkedIn. They are
    searches the candidate opens with their own account: nothing here fetches a profile."""
    company = (r.get("company") or "").strip()
    if not company or ANONYMOUS.search(company):
        return ""
    area = area_of(r.get("title") or "")
    name = company_query(company) or company
    links = []
    li = (((r.get("context") or {}).get("links") or {}).get("linkedin")) or ""
    m = re.match(r"^https?://[a-z]{0,3}\.?linkedin\.com/company/([^/?#]+)", li)
    if m:
        base = f"https://www.linkedin.com/company/{m.group(1)}/people/"
        links.append(link(base + ("?keywords=" + quote_plus(area) if area else ""), "Equipo del área"))
    links.append(link(people_search(f"jefe {area} {name}" if area else f"gerente {name}"), "Jefes"))
    links.append(link(people_search(f"reclutamiento talento {name}"), "Reclutamiento"))
    contact = r.get("contact")
    if isinstance(contact, str) and contact.strip():
        links.append(link(people_search(f"{contact.strip()} {name}"), contact.strip()))
    google = f'site:linkedin.com/in "{name}" (jefe OR gerente OR head) {area}'.strip()
    links.append(link("https://www.google.com/search?q=" + quote_plus(google), "Google"))
    return '<div class="how"><b>Contactos:</b> ' + " · ".join(links) + "</div>"


# Words that say how senior someone is, not what they work on: left out when matching an area.
ROLE_WORDS = {"de", "del", "la", "el", "y", "en", "e", "departamento", "especialista", "senior", "jefe", "jefa",
              "gerente", "subgerente", "central", "supervisor", "supervisora", "lider", "asesor", "asesora",
              "especializado", "especializada", "area", "analista", "practicante", "programa"}


SENIORITY = ("gerente central", "gerente", "subgerente", "jef", "director", "head", "supervisor", "asesor", "senior")


def seniority(role: str) -> int:
    """Lower is more senior: who to write to first."""
    low = (role or "").lower()
    for i, word in enumerate(SENIORITY):
        if re.search(r"\b" + word, low):
            return i
    return len(SENIORITY)


def _stems(text: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", (text or "").lower())
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    out = set()
    for w in re.findall(r"[a-z]+", folded):
        if w in ROLE_WORDS or len(w) < 4:
            continue
        if len(w) > 5 and w.endswith("es"):
            w = w[:-2]
        elif len(w) > 4 and w.endswith("s"):
            w = w[:-1]
        out.add(w)
    return out


def load_people(research_dir: Path, company: str) -> list[dict]:
    """People the employer itself lists (company_research/<empresa>.json -> people), each with the
    official page they came from."""
    try:
        doc = json.loads((research_dir / company_file(company)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    people = doc.get("people") if isinstance(doc, dict) else None
    return [p for p in people or [] if isinstance(p, dict) and isinstance(p.get("name"), str) and p.get("role")]


def people_for(people: list[dict], title: str, limit: int = 4) -> list[dict]:
    """Who works on the posting's area: role words shared with the area, leads first."""
    area = _stems(area_of(title))
    if not area:
        return []
    scored = []
    for p in people:
        shared = len(area & _stems(p.get("role") or ""))
        if shared:
            scored.append((shared, seniority(p["role"]), p))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]["name"]))
    return [p for _, _, p in scored[:limit]]


def people_html(r: dict) -> str:
    rows = []
    for p in r.get("people") or []:
        name = p["name"]
        official = link(p.get("url"), name) if re.match(r"^https?://", str(p.get("url") or "")) else esc(name)
        li = link(people_search(f"{name} {company_query(r.get('company') or '')}"), "LinkedIn")
        rows.append(f"<li>{official} · {esc(p['role'])} · {li}</li>")
    if not rows:
        return ""
    return '<div class="how"><b>Personas del área</b> (web oficial):<ul class="people">' + "".join(rows) + "</ul></div>"


def cv_html(r: dict) -> str:
    """The tailored CV for this posting (seen_jobs entry -> cv), as one link that works everywhere:
    a plain download in a browser (the PDF embedded, or next to the page), and in a claude.ai
    Artifact the page script saves it through the viewer's downloads capability instead."""
    href = r.get("cv_href")
    if not href:
        return ""
    name = r.get("cv_name") or href.rsplit("/", 1)[-1]
    return (f'<div class="cvrow"><a class="btn-cv" href="{esc(href)}" download="{esc(name)}" data-file="{esc(name)}" '
             f'target="_blank" rel="noopener noreferrer" title="CV sugerido para este puesto · descargar {esc(name)}">CV sugerido</a></div>')


def cv_hrefs(rows: list[dict], out_dir: Path, artifact: bool, embed: bool = False) -> dict[str, str]:
    """Set each row's cv_href and return {published name: source path} for the Artifact's files.
    `embed` puts the PDF itself in the page (a data: URI), so the one file can be opened or sent
    anywhere; otherwise the Artifact links its published copy and the local page the file on disk."""
    manifest, inline = {}, {}
    for r in rows:
        src = r.get("cv")
        if not isinstance(src, str) or not src.lower().endswith(".pdf") or not Path(src).is_file():
            continue
        name = Path(src).name
        r["cv_name"] = name
        if embed:
            if src not in inline:
                data = Path(src).read_bytes()
                if not data.startswith(b"%PDF"):
                    continue  # only a real PDF is embedded
                inline[src] = "data:application/pdf;base64," + base64.b64encode(data).decode("ascii")
            r["cv_href"] = inline[src]
        elif artifact:
            if manifest.get(name, src) != src:
                continue  # two different CVs with one file name: keep the first
            manifest[name] = src
            r["cv_href"] = name
        else:
            r["cv_href"] = os.path.relpath(src, out_dir).replace(os.sep, "/")
    return manifest


def logo_classes(rows: list[dict]) -> dict[str, str]:
    """One CSS class per distinct logo, so a company with ten postings embeds its image once."""
    out: dict[str, str] = {}
    for r in rows:
        uri = r.get("logo")
        if uri and uri not in out:
            out[uri] = f"lg{len(out)}"
    return out


def logo_html(r: dict, classes: dict[str, str] | None = None) -> str:
    cls = (classes or {}).get(r.get("logo") or "")
    if cls:
        return f'<span class="logo img {cls}" aria-hidden="true"></span>'
    if r.get("logo"):
        return f'<img class="logo" src="{esc(r["logo"])}" alt="" width="40" height="40">'
    initials = "".join(w[0] for w in re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9]+", r.get("company") or "")[:2]).upper()
    return f'<span class="logo none" aria-hidden="true">{esc(initials or "?")}</span>'


def load_ratings(research_dir: Path, company: str) -> list[dict] | None:
    path = research_dir / company_file(company)
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ratings = ((doc.get("sources") or {}).get("reviews") or {}).get("ratings")
    return ratings if isinstance(ratings, list) else []


def fmt_es(value) -> str:
    """4.26 -> '4,26'; 20188 -> '20.188' (Peruvian Spanish separators)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "?"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.2f}".replace(".", ",")
    return f"{int(value):,}".replace(",", ".")


def fmt_rating(value) -> str:
    """4.26 -> '4,26'; 3.9 -> '3,9'; 4 -> '4,0'."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "?"
    text = f"{float(value):.2f}".rstrip("0")
    return (text + "0" if text.endswith(".") else text).replace(".", ",")


def rating_html(ratings: list[dict] | None) -> str:
    if ratings is None:
        return '<span class="dim">sin investigar</span>'
    if not ratings:
        return '<span class="dim">sin calificación (no es mala señal)</span>'
    parts = []
    for r in ratings:
        n = r.get("n")
        small = not isinstance(n, int) or n < SMALL_N  # no count shown counts as small
        area = ""
        if r.get("area"):
            area_n = r.get("area_n")
            tag = " · referencial" if isinstance(area_n, int) and area_n < SMALL_N else ""
            area = f'<br><span class="dim">{esc(r["area"])}: {esc(fmt_es(area_n))} opiniones{tag}</span>'
        latest = f' · última {esc(r["latest"])}' if r.get("latest") else ""
        ref = ' <span class="tag">referencial</span>' if small else ""
        count = f"n={esc(fmt_es(n))}" if isinstance(n, int) else "sin n"
        note = f'<br><span class="dim">{esc(r["note"])}</span>' if r.get("note") else ""
        parts.append(
            f'{link(r.get("url"), r.get("source") or "fuente")} <b>{esc(fmt_rating(r.get("rating")))}</b>'
            f' <span class="dim">({count}{latest})</span>{ref}{area}{note}'
        )
    return "<br>".join(parts)


FRESH_CSS = {"muy alta": "f4", "alta": "f3", "media": "f2", "baja": "f1"}


def freshness_of(fresh: dict | None) -> str:
    """pe_frescura's freshness level; frescura.json files written before it existed lack it."""
    if not fresh:
        return "sin revisar"
    level = fresh.get("freshness")
    if level in pe_frescura.FRESHNESS_ORDER:
        return level
    age = fresh.get("age_days")
    if not isinstance(age, int):
        return "sin fecha"
    if fresh.get("risk") == "alto" and age <= pe_frescura.ACTIVE_DAYS:
        return "baja"  # closed or expired
    for limit, name in ((pe_frescura.VERY_FRESH_DAYS, "muy alta"), (pe_frescura.FRESH_DAYS, "alta"), (pe_frescura.ACTIVE_DAYS, "media")):
        if age <= limit:
            return name
    return "baja"


def fresh_html(fresh: dict | None) -> str:
    if not fresh:
        return '<span class="dim">sin revisar</span>'
    floor = "≥" if fresh.get("age_is_minimum") else ""
    age = f"{floor}{fresh['age_days']} d" if fresh.get("age_days") is not None else "?"
    signals = "".join(f"<li>{esc(s)}</li>" for s in fresh.get("signals") or [])
    level = freshness_of(fresh)
    risk = fresh.get("risk")
    return (
        f'<span class="fresh {FRESH_CSS.get(level, "")}">{esc(level)}</span> · {esc(age)}'
        f'<br><span class="dim">publicada {esc(fresh.get("original_date") or "?")}</span>'
        + (f'<br><span class="risk {esc(risk)}">riesgo de ghost job {esc(risk)}</span>' if risk in ("medio", "alto") else "")
        + (f'<ul class="signals">{signals}</ul>' if signals else "")
    )


def bullets(items) -> str:
    items = [i for i in (items or []) if i]
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else '<span class="dim">—</span>'


# How each source reads to a person, and confidence as filled dots (tools/pe_contexto.py).
SOURCE_TEXT = {
    "aviso": "lo dice el aviso", "aviso + portal": "aviso y portal coinciden", "portal": "dato del portal",
    "aviso (sede del cliente)": "sede del cliente, según el aviso", "web": "buscado en la web", "nombre": "deducido del nombre",
}
CONF_DOTS = {"alta": ("●●●", "confianza alta"), "media": ("●●○", "confianza media"), "baja": ("●○○", "confianza baja")}


def maps_url(query: str) -> str:
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(query)


def _evidence(source, confidence) -> str:
    """'dato del portal ●●○' with the full wording in the tooltip."""
    if not source:
        return ""
    dots, label = CONF_DOTS.get(confidence or "", ("", ""))
    text = SOURCE_TEXT.get(source, source)
    title = f"{text}; {label}" if label else text
    return (f'<span class="ev" title="{esc(title)}">{esc(text)}'
            + (f' <span class="dots conf-{esc(confidence)}" aria-label="{esc(label)}">{dots}</span>' if dots else "")
            + "</span>")


MODALITY_CLASS = {"presencial": "pres", "híbrido": "hib", "remoto": "rem"}


def also_html(r: dict) -> str:
    """Where else the same posting is published, with the company name each portal shows."""
    links = []
    for e in r.get("also") or []:
        portal = (e.get("portal") or "otro portal").replace("-search", "")
        label = portal + (f" ({e['company']})" if e.get("company") and e.get("company") != r.get("company") else "")
        links.append(link(e.get("url"), label))
    return f'<div class="src">También en: {" · ".join(links)}</div>' if links else ""


def meta_html(r: dict) -> str:
    """Work mode as a coloured pill (it decides commutes), then level and portal, quieter."""
    modality = r.get("modality") or "no declarado"
    parts = [f'<span class="pill mod-{MODALITY_CLASS.get(modality, "nd")}">{esc(modality.capitalize())}</span>']
    if r.get("abroad"):
        where = esc(r.get("location") or "fuera del Perú")
        parts.append(f'<span class="pill abroad-onsite" title="Pide mudarse o viajar">Extranjero · {where} · no remoto</span>'
                     if r.get("abroad_onsite") else f'<span class="pill abroad">Extranjero · {where}</span>')
    if r.get("level") and r["level"] != "no declarado":
        parts.append(f'<span class="pill lvl">{esc(r["level"])}</span>')
    if r.get("portal"):
        parts.append(f'<span class="src">{esc(r["portal"].replace("-search", ""))}</span>')
    return " ".join(parts)


def employer_kinds(rubro: dict) -> list[str]:
    label = (rubro.get("label") or "").lower()
    return [name for name, active in (("Consultora", rubro.get("staffing") and "consultor" not in label),
                                      ("Recluta para un cliente", rubro.get("for_client")),
                                      ("Empresa anónima", rubro.get("anonymous"))) if active]


def context_html(ctx: dict | None) -> str:
    """Workplace and employer sector as chips, each with where the data came from and how sure it is."""
    if not ctx:
        return ""
    lg, rb, links = ctx.get("lugar") or {}, ctx.get("rubro") or {}, ctx.get("links") or {}
    districts = lg.get("districts") or []

    chips = []
    if lg.get("remote"):
        chips.append('<span class="chip kind">Remoto</span>')
    chips += [f'<button type="button" class="chip place" data-set-filter="district" data-value="{esc(d)}" title="Filtrar por {esc(d)}">{esc(d)}</button>' for d in districts]
    # The Lima zone is left to the zone filter: on each row it only repeated the district.
    if not chips:
        chips.append('<span class="chip muted">Lugar sin dato</span>')
    notes = []
    if lg.get("address"):
        notes.append(esc(lg["address"]))
    if lg.get("residence_required"):
        notes.append("Pide vivir en o cerca de " + esc(", ".join(lg["residence_required"])))
    note_html = "".join(f'<div class="ctx-note">{n}</div>' for n in notes)
    place = (f'<div class="ctx-row"><span class="ctx-k">Lugar</span><div class="ctx-v">{" ".join(chips)} '
             f'{_evidence(lg.get("source"), lg.get("confidence"))}{note_html}</div></div>')

    label = rb.get("label") or ""
    kinds = employer_kinds(rb)
    sector_chips = ([f'<button type="button" class="chip sector" data-set-filter="sector" data-value="{esc(label)}" title="Filtrar por {esc(label)}">{esc(label)}</button>'] if label
                    else ['<span class="chip muted">Rubro sin identificar</span>'])
    sector_chips += [f'<button type="button" class="chip kind" data-set-filter="kind" data-value="{esc(k)}" title="Filtrar por {esc(k)}">{esc(k)}</button>' for k in kinds]
    sector = (f'<div class="ctx-row"><span class="ctx-k">Rubro</span><div class="ctx-v">{" ".join(sector_chips)} '
              f'{_evidence(rb.get("source"), rb.get("confidence"))}</div></div>')

    buttons = []
    # A map only for a real address: a district alone drops the pin in its center, not at the job.
    if not lg.get("remote") and lg.get("address"):
        query = lg["address"]
        if districts and districts[0].lower() not in query.lower():
            query += f", {districts[0]}"
        buttons.append(f'<a class="btn" href="{esc(maps_url(query + ", Lima, Perú"))}" target="_blank" '
                       f'rel="noopener noreferrer" title="{esc(lg["address"])}">Mapa</a>')
    for key, label in (("website", "Web"), ("linkedin", "LinkedIn"), ("computrabajo", "Computrabajo")):
        if isinstance(links.get(key), str) and re.match(r"^https?://", links[key]):  # the file may be hand-edited
            buttons.append(f'<a class="btn" href="{esc(links[key])}" target="_blank" rel="noopener noreferrer">{label}</a>')
    actions = (f'<div class="ctx-row"><span class="ctx-k"></span><div class="ctx-v ctx-btns">{"".join(buttons)}</div></div>'
               if buttons else "")
    return f'<div class="ctx">{place}{sector}{actions}</div>'


# Where a posting is. In the candidate's own country any work mode is in play; abroad, the
# profile's `Abroad:` line decides: remote only unless it says relocating or travelling is fine.
PERU_PLACES = re.compile(
    r"\b(per[uú]|lima|callao|amazonas|[aá]ncash|apur[ií]mac|arequipa|ayacucho|cajamarca|cusco|cuzco|huancavelica|"
    r"hu[aá]nuco|ica|jun[ií]n|la libertad|lambayeque|loreto|madre de dios|moquegua|pasco|piura|puno|san mart[ií]n|"
    r"tacna|tumbes|ucayali|trujillo|chiclayo|huancayo|chimbote|iquitos|pucallpa)\b", re.I)
REMOTE_WORDS = re.compile(r"\b(remote|remoto|anywhere|teletrabajo|worldwide)\b", re.I)
LOCAL_PORTALS = {"bumeran-search", "computrabajo-search"}  # their listings are Peruvian only


ABROAD_LABEL = {"no": "no busco fuera del Perú", "remoto": "solo remoto", "viajar": "acepto mudarme o viajar"}


def read_abroad(profile: Path) -> dict:
    """The profile's `Abroad:` line as {"mode": "no" | "remoto" | "viajar", "text": what it says}.
    The search is in the candidate's own city and country unless the line says otherwise: a
    missing line or a placeholder means no postings abroad; "remote only" lets in remote ones
    abroad; relocating or travelling lets in every one."""
    try:
        text = profile.read_text(encoding="utf-8")
    except OSError:
        return {"mode": "no", "text": None}
    m = re.search(r"\*\*Abroad:\*\*[ \t]*([^\n<]*)", text)
    value = m.group(1).strip() if m else ""
    if not value or value.startswith("["):
        return {"mode": "no", "text": None}
    if re.match(r"(?i)(no|none|ninguno|solo (en )?(lima|per[uú]))\b", value):
        return {"mode": "no", "text": value}
    only_remote = re.search(r"remote only|only remote|solo remoto", value, re.I)
    if re.search(r"relocat|mudar|viaj|travel|on-?site|presencial", value, re.I) and not only_remote:
        return {"mode": "viajar", "text": value}
    if only_remote or re.search(r"remot", value, re.I):
        return {"mode": "remoto", "text": value}
    return {"mode": "no", "text": value}


def abroad_excluded(r: dict, mode: str) -> bool:
    """Whether the profile leaves this posting out: abroad when it searches only at home, or
    abroad and not remote when it accepts remote work only."""
    if not r.get("abroad"):
        return False
    return mode == "no" or (mode == "remoto" and bool(r.get("abroad_onsite")))


def posting_location(entry: dict) -> str | None:
    """The place the portal gives: the state's `location`, else the saved detail's."""
    if isinstance(entry.get("location"), str) and entry["location"].strip():
        return entry["location"].strip()
    detail = entry.get("detail")
    if not isinstance(detail, str) or not detail.lower().endswith(".json"):
        return None
    try:
        doc = json.loads(Path(detail).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    loc = doc.get("location") if isinstance(doc, dict) else None
    return loc.strip() if isinstance(loc, str) and loc.strip() else None


def is_abroad(entry: dict, location: str | None) -> bool:
    """Outside Peru: a place that names no Peruvian region, on a portal that is not Peru-only.
    "Remote" on its own names no country, so it is remote, not abroad."""
    if entry.get("portal") in LOCAL_PORTALS or not location or PERU_PLACES.search(location):
        return False
    return not re.match(r"\s*(remote|remoto|anywhere|worldwide)\b", location, re.I)


def fresh_from_state(entry: dict, today: date) -> dict | None:
    """Freshness from posted_date alone, for postings with no portal detail (pasted from a
    Facebook group or a university board): age only, no ghost-job signals."""
    try:
        posted = date.fromisoformat(str(entry.get("posted_date")))
    except ValueError:
        return None
    age = (today - posted).days
    if age < 0:
        return None
    window = "fresca" if age <= pe_frescura.FRESH_DAYS else "en curso" if age <= pe_frescura.ACTIVE_DAYS else "vieja"
    fresh = {"age_days": age, "original_date": posted.isoformat(), "age_is_minimum": False, "window": window,
             "risk": None, "signals": ["solo la fecha de publicación (sin detalle del portal)"]}
    fresh["freshness"] = freshness_of(fresh)
    return fresh


def collect(state: dict, fresh_rows: list[dict], research_dir: Path, context_rows: list[dict] | None = None,
            today: date | None = None) -> dict:
    today = today or date.today()
    seen = state.get("seen") or {}
    fresh_by_id = {pe_frescura.posting_id(r): r for r in fresh_rows}
    ctx_by_id = {pe_frescura.posting_id(r): r for r in (context_rows or [])}
    logos = load_logos(research_dir)
    ranked, unranked = [], []
    # The same posting on another portal (tools/pe_duplicados.py): shown once, on the one kept.
    also: dict[str, list[dict]] = {}
    for key, e in seen.items():
        if isinstance(e, dict) and e.get("duplicate_of") in seen:
            also.setdefault(e["duplicate_of"], []).append(e)
    def own_id(e: dict) -> str:
        return pe_frescura.posting_id({"id": e.get("id"), "url": e.get("url")})

    # A key several postings share (SERVIR's one search page, predoc.org's listing, a missing
    # URL) names none of them: joining on it would hand one posting another's freshness.
    shared = Counter(own_id(e) for e in seen.values() if isinstance(e, dict))
    for key, e in seen.items():
        if not isinstance(e, dict) or e.get("duplicate_of") in seen:
            continue
        row = dict(e, key=key, also=also.get(key, []))
        if e.get("status") == "ranked":
            pid = own_id(e)
            if shared[pid] > 1:
                pid = ""
            fresh = (fresh_by_id.get(pid) if pid else None) or fresh_from_state(e, today)
            row["fresh"] = fresh
            row["context"] = ctx_by_id.get(pid) if pid else None
            row["action"] = action_for(e, fresh, today)
            row["closes"] = deadline_info(e, today)
            row["ratings"] = load_ratings(research_dir, e.get("company") or "")
            row["logo"] = logo_uri(research_dir, logos, e.get("company") or "")
            row["people"] = people_for(load_people(research_dir, e.get("company") or ""), e.get("title") or "")
            row["location"] = posting_location(e)
            row["abroad"] = is_abroad(e, row["location"])
            remote = e.get("modality") == "remoto" or bool(REMOTE_WORDS.search(row["location"] or ""))
            row["abroad_onsite"] = row["abroad"] and not remote  # needs moving or travelling
            ranked.append(row)
        elif e.get("status") in (None, "new") and e.get("fit") in ("high", "medium"):
            unranked.append(row)
    ranked.sort(key=lambda r: (ACTION_ORDER[r["action"]], -(r.get("rank_score") or 0)))
    unranked.sort(key=lambda r: r.get("posted_date") or "", reverse=True)
    shortlist = [r for r in ranked if r.get("rank_verdict") in SHORTLIST]
    return {
        "seen": seen,
        "ranked": ranked,
        "unranked": unranked,
        "shortlist": shortlist,
        "portals": Counter((e or {}).get("portal") or "?" for e in seen.values()),
        "verdicts": Counter(r.get("rank_verdict") for r in ranked),
        "freshness": Counter(freshness_of(r["fresh"]) for r in shortlist),
        "rated": sum(1 for r in shortlist if r["ratings"]),
        "actions": Counter(r["action"] for r in ranked),
    }


_DARK = """--bg:#0f1318;--surface:#171c23;--surface-2:#1d232c;--ink:#e7ebf0;--dim:#9ba6b4;--faint:#6f7a88;--line:#2a313b;
--accent:#9cc1ea;--accent-ink:#0f1318;--accent-soft:#1c2a3b;--go:#4ade80;--go-bg:#13301e;--warn:#fbbf24;--warn-bg:#382c0f;
--stop:#f87171;--stop-bg:#3a1717;--muted-bg:#262d37;--out:#8e99a8;--chip-place:#1b3654;--chip-sector:#222b36;
--mod-pres:#c7d6ea;--mod-pres-bg:#24324a;--mod-hib:#c4b5fd;--mod-hib-bg:#2d2350;--shadow:0 1px 0 rgba(255,255,255,.03),0 10px 30px -18px rgba(0,0,0,.8);
color-scheme:dark"""

# Light tokens on :root; dark ones under prefers-color-scheme (unless the host forces light)
# and again under data-theme="dark", so a host theme toggle (or the page's own) wins both ways.
CSS = """
/* Layout: the five workflow steps as a strip, what closes this week, then one posting at a time. */
:root{--bg:#f3f4f6;--surface:#fff;--surface-2:#f8f9fb;--ink:#17202b;--dim:#5b6675;--faint:#8b95a3;--line:#e1e4e9;
--accent:#1f3a5f;--accent-ink:#fff;--accent-soft:#e7eef7;--go:#15803d;--go-bg:#dcfce7;--warn:#a16207;--warn-bg:#fef3c7;
--stop:#b91c1c;--stop-bg:#fee2e2;--muted-bg:#e8eaee;--out:#6b7280;--chip-place:#dbe8f6;--chip-sector:#eef1f5;
--mod-pres:#1f3a5f;--mod-pres-bg:#e3e9f1;--mod-hib:#6d28d9;--mod-hib-bg:#ede9fe;
--shadow:0 1px 2px rgba(16,24,40,.05),0 12px 32px -20px rgba(16,24,40,.28);color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){""" + _DARK + """}}
:root[data-theme="dark"]{""" + _DARK + """}
[hidden]{display:none!important}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;-webkit-font-smoothing:antialiased}
header,main,footer{max-width:1180px;margin:0 auto;padding-block:18px;
padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3{text-wrap:balance;margin:0}
.num,.ring b,.step .big,.pos{font-variant-numeric:tabular-nums}
a{color:var(--accent);text-underline-offset:2px}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible,.track:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
code{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;background:var(--muted-bg);padding:1px 5px;border-radius:4px}
.dim{color:var(--dim)}
.topline{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
.eyebrow{font-size:11.5px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin-bottom:4px}
h1{font-size:26px;line-height:1.2;letter-spacing:-.01em}
.sub{color:var(--dim);margin-top:6px;max-width:72ch}
.run-date{margin:7px 0 0;color:var(--dim);font-size:12.5px}.run-date strong{color:var(--ink)}
.icon-btn{flex:none;all:unset;cursor:pointer;width:36px;height:36px;border-radius:10px;display:grid;place-items:center;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:16px}
.icon-btn:hover{border-color:var(--accent)}
/* workflow strip */
.flow{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:0;margin-top:18px;background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);overflow:hidden}
.step{padding:14px 16px;border-right:1px solid var(--line);min-width:0}.step:last-child{border-right:0}
.step .n{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;font-weight:600;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.step .big{font-size:26px;font-weight:700;line-height:1.15;margin-top:4px}.step small{display:block;color:var(--dim);font-size:12px;line-height:1.4}
.step:last-child .big{color:var(--go)}
.tag{font-size:10.5px;border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--dim);font-weight:500;text-transform:none;letter-spacing:0}
/* sections */
h2{font-size:18px;margin:30px 0 12px;display:flex;align-items:center;gap:8px}
.count-pill{font-size:12px;font-weight:700;background:var(--accent-soft);color:var(--accent);border-radius:999px;padding:1px 9px}
/* what closes this week */
.soon-wrap{display:flex;align-items:center;gap:8px;margin-top:12px}
.soon-track{--visible:4;display:flex;gap:10px;min-width:0;flex:1;overflow-x:auto;scroll-snap-type:x mandatory;scroll-behavior:smooth;overscroll-behavior-x:contain;scrollbar-width:none}
.soon-track::-webkit-scrollbar{display:none}
.soon-nav{flex:none;width:34px;height:34px;border:1px solid var(--line);border-radius:50%;background:var(--surface-2);color:var(--ink);font:700 20px/1 system-ui;cursor:pointer}
.soon-nav:disabled{opacity:.35;cursor:default}
.soon-nav[hidden]{display:none}
.soon-card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}
.soon-card{flex:0 0 calc((100% - (var(--visible) - 1)*10px)/var(--visible));scroll-snap-align:start;scroll-snap-stop:always}
.soon-card .t{font-weight:600;line-height:1.3}.soon-card .co{color:var(--dim);font-size:12.5px}
.soon-card .row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.soon-card .how{margin-top:0}
.link-btn{all:unset;cursor:pointer;color:var(--accent);font-size:12.5px;font-weight:600}.link-btn:hover{text-decoration:underline}
.note{font-size:12px;color:var(--faint);margin:6px 0 0}
/* toolbar */
.toolbar{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px;display:grid;grid-template-columns:minmax(0,1fr);gap:10px;margin-bottom:12px}
.filters{display:flex;flex-wrap:wrap;gap:8px}
.filters input,.filters select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface-2);color:var(--ink);min-width:0}
.filters input[type=search]{flex:1 1 220px}
.filters select{flex:0 1 auto}
.quick{display:flex;align-items:center;flex-wrap:wrap;gap:7px;font-size:12px;color:var(--dim)}
.quick button{font:inherit;cursor:pointer;border:1px solid transparent}.quick button[aria-pressed="true"]{outline:2px solid var(--accent);outline-offset:1px}
.quick select{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:7px;background:var(--surface-2);color:var(--ink);max-width:100%;min-width:0}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;font-size:12px;color:var(--dim)}
/* carousel bar */
.bar{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 0;background:var(--bg)}
.bar.list-mode .ticks,.bar.list-mode .pos{display:none}
.nav{all:unset;cursor:pointer;width:38px;height:38px;border-radius:999px;display:grid;place-items:center;background:var(--accent);color:var(--accent-ink);font-size:20px;line-height:1;font-weight:700}
.nav:hover{filter:brightness(1.12)}.nav[disabled]{opacity:.35;cursor:default;filter:none}
.pos{font-size:14px;color:var(--dim);min-width:74px;text-align:center}.pos b{color:var(--ink);font-size:16px}
.ticks{flex:1 1 200px;display:flex;gap:2px;height:22px;align-items:center;min-width:0}
.tick{all:unset;cursor:pointer;flex:1 1 0;min-width:2px;height:8px;border-radius:2px;background:var(--line);transition:height .15s}
.tick.go{background:var(--go)}.tick.warn{background:var(--warn)}.tick.stop{background:var(--stop)}.tick.muted{background:var(--faint)}
.tick.done{opacity:.35}.tick.cur{height:20px;box-shadow:0 0 0 2px var(--bg),0 0 0 3px var(--ink)}
.tick:hover{height:16px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;background:var(--surface)}
.seg button{all:unset;cursor:pointer;padding:6px 12px;font-size:12.5px;font-weight:600;color:var(--dim)}
.seg button[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink)}
.progress{font-size:12.5px;color:var(--dim)}.progress b{color:var(--ink)}
/* the track: one card per view, swipe or arrows; .list shows them all */
.track{display:flex;gap:16px;overflow-x:auto;overflow-y:hidden;scroll-snap-type:x mandatory;scroll-behavior:smooth;overscroll-behavior-x:contain;
scrollbar-width:none;align-items:flex-start;position:relative;transition:height .2s ease;padding:2px}
.track::-webkit-scrollbar{display:none}
.track>.card{flex:0 0 calc(100% - 4px);scroll-snap-align:start;scroll-snap-stop:always}
.track.list{display:grid;grid-template-columns:minmax(0,1fr);overflow:visible;height:auto!important;gap:14px}
.empty{padding:40px 16px;text-align:center;color:var(--dim);background:var(--surface);border:1px dashed var(--line);border-radius:14px}
/* the posting card */
.card{position:relative;background:var(--surface);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:18px 20px 20px;min-width:0;overflow:hidden}
.card::before{content:"";position:absolute;inset:0 0 auto 0;height:4px;background:var(--line)}
.card.go::before{background:var(--go)}.card.warn::before{background:var(--warn)}.card.stop::before{background:var(--stop)}.card.muted::before{background:var(--faint)}
.card.is-done{opacity:.6}.card.is-done:hover{opacity:1}
.card-top{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.card-top .grow{flex:1}
.done-l{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:var(--dim);cursor:pointer;border:1px solid var(--line);border-radius:999px;padding:3px 10px 3px 8px;user-select:none}
.done-l:has(input:checked){background:var(--go-bg);color:var(--go);border-color:transparent}
.done-l input{margin:0;accent-color:var(--go)}
.card-head{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:start;margin-top:14px}
.card-head h3{font-size:19px;line-height:1.3}.card-head h3 a{color:var(--ink);text-decoration:none}.card-head h3 a:hover{color:var(--accent);text-decoration:underline}
.co{color:var(--dim);font-weight:500;margin-top:2px}
.ring{--p:0;--c:var(--accent);width:62px;height:62px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--c) calc(var(--p)*1%),var(--line) 0)}
.ring>span{width:52px;height:52px;border-radius:50%;background:var(--surface);display:grid;place-items:center;text-align:center;line-height:1}
.ring b{font-size:19px}.ring small{display:block;font-size:9.5px;color:var(--faint);margin-top:2px;text-transform:uppercase;letter-spacing:.04em}
.card.go .ring{--c:var(--go)}.card.warn .ring{--c:var(--warn)}.card.stop .ring{--c:var(--stop)}.card.muted .ring{--c:var(--faint)}
.verdict{font-size:11.5px;color:var(--dim);text-align:center;margin-top:4px;white-space:nowrap}
.card-body{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:14px 28px;margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}
.blk+.blk{margin-top:14px}
.blk h4{margin:0 0 6px;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--faint)}
.blk ul{margin:0;padding-left:18px}.blk li{margin:0 0 4px}
.blk.gaps li::marker{color:var(--stop)}.blk.strengths li::marker{color:var(--go)}
.apply{background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.logo{flex:none;width:40px;height:40px;border-radius:10px;object-fit:contain;background:#fff center/contain no-repeat;border:1px solid var(--line)}
.logo.none{display:inline-flex;align-items:center;justify-content:center;background:var(--muted-bg);color:var(--dim);font-size:13px;font-weight:700}
.soon-card .logo{width:28px;height:28px;border-radius:7px;font-size:10px}
/* badges, pills and chips */
.badge{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap}
.badge.go{background:var(--go-bg);color:var(--go)}.badge.warn{background:var(--warn-bg);color:var(--warn)}
.badge.stop{background:var(--stop-bg);color:var(--stop)}.badge.muted{background:var(--muted-bg);color:var(--dim)}
.badge.out{background:var(--muted-bg);color:var(--out)}
.risk{font-size:12px}.risk.medio{color:var(--warn)}.risk.alto{color:var(--stop)}
.fresh,.close{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:700;white-space:nowrap}
.fresh.f4{background:var(--go);color:var(--surface)}.fresh.f3{background:var(--go-bg);color:var(--go)}
.fresh.f2{background:var(--warn-bg);color:var(--warn)}.fresh.f1{background:var(--stop-bg);color:var(--stop)}
.close{border:1px solid var(--line);color:var(--dim);font-weight:600}
.close.today{background:var(--stop);color:#fff;border-color:transparent}.close.soon{background:var(--stop-bg);color:var(--stop);border-color:transparent}
.close.week{background:var(--warn-bg);color:var(--warn);border-color:transparent}.close.past{color:var(--faint);text-decoration:line-through}
.signals{color:var(--dim);font-size:12px;margin-top:4px!important}
.how{margin-top:6px;font-size:12.5px;color:var(--dim)}.how b{color:var(--ink)}
.cvrow{margin:0 0 8px}
.btn-cv{display:inline-flex;align-items:center;gap:6px;font:600 13px/1 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;padding:9px 14px;border-radius:9px;
border:1px solid var(--accent);background:var(--accent);color:var(--accent-ink);text-decoration:none;cursor:pointer}
.btn-cv::before{content:"↓";font-weight:700}
.btn-cv:hover{filter:brightness(1.12)}.btn-cv[aria-disabled="true"]{opacity:.55;cursor:not-allowed}
.people{margin:3px 0 0;padding-left:16px}.people li{margin:1px 0}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-top:8px}
.pill{display:inline-block;padding:2px 10px;border-radius:6px;font-size:12px;font-weight:600;line-height:1.5}
.pill.mod-pres{background:var(--mod-pres-bg);color:var(--mod-pres)}.pill.mod-hib{background:var(--mod-hib-bg);color:var(--mod-hib)}
.pill.mod-rem{background:var(--go-bg);color:var(--go)}.pill.mod-nd{border:1px dashed var(--line);color:var(--dim);font-weight:500}
.pill.abroad{background:var(--accent-soft);color:var(--accent)}.pill.abroad-onsite{background:var(--warn-bg);color:var(--warn)}
.pref{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 14px;font-size:12.5px;padding-top:10px;border-top:1px solid var(--line)}
.pref span{min-width:0;overflow-wrap:anywhere}
.cov table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:10px;min-width:760px}
.cov th,.cov td{text-align:left;vertical-align:top;padding:7px 8px;border-bottom:1px solid var(--line)}
.cov th{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.05em}
.cov .wrap{overflow-x:auto}.cov .links a{margin-right:8px;white-space:nowrap}
.st{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11.5px;font-weight:700;white-space:nowrap}
.st.ok{background:var(--go-bg);color:var(--go)}.st.err{background:var(--stop-bg);color:var(--stop)}.st.man{background:var(--muted-bg);color:var(--dim)}
.pill.lvl{background:var(--muted-bg);color:var(--ink);font-weight:500}.src{font-size:11.5px;color:var(--dim)}
.chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;line-height:1.6;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
button.chip{font:inherit;font-size:12px;cursor:pointer}button.chip:hover{outline:1px solid var(--accent)}
.chip.place{background:var(--chip-place);border-color:transparent;font-weight:600}
.chip.zone{color:var(--dim)}
.chip.sector{background:var(--chip-sector);border-color:transparent}
.chip.kind{background:var(--warn-bg);color:var(--warn);border-color:transparent}
.chip.muted{color:var(--dim);border-style:dashed}
.ev{font-size:11.5px;color:var(--dim)}
.dots{letter-spacing:1px}.dots.conf-alta{color:var(--go)}.dots.conf-media{color:var(--warn)}.dots.conf-baja{color:var(--stop)}
.ctx{display:grid;gap:6px;font-size:12.5px}
.ctx-row{display:grid;grid-template-columns:52px minmax(0,1fr);gap:8px;align-items:start}
.ctx-k{font-size:10.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;padding-top:3px;font-weight:600}
.ctx-v{display:flex;flex-wrap:wrap;align-items:center;gap:4px;min-width:0}
.ctx-v .ev,.ctx-v .ctx-note{flex-basis:100%}
.ctx-note{color:var(--dim);font-size:11.5px}
.ctx-btns{gap:6px}
.btn{display:inline-block;padding:3px 11px;border:1px solid var(--line);border-radius:7px;font-size:12px;text-decoration:none;color:var(--accent);background:var(--surface);font-weight:600}
.btn:hover{border-color:var(--accent)}
.rating b{font-size:15px}
details{margin:18px 0;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px 16px}
details[open]{padding-bottom:16px}
summary{cursor:pointer;font-weight:700}
details .track{margin-top:12px}
details .card{box-shadow:none}
.unranked{margin:10px 0 0;padding-left:18px}.unranked li{margin:4px 0}
.fine{font-size:12px;color:var(--faint);max-width:90ch}
footer{color:var(--faint);font-size:12px;padding-bottom:28px}
@media (max-width:900px){.soon-track{--visible:2}.flow{grid-template-columns:repeat(2,minmax(0,1fr))}.step{border-bottom:1px solid var(--line)}
.step:nth-child(2n){border-right:0}.card-body{grid-template-columns:minmax(0,1fr)}}
@media (max-width:560px){.soon-track{--visible:1}.soon-nav{width:30px;height:30px}.flow{grid-template-columns:minmax(0,1fr)}.step{border-right:0}h1{font-size:22px}
.card{padding:16px 14px 16px}.card-head{grid-template-columns:auto minmax(0,1fr)}.card-head .score{grid-column:1/-1;display:flex;align-items:center;gap:10px}
.verdict{margin:0}.ticks{order:5;flex-basis:100%}}
@media print{.bar,.toolbar,.icon-btn,.soon-nav{display:none}.soon-track{display:block;overflow:visible}.soon-card{break-inside:avoid;margin-bottom:12px}.track{display:block;height:auto!important;overflow:visible}.card{break-inside:avoid;margin-bottom:12px;box-shadow:none}}
@media (prefers-reduced-motion:reduce){.track{scroll-behavior:auto;transition:none}}
"""

# Saving a CV: a plain download everywhere; inside a claude.ai Artifact (whose frame blocks
# download links) through the downloads capability; an embedded data: PDF goes through a blob
# so every browser saves it with its name.
CV_JS = """
(function(){
  function blobOf(href){return fetch(href).then(function(r){if(!r.ok)throw {code:'fetch'};return r.blob();});}
  document.addEventListener('click',function(ev){
    var a=ev.target.closest&&ev.target.closest('a.btn-cv');
    if(!a)return;
    var href=a.getAttribute('href'),name=a.dataset.file||'CV.pdf',label=a.textContent;
    var host=window.claude&&window.claude.use;
    if(!host&&href.indexOf('data:')!==0)return;  // a plain link: the browser handles it
    ev.preventDefault();
    if(a.getAttribute('aria-disabled')==='true')return;
    function done(t){a.textContent=t;setTimeout(function(){a.textContent=label;a.removeAttribute('aria-disabled');},2500);}
    a.setAttribute('aria-disabled','true');a.textContent='Preparando…';
    if(host){
      window.claude.use('downloads').then(function(d){
        if(!d){done('No disponible aquí');return;}
        return blobOf(href).then(function(blob){return d.save({filename:name,data:blob});})
          .then(function(){done('Descargado ✓');},function(e){var c=e&&e.code;done(c==='declined'?'Cancelado':c==='rate_limited'?'Espera un momento':'No disponible');});
      },function(){done('No disponible aquí');});
      return;
    }
    blobOf(href).then(function(blob){
      var url=URL.createObjectURL(blob),tmp=document.createElement('a');
      tmp.href=url;tmp.download=name;document.body.appendChild(tmp);tmp.click();tmp.remove();
      setTimeout(function(){URL.revokeObjectURL(url);},4000);done('Descargado ✓');
    },function(){done('No disponible');});
  });
})();
"""

JS = """
(function(){
  function get(k,d){try{var v=localStorage.getItem(k);return v===null?d:JSON.parse(v);}catch(e){return d;}}
  function put(k,v){try{localStorage.setItem(k,JSON.stringify(v));}catch(e){}}
  var root=document.documentElement;
  // Theme: follows the system (or the claude.ai host) until the viewer picks one here.
  var tb=document.getElementById('theme');
  var theme=get('pe_revision_theme',null);if(theme&&!root.getAttribute('data-theme'))root.setAttribute('data-theme',theme);
  if(tb)tb.addEventListener('click',function(){
    var dark=root.getAttribute('data-theme')?root.getAttribute('data-theme')==='dark':matchMedia('(prefers-color-scheme: dark)').matches;
    var next=dark?'light':'dark';root.setAttribute('data-theme',next);put('pe_revision_theme',next);
  });

  var track=document.getElementById('main');
  if(!track)return;
  var soonTrack=document.getElementById('soon-track');
  if(soonTrack){
    var soonCards=Array.prototype.slice.call(soonTrack.querySelectorAll('.soon-card'));
    var soonPrev=document.getElementById('soon-prev'),soonNext=document.getElementById('soon-next');
    function soonStep(){return soonCards.length>1?soonCards[1].offsetLeft-soonCards[0].offsetLeft:soonTrack.clientWidth;}
    function soonVisible(){return parseInt(getComputedStyle(soonTrack).getPropertyValue('--visible'),10)||1;}
    function updateSoon(){
      var overflow=soonTrack.scrollWidth>soonTrack.clientWidth+2;
      soonPrev.hidden=soonNext.hidden=!overflow;
      soonPrev.disabled=soonTrack.scrollLeft<2;
      soonNext.disabled=soonTrack.scrollLeft>=soonTrack.scrollWidth-soonTrack.clientWidth-2;
    }
    function moveSoon(dir){
      var step=soonStep(),max=Math.max(0,soonCards.length-soonVisible());
      var index=Math.max(0,Math.min(max,Math.round(soonTrack.scrollLeft/step)+dir));
      soonTrack.scrollTo({left:index*step,behavior:'smooth'});
    }
    soonPrev.addEventListener('click',function(){moveSoon(-1);});
    soonNext.addEventListener('click',function(){moveSoon(1);});
    soonTrack.addEventListener('scroll',updateSoon);
    window.addEventListener('resize',updateSoon);
    soonTrack.closest('details').addEventListener('toggle',updateSoon);
    updateSoon();
  }
  var cards=Array.prototype.slice.call(track.querySelectorAll('.card'));
  var prev=document.getElementById('prev'),next=document.getElementById('next'),pos=document.getElementById('pos'),tot=document.getElementById('tot');
  var ticks=document.getElementById('ticks'),empty=document.getElementById('empty');
  var cur=0,view=get('pe_revision_view_v2','list');

  // "Revisada": a per-browser convenience, never written back to the state files.
  var done=get('pe_revision_done',{});
  function count(){var c=document.getElementById('cnt');if(c)c.textContent=track.querySelectorAll('input.done:checked').length;}
  document.querySelectorAll('input.done').forEach(function(cb){
    var card=cb.closest('.card');cb.checked=!!done[cb.dataset.key];card.classList.toggle('is-done',cb.checked);
    cb.addEventListener('change',function(){
      done[cb.dataset.key]=cb.checked;put('pe_revision_done',done);card.classList.toggle('is-done',cb.checked);count();
      if(fd.value){apply();return;}
      drawTicks();
      if(cb.checked&&view==='car'&&track.contains(card))setTimeout(function(){go(cur+1);},260);
    });
  });
  count();

  function visible(){return cards.filter(function(c){return !c.hidden;});}
  function fit(){
    if(view!=='car'){track.style.height='';return;}
    var v=visible()[cur];track.style.height=v?(v.offsetHeight+4)+'px':'';
  }
  function mark(){
    var v=visible(),n=v.length;
    if(cur>n-1)cur=Math.max(0,n-1);
    pos.textContent=n?cur+1:0;tot.textContent=n;
    prev.disabled=cur<=0;next.disabled=cur>=n-1;
    empty.hidden=n>0;
    Array.prototype.forEach.call(ticks.children,function(t,i){t.classList.toggle('cur',i===cur&&view==='car');});
    fit();
  }
  function go(i,instant){
    var v=visible();if(!v.length){mark();return;}
    cur=Math.max(0,Math.min(v.length-1,i));
    if(view==='car'){
      track.scrollTo({left:v[cur].offsetLeft-v[0].offsetLeft,behavior:instant?'auto':'smooth'});
    }
    mark();
  }
  function drawTicks(){
    ticks.innerHTML='';
    visible().forEach(function(c,i){
      var t=document.createElement('button');t.type='button';
      t.className='tick '+(c.dataset.tone||'')+(c.classList.contains('is-done')?' done':'');
      t.title=(i+1)+' · '+c.dataset.label;t.setAttribute('aria-label','Ir a '+(i+1)+': '+c.dataset.label);
      t.addEventListener('click',function(){if(view!=='car')setView('car');go(i);});
      ticks.appendChild(t);
    });
    mark();
  }
  // Swiping or scrolling the track moves the counter.
  var raf=0;
  track.addEventListener('scroll',function(){
    if(view!=='car')return;cancelAnimationFrame(raf);
    raf=requestAnimationFrame(function(){
      var v=visible(),x=track.scrollLeft,best=0,bd=1e9,base=v.length?v[0].offsetLeft:0;
      v.forEach(function(c,i){var d=Math.abs(c.offsetLeft-base-x);if(d<bd){bd=d;best=i;}});
      if(best!==cur){cur=best;mark();}
    });
  },{passive:true});
  prev.addEventListener('click',function(){go(cur-1);});
  next.addEventListener('click',function(){go(cur+1);});
  document.addEventListener('keydown',function(e){
    if(view!=='car'||e.altKey||e.ctrlKey||e.metaKey)return;
    var tag=(e.target.tagName||'').toLowerCase();if(tag==='input'||tag==='select'||tag==='textarea')return;
    if(e.key==='ArrowRight'){e.preventDefault();go(cur+1);}else if(e.key==='ArrowLeft'){e.preventDefault();go(cur-1);}
  });
  window.addEventListener('resize',function(){go(cur,true);});

  function setView(v){
    view=v;put('pe_revision_view_v2',v);
    track.classList.toggle('list',v!=='car');
    track.setAttribute('aria-roledescription',v==='car'?'carrusel':'lista');
    document.querySelector('.bar').classList.toggle('list-mode',v!=='car');
    document.querySelectorAll('.seg button').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.view===v));});
    [prev,next].forEach(function(b){b.hidden=v!=='car';});
    go(cur,true);
  }
  document.querySelectorAll('.seg button').forEach(function(b){b.addEventListener('click',function(){setView(b.dataset.view);});});

  // Filters and order.
  var q=document.getElementById('q'),fa=document.getElementById('fa'),fm=document.getElementById('fm'),fr=document.getElementById('fr'),
      fz=document.getElementById('fz'),fc=document.getElementById('fc'),fd=document.getElementById('fd'),so=document.getElementById('so'),
      fx=document.getElementById('fx'),fdistrict=document.getElementById('f-district'),
      fsector=document.getElementById('f-sector'),fkind=document.getElementById('f-kind');
  // Abroad and not remote: shown only when the profile (or this browser's choice) accepts moving.
  fx.checked=get('pe_revision_abroad',false);
  fx.addEventListener('change',function(){put('pe_revision_abroad',fx.checked);apply();});
  var WITHIN={hoy:['hoy'],'3d':['hoy','3d'],'7d':['hoy','3d','7d']};
  function closeOk(b){var v=fc.value;if(!v)return true;if(v==='fecha')return b!=='sin';if(v==='sin')return b==='sin';return (WITHIN[v]||[]).indexOf(b)>=0;}
  function doneOk(c){var v=fd.value;if(!v)return true;var d=c.classList.contains('is-done');return v==='done'?d:!d;}
  function apply(){
    var t=(q.value||'').toLowerCase();
    cards.forEach(function(c){
      var ok=(!t||c.dataset.text.indexOf(t)>=0)&&(!fa.value||c.dataset.action===fa.value)
        &&(!fm.value||c.dataset.modality===fm.value)&&(!fr.value||c.dataset.fresh===fr.value)
        &&(!fz.value||c.dataset.zones.split('|').indexOf(fz.value)>=0)
        &&(!fdistrict.value||c.dataset.districts.split('|').indexOf(fdistrict.value)>=0)
        &&(!fsector.value||c.dataset.sector===fsector.value)
        &&(!fkind.value||c.dataset.kinds.split('|').indexOf(fkind.value)>=0)
        &&closeOk(c.dataset.close)&&doneOk(c)
        &&(fx.checked||c.dataset.excl!=='1');
      c.hidden=!ok;
    });
    document.querySelectorAll('[data-quick-mode]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.quickMode===fm.value));});
    cur=0;drawTicks();go(0,true);
  }
  function sort(){
    var p=so.value.split(':'),key=p[0],sign=p[1]==='asc'?1:-1;
    cards.sort(function(a,b){
      var x=a.getAttribute('data-s-'+key),y=b.getAttribute('data-s-'+key),nx=parseFloat(x),ny=parseFloat(y);
      var c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:String(x).localeCompare(String(y),'es');
      return c*sign||(parseFloat(b.getAttribute('data-s-encaje'))-parseFloat(a.getAttribute('data-s-encaje')));
    });
    cards.forEach(function(c){track.appendChild(c);});
    cur=0;drawTicks();go(0,true);
  }
  [q,fa,fm,fr,fz,fc,fd,fdistrict,fsector,fkind].forEach(function(el){el.addEventListener('input',apply);el.addEventListener('change',apply);});
  document.querySelectorAll('[data-quick-mode]').forEach(function(b){b.addEventListener('click',function(){fm.value=b.dataset.quickMode;apply();});});
  document.addEventListener('click',function(e){
    var b=e.target.closest&&e.target.closest('button[data-set-filter]');if(!b)return;
    var controls={district:fdistrict,sector:fsector,kind:fkind,zone:fz},select=controls[b.dataset.setFilter];
    if(select){select.value=b.dataset.value;apply();document.getElementById('decidir').scrollIntoView({behavior:'smooth',block:'start'});}
  });
  so.addEventListener('change',sort);

  // "Ver ficha" from the closing-soon list: clear what hides it, then show it.
  document.querySelectorAll('[data-goto]').forEach(function(b){
    b.addEventListener('click',function(){
      var card=cards.filter(function(c){return c.dataset.key===b.dataset.goto;})[0];if(!card)return;
      if(card.hidden){[q,fa,fm,fr,fz,fc,fd,fdistrict,fsector,fkind].forEach(function(el){el.value='';});apply();}
      if(view==='car')go(visible().indexOf(card));
      card.scrollIntoView({behavior:'smooth',block:'start'});
    });
  });

  setView(view==='car'?'car':'list');apply();
  if('ResizeObserver' in window)new ResizeObserver(function(){fit();}).observe(track);
})();
"""


def coverage_html(cov: dict | None) -> str:
    """Which portals the search covers and how the last run went (tools/pe_pipeline.py writes
    reports/busqueda/cobertura.json): CLI results and errors, or the links to search by hand."""
    if not isinstance(cov, dict) or not isinstance(cov.get("portales"), list):
        return ('<details class="cov"><summary>Portales y cobertura</summary><p class="fine">Sin registro todavía: '
                'corre <code>python tools/pe_pipeline.py portales</code> (o <code>buscar</code>).</p></details>')
    portals = [p for p in cov["portales"] if isinstance(p, dict)]
    rows = []
    for p in portals:
        if p.get("metodo") == "cli":
            if not p.get("consultas"):
                status = '<span class="st man">sin correr</span>'
            elif p.get("fallidas"):
                status = f'<span class="st err">{esc(p["fallidas"])} de {esc(p["consultas"])} fallaron</span>'
            else:
                status = f'<span class="st ok">{esc(p["consultas"])} consultas ok</span>'
            last = f'{status} <span class="dim">{esc(p.get("resultados", 0))} resultados · {esc(p.get("fecha") or "—")}</span>'
        else:
            last = '<span class="st man">a mano</span>'
        errors = "<br>".join(esc(e) for e in (p.get("errores") or [])[:3]) or '<span class="dim">—</span>'
        links = " ".join(link(x.get("url"), x.get("q") or "abrir") for x in p.get("enlaces") or [] if isinstance(x, dict))
        rows.append(f'<tr><td><b>{link(p.get("home"), p.get("nombre"))}</b><br>'
                    f'<span class="dim">{esc(p.get("nota") or "")}</span></td>'
                    f'<td>{"CLI" if p.get("metodo") == "cli" else "Enlace"}</td><td>{last}</td><td>{errors}</td>'
                    f'<td class="links">{links or "<span class=dim>—</span>"}</td></tr>')
    clis = [p for p in portals if p.get("metodo") == "cli"]
    clean = sum(1 for p in clis if p.get("consultas") and not p.get("fallidas"))
    return (f'<details class="cov"><summary>Portales y cobertura · {len(rows)} portales, {clean} de {len(clis)} CLI sin errores '
            f'· registro del {esc(cov.get("fecha") or "—")}</summary>'
            '<div class="wrap"><table><thead><tr><th>Portal</th><th>Cómo</th><th>Última búsqueda</th>'
            f'<th>Errores</th><th>Buscar a mano</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="fine">Los portales con CLI se buscan solos con <code>python tools/pe_pipeline.py buscar</code>; los demás se '
            'abren con estos enlaces (las ofertas que encuentres se pegan a Claude para evaluarlas).</p></details>')


def render(data: dict, today: date, title: str | None, artifact: bool = False) -> str:
    """The page. `artifact` drops the document wrapper (the claude.ai Artifact publisher adds
    its own <!doctype>/<head>/<body>) and declares the safe-area side gutter it relies on.
    Either way it is one file with no outside requests: it opens the same from disk, a phone or a host."""
    page_title = f"Ofertas de {title}" if title else "Revisión de ofertas"
    rows = data["ranked"]
    classes = logo_classes(rows)
    logo_css = "".join(f".{cls}{{background-image:url({uri})}}" for uri, cls in classes.items())
    style = f"<style>{CSS}{logo_css}</style>"
    head = (
        f'<title>{esc(page_title)}</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n{style}\n'
        if artifact
        else '<!doctype html>\n<html lang="es"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        f'<meta name="color-scheme" content="light dark">\n'
        f"<title>{esc(page_title)}</title>{style}</head><body>\n"
    )
    tail = "" if artifact else "</body></html>\n"
    return head + _body(data, today, title, classes) + tail


def _sort_keys(r: dict) -> dict:
    fresh = r.get("fresh") or {}
    ratings = r.get("ratings") or []
    rating = next((x.get("rating") for x in ratings if isinstance(x.get("rating"), (int, float))), None)
    age = fresh.get("age_days")
    closes = r.get("closes") or {"sort": 99999}
    return {
        "accion": ACTION_ORDER[r["action"]], "encaje": r.get("rank_score") or 0,
        "puesto": f'{r.get("title") or ""} {r.get("company") or ""}'.lower(),
        "frescura": age if isinstance(age, int) else 99999, "reputacion": rating if rating is not None else -1,
        "cierre": closes["sort"],
    }


def card_html(r: dict, classes: dict[str, str]) -> str:
    """One posting: what to do and why on top, the evidence below - fit on the left, how to
    apply and what is known about the employer on the right."""
    labels = {k: (label, css) for k, label, css in ACTIONS}
    label, css = labels[r["action"]]
    ctx = r.get("context") or {}
    lg, rb = ctx.get("lugar") or {}, ctx.get("rubro") or {}
    zones = "|".join((["Remoto"] if lg.get("remote") else []) + (lg.get("zones") or []))
    districts = "|".join(lg.get("districts") or [])
    kinds = "|".join(employer_kinds(rb))
    text = " ".join(str(x or "") for x in (r.get("title"), r.get("company"), r.get("modality"), r.get("level"),
                                             " ".join(lg.get("districts") or []), rb.get("label"))).lower()
    closes = r.get("closes") or {"label": "—", "bucket": "sin", "css": "", "sort": 99999}
    sort_attrs = " ".join(f'data-s-{k}="{esc(v)}"' for k, v in _sort_keys(r).items())
    score = r.get("rank_score")
    pct = score if isinstance(score, (int, float)) and 0 <= score <= 100 else 0
    fresh = r.get("fresh")
    level = freshness_of(fresh)
    close_pill = (f'<span class="close {esc(closes["css"])}" title="Fecha de cierre que escribe la oferta">'
                  f'Cierra: {esc(closes["label"])}</span>' if closes["bucket"] != "sin" else "")
    fresh_pill = (f'<span class="fresh {FRESH_CSS.get(level, "")}" title="Frescura vacante">Frescura vacante: {esc(level)}</span>'
                  if fresh else "")
    how = f'<div class="how"><b>Cómo:</b> {esc(r["how_to_apply"])}</div>' if r.get("how_to_apply") else ""
    applied = (f'<div class="how"><b>Postulación registrada</b>'
               f'{" · " + esc(r.get("applied_date")) if r.get("applied_date") else ""}</div>'
               if r["action"] == "postulada" else "")
    apply_blk = applied + cv_html(r) + how + contacts_html(r) + people_html(r)
    apply_blk = (f'<div class="blk apply"><h4>{"Postulación" if applied else "Postular"}</h4>{apply_blk}</div>'
                 if apply_blk.strip() else "")
    place = context_html(r.get("context"))
    return (
        f'<article class="card {css}" data-key="{esc(r["key"])}" data-tone="{css}" '
        f'data-label="{esc((r.get("title") or "") + " · " + (r.get("company") or ""))}" '
        f'data-action="{esc(r["action"])}" data-modality="{esc(r.get("modality"))}" data-fresh="{esc(level)}" '
        f'data-close="{esc(closes["bucket"])}" data-zones="{esc(zones)}" data-districts="{esc(districts)}" '
        f'data-sector="{esc(rb.get("label"))}" data-kinds="{esc(kinds)}" data-text="{esc(text)}" '
        f'data-excl="{"1" if r.get("abroad_excluded") else ""}" {sort_attrs}>'
        f'<div class="card-top"><span class="badge {css}">{esc(label)}</span>{close_pill}{fresh_pill}'
        f'<span class="grow"></span><label class="done-l"><input type="checkbox" class="done" data-key="{esc(r["key"])}"> Revisada</label></div>'
        f'<div class="card-head">{logo_html(r, classes)}<div><h3>{link(r.get("url"), r.get("title"))}</h3>'
        f'<div class="co">{esc(r.get("company"))}</div><div class="meta">{meta_html(r)}</div>{also_html(r)}</div>'
        f'<div class="score"><div class="ring" style="--p:{esc(pct)}" title="Encaje: puntaje de /rank sobre 100"><span><b>{esc(score if score is not None else "—")}</b>'
        f'</span></div><div class="verdict">{esc(r.get("rank_verdict"))}</div></div></div>'
        f'<div class="card-body"><div>'
        f'<div class="blk strengths"><h4>Fortalezas</h4>{bullets(r.get("strengths"))}</div>'
        f'<div class="blk gaps"><h4>Brechas</h4>{bullets(r.get("gaps"))}</div>'
        f'<div class="blk"><h4>Frescura vacante</h4>{fresh_html(fresh)}</div></div>'
        f'<div>{apply_blk}'
        + (f'<div class="blk"><h4>Lugar y rubro</h4>{place}</div>' if place else "")
        + f'<div class="blk rating"><h4>Reputación</h4>{rating_html(r.get("ratings"))}</div></div></div>'
        "</article>"
    )


def _body(data: dict, today: date, title: str | None, classes: dict[str, str] | None = None) -> str:
    classes = classes or {}
    sl = data["shortlist"]
    ready = data["actions"]["ya"] + data["actions"]["pronto"]
    confirm = data["actions"]["confirmar"] + data["actions"]["antes"]
    portals = " · ".join(f"{esc(p.replace('-search', ''))} {n}" for p, n in data["portals"].most_common())
    verdicts = " · ".join(f"{esc(v)} {n}" for v, n in data["verdicts"].most_common())
    order = list(pe_frescura.FRESHNESS_ORDER) + ["sin revisar"]
    freshness = " · ".join(f"{esc(k)} {data['freshness'][k]}" for k in order if data["freshness"][k]) or "—"
    steps = [
        ("1 · Búsqueda", len(data["seen"]), f"vistas · {portals}", "/scrape"),
        ("2 · Encaje", len(data["ranked"]), f"evaluadas · {verdicts}", "/rank"),
        ("3 · Frescura vacante", len(sl), f"en lista corta · {freshness}", "pe_frescura"),
        ("4 · Reputación", data["rated"], f"de {len(sl)} con calificación", "company"),
        ("5 · Acción", ready, f"para postular · {confirm} por confirmar"
         + (f" · {data['actions']['postulada']} postuladas" if data["actions"]["postulada"] else ""), "/apply"),
    ]
    flow = "".join(
        f'<div class="step"><div class="n">{esc(n)} <span class="tag">{esc(cmd)}</span></div>'
        f'<div class="big">{esc(big)}</div><small>{sub}</small></div>'
        for n, big, sub, cmd in steps
    )

    main_rows = [r for r in data["ranked"] if r["action"] not in ("descartar", "cerrada", "postulada")]
    applied_rows = [r for r in data["ranked"] if r["action"] == "postulada"]
    closed_rows = [r for r in data["ranked"] if r["action"] == "cerrada"]
    out_rows = [r for r in data["ranked"] if r["action"] == "descartar"]
    modalities = sorted({r.get("modality") for r in main_rows if r.get("modality")})
    zone_set = sorted({z for r in main_rows for z in ((((r.get("context") or {}).get("lugar") or {}).get("zones")) or [])})
    if any((((r.get("context") or {}).get("lugar") or {}).get("remote")) for r in main_rows):
        zone_set.append("Remoto")
    zone_opts = "".join(f'<option value="{esc(z)}">{esc(z)}</option>' for z in zone_set)
    district_set = sorted({d for r in main_rows for d in ((((r.get("context") or {}).get("lugar") or {}).get("districts")) or [])})
    sector_set = sorted({label for r in main_rows
                         if (label := (((r.get("context") or {}).get("rubro") or {}).get("label")))})
    kind_set = sorted({k for r in main_rows for k in employer_kinds(((r.get("context") or {}).get("rubro") or {}))})
    district_opts = "".join(f'<option value="{esc(d)}">{esc(d)}</option>' for d in district_set)
    sector_opts = "".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in sector_set)
    kind_opts = "".join(f'<option value="{esc(k)}">{esc(k)}</option>' for k in kind_set)
    mode_buttons = "".join(f'<button type="button" data-quick-mode="{esc(m)}" aria-pressed="false" class="pill mod-{MODALITY_CLASS[m]}">{esc(m.capitalize())}</button>'
                           for m in ("presencial", "híbrido", "remoto") if m in modalities)
    action_opts = "".join(f'<option value="{k}">{esc(lbl)}</option>' for k, lbl, _ in ACTIONS if k not in ("descartar", "cerrada", "postulada"))
    modality_opts = "".join(f'<option value="{esc(m)}">{esc(m)}</option>' for m in modalities)
    sort_opts = "".join(
        f'<option value="{v}">{esc(lbl)}</option>'
        for v, lbl in (("accion:asc", "Orden: acción más urgente"), ("encaje:desc", "Orden: mayor encaje"),
                       ("cierre:asc", "Orden: cierra antes"), ("frescura:asc", "Orden: más reciente"),
                       ("reputacion:desc", "Orden: mejor reputación"), ("puesto:asc", "Orden: puesto, A a Z"))
    )
    unranked = "".join(
        f'<li>{esc(r.get("posted_date") or "?")} · {link(r.get("url"), r.get("title"))} · {esc(r.get("company"))}'
        f' <span class="dim">({esc(r.get("modality"))} · {esc(r.get("level"))})</span></li>'
        for r in data["unranked"]
    )
    soon = sorted((r for r in main_rows if r["closes"]["bucket"] in ("hoy", "3d", "7d")),
                  key=lambda r: (r["closes"]["sort"], -(r.get("rank_score") or 0)))
    urgent = ""
    if soon:
        items = "".join(
            f'<div class="soon-card"><div class="row"><span class="close {esc(r["closes"]["css"])}">{esc(r["closes"]["label"])}</span>'
            f'<b class="num">{esc(r.get("rank_score"))}</b></div>'
            f'<div class="row">{logo_html(r, classes)}<div style="min-width:0"><div class="t">{link(r.get("url"), r.get("title"))}</div>'
            f'<div class="co">{esc(r.get("company"))}</div></div></div>'
            + (f'<div class="how"><b>Cómo:</b> {esc(r["how_to_apply"])}</div>' if r.get("how_to_apply") else "")
            + f'<div class="row">{cv_html(r).replace("cvrow", "cvrow-in")}'
            f'<button type="button" class="link-btn" data-goto="{esc(r["key"])}">Ver ficha →</button></div></div>'
            for r in soon
        )
        urgent = (f'<details class="soon-details" open><summary>Cierran en los próximos 7 días '
                  f'<span class="count-pill">{len(soon)}</span></summary>'
                  f'<div class="soon-wrap"><button type="button" class="soon-nav" id="soon-prev" aria-label="Cierres anteriores">‹</button>'
                  f'<div class="soon-track" id="soon-track" tabindex="0" aria-label="Ofertas que cierran en siete días">{items}</div>'
                  f'<button type="button" class="soon-nav" id="soon-next" aria-label="Más cierres">›</button></div>'
                  '<p class="note">Fecha de cierre que escribe la oferta, no el vencimiento del portal.</p></details>')
    heading = esc(title) if title else "Revisión de ofertas"
    pref = data.get("abroad_pref") or {"mode": "no", "text": None}
    excluded = 0
    for r in data["ranked"]:
        r["abroad_excluded"] = abroad_excluded(r, pref["mode"])
        excluded += bool(r["abroad_excluded"] and r in main_rows)
    pref_html = (
        f'<div class="pref"><span>Búsqueda en Lima, Perú · fuera del Perú: <b>{esc(ABROAD_LABEL[pref["mode"]])}</b> '
        f'<span class="dim">· {esc(pref["text"]) if pref.get("text") else "el perfil no pide buscar en el extranjero"}'
        f' (línea <code>Abroad</code> de <code>CLAUDE.md</code>; cámbiala ahí o con <code>/setup</code>)</span></span>'
        f'<label class="done-l"><input type="checkbox" id="fx"> '
        f'Mostrar también las del extranjero que tu perfil deja fuera ({excluded})</label></div>'
    )
    coverage = coverage_html(data.get("cobertura"))
    runs = [p for p in ((data.get("cobertura") or {}).get("portales") or [])
            if isinstance(p, dict) and p.get("ok") and re.match(r"\d{4}-\d{2}-\d{2}", str(p.get("fecha") or ""))]
    latest = max((str(p["fecha"])[:10] for p in runs), default=None)
    run_label = (f'{date.fromisoformat(latest).strftime("%d/%m/%Y")} · '
                 + ", ".join(esc(p.get("nombre")) for p in runs if str(p["fecha"]).startswith(latest))
                 if latest else "sin búsquedas registradas")

    return f"""<header>
<div class="topline"><div><div class="eyebrow">Revisión de ofertas · {esc(today.strftime('%d/%m/%Y'))}</div>
<h1>{heading}</h1>
<p class="run-date">Última búsqueda: <strong>{run_label}</strong></p>
<p class="sub">Desde <code>seen_jobs.json</code>, la frescura y <code>company_research/</code>. El encaje es un triaje de
<code>/rank</code>; <code>/apply</code> vuelve a evaluar antes de postular.</p></div>
<button type="button" class="icon-btn" id="theme" title="Cambiar tema claro/oscuro" aria-label="Cambiar tema">◐</button></div>
<div class="flow">{flow}</div></header>
<main>
{urgent}
<h2 id="decidir">Para decidir ({len(main_rows)})</h2>
<div class="toolbar">
<div class="filters"><input id="q" type="search" placeholder="Buscar puesto, empresa, distrito…" aria-label="Buscar">
<select id="so" aria-label="Orden">{sort_opts}</select>
<select id="fa" aria-label="Acción"><option value="">Toda acción</option>{action_opts}</select>
<select id="fm" aria-label="Modalidad"><option value="">Toda modalidad</option>{modality_opts}</select>
<select id="fr" aria-label="Frescura vacante"><option value="">Toda frescura vacante</option><option value="muy alta">muy alta</option><option value="alta">alta</option><option value="media">media</option><option value="baja">baja</option></select>
<select id="fz" aria-label="Zona"><option value="">Toda zona</option>{zone_opts}</select>
<select id="fc" aria-label="Cierre"><option value="">Todo cierre</option><option value="hoy">Cierra hoy</option><option value="3d">Hasta 3 días</option><option value="7d">Esta semana</option><option value="fecha">Con fecha de cierre</option><option value="sin">Sin fecha</option></select>
<select id="fd" aria-label="Revisadas"><option value="">Revisadas y pendientes</option><option value="pend">Solo pendientes</option><option value="done">Solo revisadas</option></select></div>
<div class="quick" aria-label="Filtros rápidos"><span>Modalidad:</span><button type="button" data-quick-mode="" aria-pressed="true" class="pill">Todas</button>{mode_buttons}
<select id="f-district" aria-label="Distrito"><option value="">Todos los distritos</option>{district_opts}</select>
<select id="f-sector" aria-label="Rubro"><option value="">Todos los rubros</option>{sector_opts}</select>
<select id="f-kind" aria-label="Tipo de empresa"><option value="">Todo tipo de empresa</option>{kind_opts}</select></div>
<div class="legend">
<span>Confianza del dato: <span class="dots conf-alta">●●●</span> alta · <span class="dots conf-media">●●○</span> media · <span class="dots conf-baja">●○○</span> baja</span>
<span>En carrusel: teclas ← → o desliza para pasar de oferta.</span></div>{pref_html}</div>
<div class="bar"><button type="button" class="nav" id="prev" aria-label="Oferta anterior">‹</button>
<div class="pos" aria-live="polite"><b id="pos">1</b> / <span id="tot">{len(main_rows)}</span></div>
<button type="button" class="nav" id="next" aria-label="Oferta siguiente">›</button>
<div class="ticks" id="ticks" aria-label="Todas las ofertas, en orden"></div>
<span class="progress">Revisadas <b class="count" id="cnt">0</b> de {len(main_rows)}</span>
<div class="seg" role="group" aria-label="Vista"><button type="button" data-view="car" aria-pressed="true">Carrusel</button><button type="button" data-view="list" aria-pressed="false">Lista</button></div></div>
<div class="track list" id="main" tabindex="0" aria-label="Ofertas para decidir">{"".join(card_html(r, classes) for r in main_rows)}</div>
<div class="empty" id="empty" hidden>Ninguna oferta con estos filtros.</div>
<details><summary>Postuladas ({len(applied_rows)})</summary>
<div class="track list">{"".join(card_html(r, classes) for r in applied_rows)}</div></details>
<details><summary>Cerradas: su fecha de cierre ya pasó ({len(closed_rows)})</summary>
<div class="track list">{"".join(card_html(r, classes) for r in closed_rows)}</div></details>
<details><summary>Descartadas o no elegibles ({len(out_rows)})</summary>
<div class="track list">{"".join(card_html(r, classes) for r in out_rows)}</div></details>
<details><summary>Vistas y relevantes, sin evaluar ({len(data["unranked"])})</summary><ul class="unranked">{unranked}</ul></details>
{coverage}
<p class="fine">Frescura vacante: edad desde la publicación original; "≥" es un mínimo (Computrabajo reinicia la fecha al republicar);
el vencimiento del portal no es una fecha de cierre (<code>10-peru-market.md</code>, sección 10).
Reputación: cada calificación con su n, área y fecha; con menos de {SMALL_N} opiniones es referencial y una certificación pagada no es independiente (sección 11).
"Revisada" se guarda solo en este navegador.</p>
</main>
<footer>Generado por <code>tools/pe_revision.py</code> · ai-job-search-pe · {esc(today.isoformat())}</footer>
<script>{JS}{CV_JS}</script>
"""


def export(data: dict, today: date, title: str | None) -> dict:
    """The page's content as plain data, for a script or a model to read instead of the HTML:
    the same rows, actions and evidence, without logos or markup."""
    labels = {k: label for k, label, _ in ACTIONS}

    def item(r: dict) -> dict:
        fresh = r.get("fresh") or {}
        ctx = r.get("context") or {}
        lg, rb = ctx.get("lugar") or {}, ctx.get("rubro") or {}
        closes = r.get("closes") or {}
        return {
            "key": r["key"], "title": r.get("title"), "company": r.get("company"), "url": r.get("url"),
            "portal": r.get("portal"), "action": r["action"], "action_label": labels[r["action"]],
            "application_status": r.get("application_status"), "applied_date": r.get("applied_date"),
            "score": r.get("rank_score"), "verdict": r.get("rank_verdict"),
            "deadline": r.get("deadline"), "days_to_close": closes.get("days"), "closes_label": closes.get("label"),
            "freshness": freshness_of(r.get("fresh")), "age_days": fresh.get("age_days"),
            "original_date": fresh.get("original_date"), "ghost_risk": fresh.get("risk"), "fresh_signals": fresh.get("signals") or [],
            "modality": r.get("modality"), "level": r.get("level"), "location": r.get("location"),
            "abroad": bool(r.get("abroad")), "abroad_onsite": bool(r.get("abroad_onsite")),
            "districts": lg.get("districts") or [], "zones": lg.get("zones") or [], "address": lg.get("address"),
            "sector": rb.get("label"), "how_to_apply": r.get("how_to_apply"), "contact": r.get("contact"),
            "cv": r.get("cv") if r.get("cv_name") else None,
            "strengths": r.get("strengths") or [], "gaps": r.get("gaps") or [],
            "ratings": [{k: x.get(k) for k in ("source", "rating", "n", "area", "area_n", "latest", "url")}
                        for x in (r.get("ratings") or [])],
            "also": [{"portal": e.get("portal"), "company": e.get("company"), "url": e.get("url")} for e in r.get("also") or []],
            "people": [{"name": p.get("name"), "role": p.get("role")} for p in r.get("people") or []],
        }

    a = data["actions"]
    return {
        "generated": today.isoformat(), "candidate": title,
        "preferencias": {"extranjero": (data.get("abroad_pref") or {"mode": "no"})["mode"],
                         "extranjero_texto": (data.get("abroad_pref") or {}).get("text")},
        "counts": {"seen": len(data["seen"]), "ranked": len(data["ranked"]), "shortlist": len(data["shortlist"]),
                   "postular": a["ya"] + a["pronto"], "confirmar": a["confirmar"] + a["antes"],
                   "si_hay_tiempo": a["tiempo"], "postuladas": a["postulada"],
                   "cerradas": a["cerrada"], "descartar": a["descartar"],
                   "sin_evaluar": len(data["unranked"])},
        "para_decidir": [item(r) for r in data["ranked"] if r["action"] not in ("descartar", "cerrada", "postulada")],
        "postuladas": [item(r) for r in data["ranked"] if r["action"] == "postulada"],
        "cerradas": [item(r) for r in data["ranked"] if r["action"] == "cerrada"],
        "descartadas": [item(r) for r in data["ranked"] if r["action"] == "descartar"],
        "sin_evaluar": [{"key": r["key"], "title": r.get("title"), "company": r.get("company"), "url": r.get("url"),
                         "posted_date": r.get("posted_date"), "fit": r.get("fit")} for r in data["unranked"]],
    }


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--state", type=Path, default=Path("job_scraper/seen_jobs.json"))
    ap.add_argument("--frescura", type=Path, default=Path("reports/frescura.json"),
                    help="tools/pe_frescura.py --json output (skipped when missing)")
    ap.add_argument("--research", type=Path, default=Path("company_research"))
    ap.add_argument("--contexto", type=Path, default=Path("reports/contexto.json"),
                    help="tools/pe_contexto.py --json output: workplace and sector (skipped when missing)")
    ap.add_argument("--out", type=Path, default=Path("reports/revision-ofertas.html"))
    ap.add_argument("--json", type=Path, dest="json_out",
                    help="also write the page's content as JSON here, for scripts or a model to read")
    ap.add_argument("--titulo", help="name shown in the heading, e.g. the candidate's")
    ap.add_argument("--perfil", type=Path, default=Path("CLAUDE.md"),
                    help="the profile whose `Abroad:` line says if non-remote postings abroad are in play")
    ap.add_argument("--cobertura", type=Path, default=Path("reports/busqueda/cobertura.json"),
                    help="tools/pe_pipeline.py's portal coverage (skipped when missing)")
    ap.add_argument("--artifact", action="store_true",
                    help="omit the document wrapper, for publishing as a claude.ai Artifact")
    ap.add_argument("--no-embed-cvs", action="store_true",
                    help="local page: link each CV on disk instead of embedding it (smaller file, works only on this computer)")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today(), help="YYYY-MM-DD (for testing)")
    ap.add_argument("--cv-manifest", type=Path,
                    help="with --artifact: write {published name: CV path} here, the files to publish with the page")
    args = ap.parse_args(argv)

    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read {args.state}: {exc}", file=sys.stderr)
        return 1
    fresh_rows = []
    if args.frescura.is_file():
        try:
            fresh_rows = json.loads(args.frescura.read_text(encoding="utf-8")).get("postings", [])
        except (OSError, ValueError, AttributeError) as exc:
            print(f"cannot read {args.frescura}: {exc}", file=sys.stderr)
            return 1
    context_rows = []
    if args.contexto.is_file():
        try:
            context_rows = json.loads(args.contexto.read_text(encoding="utf-8")).get("postings", [])
        except (OSError, ValueError, AttributeError) as exc:
            print(f"cannot read {args.contexto}: {exc}", file=sys.stderr)
            return 1
    data = collect(state, fresh_rows, args.research, context_rows, args.today)
    data["abroad_pref"] = read_abroad(args.perfil)
    try:
        data["cobertura"] = json.loads(args.cobertura.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data["cobertura"] = None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    embed = not args.artifact and not args.no_embed_cvs
    manifest = cv_hrefs(data["ranked"], args.out.parent.resolve(), args.artifact, embed=embed)
    if args.cv_manifest:
        args.cv_manifest.write_text(json.dumps({k: str(Path(v).resolve()) for k, v in manifest.items()},
                                               ensure_ascii=False, indent=1), encoding="utf-8")
    args.out.write_text(render(data, args.today, args.titulo, args.artifact), encoding="utf-8")
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(export(data, args.today, args.titulo), ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    a = data["actions"]
    print(f"{args.out}: {len(data['ranked'])} evaluadas · postular {a['ya'] + a['pronto']} · "
          f"confirmar {a['confirmar'] + a['antes']} · si hay tiempo {a['tiempo']} · cerradas {a['cerrada']} · descartar {a['descartar']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
