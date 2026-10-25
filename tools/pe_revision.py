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
    return '<div class="contacts"><div class="lb">Contactos en LinkedIn</div><div class="cchips">' + "".join(links) + "</div></div>"


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


_DARK = """--bg:#0f1318;--surface:#171c23;--surface-2:#1d232c;--ink:#e7ebf0;--ink-2:#c9d1db;--dim:#9ba6b4;--faint:#8a94a3;
--line:#2a313b;--line-strong:#3a424e;--accent:#8fb0ff;--accent-ink:#0f1318;--accent-soft:#1c2a4a;--accent-deep:#c7d6ff;
--go:#8fb0ff;--go-bg:#1c2a4a;--go-ink:#c7d6ff;--warn:#f5a35c;--warn-bg:#3a2712;--warn-ink:#fcd9b0;
--stop:#f08a6a;--stop-bg:#3b1c12;--stop-ink:#fbc9b5;--muted-bg:#262d37;--out:#9ba6b4;--chip-place:#1c2a4a;--chip-sector:#262d37;
--mod-pres:#c9d1db;--mod-pres-bg:#262d37;--mod-hib:#c4b5fd;--mod-hib-bg:#2d2350;--row-open:#18233a;color-scheme:dark"""

# Light tokens on :root; dark ones under prefers-color-scheme (unless the host forces light)
# and again under data-theme="dark", so a host theme toggle (or the page's own) wins both ways.
# Blue means apply and orange means confirm: they differ in lightness as well as hue.
CSS = """
/* Layout: a header band with the counts, what closes this week, then filters beside one compact row per posting. */
:root{--bg:#f5f6f8;--surface:#fff;--surface-2:#f7f8fa;--ink:#111827;--ink-2:#374151;--dim:#5b6474;--faint:#6b7280;
--line:#e3e6eb;--line-strong:#d5d9e0;--accent:#1d4ed8;--accent-ink:#fff;--accent-soft:#e8eefe;--accent-deep:#1e3a8a;
--go:#1d4ed8;--go-bg:#e8eefe;--go-ink:#1e3a8a;--warn:#b45309;--warn-bg:#fdf0e1;--warn-ink:#92400e;
--stop:#9a3412;--stop-bg:#fde4d3;--stop-ink:#7c2d12;--muted-bg:#eef0f3;--out:#6b7280;--chip-place:#e8eefe;--chip-sector:#eef0f3;
--mod-pres:#374151;--mod-pres-bg:#eef0f3;--mod-hib:#5b21b6;--mod-hib-bg:#efeafe;--row-open:#f7f9ff;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){""" + _DARK + """}}
:root[data-theme="dark"]{""" + _DARK + """}
[hidden]{display:none!important}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;-webkit-font-smoothing:antialiased}
.wrap-x{max-width:1240px;margin:0 auto;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
h1,h2,h3,h4{text-wrap:balance;margin:0}
.num,.kpi b,.sc b{font-variant-numeric:tabular-nums}
a{color:var(--accent);text-underline-offset:2px}a:hover{color:var(--accent-deep)}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
code{font:12px ui-monospace,SFMono-Regular,Consolas,monospace;background:var(--muted-bg);padding:1px 5px;border-radius:4px}
.dim{color:var(--dim)}
/* header band */
.top{background:var(--surface);border-bottom:1px solid var(--line)}
.top .wrap-x{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:16px;padding-top:22px;padding-bottom:18px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
h1{font-size:28px;line-height:1.15;letter-spacing:-.01em;margin-top:4px}
.run-date{margin:6px 0 0;color:var(--dim);font-size:13px}.run-date strong{color:var(--dim);font-weight:400}
.top-right{display:flex;align-items:flex-end;gap:10px;flex-wrap:wrap}
.kpis{display:flex;flex-wrap:wrap;border:1px solid var(--line);border-radius:12px;overflow:hidden;background:var(--surface)}
.kpi{padding:10px 18px;border-right:1px solid var(--line);min-width:96px}.kpi:last-child{border-right:0}
.kpi b{display:block;font-size:22px;line-height:1.2}.kpi span{font-size:12px;color:var(--dim)}
.kpi.go{background:var(--accent-soft)}.kpi.go b{color:var(--accent)}.kpi.go span{color:var(--accent-deep)}
.kpi.warn b{color:var(--warn)}
.icon-btn{all:unset;cursor:pointer;width:44px;height:44px;border-radius:12px;display:grid;place-items:center;border:1px solid var(--line);background:var(--surface);color:var(--ink);font-size:16px}
.icon-btn:hover{border-color:var(--accent)}
main.wrap-x{padding-top:26px;padding-bottom:40px;display:flex;flex-direction:column;gap:26px}
h2{font-size:18px;line-height:1.3}
.h2-row{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}
.note{font-size:12px;color:var(--dim);margin:0}
.count-pill{font-size:12px;font-weight:700;background:var(--accent-soft);color:var(--accent-deep);border-radius:999px;padding:1px 9px;vertical-align:2px}
/* the workflow, folded */
.flowd{background:transparent;border:0;padding:0;margin:0}
.flowd>summary{font-size:13px;font-weight:600;color:var(--dim)}
.flow{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));margin-top:10px;background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.step{padding:12px 14px;border-right:1px solid var(--line);min-width:0}.step:last-child{border-right:0}
.step .n{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;font-weight:600;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.step .big{font-size:20px;font-weight:700;margin-top:2px}.step small{display:block;color:var(--dim);font-size:12px;line-height:1.4}
.tag{font-size:10.5px;border:1px solid var(--line);border-radius:4px;padding:0 5px;color:var(--dim);font-weight:500;text-transform:none;letter-spacing:0}
/* this week */
.week{display:flex;flex-direction:column;gap:12px}
.soon-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:12px}
.soon-card{all:unset;box-sizing:border-box;cursor:pointer;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:6px;min-width:0;min-height:132px;font-size:14px;line-height:1.5;color:var(--ink)}
.soon-card:hover{border-color:var(--accent)}.soon-card.urgent{border-color:var(--warn)}
.soon-card .row1{display:flex;align-items:center;justify-content:space-between;gap:8px}
.soon-card .row1 b{font-size:15px}
.soon-card .t{font-weight:600;line-height:1.3}.soon-card .co{color:var(--dim);font-size:12.5px}
.soon-card .how{font-size:12px;color:var(--dim);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.soon-card .cvst{font-size:12px;font-weight:600;color:var(--dim)}.soon-card .cvst.ok{color:var(--accent)}
.soon-card .acts{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:auto;padding-top:4px}
.soon-more{background:transparent;border:0;padding:0;margin:0}
.soon-more>summary{font-size:13px;font-weight:600;color:var(--accent)}
.soon-more .soon-grid{margin-top:12px}
.link-btn{all:unset;cursor:pointer;color:var(--accent);font-size:13px;font-weight:600;min-height:32px;display:inline-flex;align-items:center}.link-btn:hover{text-decoration:underline}
/* decide: filters beside the list */
.decide{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}
.side{flex:1 1 240px;max-width:260px;display:flex;flex-direction:column;gap:18px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px}
.side .fl{display:flex;flex-direction:column;gap:6px}
.side .fl>label,.side .fl>.lb{font-size:12px;font-weight:600;color:var(--ink-2)}
.side input[type=search],.side select{font:inherit;font-size:14px;padding:9px 10px;border:1px solid var(--line-strong);border-radius:9px;background:var(--surface-2);color:var(--ink);min-height:44px;width:100%;min-width:0}
.acts-list{display:flex;flex-direction:column;gap:6px}
.acts-list button{all:unset;box-sizing:border-box;cursor:pointer;display:flex;justify-content:space-between;align-items:center;min-height:40px;padding:0 12px;border-radius:9px;font-size:14px;background:var(--muted-bg);color:var(--ink-2)}
.acts-list button[aria-pressed="true"]{background:var(--ink);color:var(--surface);font-weight:700}
.acts-list button span{font-variant-numeric:tabular-nums}
.quick{display:flex;flex-wrap:wrap;gap:6px}
.quick button{font:inherit;cursor:pointer;border:1px solid var(--line-strong);background:var(--surface);color:var(--ink-2);border-radius:999px;padding:6px 12px;min-height:36px;font-size:13px;font-weight:600}
.quick button[aria-pressed="true"]{background:var(--ink);border-color:var(--ink);color:var(--surface)}
.side fieldset{border:0;margin:0;padding:0;display:flex;flex-direction:column;gap:2px;min-width:0}
.side legend{font-size:12px;font-weight:600;color:var(--ink-2);padding:0;margin-bottom:6px}
.chk{display:flex;align-items:center;gap:8px;min-height:32px;font-size:14px;color:var(--ink);cursor:pointer}
.chk input{width:16px;height:16px;margin:0;accent-color:var(--accent)}
.morefl{background:transparent;border:0;border-top:1px solid var(--line);border-radius:0;padding:12px 0 0;margin:0;display:block}
.morefl>summary{cursor:pointer;font-size:13px;font-weight:600;color:var(--accent)}.morefl .fl{margin-top:12px}
.legend{font-size:12px;color:var(--dim);display:flex;flex-direction:column;gap:6px;margin-top:12px}
.pref{font-size:12px;color:var(--dim);display:flex;flex-direction:column;gap:8px}
.pref b{color:var(--ink)}.pref span{overflow-wrap:anywhere}
.listcol{flex:999 1 640px;min-width:0;display:flex;flex-direction:column;gap:10px}
.list-head{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.list-head h2 .dim{font-weight:500}
.list-tools{display:flex;align-items:center;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--ink-2)}
.list-tools select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid var(--line-strong);border-radius:9px;background:var(--surface);color:var(--ink);min-height:40px}
.progress b{color:var(--ink)}
.cols{display:grid;grid-template-columns:var(--cols);gap:14px;padding:0 18px;font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
:root{--cols:64px minmax(0,1fr) 112px 128px 120px 140px 32px}
.track{background:var(--surface);border:1px solid var(--line);border-radius:14px;overflow:hidden}
.empty{padding:36px 16px;text-align:center;color:var(--dim);background:var(--surface);border:1px dashed var(--line);border-radius:14px}
/* one posting: a row that opens */
.card{border-bottom:1px solid var(--line);min-width:0}.card:last-child{border-bottom:0}
.card.is-done .row{opacity:.55}
.rowd{margin:0;padding:0;border:0;border-radius:0;background:transparent}
.row{list-style:none;cursor:pointer;display:grid;grid-template-columns:var(--cols);gap:14px;align-items:center;padding:14px 18px}
.row::-webkit-details-marker{display:none}
.row:hover{background:var(--surface-2)}
.rowd[open]>.row{background:var(--row-open)}
.sc{display:flex;flex-direction:column;gap:5px}.sc b{font-size:20px;line-height:1}
.bar{display:block;width:48px;height:4px;border-radius:2px;background:var(--line)}.bar i{display:block;height:4px;border-radius:2px;background:var(--faint)}
.card.go .bar i{background:var(--go)}.card.warn .bar i{background:var(--warn)}.card.stop .bar i{background:var(--stop)}
.who{display:flex;align-items:center;gap:12px;min-width:0}
.tt{display:flex;flex-direction:column;min-width:0}
.tt .t{font-weight:600;line-height:1.3;color:var(--ink)}.tt .co{font-size:13px;color:var(--dim)}
.tags{display:contents}
.c-mod{font-size:13px;color:var(--ink-2)}
.c-fresh{display:flex;align-items:center;gap:6px;font-size:13px;color:var(--ink-2)}
.dot{flex:none;width:8px;height:8px;border-radius:4px;background:var(--faint)}
.dot.f4,.dot.f3{background:var(--go)}.dot.f2{background:var(--warn)}.dot.f1{background:var(--faint)}
.chev{display:block;justify-self:end;width:16px;height:16px;overflow:visible;transform-origin:50% 50%;transition:transform .15s;color:var(--dim)}.rowd[open] .chev{transform:rotate(180deg)}
.nodate{font-size:13px;color:var(--dim)}
.detail{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;padding:4px 18px 22px 96px}
.detail h4{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:0 0 8px}
.detail h4:not(:first-child){margin-top:16px}
.detail ul{margin:0;padding-left:18px}.detail li{margin:0 0 6px}
.d-why ul,.d-gaps ul{list-style:none;padding:0}.d-why li,.d-gaps li{position:relative;padding-left:14px}
.d-why li::before,.d-gaps li::before{content:"";position:absolute;left:0;top:.62em;width:6px;height:6px;border-radius:3px;background:var(--go)}
.d-gaps li::before{background:var(--warn)}
.d-more{grid-column:1/-1;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;border-top:1px solid var(--line);padding-top:14px;font-size:13px}
.d-more h4{margin-bottom:6px}
.apply{display:flex;flex-direction:column;gap:10px;background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:14px;align-self:start}
.apply h4{margin:0}
.d-links{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px}
.logo{flex:none;width:36px;height:36px;border-radius:9px;object-fit:contain;background:#fff center/contain no-repeat;border:1px solid var(--line)}
.logo.none{display:inline-flex;align-items:center;justify-content:center;background:var(--muted-bg);color:var(--ink-2);font-size:12px;font-weight:700;border-color:transparent}
.soon-card .logo{width:28px;height:28px;border-radius:7px;font-size:10px}
/* badges, pills and chips */
.badge,.close,.fresh{display:inline-flex;align-items:center;justify-self:start;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.badge.go{background:var(--go-bg);color:var(--go-ink)}.badge.warn{background:var(--warn-bg);color:var(--warn-ink)}
.badge.stop{background:var(--stop-bg);color:var(--stop-ink)}.badge.muted{background:var(--muted-bg);color:var(--ink-2)}
.badge.out{background:var(--muted-bg);color:var(--out)}
.close{background:var(--muted-bg);color:var(--ink-2)}
.close.today{background:var(--warn);color:var(--surface)}.close.soon{background:var(--warn-bg);color:var(--warn-ink)}
.close.week{background:var(--warn-bg);color:var(--warn-ink)}.close.past{background:transparent;color:var(--faint);text-decoration:line-through}
.fresh.f4{background:var(--go);color:var(--accent-ink)}.fresh.f3{background:var(--go-bg);color:var(--go-ink)}
.fresh.f2{background:var(--warn-bg);color:var(--warn-ink)}.fresh.f1{background:var(--muted-bg);color:var(--ink-2)}
.risk{font-size:12px}.risk.medio{color:var(--warn)}.risk.alto{color:var(--stop)}
.signals{color:var(--dim);font-size:12px;margin-top:4px!important}
.how{font-size:13px;color:var(--ink-2)}.how b{color:var(--ink)}
.cvrow{margin:0}
.btn-cv{align-self:flex-start;display:inline-flex;align-items:center;gap:8px;font:600 14px/1 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;min-height:44px;padding:0 16px;border-radius:10px;
border:1px solid var(--accent);background:var(--accent);color:var(--accent-ink);text-decoration:none;cursor:pointer}
.btn-cv::before{content:"↓";font-weight:700}
.btn-cv:hover{filter:brightness(1.1);color:var(--accent-ink)}.btn-cv[aria-disabled="true"]{opacity:.55;cursor:not-allowed}
.soon-card .btn-cv{min-height:36px;padding:0 12px;font-size:13px}
.people{margin:3px 0 0;padding-left:16px}.people li{margin:1px 0}
.meta{display:flex;flex-wrap:wrap;align-items:center;gap:5px}
.pill{display:inline-block;padding:2px 10px;border-radius:6px;font-size:12px;font-weight:600;line-height:1.5}
.pill.mod-pres{background:var(--mod-pres-bg);color:var(--mod-pres)}.pill.mod-hib{background:var(--mod-hib-bg);color:var(--mod-hib)}
.pill.mod-rem{background:var(--go-bg);color:var(--go-ink)}.pill.mod-nd{border:1px dashed var(--line-strong);color:var(--dim);font-weight:500}
.pill.abroad{background:var(--accent-soft);color:var(--accent-deep)}.pill.abroad-onsite{background:var(--warn-bg);color:var(--warn-ink)}
.pill.lvl{background:var(--muted-bg);color:var(--ink);font-weight:500}.src{font-size:12px;color:var(--dim)}
.chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;line-height:1.6;border:1px solid var(--line);background:var(--surface);color:var(--ink)}
button.chip{font:inherit;font-size:12px;cursor:pointer}button.chip:hover{outline:1px solid var(--accent)}
.chip.place{background:var(--chip-place);border-color:transparent;font-weight:600}
.chip.zone{color:var(--dim)}
.chip.sector{background:var(--chip-sector);border-color:transparent}
.chip.kind{background:var(--warn-bg);color:var(--warn-ink);border-color:transparent}
.chip.muted{color:var(--dim);border-style:dashed}
.ev{font-size:12px;color:var(--dim)}
.dots{letter-spacing:1px}.dots.conf-alta{color:var(--go)}.dots.conf-media{color:var(--warn)}.dots.conf-baja{color:var(--faint)}
.ctx{display:grid;gap:6px;font-size:13px}
.ctx-row{display:grid;grid-template-columns:48px minmax(0,1fr);gap:8px;align-items:start}
.ctx-k{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;padding-top:3px;font-weight:600}
.ctx-v{display:flex;flex-wrap:wrap;align-items:center;gap:4px;min-width:0}
.ctx-v .ev,.ctx-v .ctx-note{flex-basis:100%}
.ctx-note{color:var(--dim);font-size:12px}
.ctx-btns{gap:6px}
.btn{display:inline-block;padding:4px 11px;border:1px solid var(--line-strong);border-radius:8px;font-size:12px;text-decoration:none;color:var(--accent);background:var(--surface);font-weight:600}
.btn:hover{border-color:var(--accent)}
.rating b{font-size:15px}
.done-l{display:inline-flex;align-items:center;gap:8px;font-size:13px;font-weight:600;color:var(--ink-2);cursor:pointer;min-height:36px;user-select:none}
.done-l input{margin:0;width:16px;height:16px;accent-color:var(--accent)}
/* summary cards and the folded sections */
.more{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,180px),1fr));gap:12px}
.more a{display:flex;flex-direction:column;gap:2px;padding:14px 16px;background:var(--surface);border:1px solid var(--line);border-radius:12px;text-decoration:none;color:var(--ink)}
.more a:hover{border-color:var(--accent);color:var(--ink)}
.more b{font-size:20px;font-variant-numeric:tabular-nums}.more strong{font-weight:600}.more span{font-size:12px;color:var(--dim)}
details.sec{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 18px}
details.sec>summary{cursor:pointer;font-weight:700}
details.sec .track{margin-top:12px}
.unranked{margin:10px 0 0;padding-left:18px}.unranked li{margin:4px 0}
.nuevas .filters,.cov .filters{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px}
.nuevas input,.nuevas select{font:inherit;font-size:13px;padding:7px 10px;border:1px solid var(--line-strong);border-radius:8px;background:var(--surface-2);color:var(--ink);min-width:0}
.nuevas input[type=search]{flex:1 1 220px}
.nuevas table,.cov table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px;min-width:760px}
.nuevas th,.nuevas td,.cov th,.cov td{text-align:left;vertical-align:top;padding:8px;border-bottom:1px solid var(--line)}
.nuevas th,.cov th{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em}
.nuevas .wrap{overflow-x:auto;max-height:70vh;overflow-y:auto}.cov .wrap{overflow-x:auto}.cov .links a{margin-right:8px;white-space:nowrap}
.st{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.st.ok{background:var(--go-bg);color:var(--go-ink)}.st.err{background:var(--stop-bg);color:var(--stop-ink)}.st.man{background:var(--muted-bg);color:var(--ink-2)}
.fine{font-size:12px;color:var(--dim);max-width:90ch;margin:0}
/* open posting */
.detail{display:flex!important;flex-direction:column;gap:18px;padding:18px 24px 24px 96px}
.d-actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.d-actions .grow{flex:1}
.btn-primary{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 16px;border-radius:10px;background:var(--accent);color:var(--accent-ink);font-weight:600;text-decoration:none}
.btn-primary svg{width:14px;height:14px}.btn-primary:hover{filter:brightness(1.1);color:var(--accent-ink)}
.d-actions .btn-cv{background:var(--surface);color:var(--ink);border-color:var(--line-strong)}.d-actions .btn-cv:hover{border-color:var(--accent);color:var(--ink);filter:none}
.seen-btn{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 16px;border-radius:10px;border:1px solid var(--line-strong);background:var(--surface);color:var(--ink-2);font-weight:600;cursor:pointer;user-select:none}
.seen-btn:has(input:checked){border-color:var(--accent);background:var(--accent-soft);color:var(--accent-deep)}
.seen-btn input{margin:0;width:16px;height:16px;accent-color:var(--accent)}
.d-src{font-size:12px;color:var(--dim)}
.facts{margin:0;display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid var(--line);border-radius:12px;overflow:hidden}
.facts>div{padding:12px 16px;background:var(--surface);border-right:1px solid var(--line);min-width:0}.facts>div:last-child{border-right:0}
.facts dt{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--dim)}
.facts dd{margin:0}.facts .v{margin-top:4px;font-weight:600;color:var(--ink);overflow-wrap:anywhere}.facts .n{margin-top:2px;font-size:12px;color:var(--dim)}
.d-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1.1fr);gap:16px;align-items:start}
.d-card{border:1px solid var(--line);border-radius:12px;padding:16px;display:flex;flex-direction:column;gap:10px;min-width:0}
.d-card h4{margin:0;display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700;letter-spacing:0;text-transform:none}
.d-card .ic{flex:none;width:22px;height:22px;border-radius:11px;display:grid;place-items:center}.d-card .ic svg{width:12px;height:12px}
.d-card.why h4{color:var(--go-ink)}.d-card.why .ic{background:var(--go-bg);color:var(--go)}
.d-card.gaps h4{color:var(--warn-ink)}.d-card.gaps .ic{background:var(--warn-bg);color:var(--warn)}
.d-card ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.d-card.why li,.d-card.gaps li{position:relative;padding-left:16px}
.d-card.why li::before,.d-card.gaps li::before{content:"";position:absolute;left:0;top:.6em;width:6px;height:6px;border-radius:3px;background:var(--go)}
.d-card.gaps li::before{background:var(--warn)}
.d-card.apply{background:var(--surface-2)}.d-card.apply h4{color:var(--ink)}
.contacts .lb{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin-bottom:8px}
.cchips{display:flex;flex-wrap:wrap;gap:8px}
.cchips a{display:inline-flex;align-items:center;min-height:36px;padding:0 12px;border-radius:999px;border:1px solid var(--line-strong);background:var(--surface);color:var(--ink);font-size:13px;font-weight:600;text-decoration:none}
.cchips a:hover{border-color:var(--accent);color:var(--ink)}
.d-ev{background:transparent;border:0;border-top:1px solid var(--line);border-radius:0;padding:12px 0 0;margin:0}
.d-ev>summary{cursor:pointer;font-size:12px;font-weight:600;color:var(--dim)}
.d-ev .d-more{border-top:0;padding-top:12px}
/* sortable headers */
.cols{padding:0 18px}.colsort{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:4px;font:inherit;letter-spacing:inherit;text-transform:inherit;color:var(--dim);min-height:28px}
.colsort:hover{color:var(--ink)}.colsort:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.colsort .arr::after{content:"↕";font-size:11px;opacity:.5}
.colsort[aria-sort="ascending"]{color:var(--ink)}.colsort[aria-sort="ascending"] .arr::after{content:"↑";opacity:1}
.colsort[aria-sort="descending"]{color:var(--ink)}.colsort[aria-sort="descending"] .arr::after{content:"↓";opacity:1}
/* portal coverage */
.cov-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,200px),1fr));gap:12px;margin-top:14px}
.tile{border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:2px}
.tile b{font-size:26px;line-height:1.15;font-variant-numeric:tabular-nums}.tile small{font-size:14px;font-weight:600;color:var(--dim)}.tile span{font-size:12px;color:var(--dim)}
.cov-h{font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--dim);margin:20px 0 10px}
.cov-chart{display:flex;flex-direction:column;gap:12px}
.cbar{display:grid;grid-template-columns:170px minmax(0,1fr) 64px 170px;gap:12px;align-items:center;font-size:13px}
.cname{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.cname a{color:var(--ink);text-decoration:none}.cname a:hover{color:var(--accent)}
.ctrack{display:block;height:10px;border-radius:0 4px 4px 0;background:transparent;border-left:1px solid var(--line-strong)}
.ctrack i{display:block;height:10px;border-radius:0 4px 4px 0;background:var(--accent)}
.cval{font-weight:700;text-align:right}
.cerr{grid-column:2/-1;margin:0;padding-left:16px;font-size:12px;color:var(--stop-ink)}
.cov-manual{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,240px),1fr));gap:12px}
.mcard{border:1px solid var(--line);border-radius:12px;padding:14px;display:flex;flex-direction:column;gap:6px;font-size:13px}
.mcard>span{color:var(--dim);font-size:12px}.mcard b a{color:var(--ink)}
.mlinks{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.mlinks a{display:inline-flex;align-items:center;min-height:32px;padding:0 10px;border-radius:999px;border:1px solid var(--line-strong);font-size:12px;font-weight:600;text-decoration:none;color:var(--accent)}
.cov-table{background:transparent;border:0;padding:0;margin:16px 0 8px}.cov-table>summary{font-size:13px;font-weight:600;color:var(--accent)}
@media (max-width:900px){.facts{grid-template-columns:repeat(2,minmax(0,1fr))}.facts>div{border-bottom:1px solid var(--line)}
.d-grid{grid-template-columns:minmax(0,1fr)}.detail{padding-left:18px!important}.cbar{grid-template-columns:110px minmax(0,1fr) 52px}.cbar .st{grid-column:2/-1;justify-self:start}}
footer{color:var(--dim);font-size:12px;border-top:1px solid var(--line);padding-top:16px;padding-bottom:28px}
@media (max-width:900px){.flow{grid-template-columns:repeat(2,minmax(0,1fr))}.step{border-bottom:1px solid var(--line)}
.step:nth-child(2n){border-right:0}.detail,.d-more{grid-template-columns:minmax(0,1fr)}.detail{padding-left:18px}.side{max-width:none}}
@media (max-width:760px){.cols{display:none}
.row{grid-template-columns:48px minmax(0,1fr);gap:10px 12px;align-items:start}
.tags{display:flex;flex-wrap:wrap;gap:6px;grid-column:2;align-items:center}
.chev{display:none}.bar{width:36px}.kpi{flex:1 1 30%;min-width:0;padding:8px 12px}.kpi b{font-size:19px}}
@media (max-width:560px){.flow{grid-template-columns:minmax(0,1fr)}.step{border-right:0}h1{font-size:22px}}
@media print{.side,.icon-btn,.list-tools select,.more{display:none}.decide{display:block}.rowd>.detail{display:grid}.card{break-inside:avoid}}
@media (prefers-reduced-motion:reduce){.chev{transition:none}}
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

  // The summary cards open the section they name.
  document.querySelectorAll('[data-open]').forEach(function(a){
    a.addEventListener('click',function(){var d=document.getElementById(a.dataset.open);if(d)d.open=true;});
  });

  var track=document.getElementById('main');
  if(!track)return;
  var cards=Array.prototype.slice.call(track.querySelectorAll('.card'));
  var tot=document.getElementById('tot'),empty=document.getElementById('empty');

  // "Revisada": a per-browser convenience, never written back to the state files.
  var done=get('pe_revision_done',{});
  function count(){var c=document.getElementById('cnt');if(c)c.textContent=track.querySelectorAll('input.done:checked').length;}
  document.querySelectorAll('input.done').forEach(function(cb){
    var card=cb.closest('.card');cb.checked=!!done[cb.dataset.key];card.classList.toggle('is-done',cb.checked);
    cb.addEventListener('change',function(){
      done[cb.dataset.key]=cb.checked;put('pe_revision_done',done);card.classList.toggle('is-done',cb.checked);count();
      if(fd.value)apply();
    });
  });
  count();

  // Filters and order.
  var q=document.getElementById('q'),fa=document.getElementById('fa'),fr=document.getElementById('fr'),
      mods=Array.prototype.slice.call(document.querySelectorAll('input.fmod')),
      fz=document.getElementById('fz'),fc=document.getElementById('fc'),fd=document.getElementById('fd'),so=document.getElementById('so'),
      fx=document.getElementById('fx'),fdistrict=document.getElementById('f-district'),
      fsector=document.getElementById('f-sector'),fkind=document.getElementById('f-kind');
  var controls=[q,fa,fr,fz,fc,fd,fdistrict,fsector,fkind];
  function modOk(c){
    var on=mods.filter(function(m){return m.checked;}).map(function(m){return m.value;});
    return on.length===mods.length||on.indexOf(c.dataset.modality||'no declarado')>=0;
  }
  // Abroad and not remote: shown only when the profile (or this browser's choice) accepts moving.
  fx.checked=get('pe_revision_abroad',false);
  fx.addEventListener('change',function(){put('pe_revision_abroad',fx.checked);apply();});
  var WITHIN={hoy:['hoy'],'3d':['hoy','3d'],'7d':['hoy','3d','7d']};
  function closeOk(b){var v=fc.value;if(!v)return true;if(v==='fecha')return b!=='sin';if(v==='sin')return b==='sin';return (WITHIN[v]||[]).indexOf(b)>=0;}
  function doneOk(c){var v=fd.value;if(!v)return true;var d=c.classList.contains('is-done');return v==='done'?d:!d;}
  function apply(){
    var t=(q.value||'').toLowerCase(),shown=0;
    cards.forEach(function(c){
      var ok=(!t||c.dataset.text.indexOf(t)>=0)&&(!fa.value||c.dataset.action===fa.value)
        &&modOk(c)&&(!fr.value||c.dataset.fresh===fr.value)
        &&(!fz.value||c.dataset.zones.split('|').indexOf(fz.value)>=0)
        &&(!fdistrict.value||c.dataset.districts.split('|').indexOf(fdistrict.value)>=0)
        &&(!fsector.value||c.dataset.sector===fsector.value)
        &&(!fkind.value||c.dataset.kinds.split('|').indexOf(fkind.value)>=0)
        &&closeOk(c.dataset.close)&&doneOk(c)
        &&(fx.checked||c.dataset.excl!=='1');
      c.hidden=!ok;if(ok)shown++;
    });
    tot.textContent=shown;empty.hidden=shown>0;
    document.querySelectorAll('[data-quick-action]').forEach(function(b){b.setAttribute('aria-pressed',String(b.dataset.quickAction===fa.value));});
  }
  var heads=Array.prototype.slice.call(document.querySelectorAll('button.colsort'));
  function sort(){
    var p=so.value.split(':'),key=p[0],sign=p[1]==='asc'?1:-1;
    heads.forEach(function(h){h.setAttribute('aria-sort',h.dataset.sort===key?(sign>0?'ascending':'descending'):'none');});
    cards.sort(function(a,b){
      var x=a.getAttribute('data-s-'+key),y=b.getAttribute('data-s-'+key),nx=parseFloat(x),ny=parseFloat(y);
      var c=(!isNaN(nx)&&!isNaN(ny))?nx-ny:String(x).localeCompare(String(y),'es');
      return c*sign||(parseFloat(b.getAttribute('data-s-encaje'))-parseFloat(a.getAttribute('data-s-encaje')));
    });
    cards.forEach(function(c){track.appendChild(c);});
  }
  controls.forEach(function(el){el.addEventListener('input',apply);el.addEventListener('change',apply);});
  mods.forEach(function(m){m.addEventListener('change',apply);});
  document.querySelectorAll('[data-quick-action]').forEach(function(b){b.addEventListener('click',function(){fa.value=b.dataset.quickAction;apply();});});
  document.addEventListener('click',function(e){
    var b=e.target.closest&&e.target.closest('button[data-set-filter]');if(!b)return;
    var sel={district:fdistrict,sector:fsector,kind:fkind,zone:fz}[b.dataset.setFilter];
    if(sel){sel.value=b.dataset.value;apply();document.getElementById('decidir').scrollIntoView({behavior:'smooth',block:'start'});}
  });
  so.addEventListener('change',sort);
  heads.forEach(function(h){
    h.addEventListener('click',function(){
      var cur=h.getAttribute('aria-sort'),dir=cur==='none'?h.dataset.first:(cur==='ascending'?'desc':'asc');
      so.value=h.dataset.sort+':'+dir;
      if(so.value!==h.dataset.sort+':'+dir){var o=document.createElement('option');o.value=h.dataset.sort+':'+dir;o.textContent=h.textContent+(dir==='asc'?' ↑':' ↓');so.appendChild(o);so.value=o.value;}
      sort();
    });
  });

  // "Ver ficha" from this week's cards: clear what hides the posting, open it and bring it into view.
  document.querySelectorAll('[data-goto]').forEach(function(b){
    b.addEventListener('click',function(){
      var card=cards.filter(function(c){return c.dataset.key===b.dataset.goto;})[0];if(!card)return;
      if(card.hidden){controls.forEach(function(el){el.value='';});mods.forEach(function(m){m.checked=true;});apply();}
      var d=card.querySelector('details');if(d)d.open=true;
      card.scrollIntoView({behavior:'smooth',block:'start'});
    });
  });

  apply();
})();
"""



