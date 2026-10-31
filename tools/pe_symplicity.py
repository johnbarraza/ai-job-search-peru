#!/usr/bin/env python3
"""Read the candidate's own UP Symplicity job search in a local Chrome session.

The user signs in inside Chrome (including PIN/SSO). This program never accepts,
reads or saves a password. Chrome's separate user-data directory is outside the
repository; it is removed when the user-selected retention period expires.
"""

import argparse
import ctypes
import json
import os
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ORIGIN = "https://up-csm.symplicity.com"
JOBS_URL = ORIGIN + "/students/app/jobs/discover"
RESULTS_URL = ORIGIN + "/students/app/jobs/search?perPage=20&page=1&sort=%21postdate"
APPLIED_URL = ORIGIN + "/students/app/jobs/applied"
RESUMES_URL = ORIGIN + "/students/index.php?s=resume&mode=list&ss=resumes"
MAX_RESUME_BYTES = 64 * 1024 * 1024
SESSION_ROOT = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "ai-job-search-pe" / "symplicity"
ACCOUNT_RE = re.compile(r"[a-zA-Z0-9_-]{1,40}\Z")
KEYWORD_SELECTOR = "#jobs-keyword-input"

# Only job-related elements are read. Never print page HTML, cookies, profile
# details, passwords, or the current URL (an SSO URL may contain a token).
EXTRACT_JS = r"""() => {
  const text = el => (el && el.textContent || '').replace(/\s+/g, ' ').trim();
  return [...document.querySelectorAll('.list-page-job-search .list-item')].map((row, index) => {
    return {
      index,
      title: text(row.querySelector('.inline-block.list-item-title > span')) || text(row.querySelector('.list-item-title')),
      company: text(row.querySelector('.list-item-subtitle')),
      deadline_text: text(row.querySelector('.text-warn')),
    };
  }).filter(x => x.title);
}"""

EXTRACT_APPLIED_JS = r"""() => [...document.querySelectorAll('ul.list_job_applications > li.list-item')]
  .map(row => {
    const link = row.querySelector('a[href*="/jobs/detail/"]');
    const text = el => (el && el.textContent || '').replace(/\s+/g, ' ').trim();
    return {title: text(link), company: text(row.querySelector('.list-item-subtitle')),
      url: link && link.href || '', applied: /Solicitud enviada/i.test(text(row))};
  }).filter(row => row.title && row.url)"""

EXTRACT_DETAIL_JS = r"""() => {
  const panel = document.querySelector('jobs-detail');
  if (!panel) return null;
  const text = node => (node && node.innerText || '').trim();
  const headings = [...panel.querySelectorAll('h1,h2,h3,h4')];
  const section = (prefix, parent=false, exact=false) => {
    const heading = headings.find(h => exact ? text(h).toLowerCase() === prefix
                                        : text(h).toLowerCase().startsWith(prefix));
    return heading ? text(parent ? heading.parentElement : heading.nextElementSibling) : '';
  };
  return {
    title: text(headings[0]), company: text(headings[headings.length - 1]),
    posted_age: text(panel.querySelector('.header-title-label')),
    description: section('descripci'), qualifications: section('cualificaciones'),
    additional: section('detalles adicionales', true),
    requirements: section('requisitos', true, true),
    process: section('proceso de solicitud', true),
  };
}"""

EXTRACT_RESUMES_JS = r"""() => [...document.querySelectorAll('.list-item')].map(row => {
  const edit = row.querySelector('a[href*="mode=form"][href*="id="]');
  const id = edit ? new URL(edit.href, location.origin).searchParams.get('id') : '';
  const name = (row.querySelector('h2,h3,h4')?.textContent || '').trim();
  return {id, name, is_default: !row.querySelector('a[onclick*="mode=def"]')};
}).filter(row => row.id && row.name)"""


def account_dir(account: str) -> Path:
    if not ACCOUNT_RE.fullmatch(account):
        raise ValueError("--account debe tener 1–40 letras, números, guiones o guiones bajos")
    if SESSION_ROOT.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError("la sesión debe estar fuera del repositorio")
    return SESSION_ROOT / account


def _safe_remove(path: Path) -> None:
    """Delete only the exact account directory below the app's local state root."""
    root = SESSION_ROOT.resolve()
    target = path.resolve()
    if target.parent != root or not ACCOUNT_RE.fullmatch(target.name):
        raise ValueError("ruta de sesión inesperada; no se borró nada")
    if target.exists():
        shutil.rmtree(target)


