#!/usr/bin/env python3
"""Search SERVIR public-sector jobs and internships without a browser or LLM.

The site is JSF: each request needs the current ViewState. Its listings have no stable
per-posting URL, so each result includes the public convocatoria number and search page.
Install dependencies with: pip install -r tools/requirements-servir.txt
"""

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("SERVIR necesita requests y beautifulsoup4: pip install -r tools/requirements-servir.txt", file=sys.stderr)
    raise SystemExit(2)

import robots_check


BASE = "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/"
KINDS = {
    "laboral": ("ofertas_laborales.xhtml", "frmLstOfertsLabo", "txtPerfil"),
    "practicas": ("modalidades_formativas.xhtml", "frmLstModFormativas", "txtCriterio"),
}
# JSF numbers its buttons when the page is built (j_idt46...): a redeploy can renumber them,
# so the search button is read from the page by its title, with these as the last resort.
SEARCH_BUTTON = {"laboral": "j_idt46", "practicas": "j_idt43"}
UA = "Mozilla/5.0 (compatible; pe-servir/1.0; personal job search)"
MAX_PAGES = 40  # 10 per page: a query that matches more is too broad to read whole
PAUSE = 0.5  # seconds between page requests: a public-sector server, kept gentle


def clean(value):
    return " ".join(str(value or "").split())


def normalized(value):
    return "".join(c for c in unicodedata.normalize("NFD", clean(value).casefold()) if unicodedata.category(c) != "Mn")


def iso(value):
    try:
        return datetime.strptime(clean(value), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_rows(html, kind, url):
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for box in soup.select(".cuadro-vacantes"):
        title_node = box.select_one(".titulo-vacante label")
        company_node = box.select_one(".nombre-entidad")
        if not title_node or not company_node:
            continue
        title, company = clean(title_node.get_text(" ")), clean(company_node.get_text(" "))
        fields = {}
        for label in box.select(".sub-titulo"):
            value = label.parent.select_one(".detalle-sp")
            if value:
                fields[normalized(label.get_text(" ")).rstrip(":")] = clean(value.get_text(" "))
        ref = fields.get("numero de convocatoria")
        if not ref:
            continue
        # A convocatoria number can be reused by another institution or in another year.
        basis = "|".join(normalized(x) for x in (kind, company, ref, title))
        ident = "servir:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        start = fields.get("fecha inicio de publicacion") or fields.get("inicio de publicacion")
        end = fields.get("fecha fin de publicacion") or fields.get("cierre de publicacion")
        results.append({
            "id": ident, "site": "talento-peru", "type": kind, "title": title, "company": company,
            "location": fields.get("ubicacion"), "date": iso(start), "validThrough": iso(end),
            "ref": ref, "url": url, "salary": fields.get("remuneracion"),
            "level": fields.get("tipo de modalidad") if kind == "practicas" else None,
            "modality": None,
        })
    page = re.search(r"Página\s+(\d+)\s+de\s+(\d+)", soup.get_text(" ", strip=True))
    next_button = next((b.get("id") for b in soup.select("button[id]") if clean(b.get_text(" ")) == "Sig."), None)
    view = soup.select_one('input[name="javax.faces.ViewState"]')
    return results, (int(page.group(1)), int(page.group(2))) if page else None, next_button, view.get("value") if view else None


def partial(response, form):
    root = ET.fromstring(response.text)
    update = next((x.text for x in root.findall(".//update") if x.get("id") == form), None)
    view = next((x.text for x in root.findall(".//update") if x.get("id") == "javax.faces.ViewState"), None)
    if not update or not view:
        raise ValueError("SERVIR cambió su respuesta JSF o la sesión venció")
    return update, view


def search_kind(session, kind, query, location, limit, jobage, field="perfil"):
    page, form, query_field = KINDS[kind]
    if kind == "laboral" and field == "puesto":
        query_field = "txtPuesto"  # the job title; txtPerfil matches the career a posting asks for
    url = BASE + page
    response = session.get(url, timeout=30)
    response.raise_for_status()
    html = response.text
    _, _, _, view = parse_rows(html, kind, url)
    if not view:
        raise ValueError("SERVIR no entregó javax.faces.ViewState")
    button = BeautifulSoup(html, "html.parser").select_one(f'button[id^="{form}:"][title="Buscar"]')
    source = button.get("id") if button else f"{form}:{SEARCH_BUTTON[kind]}"
    found, seen, previous_page, pages = [], set(), 0, 0
    while True:
        pages += 1
        if pages > MAX_PAGES:
            break
        if pages > 1:
            time.sleep(PAUSE)
        payload = {
            "javax.faces.partial.ajax": "true", "javax.faces.source": source,
            "javax.faces.partial.execute": "@all", "javax.faces.partial.render": form + ":mensaje " + form,
            form: form, "javax.faces.ViewState": view, source: source,
            form + ":cboDep_input": "15" if normalized(location) == "lima" else "0",
            form + ":" + query_field: query,
        }
        if kind == "laboral":
            payload[form + ":modalidadAcceso"] = "03"
        response = session.post(url, data=payload, headers={"Faces-Request": "partial/ajax"}, timeout=30)
        response.raise_for_status()
        html, view = partial(response, form)
        rows, page_info, next_button, _ = parse_rows(html, kind, url)
        if not page_info and not rows:
            break
        if page_info and page_info[0] <= previous_page:
            raise ValueError("SERVIR no avanzó de página; se detuvo la búsqueda")
        if page_info:
            previous_page = page_info[0]
        for row in rows:
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            if jobage and row["date"] and (date.today() - date.fromisoformat(row["date"])).days > jobage:
                continue
            found.append(row)
            if len(found) >= limit:
                return found
        if not page_info or page_info[0] >= page_info[1] or not next_button:
            break
        source = next_button
    return found


def search(query, location="Lima", kind="ambos", limit=100, jobage=None, field="perfil"):
    kinds = list(KINDS) if kind == "ambos" else [kind]
    for selected in kinds:
        code, reason = robots_check.gate(BASE + KINDS[selected][0])
        if code:
            raise RuntimeError("SERVIR robots.txt: " + reason)
    with requests.Session() as session:
        session.headers["User-Agent"] = UA
        # Each listing gets its own limit: with a shared one, a broad query fills it with
        # jobs and the internships are never read.
        results = []
        for selected in kinds:
            results.extend(search_kind(session, selected, query, location, limit, jobage, field))
    return {"results": results, "query": query, "location": location, "type": kind}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    se = sub.add_parser("search", help="search public-sector jobs and internships")
    se.add_argument("-q", "--query", default="")
    se.add_argument("-l", "--location", default="Lima", choices=["Lima", "Perú"])
    se.add_argument("--tipo", choices=["laboral", "practicas", "ambos"], default="ambos")
    se.add_argument("--limit", type=int, default=100, help="per listing (laboral and practicas each)")
    se.add_argument("--jobage", type=int)
    se.add_argument("--campo", choices=["perfil", "puesto"], default="perfil",
                    help="laboral: perfil = the career a posting asks for (economia, psicologia); "
                         "puesto = words in the job title (supervisor, analista)")
    args = ap.parse_args(argv)
    if args.limit < 1 or args.limit > 500 or args.jobage is not None and args.jobage < 0:
        ap.error("--limit debe ser 1..500 y --jobage no puede ser negativo")
    try:
        print(json.dumps(search(args.query, args.location, args.tipo, args.limit, args.jobage, args.campo), ensure_ascii=False))
        return 0
    except (requests.RequestException, ET.ParseError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc), "code": "SERVIR_ERROR"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