def coverage_html(cov: dict | None) -> str:
    """Which portals the search covers and how the last run went (tools/pe_pipeline.py writes
    reports/busqueda/cobertura.json): three headline numbers, one bar per searched portal (results,
    with its state as an icon and words), the links for the portals searched by hand, and the
    same data as a table."""
    if not isinstance(cov, dict) or not isinstance(cov.get("portales"), list):
        return ('<details class="cov"><summary>Portales y cobertura</summary><p class="fine">Sin registro todavía: '
                'corre <code>python tools/pe_pipeline.py portales</code> (o <code>buscar</code>).</p></details>')
    portals = [p for p in cov["portales"] if isinstance(p, dict)]
    clis = [p for p in portals if p.get("metodo") == "cli"]
    manual = [p for p in portals if p.get("metodo") != "cli"]
    clean = sum(1 for p in clis if p.get("consultas") and not p.get("fallidas"))
    total = sum(int(p.get("resultados") or 0) for p in clis)
    queries = sum(int(p.get("consultas") or 0) for p in clis)
    failed = sum(int(p.get("fallidas") or 0) for p in clis)
    top = max((int(p.get("resultados") or 0) for p in clis), default=0) or 1
    tiles = (f'<div class="tile"><b>{fmt_es(total)}</b><span>resultados en la última búsqueda</span></div>'
             f'<div class="tile"><b>{queries - failed} <small>de {queries}</small></b><span>consultas sin error</span></div>'
             f'<div class="tile"><b>{clean} <small>de {len(clis)}</small></b><span>portales automáticos sin errores</span></div>')
    bars = []
    for p in sorted(clis, key=lambda x: -int(x.get("resultados") or 0)):
        n = int(p.get("resultados") or 0)
        ran, bad = int(p.get("consultas") or 0), int(p.get("fallidas") or 0)
        if not ran:
            state = '<span class="st man">sin correr</span>'
        elif bad:
            state = f'<span class="st err">⚠ {bad} de {ran} fallaron</span>'
        else:
            state = f'<span class="st ok">✓ {ran} de {ran} consultas</span>'
        tip = f'{p.get("nombre")}: {fmt_es(n)} resultados · {ran} consultas' + (
            f' · {p["paginas_extra"]} páginas extra' if p.get("paginas_extra") else "") + f' · {p.get("fecha") or "—"}'
        errs = "".join(f'<li>{esc(e)}</li>' for e in (p.get("errores") or [])[:3])
        bars.append(f'<div class="cbar" role="listitem" title="{esc(tip)}"><span class="cname">{link(p.get("home"), p.get("nombre"))}</span>'
                    f'<span class="ctrack"><i style="width:{max(2, round(100 * n / top))}%"></i></span>'
                    f'<span class="cval num">{fmt_es(n)}</span>{state}'
                    + (f'<ul class="cerr">{errs}</ul>' if errs else "") + '</div>')
    hand = "".join(
        f'<div class="mcard"><b>{link(p.get("home"), p.get("nombre"))}</b><span>{esc(p.get("nota") or "")}</span>'
        f'<div class="mlinks">{"".join(link(x.get("url"), x.get("q") or "abrir") for x in p.get("enlaces") or [] if isinstance(x, dict))}</div></div>'
        for p in manual)
    rows = []
    for p in portals:
        if p.get("metodo") == "cli":
            last = f'{esc(p.get("ok", 0))} de {esc(p.get("consultas") or 0)} ok · {esc(p.get("resultados", 0))} resultados · {esc(p.get("fecha") or "—")}'
        else:
            last = "a mano"
        errors = "<br>".join(esc(e) for e in (p.get("errores") or [])[:3]) or "—"
        rows.append(f'<tr><td>{link(p.get("home"), p.get("nombre"))}</td><td>{"CLI" if p.get("metodo") == "cli" else "Enlace"}</td>'
                    f'<td>{last}</td><td>{errors}</td></tr>')
    return (f'<details class="cov"><summary>Portales y cobertura · {len(portals)} portales, {clean} de {len(clis)} CLI sin errores '
            f'· registro del {esc(cov.get("fecha") or "—")}</summary>'
            f'<div class="cov-tiles">{tiles}</div>'
            f'<h4 class="cov-h">Resultados por portal con búsqueda automática</h4><div class="cov-chart" role="list">{"".join(bars)}</div>'
            + (f'<h4 class="cov-h">Buscar a mano</h4><div class="cov-manual">{hand}</div>' if hand else "")
            + '<details class="cov-table"><summary>Ver como tabla</summary><div class="wrap"><table><thead><tr><th>Portal</th><th>Cómo</th>'
            f'<th>Última búsqueda</th><th>Errores</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>'
            '<p class="fine">Los portales con búsqueda automática corren con <code>python tools/pe_pipeline.py buscar</code>; '
            'los demás se abren con estos enlaces y las ofertas que encuentres se pegan a Claude para evaluarlas.</p></details>')


