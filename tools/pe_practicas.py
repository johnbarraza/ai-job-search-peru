#!/usr/bin/env python3
"""Search public Practicas.pe listings and read one posting, without an LLM.

Install requests and Beautiful Soup with: pip install -r tools/requirements-servir.txt
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlencode, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Instala las dependencias: pip install -r tools/requirements-servir.txt", file=sys.stderr)
    raise SystemExit(2)

import robots_check


BASE = "https://www.practicas.pe"
SEARCH = BASE + "/resultados-busqueda.php"
UA = "Mozilla/5.0 (compatible; pe-practicas/1.0; personal job search)"
MAX_PAGES = 30
PAUSE = 0.5  # seconds between result pages


def clean(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def date_of(text):
    match = re.search(r"(\d{2}/\d{2}/\d{4})", text or "")
    return datetime.strptime(match.group(1), "%d/%m/%Y").date().isoformat() if match else None


def parse_search(html):
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for card in soup.select("section.convocatorias article"):
        anchor = card.select_one("h3 a[href]")
        if not anchor:
            continue
        url = anchor.get("href", "")
        match = re.search(r"-(\d+)\.html$", urlparse(url).path)
        if not match or urlparse(url).netloc not in ("www.practicas.pe", "practicas.pe"):
            continue
        paragraphs = card.select(".text-left p")
        badges = {name: clean(card.select_one(".badge--" + css)) for name, css in
                  (("location", "blue"), ("salary", "red"), ("level", "green"),
                   ("modality", "cyan"), ("deadline", "orange"))}
        title = clean(anchor)
        rows.append({"id": match.group(1), "site": "practicas-pe", "title": title,
                     "company": clean(paragraphs[0]).removeprefix("Entidad:").strip() if paragraphs else "",
                     "requirements": clean(paragraphs[1]).removeprefix("Se requiere:").strip() if len(paragraphs) > 1 else "",
                     "url": url, "location": badges["location"], "salary": badges["salary"],
                     "level": badges["level"], "modality": badges["modality"].lower() or None,
                     "deadline": date_of(badges["deadline"]), "date": None})
    pages = [int(x) for x in re.findall(r"[?&]page=(\d+)", html)]
    return rows, max(pages, default=1)


def search(query, location="Lima", limit=100):
    params = {"q": query, "dep": "15" if location.lower() == "lima" else "0"}
    url = SEARCH + "?" + urlencode(params)
    code, reason = robots_check.gate(url)
    if code:
        raise RuntimeError("Practicas.pe robots.txt: " + reason)
    found = {}
    with requests.Session() as session:
        session.headers["User-Agent"] = UA
        page = 1
        while len(found) < limit and page <= MAX_PAGES:
            if page > 1:
                time.sleep(PAUSE)
            response = session.get(SEARCH, params={**params, "page": page, "sort": "1-fecha_publicacion"}, timeout=30)
            response.raise_for_status()
            rows, total_pages = parse_search(response.text)
            if not rows:
                break
            for row in rows:
                found.setdefault(row["id"], row)
                if len(found) >= limit:
                    break
            if page >= total_pages:
                break
            page += 1
    return {"results": list(found.values()), "query": query, "location": location}


def detail(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc not in ("www.practicas.pe", "practicas.pe") or not re.fullmatch(r"/oferta-[^/]+\.html", parsed.path):
        raise ValueError("URL de convocatoria de Prácticas.pe inválida")
    code, reason = robots_check.gate(url)
    if code:
        raise RuntimeError("Practicas.pe robots.txt: " + reason)
    response = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    match = re.search(r"-(\d+)\.html$", parsed.path)
    posted = re.search(r'"datePosted"\s*:\s*"(\d{4}-\d{2}-\d{2})', response.text)
    expiry = re.search(r'"validThrough"\s*:\s*"(\d{4}-\d{2}-\d{2})', response.text)
    title = clean(soup.select_one("h1"))
    description = "\n".join(clean(x) for x in soup.select(".requisitos, .condiciones-contrato") if clean(x))
    return {"id": match.group(1), "url": url, "title": title, "description": description,
            "date": posted.group(1) if posted else None, "deadline": expiry.group(1) if expiry else None,
            "source": "practicas.pe"}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    se = sub.add_parser("search")
    se.add_argument("-q", "--query", required=True)
    se.add_argument("-l", "--location", default="Lima", choices=["Lima", "Perú"])
    se.add_argument("--limit", type=int, default=100)
    de = sub.add_parser("detail")
    de.add_argument("url")
    args = ap.parse_args(argv)
    if args.command == "search" and not 1 <= args.limit <= 500:
        ap.error("--limit debe estar entre 1 y 500")
    try:
        value = search(args.query, args.location, args.limit) if args.command == "search" else detail(args.url)
        print(json.dumps(value, ensure_ascii=False))
        return 0
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "code": "PRACTICAS_ERROR"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