def _meta(path: Path) -> dict:
    try:
        obj = json.loads((path / "session.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _expiry(meta: dict) -> datetime | None:
    try:
        dt = datetime.fromisoformat(meta["expires_at"])
        return dt if dt.tzinfo else None
    except (KeyError, TypeError, ValueError):
        return None


def _write_meta(path: Path, hours: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    data = {"created_at": now.isoformat(), "expires_at": (now + timedelta(hours=hours)).isoformat(),
            "retention_hours": hours}
    target = path / "session.json"
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o700)
        target.chmod(0o600)


def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    """Encrypt auth state for this Windows account, without a key file or password."""
    if os.name != "nt":
        raise RuntimeError("La persistencia protegida requiere Windows (DPAPI)")

    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_char))]

    source = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    outgoing = Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.restype = ctypes.c_int
    if not fn(ctypes.byref(incoming), None, None, None, None, 0, ctypes.byref(outgoing)):
        raise RuntimeError("Windows no pudo proteger o abrir la sesión local")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(outgoing.data)


def _save_auth(context, page, path: Path) -> None:
    # storage_state stays in memory and is immediately encrypted. Playwright's
    # plain auth JSON must never be written to disk or included in a report.
    state = context.storage_state()
    session = page.evaluate("() => Object.fromEntries(Object.entries(sessionStorage))")
    payload = {"state": state, "session": {ORIGIN: session}}
    target = path / "auth.dpapi"
    temporary = path / "auth.dpapi.tmp"
    temporary.write_bytes(_dpapi(json.dumps(payload).encode("utf-8")))
    os.replace(temporary, target)


def _restore_auth(context, path: Path) -> None:
    target = path / "auth.dpapi"
    if not target.is_file():
        return
    try:
        payload = json.loads(_dpapi(target.read_bytes(), decrypt=True))
        context.add_cookies(payload["state"]["cookies"])
        origins = {row["origin"]: {item["name"]: item["value"] for item in row.get("localStorage") or []}
                   for row in payload["state"].get("origins") or []}
        session = payload.get("session") or {}
        script = ("(() => { const local = " + json.dumps(origins) + "; const session = " + json.dumps(session)
                  + "; for (const [k,v] of Object.entries(local[location.origin] || {})) localStorage.setItem(k,v);"
                  + " for (const [k,v] of Object.entries(session[location.origin] || {})) sessionStorage.setItem(k,v); })();")
        context.add_init_script(script)
    except (OSError, ValueError, KeyError, RuntimeError):
        raise RuntimeError("No se pudo recuperar la sesión local; ejecuta login para crearla de nuevo") from None


def session_state(path: Path) -> str:
    if not path.exists():
        return "missing"
    expiry = _expiry(_meta(path))
    return "valid" if expiry and datetime.now(timezone.utc) < expiry else "expired"


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Instala Playwright: uv pip install --python .venv/Scripts/python.exe -r tools/requirements-symplicity.txt") from exc
    return sync_playwright


def _open_context(browser, path: Path, headed: bool):
    return browser.chromium.launch_persistent_context(
        str(path / "chrome"), channel="chrome", headless=not headed,
        accept_downloads=False, locale="es-PE",
    )


def _jobs_ready(page) -> bool:
    return page.locator(KEYWORD_SELECTOR).count() > 0


def _login_page(page) -> bool:
    return page.locator('input[type="password"]').count() > 0 and not _jobs_ready(page)


def _wait_for_login(page, seconds: int) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _jobs_ready(page):
            return True
        if "/students/app/" in page.url and "/jobs/discover" not in page.url:
            page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    return False


def _job_id(url: str, explicit: str = "") -> str:
    if explicit:
        return explicit
    parsed = urlparse(url)
    if parsed.hostname != "up-csm.symplicity.com":
        return ""
    match = re.search(r"/jobs/detail/([a-zA-Z0-9_-]+)", parsed.path)
    return (match.group(1) if match else (parse_qs(parsed.query).get("currentJobId") or [""])[0])