def nuevas_html(report: dict | None, seen: dict) -> str:
    """The last search's postings not yet evaluated (tools/pe_pipeline.py buscar ->
    reports/busqueda/nuevas.json), minus any registered since: a list to skim before /rank."""
    if not isinstance(report, dict) or not isinstance(report.get("nuevas"), list):
        return ""
    known = {pe_frescura.posting_id({"id": e.get("id"), "url": e.get("url")}) for e in seen.values() if isinstance(e, dict)}
    rows = [p for p in report["nuevas"] if isinstance(p, dict) and pe_frescura.posting_id(p) not in known]
    if not rows:
        return ""
    portals = sorted({p.get("portal") or "?" for p in rows})
    queries = sorted({p.get("query") or "" for p in rows if p.get("query")})
    body = []
    for p in rows:
        text = " ".join(str(p.get(k) or "") for k in ("title", "company", "location", "query")).lower()
        kind = " · ".join(x for x in (p.get("modality"), p.get("level")) if x and x != "no declarado")
        body.append(
            f'<tr data-text="{esc(text)}" data-portal="{esc(p.get("portal") or "?")}" data-query="{esc(p.get("query") or "")}">'
            f'<td class="num">{esc(p.get("date") or "—")}</td><td><b>{link(p.get("url"), p.get("title") or "—")}</b>'
            f'<br><span class="dim">{esc(p.get("company") or "")}</span></td>'
            f'<td>{esc(p.get("location") or "—")}</td><td>{esc(kind or "—")}</td>'
            f'<td>{esc((p.get("portal") or "?").replace("-", " "))}</td><td class="dim">{esc(p.get("query") or "")}</td></tr>')
    portal_opts = "".join(f'<option value="{esc(x)}">{esc(x.replace("-", " "))}</option>' for x in portals)
    query_opts = "".join(f'<option value="{esc(x)}">{esc(x)}</option>' for x in queries)
    return (f'<details class="nuevas"><summary>Nuevas de la última búsqueda, sin evaluar ({len(rows)}) · '
            f'{esc(report.get("generated") or "—")}</summary>'
            '<p class="fine">Lo que trajo <code>python tools/pe_pipeline.py buscar</code> y aún no pasa por <code>/rank</code>: '
            'sin encaje, frescura ni lugar verificados. Solo para darles una mirada.</p>'
            '<div class="filters"><input id="nq" type="search" placeholder="Buscar en las nuevas" aria-label="Buscar en las nuevas">'
            f'<select id="np" aria-label="Portal"><option value="">Todo portal</option>{portal_opts}</select>'
            f'<select id="nc" aria-label="Consulta"><option value="">Toda consulta</option>{query_opts}</select>'
            f'<span class="progress">Mostrando <b id="ncount">{len(rows)}</b> de {len(rows)}</span></div>'
            '<div class="wrap"><table><thead><tr><th>Publicada</th><th>Oferta</th><th>Lugar</th><th>Modalidad y nivel</th>'
            f'<th>Portal</th><th>Consulta</th></tr></thead><tbody id="nrows">{"".join(body)}</tbody></table></div></details>')


