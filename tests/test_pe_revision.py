"""Tests for tools/pe_revision.py: the review page built from the workflow's files (Peru fork)."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pe_revision", REPO / "tools" / "pe_revision.py")
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

BM_SEARCH = "https://www.bumeran.com.pe/empleos/asistente-de-seleccion-acme-1118482096.html"
BM_DETAIL = "https://www.bumeran.com.pe/empleos/asistente-de-seleccion-acme-s-a-1118482096.html"


def ranked(**over):
    base = {
        "title": "Asistente de Selección", "company": "Acme", "url": BM_SEARCH, "status": "ranked",
        "rank_score": 70, "rank_verdict": "Good Fit", "location_verdict": "PASS", "language_gate": "PASS",
        "modality": "híbrido", "level": "asistente", "portal": "bumeran-search",
        "strengths": ["1 año en R&S"], "gaps": ["Pide bachiller"],
    }
    base.update(over)
    return base


class Actions(unittest.TestCase):
    def test_applied_takes_precedence_over_deadline_and_freshness(self):
        entry = ranked(application_status="applied", applied_date="2026-10-24", deadline="2026-10-25")
        self.assertEqual(pr.action_for(entry, {"risk": "bajo", "window": "fresca"}, date(2026, 10, 24)), "postulada")

    def test_shortlist_actions_follow_freshness(self):
        good = ranked()
        self.assertEqual(pr.action_for(good, {"risk": "bajo", "window": "fresca"}), "ya")
        self.assertEqual(pr.action_for(good, {"risk": "bajo", "window": "en curso"}), "pronto")
        self.assertEqual(pr.action_for(good, {"risk": "medio", "window": "vieja"}), "confirmar")
        self.assertEqual(pr.action_for(good, {"risk": "alto", "window": "muy vieja"}), "antes")
        self.assertEqual(pr.action_for(good, None), "pronto")  # freshness not checked: no promise of "ya"

    def test_lower_verdicts_and_vetoes(self):
        self.assertEqual(pr.action_for(ranked(rank_verdict="Moderate Fit"), None), "tiempo")
        self.assertEqual(pr.action_for(ranked(rank_verdict="Weak Fit"), None), "descartar")
        self.assertEqual(pr.action_for(ranked(location_verdict="FAIL"), {"risk": "bajo", "window": "fresca"}), "descartar")
        self.assertEqual(pr.action_for(ranked(language_gate="FAIL"), None), "descartar")
        self.assertEqual(pr.action_for(ranked(location_verdict="FLAG"), {"window": "fresca"}), "antes")

    def test_formal_eligibility_overrides_freshness_and_an_imminent_deadline(self):
        fresh = {"risk": "bajo", "window": "fresca"}
        today = date(2026, 10, 25)
        self.assertEqual(pr.action_for(ranked(eligibility_gate="FLAG", deadline="2026-10-28"), fresh, today), "antes")
        self.assertEqual(pr.action_for(ranked(eligibility_gate="FAIL", deadline="2026-10-28"), fresh, today), "descartar")

    def test_expired_public_role_remains_visible_as_closed(self):
        today = date(2026, 10, 25)
        archived = ranked(title="Asistente de difusión ONPE", company="ONPE", status="expired",
                          deadline="2026-10-25", location_verdict="FAIL", focus_tags=["campo", "estado"],
                          screening_note="Cumple estudios y experiencia; plazo vencido")
        data = pr.collect({"seen": {"onpe": archived}}, [], Path(tempfile.mkdtemp()), today=today)
        # Exact-hour deadlines need a separate status after 18:00; the status is
        # authoritative even though an ISO date alone still reads as today.
        self.assertEqual(data["ranked"][0]["action"], "cerrada")
        page = pr.render(data, today, "César")
        self.assertIn("Ver 1 convocatoria pública cerrada", page)
        self.assertIn('id="sec-cerradas"', page)
        self.assertIn("Asistente de difusión ONPE", page)
        self.assertEqual(pr.export(data, today, "César")["cerradas"][0]["screening_note"], archived["screening_note"])


class Ratings(unittest.TestCase):
    def test_missing_research_and_missing_ratings_read_differently(self):
        self.assertIn("sin investigar", pr.rating_html(None))
        self.assertIn("no es mala señal", pr.rating_html([]))

    def test_every_rating_shows_its_count_and_small_or_missing_counts_are_referential(self):
        big = pr.rating_html([{"source": "Computrabajo", "rating": 4.26, "n": 20188, "area": "Recursos Humanos", "area_n": 12}])
        self.assertIn("4,26", big)
        self.assertIn("n=20.188", big)
        self.assertIn("12 opiniones · referencial", big)
        self.assertNotIn('<span class="tag">referencial</span>', big)
        small = pr.rating_html([{"source": "Indeed", "rating": 4.0, "n": 12}])
        self.assertIn("referencial", small)
        self.assertIn("<b>4,0</b>", small)
        self.assertEqual(pr.fmt_rating(3.9), "3,9")
        no_n = pr.rating_html([{"source": "Computrabajo", "rating": 4.2, "n": None}])
        self.assertIn("sin n", no_n)
        self.assertIn("referencial", no_n)


class Rendering(unittest.TestCase):
    def test_servir_word_is_embedded_only_for_matching_public_posting(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "reports" / "detalles"
            folder.mkdir(parents=True)
            (folder / "sv_0123456789abcdef.docx").write_bytes(b"PK\x03\x04public summary")
            with patch.object(pr, "ROOT", Path(tmp)):
                row = ranked(id="SERVIR:0123456789ABCDEF", portal="talento-peru-search")
                self.assertIn("Descargar resumen (Word)", pr.word_html(row))
                self.assertEqual(pr.word_html({**row, "id": "SERVIR:ffffffffffffffff"}), "")

    def test_role_shortcuts_classify_field_work_without_sales_field_noise(self):
        self.assertIn("campo", pr.role_tags(ranked(title="Encuestador de campo")))
        self.assertIn("campo", pr.role_tags(ranked(title="Personal de campo para investigación de mercado")))
        self.assertEqual(pr.role_tags(ranked(title="Supervisor de ventas de campo")), ["otros"])
        self.assertEqual(pr.role_tags(ranked(title="Encuestador y vendedor de campo")), ["otros"])
        self.assertIn("rrhh", pr.role_tags(ranked(title="Auxiliar de reclutamiento y selección")))
        self.assertIn("practicas", pr.role_tags(ranked(title="Practicante de recursos humanos")))
        self.assertIn("estado", pr.role_tags(ranked(portal="talento-peru-search")))

    def test_role_shortcuts_are_counted_and_initial_focus_is_configurable(self):
        seen = {"field": ranked(title="Encuestador de Campo"),
                "hr": ranked(title="Asistente de Recursos Humanos", url="https://www.linkedin.com/jobs/view/4476949303")}
        data = pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp()))
        data["nuevas"] = {"nuevas": [{"id": "SERVIR:123", "portal": "talento-peru",
                                      "title": "Practicante de psicología", "company": "Municipalidad",
                                      "url": "https://app.servir.gob.pe/"}]}
        page = pr.render(data, date(2026, 10, 25), "César", initial_focus="campo")
        self.assertIn('data-role-filter="campo" aria-pressed="false">Trabajo de campo<small>1</small>', page)
        self.assertIn('data-role-filter="rrhh" aria-pressed="false">RR. HH.<small>1</small>', page)
        self.assertIn('data-role-filter="estado" aria-pressed="false">Sector público<small>0</small>', page)
        self.assertIn('data-show-portal="talento-peru">Ver 1 nueva de SERVIR sin evaluar', page)
        self.assertIn('data-initial-focus="campo"', page)
        self.assertIn('data-roles="campo"', page)
        self.assertIn('data-roles="rrhh"', page)

    def build(self, seen, fresh=(), research=None):
        tmp = Path(tempfile.mkdtemp())
        research_dir = tmp / "company_research"
        research_dir.mkdir()
        for name, doc in (research or {}).items():
            (research_dir / pr.company_file(name)).write_text(json.dumps(doc), encoding="utf-8")
        data = pr.collect({"seen": seen}, list(fresh), research_dir)
        return data, pr.render(data, date(2026, 10, 18), "Prueba")

    def test_freshness_joins_on_the_posting_id_even_when_urls_differ(self):
        data, page = self.build(
            {"k": ranked()},
            fresh=[{"url": BM_DETAIL, "risk": "bajo", "window": "fresca", "age_days": 3, "signals": [], "original_date": "2026-10-15"}],
        )
        self.assertEqual(data["ranked"][0]["action"], "ya")
        self.assertIn("publicada 2026-10-15", page)

    def test_applied_posting_is_in_its_own_section_and_json(self):
        data, page = self.build({"k": ranked(application_status="applied", applied_date="2026-10-24")})
        exported = pr.export(data, date(2026, 10, 24), "Prueba")
        self.assertIn("Postuladas (1)", page)
        self.assertIn("Postulación registrada", page)
        self.assertEqual(exported["counts"]["postuladas"], 1)
        self.assertEqual(exported["para_decidir"], [])
        self.assertEqual(exported["postuladas"][0]["applied_date"], "2026-10-24")

    def test_values_from_the_state_are_escaped_and_only_http_links_render(self):
        evil = ranked(company='<script>alert(1)</script>', title='T "x"', url="javascript:alert(1)",
                      gaps=["<img src=x onerror=alert(1)>"])
        _, page = self.build({"k": evil})
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn('href="javascript:', page)
        self.assertNotIn("<img src=x", page)

    def test_pay_and_schedule_warnings_are_separate_from_fit_gaps(self):
        row = ranked(gaps=[], warnings=["Confirmar horario y subvención"])
        data, page = self.build({"k": row})
        self.assertIn("Advertencias</h4>", page)
        self.assertIn("Confirmar horario y subvención", page)
        self.assertEqual(pr.export(data, date(2026, 10, 18), "Prueba")["para_decidir"][0]["warnings"],
                         ["Confirmar horario y subvención"])

    def test_public_posting_links_distinguish_secondary_card_from_entity_portal(self):
        row = ranked(url="https://www.practicas.pe/oferta-123.html",
                     official_link="https://www.perupetro.com.pe/trabaja", focus_tags=["estado"])
        data, page = self.build({"k": row})
        self.assertIn("Ver ficha de Prácticas.pe", page)
        self.assertIn("Portal de la entidad", page)
        self.assertIn("estado", pr.export(data, date(2026, 10, 18), "Prueba")["para_decidir"][0]["tags"])

    def test_order_sections_and_flow_counts(self):
        seen = {
            "a": ranked(rank_score=60),
            "b": ranked(rank_score=74, url="https://www.linkedin.com/jobs/view/4476949303", company="B"),
            "c": ranked(rank_verdict="Poor Fit", rank_score=20, company="C", url="https://www.linkedin.com/jobs/view/1"),
            "d": {"title": "Sin evaluar", "company": "D", "url": "https://x.example/1", "status": "new", "fit": "medium", "posted_date": "2026-10-17"},
            "e": {"title": "Irrelevante", "company": "E", "url": "https://x.example/2", "status": "new", "fit": "low"},
        }
        data, page = self.build(seen, research={"B": {"sources": {"reviews": {"ratings": [{"source": "Computrabajo", "rating": 4.1, "n": 200}]}}}})
        self.assertEqual([r["key"] for r in data["ranked"]], ["b", "a", "c"])
        self.assertEqual([r["key"] for r in data["unranked"]], ["d"])
        self.assertEqual(data["rated"], 1)
        self.assertIn("Para decidir (2)", page)
        self.assertIn("Descartadas o no elegibles (1)", page)
        self.assertIn("sin evaluar (1)", page)


class Cli(unittest.TestCase):
    def test_writes_the_page_without_freshness_or_research(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "seen.json").write_text(json.dumps({"seen": {"k": ranked()}}), encoding="utf-8")
            out = tmp / "r" / "revision.html"
            proc = subprocess.run(
                [sys.executable, str(REPO / "tools" / "pe_revision.py"), "--state", str(tmp / "seen.json"),
                 "--frescura", str(tmp / "none.json"), "--research", str(tmp / "none"), "--out", str(out),
                 "--today", "2026-10-18"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("1 evaluadas", proc.stdout)
            page = out.read_text(encoding="utf-8")
            self.assertTrue(page.startswith("<!doctype html>"))
            self.assertNotIn("http://cdn", page)
            self.assertNotIn("<script src", page)


class ContextAndSorting(unittest.TestCase):
    CTX = {
        "url": BM_DETAIL,
        "lugar": {"districts": ["Santiago de Surco"], "zones": ["Lima Moderna"], "source": "portal", "confidence": "media",
                  "residence_required": [], "remote": False},
        "rubro": {"label": "Consultora de Recursos Humanos", "source": "portal", "confidence": "media",
                  "staffing": True, "for_client": False, "anonymous": False},
        "links": {"computrabajo": "https://pe.computrabajo.com/adecco", "linkedin": "javascript:alert(1)"},
    }

    def test_place_and_sector_render_as_chips_with_readable_evidence_and_a_map(self):
        html = pr.context_html(self.CTX)
        self.assertIn('data-set-filter="district" data-value="Santiago de Surco"', html)
        self.assertNotIn("Lima Moderna", html)  # the zone lives in the filter, not on every row
        # the sector already says "Consultora": no second chip repeating it
        self.assertNotIn('data-value="Consultora"', html)
        other = pr.context_html(dict(self.CTX, rubro=dict(self.CTX["rubro"], label="Servicios Profesionales")))
        self.assertIn('data-set-filter="kind" data-value="Consultora"', other)
        self.assertIn("dato del portal", html)
        self.assertIn("●●○", html)
        self.assertNotIn("google.com/maps", html)  # a district alone is not where the job is: no map
        with_address = pr.context_html(dict(self.CTX, lugar=dict(self.CTX["lugar"], address="Av. Circunvalación Club Golf Los Incas 206")))
        self.assertIn("query=Av.+Circunvalaci%C3%B3n+Club+Golf+Los+Incas+206%2C+Santiago+de+Surco%2C+Lima%2C+Per%C3%BA", with_address)
        self.assertIn(">Computrabajo</a>", html)
        self.assertNotIn("javascript:", html)  # a non-http link from the cache never becomes a button

    def test_work_mode_is_a_coloured_pill(self):
        self.assertIn('<span class="pill mod-hib">Híbrido</span>', pr.meta_html(ranked()))
        self.assertIn('<span class="pill mod-rem">Remoto</span>', pr.meta_html(ranked(modality="remoto")))
        self.assertIn('<span class="pill mod-nd">No declarado</span>', pr.meta_html(ranked(modality="no declarado")))
        self.assertIn('<span class="pill lvl">asistente</span>', pr.meta_html(ranked()))

    def test_remote_postings_get_no_map(self):
        ctx = dict(self.CTX, lugar=dict(self.CTX["lugar"], remote=True, address="Av. Larco 1"))
        self.assertNotIn("maps", pr.context_html(ctx))
        self.assertIn('<span class="chip kind">Remoto</span>', pr.context_html(ctx))

    def test_quick_filters_use_real_posting_values_and_show_latest_search(self):
        data = pr.collect({"seen": {"k": ranked()}}, [], Path(tempfile.mkdtemp()))
        data["ranked"][0]["context"] = dict(self.CTX, rubro={**self.CTX["rubro"], "for_client": True})
        data["cobertura"] = {"fecha": "2026-10-24", "portales": [
            {"id": "practicas-pe", "nombre": "Prácticas.pe", "metodo": "cli", "ok": 3,
             "consultas": 3, "fallidas": 0, "resultados": 80, "fecha": "2026-10-24"}]}
        page = pr.render(data, date(2026, 10, 24), None)
        for term in ('Última búsqueda: <strong>24/10/2026 · Prácticas.pe', 'id="f-district"',
                     'value="Santiago de Surco"', 'id="f-sector"', 'value="Consultora de Recursos Humanos"',
                     'id="f-kind"', 'value="Recluta para un cliente"', 'class="fmod" value="híbrido"',
                     'data-districts="Santiago de Surco"', 'data-sector="Consultora de Recursos Humanos"',
                     'data-kinds="Recluta para un cliente"'):
            self.assertIn(term, page)

    def test_cards_carry_sort_keys_and_the_order_menu_offers_them(self):
        seen = {"k": ranked(rank_score=71)}
        fresh = [{"url": BM_DETAIL, "risk": "bajo", "window": "fresca", "age_days": 3, "signals": []}]
        research = Path(tempfile.mkdtemp())
        (research / pr.company_file("Acme")).write_text(json.dumps({"sources": {"reviews": {"ratings": [{"source": "Computrabajo", "rating": 4.2, "n": 300}]}}}), encoding="utf-8")
        page = pr.render(pr.collect({"seen": seen}, fresh, research, [self.CTX]), date(2026, 10, 18), None)
        self.assertIn('data-s-encaje="71"', page)
        self.assertIn('data-s-frescura="3"', page)
        self.assertIn('data-s-reputacion="4.2"', page)
        for key in ("accion", "encaje", "puesto", "frescura", "reputacion", "cierre", "modalidad"):
            self.assertIn(f'<option value="{key}:', page)
        self.assertIn("chip place", page)


class ArtifactMode(unittest.TestCase):
    def test_artifact_mode_drops_the_wrapper_and_declares_the_gutter(self):
        data = pr.collect({"seen": {"k": ranked()}}, [], Path(tempfile.mkdtemp()))
        page = pr.render(data, date(2026, 10, 18), "César", artifact=True)
        self.assertTrue(page.startswith("<title>Ofertas de César</title>"))
        for tag in ("<!doctype", "<html", "<head>", "<body", "</body>"):  # "<head" would match <header>
            self.assertNotIn(tag, page.lower())
        self.assertIn('<meta name="claude:viewport-fit-sides" content="cover">', page)
        self.assertIn("max(16px,env(safe-area-inset-left,0px))", page)

    def test_both_modes_follow_the_host_theme_toggle(self):
        data = pr.collect({"seen": {}}, [], Path(tempfile.mkdtemp()))
        for artifact in (False, True):
            page = pr.render(data, date(2026, 10, 18), None, artifact=artifact)
            self.assertIn(':root:not([data-theme="light"])', page)
            self.assertIn(':root[data-theme="dark"]', page)
            self.assertIn("c.hidden=!ok", page)


class Deadlines(unittest.TestCase):
    TODAY = date(2026, 10, 20)

    def test_labels_and_buckets(self):
        cases = {"2026-10-20": ("HOY", "hoy"), "2026-10-21": ("mañana · 21/10", "3d"), "2026-10-25": ("en 5 días · 25/10", "7d"),
                 "2026-11-15": ("15/11", "mas"), "2026-10-18": ("venció el 18/10", "vencida"), None: ("—", "sin")}
        for deadline, (label, bucket) in cases.items():
            with self.subTest(deadline=deadline):
                info = pr.deadline_info({"deadline": deadline}, self.TODAY)
                self.assertEqual((info["label"], info["bucket"]), (label, bucket))

    def test_a_close_deadline_makes_a_shortlisted_posting_urgent(self):
        entry = ranked(deadline="2026-10-21")
        self.assertEqual(pr.action_for(entry, {"risk": "medio", "window": "vieja"}, self.TODAY), "ya")
        self.assertEqual(pr.action_for(ranked(rank_verdict="Moderate Fit", deadline="2026-10-20"), None, self.TODAY), "tiempo")

    def test_the_page_lists_what_closes_this_week_with_how_to_apply(self):
        seen = {"a": ranked(deadline="2026-10-20", how_to_apply="Formulario del BCRP"), "b": ranked(title="Otra", url=None)}
        data = pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp()), today=self.TODAY)
        page = pr.render(data, self.TODAY, None)
        self.assertIn('Esta semana cierran 1</h2>', page)
        self.assertIn('<button type="button" class="soon-card urgent" data-goto="a"', page)
        self.assertNotIn('class="soon-more"', page)  # one card: nothing to fold
        self.assertIn('<span class="close today">HOY</span>', page)
        self.assertIn("Formulario del BCRP", page)
        self.assertIn('id="fc"', page)
        self.assertIn('id="cnt"', page)


class Contacts(unittest.TestCase):
    def test_area_comes_from_the_title_without_level_words(self):
        self.assertEqual(pr.area_of("Practicante - Dpto. de Investigación Financiera"), "Investigación Financiera")
        self.assertEqual(pr.area_of("Practicante Pre Profesional de Riesgo de Mercado y Liquidez"), "Riesgo de Mercado y Liquidez")
        self.assertEqual(pr.area_of("Programa de Prácticas 2027"), "")

    def test_links_use_the_company_people_page_and_the_named_contact(self):
        r = ranked(company="BTG Pactual Perú", title="Practicante Riesgo de Mercado y Liquidez", contact="Ana Pérez",
                   context={"links": {"linkedin": "https://pe.linkedin.com/company/btgpactual"}})
        html = pr.contacts_html(r)
        self.assertIn("https://www.linkedin.com/company/btgpactual/people/?keywords=Riesgo+de+Mercado+y+Liquidez", html)
        self.assertIn("search/results/people/?keywords=Ana+P%C3%A9rez+BTG+Pactual+Per%C3%BA", html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_search_names_drop_legal_suffixes_and_prefer_the_acronym(self):
        self.assertEqual(pr.company_query("Banco Central de Reserva del Perú (BCRP)"), "BCRP")
        self.assertEqual(pr.company_query("Credicorp Ltd."), "Credicorp")
        self.assertEqual(pr.company_query("ORYGEN PERÚ S.A.A."), "ORYGEN PERÚ")
        self.assertEqual(pr.company_query("Touch Consulting S.A.C"), "Touch Consulting")
        self.assertEqual(pr.area_of("Practicante de Planeamiento Financiero y Gestión de Portafolio"), "Planeamiento Financiero y Gestión")

    def test_anonymous_employers_get_no_contact_links(self):
        self.assertEqual(pr.contacts_html(ranked(company="Importante empresa del sector")), "")


class People(unittest.TestCase):
    PEOPLE = [
        {"name": "Marco Vega", "role": "Subgerente de Investigación Económica", "url": "https://investigacion.bcrp.gob.pe/es/autores/marco-vega"},
        {"name": "César Carrera", "role": "Especialista en Investigación Económica", "url": "https://x.example/c"},
        {"name": "Luis Llosa", "role": "Supervisor Líder de Investigación Económica"},
        {"name": "Marcos Cerón", "role": "Jefe del Departamento de Investigación Financiera", "url": "javascript:alert(1)"},
        {"name": "Eduardo Moreno", "role": "Jefe del Departamento de Política Fiscal"},
    ]

    def test_the_area_picks_its_people_leads_first(self):
        got = [p["name"] for p in pr.people_for(self.PEOPLE, "Practicante - Subgerencia de Investigación Económica")]
        self.assertEqual(got[:3], ["Marco Vega", "Luis Llosa", "César Carrera"])
        self.assertEqual([p["name"] for p in pr.people_for(self.PEOPLE, "Practicante - Cuentas Fiscales")], ["Eduardo Moreno"])
        self.assertEqual(pr.people_for(self.PEOPLE, "Programa de Prácticas 2027"), [])

    def test_people_come_from_company_research_and_unsafe_links_are_dropped(self):
        research = Path(tempfile.mkdtemp())
        (research / pr.company_file("BCRP")).write_text(json.dumps({"people": self.PEOPLE}), encoding="utf-8")
        seen = {"k": ranked(company="BCRP", title="Practicante - Dpto. de Investigación Financiera")}
        row = pr.collect({"seen": seen}, [], research, today=date(2026, 10, 20))["ranked"][0]
        html = pr.people_html(row)
        self.assertIn("Marcos Cerón", html)
        self.assertNotIn("javascript:", html)
        self.assertIn("search/results/people/?keywords=Marcos+Cer%C3%B3n+BCRP", html)


class TailoredCv(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "cv").mkdir()
        self.pdf = self.root / "cv" / "CV_Ana_BCRP.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 test")

    def rows(self):
        return [dict(ranked(), key="a", cv=str(self.pdf)), dict(ranked(), key="b", cv=str(self.root / "missing.pdf"))]

    def test_local_page_links_the_pdf_relative_to_the_report(self):
        rows = self.rows()
        pr.cv_hrefs(rows, self.root / "reports", artifact=False)
        self.assertEqual(rows[0]["cv_href"], "../cv/CV_Ana_BCRP.pdf")
        self.assertNotIn("cv_href", rows[1])
        self.assertIn('<a class="btn-cv" href="../cv/CV_Ana_BCRP.pdf" download="CV_Ana_BCRP.pdf"', pr.cv_html(rows[0]))

    def test_artifact_links_the_published_copy_and_lists_the_files_to_publish(self):
        rows = self.rows()
        manifest = pr.cv_hrefs(rows, self.root / "reports", artifact=True)
        self.assertEqual(manifest, {"CV_Ana_BCRP.pdf": str(self.pdf)})
        self.assertIn('<a class="btn-cv" href="CV_Ana_BCRP.pdf" download="CV_Ana_BCRP.pdf"', pr.cv_html(rows[0]))
        self.assertEqual(pr.cv_html(rows[1]), "")

    def test_embedded_cvs_make_the_page_one_portable_file(self):
        rows = self.rows()
        self.assertEqual(pr.cv_hrefs(rows, self.root / "reports", artifact=False, embed=True), {})
        self.assertTrue(rows[0]["cv_href"].startswith("data:application/pdf;base64,"))
        self.assertIn('download="CV_Ana_BCRP.pdf"', pr.cv_html(rows[0]))

    def test_every_page_carries_the_download_script_for_any_host(self):
        data = pr.collect({"seen": {"a": ranked(cv=str(self.pdf))}}, [], Path(tempfile.mkdtemp()), today=date(2026, 10, 20))
        pr.cv_hrefs(data["ranked"], self.root, artifact=True)
        for artifact in (True, False):
            self.assertIn("window.claude.use('downloads')", pr.render(data, date(2026, 10, 20), None, artifact=artifact))


class Duplicates(unittest.TestCase):
    def test_a_posting_on_two_portals_is_shown_once_with_where_else(self):
        seen = {
            "keep": ranked(company="Santander Perú", title="Practicante de Riesgo"),
            "dup": ranked(company="Universia Perú", title="Practicante Riesgos - Santander", portal="linkedin-search",
                          url="https://www.linkedin.com/jobs/view/4475140001", duplicate_of="keep"),
        }
        data = pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp()), today=date(2026, 10, 20))
        self.assertEqual([r["key"] for r in data["ranked"]], ["keep"])
        html = pr.also_html(data["ranked"][0])
        self.assertIn("También en:", html)
        self.assertIn("linkedin (Universia Perú)", html)
        self.assertIn("https://www.linkedin.com/jobs/view/4475140001", html)

    def test_a_dangling_duplicate_of_keeps_the_posting(self):
        seen = {"a": ranked(duplicate_of="gone")}
        data = pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp()), today=date(2026, 10, 20))
        self.assertEqual(len(data["ranked"]), 1)


class Carousel(unittest.TestCase):
    def test_postings_to_decide_are_rows_that_open_beside_the_filters(self):
        seen = {"a": ranked(), "b": ranked(title="Otra", url="https://www.linkedin.com/jobs/view/4476949303")}
        page = pr.render(pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp())), date(2026, 10, 18), None)
        self.assertIn('<div class="track list" id="main"', page)
        self.assertEqual(page.count('<article class="card'), 2)
        self.assertEqual(page.count('<details class="rowd"><summary class="row">'), 2)
        for needle in ('<aside class="side"', 'data-quick-action="pronto"', 'id="tot"', 'data-open="sec-postuladas"',
                       "Por qué calza", "Ver aviso original"):
            self.assertIn(needle, page)
        for gone in ('id="prev"', 'data-view="car"', "ArrowRight"):
            self.assertNotIn(gone, page)

    def test_more_than_eight_closing_soon_fold_the_rest(self):
        today = date(2026, 10, 20)
        seen = {f"k{i}": ranked(title=f"Oferta {i}", deadline="2026-10-22", url=f"https://www.linkedin.com/jobs/view/44769493{i:02d}")
                for i in range(10)}
        page = pr.render(pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp()), today=today), today, None)
        self.assertIn("Esta semana cierran 10</h2>", page)
        self.assertIn('<details class="soon-more"><summary>Ver 2 más</summary>', page)
        self.assertEqual(page.count('class="soon-card'), 10)

    def test_a_logo_shared_by_several_postings_is_embedded_once(self):
        uri = "data:image/png;base64,iVBORw0KGgo="
        rows = [dict(ranked(), key="a", logo=uri), dict(ranked(), key="b", logo=uri)]
        classes = pr.logo_classes(rows)
        self.assertEqual(classes, {uri: "lg0"})
        self.assertIn('class="logo img lg0"', pr.logo_html(rows[0], classes))

    def test_a_passed_deadline_closes_the_posting(self):
        today = date(2026, 10, 24)
        self.assertEqual(pr.action_for(ranked(deadline="2026-10-21"), {"risk": "bajo", "window": "fresca"}, today), "cerrada")
        data = pr.collect({"seen": {"a": ranked(deadline="2026-10-21"), "b": ranked(title="Abierta", url=None)}},
                          [], Path(tempfile.mkdtemp()), today=today)
        page = pr.render(data, today, None)
        self.assertIn("Para decidir (1)", page)
        self.assertIn("Cerradas: su fecha de cierre ya pasó (1)", page)


class Export(unittest.TestCase):
    def test_formal_eligibility_note_is_visible_in_html_and_json(self):
        row = ranked(eligibility_gate="FLAG", eligibility_note="El formulario pide universidad; ISIL es instituto.")
        data = pr.collect({"seen": {"a": row}}, [], Path(tempfile.mkdtemp()), today=date(2026, 10, 25))
        page = pr.render(data, date(2026, 10, 25), None)
        self.assertIn("Elegibilidad por confirmar", page)
        self.assertIn("ISIL es instituto", page)
        exported = pr.export(data, date(2026, 10, 25), "César")["para_decidir"][0]
        self.assertEqual(exported["eligibility_gate"], "FLAG")
        self.assertIn("ISIL", exported["eligibility_note"])

    def test_the_json_twin_carries_the_rows_without_markup(self):
        data = pr.collect({"seen": {"a": ranked(deadline="2026-10-25", how_to_apply="Formulario")}}, [],
                          Path(tempfile.mkdtemp()), today=date(2026, 10, 24))
        out = pr.export(data, date(2026, 10, 24), "Ana")
        row = out["para_decidir"][0]
        self.assertEqual((row["action"], row["days_to_close"], row["how_to_apply"]), ("ya", 1, "Formulario"))
        self.assertEqual(out["counts"]["postular"], 1)
        self.assertNotIn("<", json.dumps(out, ensure_ascii=False))

    def test_the_cli_writes_the_json_next_to_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "seen.json").write_text(json.dumps({"seen": {"k": ranked()}}), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(REPO / "tools" / "pe_revision.py"), "--state", str(tmp / "seen.json"),
                 "--frescura", str(tmp / "none.json"), "--research", str(tmp / "none"), "--out", str(tmp / "r.html"),
                 "--json", str(tmp / "r.json"), "--today", "2026-10-18"],
                capture_output=True, text=True, encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(json.loads((tmp / "r.json").read_text(encoding="utf-8"))["counts"]["ranked"], 1)


class Abroad(unittest.TestCase):
    def mode(self, line):
        path = Path(tempfile.mkdtemp()) / "CLAUDE.md"
        path.write_text(line, encoding="utf-8")
        return pr.read_abroad(path)["mode"]

    def test_the_search_stays_home_unless_the_profile_says_otherwise(self):
        self.assertEqual(self.mode(""), "no")
        self.assertEqual(self.mode("- **Abroad:** [YOUR_ABROAD]"), "no")
        self.assertEqual(self.mode("- **Abroad:** remote only, for research assistant roles"), "remoto")
        self.assertEqual(self.mode("- **Abroad:** open to relocating"), "viajar")

    def test_places_abroad_are_told_from_peruvian_ones(self):
        self.assertTrue(pr.is_abroad({"portal": "linkedin-search"}, "Bogota, D.C., Colombia"))
        self.assertFalse(pr.is_abroad({"portal": "linkedin-search"}, "Lima, Peru"))
        self.assertFalse(pr.is_abroad({"portal": "linkedin-search"}, "Remote"))
        self.assertFalse(pr.is_abroad({"portal": "computrabajo-search"}, "Arequipa, Arequipa"))

    def test_the_page_hides_what_the_profile_leaves_out_and_says_why(self):
        seen = {"a": ranked(location="Santiago, Chile", portal="linkedin-search", modality="presencial",
                            url="https://www.linkedin.com/jobs/view/4476949303"),
                "b": ranked(location="Santiago, Chile", portal="linkedin-search", modality="remoto", title="Remota",
                            url="https://www.linkedin.com/jobs/view/4476949304")}
        data = pr.collect({"seen": seen}, [], Path(tempfile.mkdtemp()))
        data["abroad_pref"] = {"mode": "remoto", "text": "remote only"}
        page = pr.render(data, date(2026, 10, 18), None)
        self.assertEqual(page.count('data-excl="1"'), 1)
        self.assertIn("fuera del Perú: <b>solo remoto</b>", page)
        self.assertIn("Extranjero · Santiago, Chile · no remoto", page)
        data["abroad_pref"] = {"mode": "no", "text": None}
        self.assertEqual(pr.render(data, date(2026, 10, 18), None).count('data-excl="1"'), 2)


class SharedUrls(unittest.TestCase):
    def test_postings_that_share_a_url_never_borrow_each_others_freshness(self):
        page = "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/modalidades_formativas.xhtml"
        seen = {"a": ranked(url=page, title="Práctica A"), "b": ranked(url=page, title="Práctica B"),
                "c": ranked(url=None, title="Sin URL"), "d": ranked(url=None, title="Sin URL 2")}
        fresh = [{"url": page, "risk": "alto", "window": "vieja", "age_days": 90, "signals": []}]
        ctx = [{"url": page, "lugar": {"districts": ["Ate"]}, "rubro": {}}]
        data = pr.collect({"seen": seen}, fresh, Path(tempfile.mkdtemp()), ctx, today=date(2026, 10, 24))
        for row in data["ranked"]:
            self.assertNotEqual((row.get("fresh") or {}).get("age_days"), 90, row["title"])
            self.assertIsNone(row["context"], row["title"])

    def test_a_posting_with_its_own_id_still_joins(self):
        seen = {"a": dict(ranked(), id="SERVIR:abc", url="https://app.servir.gob.pe/x.xhtml")}
        fresh = [{"id": "servir:abc", "risk": "bajo", "window": "fresca", "age_days": 2, "signals": []}]
        data = pr.collect({"seen": seen}, fresh, Path(tempfile.mkdtemp()), today=date(2026, 10, 24))
        self.assertEqual(data["ranked"][0]["fresh"]["age_days"], 2)


class NewFromLastSearch(unittest.TestCase):
    def test_registered_new_posts_remain_visible_until_reviewed_but_applied_do_not(self):
        seen = {"new": {"id": "up:abc", "status": "new", "fit": None},
                "applied": {"id": "up:def", "status": "applied", "fit": None}}
        report = {"nuevas": [
            {"id": "up:abc", "title": "Practicante de Research", "portal": "bolsa-universidad"},
            {"id": "up:def", "title": "Practicante de Finanzas", "portal": "bolsa-universidad",
             "already_applied": True}]}
        html = pr.nuevas_html(report, seen)
        self.assertIn("Practicante de Research", html)
        self.assertIn("Ya postuladas en Bolsa UP (1): Practicante de Finanzas", html)
        self.assertNotIn("Practicante de Finanzas", html.split('<tbody id="nrows">')[1])

    def test_lists_the_last_search_apart_minus_what_was_registered_since(self):
        seen = {"k": ranked(url=BM_SEARCH)}
        report = {"generated": "2026-10-25", "nuevas": [
            {"id": "1118482096", "portal": "bumeran", "title": "Ya registrada", "url": BM_SEARCH},
            {"id": "999", "portal": "computrabajo", "title": "<b>Asistente</b> de Selección", "company": "Acme",
             "url": "javascript:alert(1)", "query": "asistente de seleccion", "date": "2026-10-25"},
        ]}
        html = pr.nuevas_html(report, seen)
        self.assertIn("sin evaluar (1)", html)
        self.assertNotIn("Ya registrada", html)
        self.assertIn("&lt;b&gt;Asistente&lt;/b&gt;", html)
        self.assertNotIn("javascript:", html)
        self.assertIn('<option value="asistente de seleccion">', html)

    def test_no_report_no_section(self):
        self.assertEqual(pr.nuevas_html(None, {}), "")
        self.assertEqual(pr.nuevas_html({"nuevas": []}, {}), "")

    def test_servir_search_page_is_not_presented_as_a_posting_link(self):
        listing = "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/ofertas_laborales.xhtml"
        direct = "https://postulacioncas.minedu.gob.pe/PostulacionCas/Home/Convocatoria/29820"
        report = {"nuevas": [
            {"id": "SERVIR:one", "portal": "talento-peru", "title": "Supervisor pedagógico", "url": listing,
             "company": "MINEDU", "ref": "0665-2026"},
            {"id": "SERVIR:two", "portal": "talento-peru", "title": "Especialista", "url": direct,
             "company": "MINEDU", "ref": "0660-2026"},
        ]}
        html = pr.nuevas_html(report, {})
        self.assertIn("<b>Supervisor pedagógico</b>", html)
        self.assertNotIn(f'<a href="{listing}" target="_blank" rel="noopener noreferrer">Supervisor pedagógico</a>', html)
        self.assertIn("Buscar en SERVIR", html)
        self.assertIn(f'<a href="{direct}" target="_blank" rel="noopener noreferrer">Especialista</a>', html)


if __name__ == "__main__":
    unittest.main()