def _deadline(value: str, today: date | None = None) -> str | None:
    match = re.search(r"\b(?:antes del|by)\s+([A-Za-z]{3})\s+(\d{1,2})\b", value, re.I)
    if not match:
        return None
    months = {abbr: number for number, abbr in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
    month = months.get(match.group(1).title())
    if not month:
        return None
    today = today or date.today()
    year = today.year + (month < today.month - 6)
    try:
        return date(year, month, int(match.group(2))).isoformat()
    except ValueError:
        return None


def normalize_results(candidates: list[dict], limit: int) -> list[dict]:
    out, seen = [], set()
    for row in candidates:
        url = str(row.get("url") or "")
        ident = _job_id(url, str(row.get("job_id") or ""))
        title = str(row.get("title") or "").strip()
        if not ident or not title or ident in seen:
            continue
        seen.add(ident)
        if not url or urlparse(url).hostname != "up-csm.symplicity.com":
            url = RESULTS_URL + "&currentJobId=" + ident
        company = str(row.get("company") or "").strip()
        modality = None
        match = re.search(r"\s+-\s+(Híbrido|Hibrido|Remoto|Presencial)\s*$", company, re.I)
        if match:
            modality = match.group(1).lower()
            company = company[:match.start()].strip()
        company = re.sub(r"\s+\(\d{11}\)\s*$", "", company).strip()
        out.append({"id": "up:" + ident, "title": title, "company": company or None,
                    "location": str(row.get("location") or "").strip() or None,
                    "date": None, "deadline": _deadline(str(row.get("deadline_text") or "")),
                    "modality": modality,
            "url": url, "portal": "bolsa-universidad", "source": "authenticated-browser"})
        if len(out) >= limit:
            break
    return out


def normalize_applications(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        url = str(row.get("url") or "")
        ident = _job_id(url)
        if not ident or ident in seen or not row.get("applied"):
            continue
        seen.add(ident)
        out.append({"id": "up:" + ident, "title": str(row.get("title") or "").strip(),
                    "company": str(row.get("company") or "").strip(), "url": url,
                    "status": "applied", "source": "symplicity-applications"})
    return out


def normalize_resumes(rows: list[dict]) -> list[dict]:
    out, seen = [], set()
    for row in rows:
        ident = str(row.get("id") or "").lower()
        name = str(row.get("name") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{32}", ident) or not name or ident in seen:
            continue
        seen.add(ident)
        out.append({"id": ident, "name": name, "is_default": bool(row.get("is_default"))})
    return out


def validate_resume_file(value: str) -> tuple[Path, int]:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise ValueError("el CV debe ser un archivo PDF existente")
    size = path.stat().st_size
    with path.open("rb") as source:
        valid_magic = source.read(5) == b"%PDF-"
    if not 5 <= size <= MAX_RESUME_BYTES or not valid_magic:
        raise ValueError("el archivo debe ser un PDF válido de hasta 64 MB")
    return path, size


def _detail_id(value: str) -> str:
    raw = value[3:] if value.lower().startswith("up:") else value
    ident = raw if re.fullmatch(r"[0-9a-fA-F]{32}", raw) else _job_id(raw)
    if not re.fullmatch(r"[0-9a-fA-F]{32}", ident):
        raise ValueError("se requiere un ID o enlace individual válido de la Bolsa UP")
    return ident.lower()


def _section_value(section: str, label: str) -> str | None:
    lines = [line.strip() for line in section.splitlines() if line.strip()]
    for index, line in enumerate(lines[:-1]):
        if line.casefold() == label.casefold():
            return lines[index + 1]
    return None


def normalize_detail(raw: dict, ident: str) -> dict:
    process = str(raw.get("process") or "")
    deadline = None
    match = re.search(r"Fecha límite de las solicitudes\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", process, re.I)
    if match:
        months = {name: number for number, name in enumerate(
            ("january", "february", "march", "april", "may", "june", "july", "august",
             "september", "october", "november", "december"), 1)}
        month = months.get(match.group(1).lower())
        if month:
            try:
                deadline = date(int(match.group(3)), month, int(match.group(2))).isoformat()
            except ValueError:
                pass
    company = re.sub(r"\s*\(\d{11}\)\s*$", "", str(raw.get("company") or "")).strip()
    additional = str(raw.get("additional") or "")
    requirements = str(raw.get("requirements") or "")
    return {"id": "up:" + ident, "url": ORIGIN + "/students/app/jobs/detail/" + ident,
            "title": str(raw.get("title") or "").strip(), "company": company,
            "description": str(raw.get("description") or "").strip(),
            "qualifications": str(raw.get("qualifications") or "").strip(),
            "requirements": requirements.strip(), "academic_level": _section_value(requirements, "Nivel Académico"),
            "modality": _section_value(additional, "Remoto/Presencial"),
            "job_type": _section_value(additional, "Tipo de Oferta"),
            "availability": _section_value(additional, "Disponibilidad"),
            "posted_age": str(raw.get("posted_age") or "").strip() or None,
            "deadline": deadline, "source": "authenticated-browser"}


def cmd_login(args) -> int:
    path = account_dir(args.account)
    if session_state(path) == "expired":
        _safe_remove(path)
    print("Chrome abrirá la Bolsa UP. Entra tú con tu usuario, contraseña y PIN si lo pide.", file=sys.stderr)
    print("El CLI no lee ni guarda tu contraseña; la sesión de Chrome queda fuera de Git.", file=sys.stderr)
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=30000)
            if not _wait_for_login(page, args.timeout):
                print(json.dumps({"error": "No se completó el inicio de sesión en el tiempo indicado", "code": "LOGIN_TIMEOUT"}, ensure_ascii=False), file=sys.stderr)
                return 2
            _save_auth(context, page, path)
            _write_meta(path, args.remember_hours)
            print(json.dumps({"ok": True, "account": args.account, "expires_at": _meta(path)["expires_at"]}, ensure_ascii=False))
            return 0
        finally:
            context.close()


def cmd_search(args) -> int:
    path = account_dir(args.account)
    state = session_state(path)
    if state != "valid":
        if state == "expired":
            _safe_remove(path)
        print(json.dumps({"error": "Inicia sesión con 'login' antes de buscar", "code": "SESSION_EXPIRED" if state == "expired" else "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
        return 2
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=args.headed)
        try:
            _restore_auth(context, path)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.locator(KEYWORD_SELECTOR).wait_for(timeout=12000)
            except Exception:
                print(json.dumps({"error": "Symplicity cerró la sesión. Ejecuta login otra vez.", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            query = page.locator(KEYWORD_SELECTOR)
            query.fill("")
            if args.query:
                # Symplicity's Angular widget commits its model on key events;
                # Playwright fill() alone leaves the keyword uncommitted.
                query.press_sequentially(args.query, delay=50)
                page.wait_for_function("value => document.querySelector('#jobs-keyword-input')?.getAttribute('ng-reflect-model') === value",
                                       arg=args.query, timeout=5000)
                page.wait_for_timeout(350)
            page.get_by_role("button", name="Buscar", exact=True).click()
            page.wait_for_timeout(1200)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # this Angular app may keep a connection open
            if _login_page(page):
                print(json.dumps({"error": "Symplicity cerró la sesión durante la búsqueda", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            applied_query = (parse_qs(urlparse(page.url).query).get("keywords") or [""])[0]
            if args.query and unquote(applied_query) != args.query:
                print(json.dumps({"error": "La palabra clave no se aplicó en Symplicity", "code": "FILTER_NOT_APPLIED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            for target_page in range(2, args.page + 1):
                next_button = page.locator("button:has-text('Siguiente')")
                if not next_button.count() or not next_button.is_enabled():
                    break
                next_button.click()
                page.wait_for_function("target => Number(new URL(location.href).searchParams.get('page')) === target",
                                       arg=target_page, timeout=12000)
                page.locator(".list-page-job-search .list-item").first.wait_for(timeout=12000)
            candidates = page.evaluate(EXTRACT_JS)
            for row in candidates:
                page.locator(".list-page-job-search .list-item").nth(row["index"]).click()
                row["url"] = page.url
            results = normalize_results(candidates, args.limit)
            if not results and not page.get_by_text("No se encontraron resultados").count():
                print(json.dumps({"error": "No se reconocieron tarjetas de vacantes; ejecutar inspect para adaptar los selectores", "code": "EXTRACTION_UNVERIFIED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            print(json.dumps({"results": results, "query": args.query, "page": args.page,
                              "generated": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def cmd_inspect(args) -> int:
    path = account_dir(args.account)
    if session_state(path) != "valid":
        print(json.dumps({"error": "Inicia sesión antes de inspeccionar", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
        return 2
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=True)
        try:
            _restore_auth(context, path)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(JOBS_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.locator(KEYWORD_SELECTOR).wait_for(timeout=12000)
            except Exception:
                print(json.dumps({"error": "Symplicity cerró la sesión", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            # Structural hints only, from the jobs area; never serialize the
            # whole authenticated page, cookies or any profile/account panel.
            info = page.evaluate("""() => ({
              keyword: !!document.querySelector('#jobs-keyword-input'),
              jobLinks: [...document.querySelectorAll('a[href*="/jobs/"]')].slice(0,20)
                .map(a => ({href: a.getAttribute('href'), text: (a.textContent || '').trim().slice(0,90),
                             parentClass: String(a.parentElement && a.parentElement.className || '').slice(0,100)})),
              candidates: [...document.querySelectorAll('[class*="job"], [id*="job"]')].slice(0,35)
                .map(x => ({tag: x.tagName, id: x.id, className: String(x.className).slice(0,100)}))
            })""")
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def cmd_applications(args) -> int:
    path = account_dir(args.account)
    if session_state(path) != "valid":
        print(json.dumps({"error": "Inicia sesión antes de leer Solicitudes", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
        return 2
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(APPLIED_URL, wait_until="domcontentloaded", timeout=30000)
            try:
                page.locator("ul.list_job_applications").wait_for(timeout=12000)
            except Exception:
                print(json.dumps({"error": "No se pudo abrir Solicitudes; quizá caducó la sesión", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            pager = page.locator('select[aria-label="Saltar a la página"]')
            if pager.count() and pager.input_value() != "0":
                previous = page.locator("ul.list_job_applications a[href*='/jobs/detail/']").first.get_attribute("href")
                pager.select_option("0")
                page.wait_for_function("previous => document.querySelector('ul.list_job_applications a[href*=\"/jobs/detail/\"]')?.getAttribute('href') !== previous",
                                       arg=previous, timeout=12000)
            rows = []
            for _ in range(args.pages):
                rows += page.evaluate(EXTRACT_APPLIED_JS)
                next_link = page.locator("a.lst-next-btn")
                if not next_link.count() or next_link.get_attribute("disabled") is not None:
                    break
                previous = page.locator("ul.list_job_applications a[href*='/jobs/detail/']").first.get_attribute("href")
                next_link.click()
                page.wait_for_function("previous => document.querySelector('ul.list_job_applications a[href*=\"/jobs/detail/\"]')?.getAttribute('href') !== previous",
                                       arg=previous, timeout=12000)
                page.locator("ul.list_job_applications").wait_for(timeout=12000)
            applications = normalize_applications(rows)
            print(json.dumps({"results": applications, "generated": datetime.now(timezone.utc).isoformat()},
                             ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def cmd_detail(args) -> int:
    ident = _detail_id(args.id)
    path = account_dir(args.account)
    if session_state(path) != "valid":
        print(json.dumps({"error": "Inicia sesión antes de abrir la ficha", "code": "LOGIN_REQUIRED"}, ensure_ascii=False), file=sys.stderr)
        return 2
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(ORIGIN + "/students/app/jobs/detail/" + ident, wait_until="domcontentloaded", timeout=30000)
            try:
                page.locator("jobs-detail").wait_for(timeout=12000)
                page.wait_for_function("() => (document.querySelector('jobs-detail .field-widget-tinymce')?.innerText || '').trim().length > 30",
                                       timeout=12000)
            except Exception:
                print(json.dumps({"error": "No se abrió la ficha; verifica la sesión y el aviso", "code": "DETAIL_UNAVAILABLE"}, ensure_ascii=False), file=sys.stderr)
                return 2
            raw = page.evaluate(EXTRACT_DETAIL_JS)
            detail = normalize_detail(raw or {}, ident)
            if not detail["title"] or not detail["description"]:
                print(json.dumps({"error": "La ficha no contiene descripción verificable", "code": "EXTRACTION_UNVERIFIED"}, ensure_ascii=False), file=sys.stderr)
                return 2
            print(json.dumps(detail, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def _resumes_page(context):
    page = context.pages[0] if context.pages else context.new_page()
    page.goto(RESUMES_URL, wait_until="domcontentloaded", timeout=30000)
    try:
        page.wait_for_function("() => [...document.querySelectorAll('h1')].some(h => (h.textContent || '').includes('Currículum'))",
                               timeout=12000)
    except Exception:
        raise RuntimeError("No se abrió Mis documentos; quizá caducó la sesión") from None
    return page


def _resumes(page) -> list[dict]:
    return normalize_resumes(page.evaluate(EXTRACT_RESUMES_JS))


def _application_modal(page, ident: str) -> None:
    page.goto(ORIGIN + "/students/app/jobs/detail/" + ident, wait_until="domcontentloaded", timeout=30000)
    page.get_by_role("button", name="Solicitar", exact=True).wait_for(timeout=12000)
    pages_before = len(page.context.pages)
    page.get_by_role("button", name="Solicitar", exact=True).click()
    try:
        page.locator('select[id*="resume"]').wait_for(timeout=12000)
    except Exception:
        pages = [page] + page.context.pages[pages_before:]
        if any(urlparse(p.url).hostname not in (None, urlparse(ORIGIN).hostname) for p in pages):
            raise RuntimeError("La vacante abrió un formulario externo; completa la postulación en la página del empleador") from None
        raise RuntimeError("No se abrió el formulario de CV de la Bolsa UP; revisa la ficha") from None


def _application_options(page) -> list[dict]:
    # The option's internal ID differs from the document ID in Mis documentos.
    # Return only names so the two identifiers cannot be confused.
    return page.locator('select[id*="resume"]').evaluate("""select => [...select.options]
      .filter(option => /[0-9a-f]{32}/i.test(option.value || ''))
      .map(option => ({name: option.textContent.trim()}))
      .filter(option => option.name)""")


def cmd_resumes(args) -> int:
    path = account_dir(args.account)
    if session_state(path) != "valid":
        raise RuntimeError("Inicia sesión antes de leer Mis documentos")
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            rows = _resumes(_resumes_page(context))
            print(json.dumps({"results": rows, "count": len(rows), "limit": None}, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def cmd_application_options(args) -> int:
    ident = _detail_id(args.job)
    path = account_dir(args.account)
    if session_state(path) != "valid":
        raise RuntimeError("Inicia sesión antes de abrir la solicitud")
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            page = _resumes_page(context)
            expected = len(_resumes(page))
            _application_modal(page, ident)
            if expected:
                page.wait_for_function("count => Array.from(document.querySelector('select[id*=\"resume\"]')?.options || []).filter(o => /[0-9a-f]{32}/i.test(o.value)).length >= count",
                                       arg=expected, timeout=8000)
            print(json.dumps({"job_id": "up:" + ident, "resumes": _application_options(page),
                              "can_upload_new": bool(page.get_by_role("button", name="Agregar un nuevo currículum").count()),
                              "submitted": False}, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def _is_applied(page, ident: str) -> bool:
    """Check the UP application list before and after sending to avoid duplicates."""
    page.goto(APPLIED_URL, wait_until="domcontentloaded", timeout=30000)
    page.locator("ul.list_job_applications").wait_for(timeout=12000)
    page.locator("ul.list_job_applications > li.list-item").first.wait_for(timeout=12000)
    pager = page.locator(".lst-jumper select")
    if pager.count():
        first_page = pager.locator("option").first.get_attribute("value")
        if first_page is not None and pager.input_value() != first_page:
            previous = page.locator("ul.list_job_applications a[href*='/jobs/detail/']").first.get_attribute("href")
            pager.select_option(first_page)
            page.wait_for_function("previous => document.querySelector('ul.list_job_applications a[href*=\"/jobs/detail/\"]')?.getAttribute('href') !== previous",
                                   arg=previous, timeout=12000)
    for _ in range(20):
        if any(row["id"] == "up:" + ident for row in normalize_applications(page.evaluate(EXTRACT_APPLIED_JS))):
            return True
        next_link = page.locator("a.lst-next-btn")
        if not next_link.count() or next_link.get_attribute("disabled") is not None:
            return False
        previous = page.locator("ul.list_job_applications a[href*='/jobs/detail/']").first.get_attribute("href")
        next_link.click()
        page.wait_for_function("previous => document.querySelector('ul.list_job_applications a[href*=\"/jobs/detail/\"]')?.getAttribute('href') !== previous",
                               arg=previous, timeout=12000)
    return False


def cmd_submit_application(args) -> int:
    """Select one stored CV and send exactly one application; verify it in Solicitudes."""
    ident = _detail_id(args.job)
    label = args.resume.strip()
    if not label:
        raise ValueError("--resume debe indicar el nombre exacto del CV guardado")
    path = account_dir(args.account)
    if session_state(path) != "valid":
        raise RuntimeError("Inicia sesión antes de enviar la solicitud")
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            page = context.pages[0] if context.pages else context.new_page()
            if _is_applied(page, ident):
                print(json.dumps({"job_id": "up:" + ident, "already_applied": True,
                                  "submitted_now": False}, ensure_ascii=False))
                return 0
            if not args.confirm:
                print(json.dumps({"job_id": "up:" + ident, "resume": label,
                                  "submitted_now": False}, ensure_ascii=False))
                return 0
            _application_modal(page, ident)
            select = page.locator('select[id*="resume"]')
            options = select.evaluate("""el => [...el.options]
              .filter(o => /[0-9a-f]{32}/i.test(o.value || ''))
              .map(o => ({name: o.textContent.trim(), value: o.value}))""")
            matches = [option for option in options if option["name"].casefold() == label.casefold()]
            if len(matches) != 1:
                raise ValueError("el CV no aparece una sola vez en el formulario; revisa application-options")
            select.select_option(value=matches[0]["value"])
            send = page.locator('.modal-footer button.btn_primary:has-text("Enviar")').last
            send.wait_for(state="visible", timeout=12000)
            if not send.is_enabled():
                raise RuntimeError("Enviar sigue deshabilitado; revisa los campos obligatorios")
            send.click()
            try:
                page.locator('select[id*="resume"]').wait_for(state="hidden", timeout=15000)
            except Exception:
                pass  # The application list is the definitive confirmation.
            # Symplicity can keep an old Angular application list in this tab after Enviar.
            # Reopen Chrome's local profile to verify the server's current state.
            context.close()
            context = _open_context(browser, path, headed=False)
            _restore_auth(context, path)
            page = context.pages[0] if context.pages else context.new_page()
            verified = False
            for attempt in range(6):
                if _is_applied(page, ident):
                    verified = True
                    break
                if attempt < 5:
                    page.wait_for_timeout(1500)
            if not verified:
                raise RuntimeError("Se pulsó Enviar, pero no se confirmó en Solicitudes; revisa la bolsa antes de repetir")
            print(json.dumps({"job_id": "up:" + ident, "resume": label,
                              "submitted_now": True, "verified_in_applications": True}, ensure_ascii=False))
            return 0
        finally:
            context.close()


def cmd_upload_resume(args) -> int:
    ident = _detail_id(args.job)
    pdf, size = validate_resume_file(args.file)
    label = args.label.strip()
    if not 1 <= len(label) <= 70 or any(c in label for c in "\r\n\t"):
        raise ValueError("--label debe tener 1–70 caracteres en una sola línea")
    path = account_dir(args.account)
    if session_state(path) != "valid":
        raise RuntimeError("Inicia sesión antes de cargar el CV")
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            page = _resumes_page(context)
            before = _resumes(page)
            if any(row["name"].casefold() == label.casefold() for row in before):
                raise ValueError("ya existe un CV con ese nombre; usa otro nombre o revisa Mis documentos")
            if not args.confirm:
                print(json.dumps({"action": "upload_resume", "label": label, "file": pdf.name,
                                  "size_bytes": size, "stored": len(before), "limit": None,
                                  "uploaded": False, "application_submitted": False}, ensure_ascii=False))
                return 0
            _application_modal(page, ident)
            page.get_by_role("button", name="Agregar un nuevo currículum").click()
            page.locator('input[type="file"]').set_input_files(str(pdf))
            page.locator("#doc-label").fill(label)
            save = page.locator('.modal-footer button.btn_primary:has-text("Guardar")').last
            save.wait_for(state="visible", timeout=12000)
            page.wait_for_function("() => [...document.querySelectorAll('.modal-footer button.btn_primary')].some(b => b.textContent.trim() === 'Guardar' && !b.disabled)",
                                   timeout=30000)
            save.click()
            try:
                page.locator("#doc-label").wait_for(state="hidden", timeout=12000)
            except Exception:
                raise RuntimeError("La bolsa no confirmó Guardar; revisa Mis documentos antes de repetir") from None
            # Never click Enviar. Confirm only that the new document appears in Mis documentos.
            page = _resumes_page(context)
            after = _resumes(page)
            added = [row for row in after if row["name"].casefold() == label.casefold()]
            if len(added) != 1:
                raise RuntimeError("La carga no quedó verificada; revisa Mis documentos antes de repetir")
            print(json.dumps({"uploaded": True, "resume": added[0], "stored": len(after),
                              "application_submitted": False}, ensure_ascii=False, indent=2))
            return 0
        finally:
            context.close()


def cmd_delete_resume(args) -> int:
    path = account_dir(args.account)
    if session_state(path) != "valid":
        raise RuntimeError("Inicia sesión antes de revisar los CV")
    sync_playwright = _playwright()
    with sync_playwright() as browser:
        context = _open_context(browser, path, headed=False)
        try:
            _restore_auth(context, path)
            page = _resumes_page(context)
            before = _resumes(page)
            matches = [row for row in before if row["id"] == (args.id or "").lower()] if args.id else [
                row for row in before if row["name"].casefold() == args.name.casefold()]
            if len(matches) != 1:
                raise ValueError("no hay un único CV con ese identificador o nombre; revisa 'resumes'")
            target = matches[0]
            if target["is_default"]:
                raise ValueError("no se borra el CV predeterminado; cambia primero el predeterminado en la bolsa")
            if not args.confirm:
                print(json.dumps({"action": "delete_resume", "resume": target,
                                  "stored": len(before), "deleted": False}, ensure_ascii=False))
                return 0
            row = page.locator(".list-item").filter(has=page.locator(f'a[href*="id={target["id"]}"]'))
            delete = row.locator('.list-item-actions-bar a[onclick^="confirmResDel"]')
            if row.count() != 1 or delete.count() != 1 or target["id"] not in (delete.get_attribute("onclick") or ""):
                raise RuntimeError("No se pudo identificar con certeza el control de borrado del CV")
            page.once("dialog", lambda dialog: dialog.accept())
            delete.click()
            page.wait_for_load_state("domcontentloaded", timeout=12000)
            page = _resumes_page(context)
            after = _resumes(page)
            if any(entry["id"] == target["id"] for entry in after):
                raise RuntimeError("El CV sigue en Mis documentos; no se confirmó el borrado")
            print(json.dumps({"deleted": True, "resume": target, "stored": len(after)}, ensure_ascii=False))
            return 0
        finally:
            context.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bolsa UP: sesión local y búsqueda de vacantes")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="abrir Chrome para entrar manualmente")
    login.add_argument("--account", default="default")
    login.add_argument("--remember-hours", type=int, default=24, help="duración local elegida, 1–720 h (24 por defecto)")
    login.add_argument("--timeout", type=int, default=300, help="segundos para completar el acceso")
    search = sub.add_parser("search", help="buscar con la sesión ya iniciada")
    search.add_argument("--account", default="default")
    search.add_argument("-q", "--query", default="", help="palabra clave; omitir para ver todas por fecha")
    search.add_argument("-n", "--limit", type=int, default=20)
    search.add_argument("--page", type=int, default=1, help="página de resultados (1 a 10)")
    search.add_argument("--headed", action="store_true", help="mostrar Chrome al buscar")
    inspect = sub.add_parser("inspect", help="ver selectores de vacantes, sin datos de sesión")
    inspect.add_argument("--account", default="default")
    applications = sub.add_parser("applications", help="leer Solicitudes ya enviadas, sin modificarlas")
    applications.add_argument("--account", default="default")
    applications.add_argument("--pages", type=int, default=5)
    detail = sub.add_parser("detail", help="leer descripción, requisitos y cierre de una ficha")
    detail.add_argument("id", help="ID, up:ID o enlace individual")
    detail.add_argument("--account", default="default")
    resumes = sub.add_parser("resumes", help="listar los CV guardados sin exponer sus enlaces PDF firmados")
    resumes.add_argument("--account", default="default")
    options = sub.add_parser("application-options", help="ver los CV seleccionables para una vacante sin postular")
    options.add_argument("job", help="ID o enlace individual")
    options.add_argument("--account", default="default")
    upload = sub.add_parser("upload-resume", help="agregar un PDF a Mis documentos desde una vacante")
    upload.add_argument("job", help="ID o enlace individual")
    upload.add_argument("file", help="PDF local")
    upload.add_argument("--label", required=True, help="nombre para reconocer el CV en la bolsa")
    upload.add_argument("--account", default="default")
    upload.add_argument("--confirm", action="store_true", help="cargarlo realmente; por defecto solo muestra el plan")
    submit = sub.add_parser("submit-application", help="enviar una postulación con un CV guardado y verificarla en Solicitudes")
    submit.add_argument("job", help="ID o enlace individual")
    submit.add_argument("--resume", required=True, help="nombre exacto del CV guardado")
    submit.add_argument("--account", default="default")
    submit.add_argument("--confirm", action="store_true", help="enviar realmente; por defecto solo muestra el plan")
    delete = sub.add_parser("delete-resume", help="borrar un CV guardado por nombre o ID exacto")
    choose = delete.add_mutually_exclusive_group(required=True)
    choose.add_argument("--name", help="nombre exacto")
    choose.add_argument("--id", help="ID del comando resumes")
    delete.add_argument("--account", default="default")
    delete.add_argument("--confirm", action="store_true", help="borrarlo realmente; por defecto solo muestra el plan")
    status = sub.add_parser("status", help="ver si la sesión local sigue dentro de su plazo")
    status.add_argument("--account", default="default")
    logout = sub.add_parser("logout", help="borrar la sesión y el perfil local")
    logout.add_argument("--account", default="default")
    args = parser.parse_args(argv)
    try:
        path = account_dir(args.account)
        if args.command == "login":
            if not 1 <= args.remember_hours <= 720 or not 30 <= args.timeout <= 900:
                parser.error("--remember-hours debe ser 1–720 y --timeout 30–900")
            return cmd_login(args)
        if args.command == "search":
            if not 1 <= args.limit <= 20 or not 1 <= args.page <= 10:
                parser.error("--limit debe ser 1–20 y --page 1–10")
            return cmd_search(args)
        if args.command == "inspect":
            return cmd_inspect(args)
        if args.command == "applications":
            if not 1 <= args.pages <= 20:
                parser.error("--pages debe ser 1–20")
            return cmd_applications(args)
        if args.command == "detail":
            return cmd_detail(args)
        if args.command == "resumes":
            return cmd_resumes(args)
        if args.command == "application-options":
            return cmd_application_options(args)
        if args.command == "upload-resume":
            return cmd_upload_resume(args)
        if args.command == "submit-application":
            return cmd_submit_application(args)
        if args.command == "delete-resume":
            return cmd_delete_resume(args)
        if args.command == "logout":
            _safe_remove(path)
            print(json.dumps({"ok": True, "state": "removed"}))
            return 0
        print(json.dumps({"account": args.account, "state": session_state(path),
                          "expires_at": _meta(path).get("expires_at")}, ensure_ascii=False))
        return 0
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "code": "LOCAL_ERROR"}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception:
        # Browser errors may include an SSO redirect URL with a one-time token.
        # Do not echo exception text or a traceback to logs or other agents.
        print(json.dumps({"error": "Falló el navegador o cambió la página; vuelve a intentar con Chrome visible.",
                          "code": "BROWSER_ERROR"}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