NUEVAS_JS = """
(function(){
  var body=document.getElementById('nrows');if(!body)return;
  var q=document.getElementById('nq'),p=document.getElementById('np'),c=document.getElementById('nc'),n=document.getElementById('ncount');
  function apply(){
    var t=(q.value||'').toLowerCase(),shown=0;
    Array.prototype.forEach.call(body.rows,function(r){
      var ok=(!t||r.dataset.text.indexOf(t)>=0)&&(!p.value||r.dataset.portal===p.value)&&(!c.value||r.dataset.query===c.value);
      r.hidden=!ok;if(ok)shown++;
    });
    n.textContent=shown;
  }
  [q,p,c].forEach(function(el){el.addEventListener('input',apply);el.addEventListener('change',apply);});
})();
"""


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
        "cierre": closes["sort"], "modalidad": r.get("modality") or "zz no declarado",
    }


CHEVRON = ('<svg class="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">'
           '<path d="M4 6l4 4 4-4"/></svg>')


def card_html(r: dict, classes: dict[str, str]) -> str:
    """One posting as a row that opens: what to do and how well it fits on the row; why it fits,
    the gaps, the evidence and how to apply inside."""
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
    age = (fresh or {}).get("age_days")
    fresh_txt = level.capitalize() + (f" · {'≥' if (fresh or {}).get('age_is_minimum') else ''}{age} d" if isinstance(age, int) else "")
    modality = r.get("modality") or "no declarado"
    close_cell = (f'<span class="close {esc(closes["css"])}" title="Fecha de cierre que escribe la oferta">{esc(closes["label"])}</span>'
                  if closes["bucket"] != "sin" else '<span class="nodate">Sin fecha</span>')
    level_txt = r.get("level") if r.get("level") and r.get("level") != "no declarado" else ""
    how = f'<div class="how"><b>Cómo:</b> {esc(r["how_to_apply"])}</div>' if r.get("how_to_apply") else ""
    applied = (f'<div class="how"><b>Postulación registrada</b>'
               f'{" · " + esc(r.get("applied_date")) if r.get("applied_date") else ""}</div>'
               if r["action"] == "postulada" else "")
    place = context_html(r.get("context"))
    posting = link(r.get("url"), "Ver aviso original ↗") if re.match(r"^https?://", str(r.get("url") or "")) else ""
    detail = detail_html(r, fresh, level, closes, applied, how, place)
    return (
        f'<article class="card {css}" data-key="{esc(r["key"])}" data-tone="{css}" '
        f'data-label="{esc((r.get("title") or "") + " · " + (r.get("company") or ""))}" '
        f'data-action="{esc(r["action"])}" data-modality="{esc(r.get("modality"))}" data-fresh="{esc(level)}" '
        f'data-close="{esc(closes["bucket"])}" data-zones="{esc(zones)}" data-districts="{esc(districts)}" '
        f'data-sector="{esc(rb.get("label"))}" data-kinds="{esc(kinds)}" data-text="{esc(text)}" '
        f'data-excl="{"1" if r.get("abroad_excluded") else ""}" {sort_attrs}>'
        f'<details class="rowd"><summary class="row">'
        f'<span class="sc" title="Encaje: puntaje de /rank sobre 100 · {esc(r.get("rank_verdict"))}"><b>{esc(score if score is not None else "—")}</b>'
        f'<span class="bar"><i style="width:{esc(pct)}%"></i></span></span>'
        f'<span class="who">{logo_html(r, classes)}<span class="tt"><span class="t">{esc(r.get("title"))}</span>'
        f'<span class="co">{esc(r.get("company"))}{" · " + esc(level_txt) if level_txt else ""}</span></span></span>'
        f'<span class="tags"><span class="c-mod">{esc(modality.capitalize())}</span>'
        f'<span class="c-close">{close_cell}</span>'
        f'<span class="c-fresh" title="Frescura de la vacante"><i class="dot {FRESH_CSS.get(level, "")}"></i>{esc(fresh_txt)}</span>'
        f'<span class="c-act"><span class="badge {css}">{esc(label)}</span></span></span>{CHEVRON}</summary>'
        f'<div class="detail">{detail}'        "</div></details></article>"
    )


ICON_CHECK = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">'
              '<path d="M3 8.5l3 3 7-7"/></svg>')
ICON_ALERT = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">'
              '<path d="M8 3v6M8 12.5v.5"/></svg>')
ICON_OUT = ('<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">'
            '<path d="M6 3h7v7M13 3L5 11M3 6v7h7"/></svg>')


def _ddmm(value) -> str:
    try:
        return date.fromisoformat(str(value)[:10]).strftime("%d/%m")
    except ValueError:
        return ""


def detail_html(r: dict, fresh: dict | None, level: str, closes: dict, applied: str, how: str, place: str) -> str:
    """The open posting, as drawn in the canvas: the actions first, the key facts in one strip,
    then why it fits, the gaps and how to apply side by side, and the sources folded at the end."""
    ctx = r.get("context") or {}
    lg, rb = ctx.get("lugar") or {}, ctx.get("rubro") or {}
    score = r.get("rank_score")
    age = (fresh or {}).get("age_days")
    floor = "≥" if (fresh or {}).get("age_is_minimum") else ""
    posted = _ddmm((fresh or {}).get("original_date") or r.get("posted_date"))
    risk = (fresh or {}).get("risk")
    places = ", ".join(lg.get("districts") or []) or ("Remoto" if lg.get("remote") else "Sin dato")
    modality = (r.get("modality") or "no declarado").capitalize()
    facts = [
        ("Encaje", f"{score} de 100" if score is not None else "—", r.get("rank_verdict") or ""),
        ("Frescura", f"{level.capitalize()}" + (f" · {floor}{age} días" if isinstance(age, int) else ""),
         (f"riesgo de ghost job {risk}" if risk in ("medio", "alto") else f"publicada el {posted}" if posted else "")),
        ("Cierre", closes["label"] if closes["bucket"] != "sin" else "Sin fecha",
         "fecha que escribe la oferta" if closes["bucket"] != "sin" else "el aviso no la indica"),
        ("Lugar", places, f"modalidad: {modality.lower()}"),
        ("Rubro", rb.get("label") or "Sin identificar", " · ".join(employer_kinds(rb))),
    ]
    dl = "".join(f'<div><dt>{esc(k)}</dt><dd class="v">{esc(v)}</dd>' + (f'<dd class="n">{esc(n)}</dd>' if n else "") + "</div>"
                 for k, v, n in facts)
    url = r.get("url")
    actions = (f'<a class="btn-primary" href="{esc(url)}" target="_blank" rel="noopener noreferrer">Ver aviso original {ICON_OUT}</a>'
               if re.match(r"^https?://", str(url or "")) else "")
    actions += cv_html(r).replace('<div class="cvrow">', "").replace("</div>", "")
    actions += (f'<label class="seen-btn"><input type="checkbox" class="done" data-key="{esc(r["key"])}"> '
                f'<span>Revisada</span></label>')
    portal = (r.get("portal") or "").replace("-search", "").replace("-", " ")
    src = " · ".join(x for x in (f"publicada en {portal}" if portal else "", f"vista el {_ddmm(r.get('first_seen'))}"
                                 if _ddmm(r.get("first_seen")) else "") if x)
    people = people_html(r)
    return (
        f'<div class="d-actions">{actions}<span class="grow"></span><span class="d-src">{esc(src)}</span></div>'
        f'<dl class="facts">{dl}</dl>'
        '<div class="d-grid">'
        f'<section class="d-card why"><h4><span class="ic">{ICON_CHECK}</span>Por qué calza</h4>{bullets(r.get("strengths"))}</section>'
        f'<section class="d-card gaps"><h4><span class="ic">{ICON_ALERT}</span>Brechas</h4>{bullets(r.get("gaps"))}</section>'
        f'<section class="d-card apply"><h4>{"Postulación" if applied else "Cómo postular"}</h4>{applied}{how}'
        f'{contacts_html(r)}{people}<div class="meta">{meta_html(r)}</div>{also_html(r)}</section>'
        '</div>'
        '<details class="d-ev"><summary>Fuentes y evidencia</summary><div class="d-more">'
        f'<div><h4>Frescura de la vacante</h4>{fresh_html(fresh)}</div>'
        f'<div><h4>Lugar y rubro</h4>{place or "<span class=dim>Sin dato</span>"}</div>'
        f'<div><h4>Reputación</h4><div class="rating">{rating_html(r.get("ratings"))}</div></div></div></details>'
    )


SOON_VISIBLE = 8  # closing-soon cards shown at once; the rest fold under "Ver N más"


def soon_card(r: dict, classes: dict[str, str]) -> str:
    has_cv = bool(r.get("cv_href"))
    return (
        f'<button type="button" class="soon-card{" urgent" if r["closes"]["bucket"] == "hoy" else ""}" data-goto="{esc(r["key"])}" '
        f'aria-label="Ver ficha: {esc(r.get("title"))}">'
        f'<span class="row1"><span class="close {esc(r["closes"]["css"])}">{esc(r["closes"]["label"])}</span>'
        f'<b class="num" title="Encaje">{esc(r.get("rank_score"))}</b></span>'
        f'<span class="t">{esc(r.get("title"))}</span><span class="co">{esc(r.get("company"))}</span>'
        f'<span class="cvst{" ok" if has_cv else ""}">{"CV listo" if has_cv else "Sin CV a medida"}</span></button>'
    )


def _body(data: dict, today: date, title: str | None, classes: dict[str, str] | None = None) -> str:
    classes = classes or {}
    sl = data["shortlist"]
    acts = data["actions"]
    ready = acts["ya"] + acts["pronto"]
    confirm = acts["confirmar"] + acts["antes"]
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
         + (f" · {acts['postulada']} postuladas" if acts["postulada"] else ""), "/apply"),
    ]
    flow = "".join(
        f'<div class="step"><div class="n">{esc(n)} <span class="tag">{esc(cmd)}</span></div>'
        f'<div class="big">{esc(big)}</div><small>{sub}</small></div>'
        for n, big, sub, cmd in steps
    )
    kpis = (f'<div class="kpi" title="vistas · {portals}"><b>{len(data["seen"])}</b><span>vistas</span></div>'
            f'<div class="kpi" title="{verdicts}"><b>{len(data["ranked"])}</b><span>evaluadas</span></div>'
            f'<div class="kpi go"><b>{ready}</b><span>para postular</span></div>'
            f'<div class="kpi warn"><b>{confirm}</b><span>por confirmar</span></div>'
            f'<div class="kpi"><b>{acts["postulada"]}</b><span>postuladas</span></div>')

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
    mode_values = [m for m in ("presencial", "híbrido", "remoto") if m in modalities]
    if any(not r.get("modality") or r.get("modality") == "no declarado" for r in main_rows):
        mode_values.append("no declarado")
    mode_checks = "".join(f'<label class="chk"><input type="checkbox" class="fmod" value="{esc(m)}" checked> {esc(m.capitalize())}</label>'
                          for m in mode_values)
    by_action = Counter(r["action"] for r in main_rows)
    action_buttons = (f'<button type="button" data-quick-action="" aria-pressed="true"><span>Todas</span><span>{len(main_rows)}</span></button>'
                      + "".join(f'<button type="button" data-quick-action="{k}" aria-pressed="false"><span>{esc(lbl)}</span>'
                                f'<span>{by_action[k]}</span></button>'
                                for k, lbl, _ in ACTIONS if by_action[k]))
    sort_opts = "".join(
        f'<option value="{v}">{esc(lbl)}</option>'
        for v, lbl in (("accion:asc", "Acción más urgente"), ("encaje:desc", "Mayor encaje"),
                       ("cierre:asc", "Cierra antes"), ("frescura:asc", "Más reciente"),
                       ("reputacion:desc", "Mejor reputación"), ("puesto:asc", "Puesto, A a Z"),
                       ("modalidad:asc", "Modalidad"))
    )
    col_buttons = "".join(
        f'<button type="button" class="colsort" data-sort="{k}" data-first="{first}" aria-sort="{"ascending" if k == "accion" else "none"}" '
        f'title="Ordenar por {esc(lbl.lower())}">{esc(lbl)}<span class="arr" aria-hidden="true"></span></button>'
        for lbl, k, first in (("Encaje", "encaje", "desc"), ("Oferta", "puesto", "asc"), ("Modalidad", "modalidad", "asc"),
                              ("Cierre", "cierre", "asc"), ("Frescura", "frescura", "asc"), ("Acción", "accion", "asc")))
    unranked = "".join(
        f'<li>{esc(r.get("posted_date") or "?")} · {link(r.get("url"), r.get("title"))} · {esc(r.get("company"))}'
        f' <span class="dim">({esc(r.get("modality"))} · {esc(r.get("level"))})</span></li>'
        for r in data["unranked"]
    )
    soon = sorted((r for r in main_rows if r["closes"]["bucket"] in ("hoy", "3d", "7d")),
                  key=lambda r: (r["closes"]["sort"], -(r.get("rank_score") or 0)))
    week = ""
    if soon:
        shown, rest = soon[:SOON_VISIBLE], soon[SOON_VISIBLE:]
        more = (f'<details class="soon-more"><summary>Ver {len(rest)} más</summary>'
                f'<div class="soon-grid">{"".join(soon_card(r, classes) for r in rest)}</div></details>' if rest else "")
        week = (f'<section class="week" aria-labelledby="semana"><div class="h2-row">'
                f'<h2 id="semana">Esta semana cierran {len(soon)}</h2>'
                '<p class="note">Fecha de cierre que escribe la oferta, no el vencimiento del portal.</p></div>'
                f'<div class="soon-grid">{"".join(soon_card(r, classes) for r in shown)}</div>{more}</section>')
    heading = esc(title) if title else "Revisión de ofertas"
    pref = data.get("abroad_pref") or {"mode": "no", "text": None}
    excluded = 0
    for r in data["ranked"]:
        r["abroad_excluded"] = abroad_excluded(r, pref["mode"])
        excluded += bool(r["abroad_excluded"] and r in main_rows)
    pref_html = (
        f'<div class="pref"><span>Búsqueda en Lima, Perú · fuera del Perú: <b>{esc(ABROAD_LABEL[pref["mode"]])}</b>'
        f' <span class="dim">· {esc(pref["text"]) if pref.get("text") else "el perfil no pide buscar en el extranjero"}'
        f' (línea <code>Abroad</code> de <code>CLAUDE.md</code>; cámbiala ahí o con <code>/setup</code>)</span></span>'
        f'<label class="done-l"><input type="checkbox" id="fx"> '
        f'Mostrar también las del extranjero que tu perfil deja fuera ({excluded})</label></div>'
    )
    coverage = coverage_html(data.get("cobertura"))
    nuevas = nuevas_html(data.get("nuevas"), data["seen"])
    n_nuevas = nuevas.count("<tr data-text=")
    cov_portals = [p for p in ((data.get("cobertura") or {}).get("portales") or []) if isinstance(p, dict)]
    clis = [p for p in cov_portals if p.get("metodo") == "cli"]
    clean = sum(1 for p in clis if p.get("consultas") and not p.get("fallidas"))
    runs = [p for p in cov_portals if p.get("ok") and re.match(r"\d{4}-\d{2}-\d{2}", str(p.get("fecha") or ""))]
    latest = max((str(p["fecha"])[:10] for p in runs), default=None)
    run_label = (f'{date.fromisoformat(latest).strftime("%d/%m/%Y")} · '
                 + ", ".join(esc(p.get("nombre")) for p in runs if str(p["fecha"]).startswith(latest))
                 if latest else "sin búsquedas registradas")
    more_cards = [("sec-postuladas", len(applied_rows), "Postuladas", "registradas en el estado"),
                  ("sec-cerradas", len(closed_rows), "Cerradas", "su fecha de cierre ya pasó"),
                  ("sec-descartadas", len(out_rows), "Descartadas", "no elegibles"),
                  ("sec-sin-evaluar", len(data["unranked"]), "Sin evaluar", "vistas y relevantes")]
    if nuevas:
        more_cards.append(("sec-nuevas", n_nuevas, "Nuevas", "de la última búsqueda"))
    more_cards.append(("sec-portales", len(cov_portals), "Portales",
                       f"{clean} de {len(clis)} con búsqueda automática sin errores" if clis else "cobertura"))
    more = "".join(f'<a href="#{sid}" data-open="{sid}"><b>{n}</b><strong>{esc(lbl)}</strong><span>{esc(note)}</span></a>'
                   for sid, n, lbl, note in more_cards)
    nuevas = nuevas.replace('<details class="nuevas">', '<details class="sec nuevas" id="sec-nuevas">', 1)
    coverage = coverage.replace('<details class="cov">', '<details class="sec cov" id="sec-portales">', 1)

    return f"""<header class="top"><div class="wrap-x">
<div><div class="eyebrow">Revisión de ofertas · {esc(today.strftime('%d/%m/%Y'))}</div>
<h1>{heading}</h1>
<p class="run-date">Última búsqueda: <strong>{run_label}</strong></p></div>
<div class="top-right"><div class="kpis">{kpis}</div>
<button type="button" class="icon-btn" id="theme" title="Cambiar tema claro/oscuro" aria-label="Cambiar tema">◐</button></div>
</div></header>
<main class="wrap-x">
{week}
<section class="decide" aria-label="Ofertas para decidir">
<aside class="side" aria-label="Filtros">
<div class="fl"><label for="q">Buscar</label><input id="q" type="search" placeholder="Puesto, empresa, distrito"></div>
<div class="fl"><span class="lb" id="lb-acc">Acción</span><input type="hidden" id="fa" value="">
<div class="acts-list" role="group" aria-labelledby="lb-acc">{action_buttons}</div></div>
<fieldset><legend>Modalidad</legend>{mode_checks}</fieldset>
<div class="fl"><label for="fc">Cierre</label><select id="fc"><option value="">Cualquier fecha</option><option value="hoy">Cierra hoy</option><option value="3d">Hasta 3 días</option><option value="7d">Esta semana</option><option value="fecha">Con fecha de cierre</option><option value="sin">Sin fecha</option></select></div>
<div class="fl"><label for="fr">Frescura de la vacante</label><select id="fr"><option value="">Toda</option><option value="muy alta">Muy alta</option><option value="alta">Alta</option><option value="media">Media</option><option value="baja">Baja</option></select></div>
<details class="morefl"><summary>Más filtros</summary>
<div class="fl"><label for="fz">Zona</label><select id="fz"><option value="">Toda zona</option>{zone_opts}</select></div>
<div class="fl"><label for="f-district">Distrito</label><select id="f-district"><option value="">Todos los distritos</option>{district_opts}</select></div>
<div class="fl"><label for="f-sector">Rubro</label><select id="f-sector"><option value="">Todos los rubros</option>{sector_opts}</select></div>
<div class="fl"><label for="f-kind">Tipo de empresa</label><select id="f-kind"><option value="">Todo tipo de empresa</option>{kind_opts}</select></div>
<div class="fl"><label for="fd">Revisadas</label><select id="fd"><option value="">Revisadas y pendientes</option><option value="pend">Solo pendientes</option><option value="done">Solo revisadas</option></select></div>
<div class="legend"><span>Confianza del dato: <span class="dots conf-alta">●●●</span> alta · <span class="dots conf-media">●●○</span> media · <span class="dots conf-baja">●○○</span> baja</span></div></details>
<div class="legend" style="border-top:1px solid var(--line);padding-top:14px;margin-top:0">{pref_html}</div>
</aside>
<div class="listcol">
<div class="list-head"><h2 id="decidir">Para decidir ({len(main_rows)}) <span class="dim">· mostrando <span id="tot">{len(main_rows)}</span></span></h2>
<div class="list-tools"><label>Orden <select id="so">{sort_opts}</select></label></div></div>
<div class="cols" role="group" aria-label="Ordenar por columna">{col_buttons}<span></span></div>
<div class="track list" id="main" aria-label="Ofertas para decidir">{"".join(card_html(r, classes) for r in main_rows)}</div>
<div class="empty" id="empty" hidden>Ninguna oferta con estos filtros.</div>
<p class="note">Haz clic en una fila para ver por qué calza, sus brechas y cómo postular · revisadas <b class="count" id="cnt">0</b> de {len(main_rows)}.</p>
</div>
</section>
<nav class="more" aria-label="Más secciones">{more}</nav>
<details class="sec" id="sec-postuladas"><summary>Postuladas ({len(applied_rows)})</summary>
<div class="track list">{"".join(card_html(r, classes) for r in applied_rows)}</div></details>
<details class="sec" id="sec-cerradas"><summary>Cerradas: su fecha de cierre ya pasó ({len(closed_rows)})</summary>
<div class="track list">{"".join(card_html(r, classes) for r in closed_rows)}</div></details>
<details class="sec" id="sec-descartadas"><summary>Descartadas o no elegibles ({len(out_rows)})</summary>
<div class="track list">{"".join(card_html(r, classes) for r in out_rows)}</div></details>
<details class="sec" id="sec-sin-evaluar"><summary>Vistas y relevantes, sin evaluar ({len(data["unranked"])})</summary><ul class="unranked">{unranked}</ul></details>
{nuevas}
{coverage}
<details class="flowd"><summary>Cómo se llegó aquí: búsqueda, encaje, frescura, reputación y acción</summary><div class="flow">{flow}</div>
<p class="fine" style="margin-top:8px">Desde <code>seen_jobs.json</code>, la frescura y <code>company_research/</code>. El encaje es un triaje de
<code>/rank</code>; <code>/apply</code> vuelve a evaluar antes de postular.</p></details>
<p class="fine">Frescura de la vacante: edad desde la publicación original; "≥" es un mínimo (Computrabajo reinicia la fecha al republicar);
el vencimiento del portal no es una fecha de cierre (<code>10-peru-market.md</code>, sección 10).
Reputación: cada calificación con su n, área y fecha; con menos de {SMALL_N} opiniones es referencial y una certificación pagada no es independiente (sección 11).
"Revisada" se guarda solo en este navegador.</p>
</main>
<footer class="wrap-x">Un solo archivo, sin conexión: se abre con doble clic o en el teléfono. Generado por <code>tools/pe_revision.py</code> · {esc(today.isoformat())}</footer>
<script>{JS}{CV_JS}{NUEVAS_JS}</script>
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
    ap.add_argument("--nuevas", type=Path, default=Path("reports/busqueda/nuevas.json"),
                    help="tools/pe_pipeline.py buscar's new postings, listed apart until /rank scores them (skipped when missing)")
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
    try:
        data["nuevas"] = json.loads(args.nuevas.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data["nuevas"] = None
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
