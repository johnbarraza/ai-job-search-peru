"""Tests for tools/pe_pipeline.py: the search's mechanical steps without a model (Peru fork)."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pe_pipeline", REPO / "tools" / "pe_pipeline.py")
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

KNOWN = "https://www.bumeran.com.pe/empleos/practicante-acme-1118482096.html"
NEW = "https://www.bumeran.com.pe/empleos/practicante-beta-1118499999.html"


class Buscar(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)
        (self.tmp / "seen.json").write_text(json.dumps({"seen": {"acme_practicante": {"url": KNOWN, "status": "ranked"}}}), encoding="utf-8")
        (self.tmp / "q.json").write_text(json.dumps({"jobage": 14, "excluir_titulo": ["senior"],
                                                     "consultas": [{"portal": "bumeran", "q": "practicante", "l": "Lima"}]}), encoding="utf-8")
        results = {"results": [
            {"id": "1118482096", "title": "Practicante", "company": "Acme", "url": KNOWN},
            {"id": "1118499999", "title": "Practicante de Finanzas", "company": "Beta", "url": NEW, "date": "2026-10-23"},
            {"id": "1118400000", "title": "Analista Senior", "company": "Gamma", "url": "https://x.example/1118400000"},
        ]}
        self.calls = []

        def fake_run(cmd, out=None, timeout=180):
            self.calls.append((cmd, out))
            if out:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(results), encoding="utf-8")
            if "Chile" in cmd:
                return None, "Request failed: 429 Too Many Requests"
            return results, None

        self.patch(pp, "run_json", fake_run)
        self.patch(pp, "cli", lambda portal: ["bun", portal])
        self.patch(pp, "LINKEDIN_PAUSE", 0)

    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def run_cmd(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return pp.main(["buscar", "--consultas", "q.json", "--state", "seen.json", "--perfil", "perfil.md", *extra])

    def with_abroad(self, line):
        (self.tmp / "perfil.md").write_text(f"- **Abroad:** {line}" if line else "", encoding="utf-8")
        (self.tmp / "q.json").write_text(json.dumps({"consultas": [
            {"portal": "linkedin", "q": "practicante", "l": "Lima, Peru"},
            {"portal": "linkedin", "q": "research assistant", "l": "Chile"},
            {"portal": "linkedin", "q": "analista", "l": "Lima, Peru"}]}), encoding="utf-8")

    def test_lists_only_postings_not_seen_and_not_excluded(self):
        self.assertEqual(self.run_cmd(), 0)
        report = json.loads((self.tmp / "reports/busqueda/nuevas.json").read_text(encoding="utf-8"))
        self.assertEqual([p["company"] for p in report["nuevas"]], ["Beta"])
        self.assertEqual(report["excluidas_por_titulo"], 1)
        cmd, out = self.calls[0]
        self.assertIn("--jobage", cmd)
        self.assertEqual(out.parent.parent, Path("reports/busqueda"))  # dated folder: never over an earlier run
        state = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state["seen"]), 1)  # without --registrar the state is untouched
        cov = json.loads((self.tmp / "reports/busqueda/cobertura.json").read_text(encoding="utf-8"))
        bumeran = next(p for p in cov["portales"] if p["id"] == "bumeran")
        self.assertEqual((bumeran["consultas"], bumeran["ok"], bumeran["resultados"]), (1, 1, 3))
        laborum = next(p for p in cov["portales"] if p["id"] == "laborum")
        self.assertIn("laborum.pe/search-jobs?q=practicante", laborum["enlaces"][0]["url"])
        bolsa = next(p for p in cov["portales"] if p["id"] == "bolsa-universidad")
        self.assertEqual(bolsa["metodo"], "manual")  # opt-in until this profile adds UP queries
        self.assertIn("up-csm.symplicity.com", bolsa["enlaces"][0]["url"])
        glassdoor = next(p for p in cov["portales"] if p["id"] == "glassdoor")
        self.assertEqual(glassdoor["metodo"], "manual")
        self.assertEqual("https://www.glassdoor.com/", glassdoor["enlaces"][0]["url"])

    def test_previous_latest_search_is_preserved_before_replacement(self):
        latest = self.tmp / "reports/busqueda/nuevas.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        previous = {"generated": "2026-10-23", "nuevas": [{"id": "older"}]}
        latest.write_text(json.dumps(previous), encoding="utf-8")
        self.assertEqual(self.run_cmd(), 0)
        run_dir = self.tmp / self.calls[0][1].parent
        saved = json.loads((run_dir / "nuevas-anteriores.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, previous)
        self.assertNotEqual(json.loads(latest.read_text(encoding="utf-8")), previous)

    def test_without_an_abroad_line_queries_abroad_are_skipped(self):
        self.with_abroad("")
        self.run_cmd()
        self.assertEqual([c[0][-1] for c in self.calls], ["Lima, Peru", "Lima, Peru"])

    def test_remote_only_runs_abroad_queries_remote_and_records_their_errors(self):
        self.with_abroad("remote only, for research assistant roles")
        self.assertEqual(self.run_cmd(), 0)
        self.assertNotIn("--remote", self.calls[0][0])
        chile = self.calls[1][0]
        self.assertEqual(chile[chile.index("--remote") + 1], "remote")
        self.assertEqual(len(self.calls), 2, "a 429 stops LinkedIn for the rest of the run")
        cov = json.loads((self.tmp / "reports/busqueda/cobertura.json").read_text(encoding="utf-8"))
        li = next(p for p in cov["portales"] if p["id"] == "linkedin")
        self.assertEqual((li["ok"], li["fallidas"]), (1, 1))
        self.assertIn("429", li["errores"][0])
        control = json.loads((self.tmp / "reports/busqueda/linkedin-control.json").read_text(encoding="utf-8"))
        self.assertEqual(control["motivo"], "429 de LinkedIn")

    def test_linkedin_waits_for_its_refresh_and_rotates_queries(self):
        self.with_abroad("remote only")
        (self.tmp / "q.json").write_text(json.dumps({"consultas": [
            {"portal": "linkedin", "q": f"analista {n}", "l": "Lima, Peru"} for n in range(10)]}), encoding="utf-8")
        self.assertEqual(self.run_cmd(), 0)
        self.assertEqual(len(self.calls), pp.LINKEDIN_SEARCHES)
        self.assertEqual(self.run_cmd(), 0, "on hold is not a failure")
        self.assertEqual(len(self.calls), pp.LINKEDIN_SEARCHES, "the second run the same day asks nothing")
        control_file = self.tmp / "reports/busqueda/linkedin-control.json"
        control = json.loads(control_file.read_text(encoding="utf-8"))
        control["next_at"] = "2000-01-01T00:00:00"
        control_file.write_text(json.dumps(control), encoding="utf-8")
        self.calls.clear()
        self.run_cmd()
        asked = {cmd[cmd.index("-q") + 1] for cmd, _ in self.calls}
        self.assertTrue({"analista 8", "analista 9"} <= asked, "next run resumes the rotation")

    def test_registrar_adds_new_postings_for_rank(self):
        self.assertEqual(self.run_cmd("--registrar"), 0)
        seen = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]
        added = [e for e in seen.values() if e.get("url") == NEW]
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0]["status"], added[0]["portal"]), ("new", "bumeran-search"))

    def test_university_board_uses_local_account_for_search_and_applications(self):
        (self.tmp / "q.json").write_text(json.dumps({"symplicity_account": "john", "consultas": [
            {"portal": "bolsa-universidad", "q": "practicante finanzas", "l": "Lima"}]}), encoding="utf-8")
        self.assertEqual(self.run_cmd("--portal", "bolsa-universidad"), 0)
        cmd = self.calls[0][0]
        self.assertIn("--account", cmd)
        self.assertEqual(cmd[cmd.index("--account") + 1], "john")
        self.assertNotIn("--jobage", cmd)
        self.assertNotIn("-l", cmd)
        self.assertEqual(len(self.calls), 2, "listing and read-only applications")
        coverage = json.loads((self.tmp / "reports/busqueda/cobertura.json").read_text(encoding="utf-8"))
        board = next(p for p in coverage["portales"] if p["id"] == "bolsa-universidad")
        self.assertEqual(board["metodo"], "cli")

    def test_university_detail_populates_deadline_and_academic_level(self):
        ident = "up:2dfc7f54f27be627979ea2c5564e28ac"
        (self.tmp / "q.json").write_text(json.dumps({"symplicity_account": "john", "consultas": [
            {"portal": "bolsa-universidad", "q": "planeamiento financiero"}]}), encoding="utf-8")

        def fake_run(cmd, out=None, timeout=180):
            self.calls.append((cmd, out))
            if "detail" in cmd:
                value = {"id": ident, "description": "Excel avanzado y SQL intermedio",
                         "deadline": "2026-11-12", "academic_level": "Alumno de pregrado",
                         "job_type": "Práctica Preprofesional", "modality": "Híbrido"}
            elif "applications" in cmd:
                value = {"results": []}
            else:
                value = {"results": [{"id": ident, "title": "Practicante de Planeamiento Financiero",
                                      "company": "Interbank", "url": "https://up-csm.symplicity.com/students/app/jobs/detail/" + ident.split(":")[1]}]}
            if out:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(value), encoding="utf-8")
            return value, None

        self.patch(pp, "run_json", fake_run)
        self.assertEqual(self.run_cmd("--portal", "bolsa-universidad", "--detalle", "--registrar"), 0)
        report = json.loads((self.tmp / "reports/busqueda/nuevas.json").read_text(encoding="utf-8"))
        row = report["nuevas"][0]
        self.assertEqual(row["deadline"], "2026-11-12")
        self.assertEqual(row["academic_level"], "Alumno de pregrado")
        self.assertIn("Excel avanzado", row["requirements"])
        self.assertTrue(Path(row["detail"]).is_file())

    def test_university_applications_mark_confirmed_postings_applied(self):
        ident = "up:95ccb8cb1e172c3af0d07d346638aab4"
        url = "https://up-csm.symplicity.com/students/app/jobs/search?currentJobId=" + ident.split(":")[1]
        (self.tmp / "q.json").write_text(json.dumps({"symplicity_account": "john", "consultas": [
            {"portal": "bolsa-universidad", "q": ""}]}), encoding="utf-8")

        def fake_run(cmd, out=None, timeout=180):
            self.calls.append((cmd, out))
            if "applications" in cmd:
                return {"results": [{"id": ident, "title": "Practicante", "status": "applied"}]}, None
            return {"results": [{"id": ident, "title": "Practicante", "company": "Banco", "url": url}]}, None

        self.patch(pp, "run_json", fake_run)
        self.assertEqual(self.run_cmd("--portal", "bolsa-universidad", "--registrar"), 0)
        report = json.loads((self.tmp / "reports/busqueda/nuevas.json").read_text(encoding="utf-8"))
        self.assertEqual(report["ya_postuladas_bolsa_up"], 1)
        self.assertTrue(report["nuevas"][0]["already_applied"])
        state = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]
        self.assertIn("up_" + ident.split(":")[1], state)
        self.assertEqual(next(e for e in state.values() if e.get("id") == ident.upper())["status"], "applied")

    def test_university_applications_sync_existing_posting_without_new_results(self):
        ident = "up:95ccb8cb1e172c3af0d07d346638aab4"
        url = "https://up-csm.symplicity.com/students/app/jobs/detail/" + ident.split(":")[1]
        (self.tmp / "seen.json").write_text(json.dumps({"seen": {"existing_up": {
            "id": ident.upper(), "title": "Practicante", "company": "Banco",
            "url": url, "status": "ranked", "fit": "high"}}}), encoding="utf-8")
        (self.tmp / "q.json").write_text(json.dumps({"symplicity_account": "cuenta_up", "consultas": [
            {"portal": "bolsa-universidad", "q": ""}]}), encoding="utf-8")

        def fake_run(cmd, out=None, timeout=180):
            if "applications" in cmd:
                return {"results": [{"id": ident, "status": "applied"}]}, None
            return {"results": [{"id": ident, "title": "Practicante", "company": "Banco", "url": url}]}, None

        self.patch(pp, "run_json", fake_run)
        self.assertEqual(self.run_cmd("--portal", "bolsa-universidad", "--registrar"), 0)
        entry = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]["existing_up"]
        self.assertEqual(entry["status"], "ranked")
        self.assertEqual(entry["application_status"], "applied")
        self.assertEqual(entry["application_source"], "bolsa-up-solicitudes")

    def test_servir_keeps_each_convocatoria_and_does_not_treat_portal_expiry_as_deadline(self):
        (self.tmp / "q.json").write_text(json.dumps({"consultas": [
            {"portal": "talento-peru", "q": "", "l": "Lima", "tipo": "practicas"}]}), encoding="utf-8")
        listing = {"results": [{"id": "servir:a123", "title": "Práctica de Economía", "company": "Entidad",
                                "url": None, "search_url": "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/modalidades_formativas.xhtml",
                                "date": "2026-10-23", "validThrough": "2026-10-30", "ref": "007 - 2026",
                                "type": "practicas", "level": "Prácticas Profesionales"}]}

        def fake_run(cmd, out=None, timeout=180):
            self.calls.append((cmd, out))
            if out:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(listing), encoding="utf-8")
            return listing, None

        self.patch(pp, "run_json", fake_run)
        self.assertEqual(self.run_cmd("--registrar", "--detalle"), 0)
        self.assertIn("--tipo", self.calls[0][0])
        self.assertNotIn("--word", self.calls[1][0])
        seen = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]
        row = seen["servir_A123"]
        self.assertEqual(row["id"], "SERVIR:A123")
        self.assertIsNone(row["url"])
        self.assertIn("modalidades_formativas.xhtml", row["search_url"])
        self.assertIsNone(row["deadline"])
        self.assertEqual(row["validThrough"], "2026-10-30")
        self.assertTrue(Path(row["detail"]).is_file())
        self.assertIn("007 - 2026", row["how_to_apply"])
        self.assertIn("Ver más", row["how_to_apply"])
        self.calls.clear()
        self.assertEqual(self.run_cmd(), 0)
        self.assertEqual(json.loads((self.tmp / "reports/busqueda/nuevas.json").read_text(encoding="utf-8"))["nuevas"], [])

    def test_practicas_pe_detail_fills_the_posting_date_and_known_urls_are_skipped(self):
        seen = {"seen": {"old": {"url": "https://www.practicas.pe/oferta-convocatoria-economia-lima-70398.html"}}}
        (self.tmp / "seen.json").write_text(json.dumps(seen), encoding="utf-8")
        (self.tmp / "q.json").write_text(json.dumps({"consultas": [
            {"portal": "practicas-pe", "q": "economia", "l": "Lima"}]}), encoding="utf-8")

        def fake_run(cmd, out=None, timeout=180):
            self.calls.append((cmd, out))
            if "detail" in cmd:
                value = {"id": "70376", "date": "2026-10-23", "deadline": "2026-11-05", "description": "Economía"}
            else:
                value = {"results": [{"id": ident, "title": "Practicante de Economía", "company": "Entidad",
                                      "url": f"https://www.practicas.pe/oferta-convocatoria-economia-lima-{ident}.html",
                                      "deadline": "2026-11-05"} for ident in ("70398", "70376")]}
            if out:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(value), encoding="utf-8")
            return value, None

        self.patch(pp, "run_json", fake_run)
        self.assertEqual(self.run_cmd("--detalle", "--registrar"), 0)
        self.assertNotIn("--jobage", self.calls[0][0])  # the listing has no posting date
        rows = json.loads((self.tmp / "reports/busqueda/nuevas.json").read_text(encoding="utf-8"))["nuevas"]
        self.assertEqual([(x["id"], x["date"]) for x in rows], [("70376", "2026-10-23")])
        state = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]
        self.assertEqual(state["practicas_70376"]["posted_date"], "2026-10-23")

    def test_paginas_reads_further_pages_until_the_listing_ends(self):
        (self.tmp / "q.json").write_text(json.dumps({"paginas": 1, "consultas": [
            {"portal": "computrabajo", "q": "asistente de seleccion", "l": "Lima", "paginas": 5}]}), encoding="utf-8")
        pages = {1: ["A1", "A2"], 2: ["B1"], 3: []}  # page 3 comes back empty: stop there

        def fake_run(cmd, out=None, timeout=180):
            self.calls.append((cmd, out))
            page = int(cmd[cmd.index("--page") + 1]) if "--page" in cmd else 1
            value = {"results": [{"id": i, "title": "Asistente de Selección", "company": "X",
                                  "url": f"https://pe.computrabajo.com/oferta-{i}"} for i in pages.get(page, [])]}
            return value, None

        self.patch(pp, "run_json", fake_run)
        self.assertEqual(self.run_cmd(), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("--page", self.calls[0][0])
        self.assertEqual(self.calls[1][1].name, "ct_asistente-de-seleccion-p2.json")
        report = json.loads((self.tmp / "reports/busqueda/nuevas.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(p["id"] for p in report["nuevas"]), ["A1", "A2", "B1"])

    def test_servir_campo_reaches_the_cli(self):
        (self.tmp / "q.json").write_text(json.dumps({"consultas": [
            {"portal": "talento-peru", "q": "supervisor", "l": "Lima", "tipo": "laboral", "campo": "puesto"}]}),
            encoding="utf-8")
        self.patch(pp, "run_json", lambda cmd, out=None, timeout=180: (self.calls.append((cmd, out)), ({"results": []}, None))[1])
        self.assertEqual(self.run_cmd(), 0)
        cmd, out = self.calls[0]
        self.assertEqual(cmd[cmd.index("--campo") + 1], "puesto")
        self.assertEqual(out.name, "sv_supervisor-laboral-puesto.json")


class Estado(unittest.TestCase):
    def test_json_brief_lists_what_closes_and_what_lacks_a_cv(self):
        review = {"generated": "2026-10-24", "counts": {"ranked": 2}, "para_decidir": [
            {"title": "A", "company": "X", "action": "ya", "score": 80, "days_to_close": 1, "closes_label": "mañana", "cv": "a.pdf"},
            {"title": "B", "company": "Y", "action": "pronto", "score": 70, "days_to_close": None, "cv": None},
        ]}
        path = Path(tempfile.mkdtemp()) / "r.json"
        path.write_text(json.dumps(review), encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(pp.estado(path, as_json=True), 0)
        brief = json.loads(buf.getvalue())
        self.assertEqual([r["title"] for r in brief["cierran_7_dias"]], ["A"])
        self.assertEqual(brief["sin_cv_a_medida"], ["B · Y"])

    def test_missing_review_says_how_to_make_it(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(pp.estado(Path(tempfile.mkdtemp()) / "none.json", as_json=False), 1)
        self.assertIn("actualizar", err.getvalue())


class Horario(unittest.TestCase):
    def test_parse_hora_takes_12_and_24_hour_forms(self):
        for text, want in [("8", (8, 0)), ("08:30", (8, 30)), ("20:30", (20, 30)), ("8am", (8, 0)),
                           ("7:30 p. m.", (19, 30)), ("8:30 PM", (20, 30)), ("12am", (0, 0)), ("12:15 p.m.", (12, 15))]:
            self.assertEqual(pp.parse_hora(text), want, text)
        for bad in ("13pm", "0am", "25:00", "8:60", "ocho"):
            with self.assertRaises(pp.argparse.ArgumentTypeError):
                pp.parse_hora(bad)

    def test_next_run_is_peru_time_or_utc_and_rolls_to_tomorrow(self):
        now = pp.datetime(2026, 11, 4, 14, 0, tzinfo=pp.timezone.utc)  # 9:00 a. m. in Peru
        self.assertEqual(pp.fecha_hora(pp.next_run(8, 0, False, now)), "05/11/2026 8:00 a. m.")
        self.assertEqual(pp.fecha_hora(pp.next_run(20, 30, False, now)), "04/11/2026 8:30 p. m.")
        self.assertEqual(pp.next_run(13, 0, True, now), pp.datetime(2026, 11, 5, 13, 0, tzinfo=pp.timezone.utc))
        self.assertEqual(pp.hora_12(pp.datetime(2026, 1, 1, 0, 5)), "12:05 a. m.")


class Diario(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cwd = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)
        (self.tmp / "reports" / "busqueda").mkdir(parents=True)
        (self.tmp / "reports" / "busqueda" / "consultas.json").write_text('{"consultas": []}', encoding="utf-8")
        self.calls = []

    def fake_run(self, agy_reply):
        def run(cmd, **kw):
            self.calls.append(cmd)
            if cmd[0].endswith("agy"):
                return pp.subprocess.CompletedProcess(cmd, 0, json.dumps(agy_reply), "")
            if cmd[0] == "powershell":
                return pp.subprocess.CompletedProcess(cmd, 0, "", "")
            return pp.subprocess.CompletedProcess(cmd, 0, "ok\n", "")
        return run

    def run_main(self, argv, agy_reply=None):
        with mock.patch.object(pp.subprocess, "run", self.fake_run(agy_reply or {})), \
             mock.patch.object(pp.shutil, "which", lambda name: name), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            code = pp.main(argv)
        return code, out.getvalue()

    def test_diario_runs_buscar_then_agy_rank_then_actualizar_and_logs(self):
        code, _ = self.run_main(["diario", "--ia", "agy"], {"status": "SUCCESS", "response": "3 evaluadas"})
        self.assertEqual(code, 0)
        self.assertEqual([c[4] if c[0] != "agy" else "agy" for c in self.calls], ["buscar", "agy", "actualizar"])
        self.assertNotIn("--dangerously-skip-permissions", self.calls[1])
        log = (self.tmp / pp.DAILY_LOG).read_text(encoding="utf-8")
        self.assertRegex(log, r"== \d\d/\d\d/\d{4} \d{1,2}:\d\d [ap]\. m\. \(hora de Perú, UTC-5\)")
        self.assertIn("3 evaluadas", log)

    def steps_run(self):
        return [c[4] if c[0] != "agy" else "agy" for c in self.calls]

    def at(self, hour, minute=0):
        """Freeze diario's clock at hour:minute in Peru on 04/11/2026."""
        frozen = pp.datetime(2026, 11, 4, hour, minute, tzinfo=pp.PERU)

        class Clock(pp.datetime):
            @classmethod
            def now(cls, tz=None):
                return frozen.astimezone(tz) if tz else frozen.replace(tzinfo=None)
        return mock.patch.object(pp, "datetime", Clock)

    def state(self, **fields):
        (self.tmp / pp.DAILY_STATE).write_text(json.dumps(fields), encoding="utf-8")

    def test_diario_fails_when_agy_does_not_succeed_and_resumes_from_rank(self):
        with self.at(8):
            code, _ = self.run_main(["diario", "--ia", "agy"], {"status": "ERROR", "response": ""})
        self.assertEqual(code, 1)
        saved = json.loads((self.tmp / pp.DAILY_STATE).read_text(encoding="utf-8"))
        self.assertEqual((saved["fecha"], saved["terminado"], saved["hechos"]), ("2026-11-04", False, ["actualizar", "buscar"]))
        self.calls.clear()
        with self.at(9):
            code, _ = self.run_main(["diario", "--ia", "agy"], {"status": "SUCCESS", "response": "ok"})
        self.assertEqual((code, self.steps_run()), (0, ["agy"]))
        self.assertIn("retoma la corrida de las 8:00 a. m.", (self.tmp / pp.DAILY_LOG).read_text(encoding="utf-8"))

    def test_an_unfinished_run_from_another_day_starts_over(self):
        self.state(fecha="2026-11-03", inicio="03/11/2026 8:00 a. m.", hechos=["buscar"], terminado=False)
        with self.at(10):
            self.run_main(["diario"])
        self.assertEqual(self.steps_run(), ["buscar", "actualizar"])

    def test_programado_at_login_does_nothing_before_the_hour_or_after_todays_run(self):
        with self.at(7, 30):
            self.assertEqual(self.run_main(["diario", "--programado", "8:00 am"])[0], 0)
        self.assertEqual(self.calls, [])
        self.state(fecha="2026-11-04", inicio="04/11/2026 8:00 a. m.", hechos=["actualizar", "buscar"], terminado=True)
        with self.at(11):
            self.run_main(["diario", "--programado", "8:00 am"])
        self.assertEqual(self.calls, [])

    def test_programado_at_login_catches_up_a_missed_run_and_resumes_a_cut_one(self):
        with self.at(11):  # the PC was off at 8:00
            self.run_main(["diario", "--programado", "8:00 am"])
        self.assertEqual(self.steps_run(), ["buscar", "actualizar"])
        self.calls.clear()
        self.state(fecha="2026-11-04", inicio="04/11/2026 8:00 a. m.", hechos=["buscar"], terminado=False)
        with self.at(7):  # cut short; resumes at login even before the hour
            self.run_main(["diario", "--programado", "8:00 am"])
        self.assertEqual(self.steps_run(), ["actualizar"])

    @unittest.skipUnless(pp.sys.platform == "win32", "Task Scheduler")
    def test_programar_registers_a_daily_and_login_task_at_the_pcs_clock(self):
        code, out = self.run_main(["programar", "--hora", "8:00 am", "--ia", "agy"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls[0][-2], "-EncodedCommand")
        script = pp.base64.b64decode(self.calls[0][-1]).decode("utf-16-le")
        local = pp.next_run(8, 0, False, pp.datetime.now(pp.timezone.utc)).astimezone()
        self.assertIn(f"AddHours({local.hour}).AddMinutes({local.minute})", script)
        self.assertIn("-AtLogOn", script)
        self.assertIn("-StartWhenAvailable", script)
        self.assertIn("diario --programado 08:00 --ia agy", script)
        self.assertIn(f"-WorkingDirectory '{self.tmp}'", script)
        self.assertIn("8:00 a. m. hora de Perú (UTC-5) = 13:00 UTC", out)



class Descartar(unittest.TestCase):
    def test_discard_then_undo_restores_the_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "seen.json"
            state.write_text(json.dumps({"seen": {"k": {"title": "Practicante", "status": "ranked"}}}), encoding="utf-8")
            with mock.patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(pp.main(["descartar", "k", "--state", str(state)]), 0)
                e = json.loads(state.read_text(encoding="utf-8"))["seen"]["k"]
                self.assertEqual((e["status"], e["status_before_discard"]), ("skipped", "ranked"))
                self.assertEqual(pp.main(["descartar", "k", "--deshacer", "--state", str(state)]), 0)
            e = json.loads(state.read_text(encoding="utf-8"))["seen"]["k"]
            self.assertEqual(e["status"], "ranked")
            self.assertNotIn("discarded_by_user", e)
            with mock.patch("sys.stdout", new=io.StringIO()), mock.patch("sys.stderr", new=io.StringIO()):
                self.assertEqual(pp.main(["descartar", "no-existe", "--state", str(state)]), 1)


class Evaluar(unittest.TestCase):
    def test_runs_rank_with_the_chosen_cli_then_refreshes_the_page(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout='{"status": "SUCCESS", "response": "ok"}' if "agy" in cmd[0] else "ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pp, "DAILY_LOG", Path(tmp) / "diario.log"),                 mock.patch.object(pp.shutil, "which", side_effect=lambda c: c),                 mock.patch.object(pp.subprocess, "run", side_effect=fake_run), mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(pp.main(["evaluar", "--ia", "agy", "--titulo", "Ana"]), 0)
            self.assertIn("evaluar ==", (Path(tmp) / "diario.log").read_text(encoding="utf-8"))
        self.assertEqual(calls[0][:2], ["agy", "-p"])
        self.assertEqual(calls[1][-3:], ["actualizar", "--titulo", "Ana"])


class Consultas(unittest.TestCase):
    def test_writes_queries_from_phrases_and_replaces_only_the_example(self):
        spec = importlib.util.spec_from_file_location("perfiles", pp.TOOLS / "perfiles.py")
        perfiles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(perfiles)
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stdout", new=io.StringIO()):
            path = Path(tmp) / "consultas.json"
            path.write_text(json.dumps(perfiles.STARTER_QUERIES), encoding="utf-8")
            self.assertEqual(pp.main(["consultas", "--es-ejemplo", "--consultas", str(path)]), 0)
            self.assertEqual(pp.main(["consultas", "--q", "practicante finanzas, analista", "--ciudad", "Arequipa",
                                      "--consultas", str(path)]), 0)
            cfg = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn({"portal": "linkedin", "q": "analista", "l": "Arequipa, Peru"}, cfg["consultas"])
            self.assertIn("research assistant", [q["q"] for q in cfg["consultas"]])  # every sector by default
            self.assertEqual(pp.main(["consultas", "--es-ejemplo", "--consultas", str(path)]), 3)
            pp.main(["consultas", "--q", "otra cosa", "--consultas", str(path)])  # the user's own: kept
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), cfg)

    def test_answers_shape_the_queries_and_fill_only_empty_profile_lines(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stdout", new=io.StringIO()):
            path, profile = Path(tmp) / "consultas.json", Path(tmp) / "CLAUDE.md"
            profile.write_text("- **Target level:** [YOUR_TARGET_LEVEL]\n- **Work mode preference:** [YOUR_WORK_MODE_PREFERENCE]\n"
                               "- **Abroad:** [YOUR_ABROAD]\n- **Location:** [YOUR_CITY], [YOUR_COUNTRY]\n", encoding="utf-8")
            # No typing: the phrases come from the stage and the chosen areas.
            self.assertEqual(pp.main(["consultas", "--area", "finanzas,economia", "--sector", "privado,publico",
                                      "--modalidad", "hibrido", "--lugar", "afuera-remoto", "--etapa", "estudiante",
                                      "--ciclo", "7-8", "--merito", "quinto",
                                      "--consultas", str(path), "--perfil", str(profile)]), 0)
            qs = json.loads(path.read_text(encoding="utf-8"))["consultas"]
            self.assertIn({"portal": "bumeran", "q": "practicante finanzas", "l": "Lima"}, qs)  # hybrid: no filter
            self.assertIn({"portal": "computrabajo", "q": "practicante economía", "l": "Lima"}, qs)
            self.assertIn({"portal": "practicas-pe", "q": "finanzas", "l": "Lima"}, qs)
            self.assertIn({"portal": "talento-peru", "q": "", "l": "Lima", "tipo": "practicas"}, qs)
            self.assertIn({"portal": "linkedin", "q": "practicante finanzas", "l": "Latin America", "remote": "remote"}, qs)
            self.assertFalse(any(q["portal"] == "talento-peru" and q.get("tipo") == "laboral" for q in qs))  # a student
            self.assertNotIn("research assistant", [q["q"] for q in qs])  # research was not chosen
            text = profile.read_text(encoding="utf-8")
            for want in ("práctica preprofesional", "híbrido", "solo remoto fuera del Perú", "Lima, Perú",
                         "- **Ciclo:** 7.º u 8.º ciclo", "- **Mérito académico:** Quinto superior"):
                self.assertIn(want, text)
            # Only the public sector, for a professional: SERVIR's laboral search, nothing private;
            # and lines already written are not added twice.
            pp.main(["consultas", "--sector", "publico", "--etapa", "profesional", "--merito", "tercio",
                     "--reemplazar", "--consultas", str(path), "--perfil", str(profile)])
            self.assertEqual({q["portal"] for q in json.loads(path.read_text(encoding="utf-8"))["consultas"]}, {"talento-peru"})
            self.assertEqual(profile.read_text(encoding="utf-8").count("Mérito académico"), 1)

    def test_every_question_can_be_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stdout", new=io.StringIO()):
            path = Path(tmp) / "consultas.json"
            self.assertEqual(pp.main(["consultas", "--consultas", str(path), "--perfil", str(Path(tmp) / "none.md")]), 0)
            qs = json.loads(path.read_text(encoding="utf-8"))["consultas"]
            self.assertEqual({q["portal"] for q in qs}, {"bumeran", "computrabajo", "linkedin", "practicas-pe", "talento-peru"})
            self.assertIn({"portal": "bumeran", "q": "practicante", "l": "Lima"}, qs)


class LeerCv(unittest.TestCase):
    def test_reads_areas_stage_cycle_and_merit_without_a_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cv = Path(tmp) / "cv.txt"
            cv.write_text("Estudiante de Economía en la Universidad del Pacífico, 8.º ciclo, Quinto Superior. Practicante de "
                          "Finanzas Corporativas: valorización de empresas y riesgo de crédito. Python, SQL, Stata, Excel "
                          "y econometría. Inglés avanzado (C1). CFA Level I candidate.", encoding="utf-8")
            found = pp.read_cv(pp.cv_text(cv))
        self.assertEqual(set(found["area"].split(",")), {"economia", "finanzas", "datos"})
        self.assertEqual((found["etapa"], found["ciclo"], found["merito"]), ("estudiante", "7-8", "quinto"))
        self.assertEqual((found["universidad"], found["ingles"], found["certificaciones"]),
                         ("Universidad del Pacífico", "avanzado", "cfa1"))
        self.assertEqual(set(found["herramientas"].split(",")), {"excel", "python", "sql", "stata"})
        derecho = pp.read_cv(" bachiller en derecho, abogado ")
        self.assertEqual((derecho["area"], derecho["etapa"], derecho["ciclo"]), ("derecho", "egresado", ""))
        self.assertEqual(set(pp.read_cv(" nada que ver ").values()), {""})


class RankLexico(unittest.TestCase):
    FICHA = {"carreras": ["finanzas"], "etapa": "estudiante", "ciclo": "5-6", "merito": "tercio", "ingles": "intermedio",
             "herramientas": ["excel"], "experiencia": "ninguna", "sectores": ["privado"]}

    def test_scores_by_words_and_flags_unmet_formal_requirements(self):
        lx = pp.pe_lexico
        good = lx.score({"title": "Practicante de Finanzas", "level": "práctica preprofesional"}, self.FICHA)
        self.assertEqual((good["status"], good["rank_method"], good["eligibility_gate"]), ("ranked", "lexico", "PASS"))
        self.assertEqual(good["rank_score"], 70)  # title only: capped, never Strong Fit
        # A job for graduates (SERVIR CAS) for a student, even in the student's area: low and flagged.
        cas = lx.score({"title": "ESPECIALISTA EN GESTION DE RIESGOS", "type": "laboral"}, self.FICHA)
        self.assertLess(cas["rank_score"], 45)
        self.assertEqual(cas["eligibility_gate"], "FLAG")
        self.assertEqual(lx.required_years(lx.fold("experiencia laboral general minima de cinco (05) anos")), 5.0)
        # The public sector is strict: the same unmet requirement there means discard (gate FAIL).
        public = lx.score({"title": "ESPECIALISTA EN GESTION DE RIESGOS", "type": "laboral"}, self.FICHA, lambda e: "publico")
        self.assertEqual(public["eligibility_gate"], "FAIL")
        private = lx.score({"title": "ESPECIALISTA EN GESTION DE RIESGOS"}, self.FICHA, lambda e: "privado")
        self.assertEqual(private["eligibility_gate"], "FLAG")
        with tempfile.TemporaryDirectory() as tmp:
            detail = Path(tmp) / "d.json"
            detail.write_text(json.dumps({"description": "Practicante de riesgos. Requisitos: estudiante desde el 8.º ciclo, "
                                                         "quinto superior, inglés avanzado, Power BI y Python."}), encoding="utf-8")
            hard = lx.score({"title": "Practicante de Riesgos", "detail": str(detail)}, self.FICHA)
        self.assertEqual((hard["eligibility_gate"], hard["language_gate"]), ("FLAG", "PASS"))  # English never gates
        self.assertIn("8.º ciclo", hard["eligibility_note"])
        self.assertIn("quinto superior", hard["eligibility_note"])
        self.assertTrue(any("Power BI" in w for w in hard["warnings"]))  # tools are reported, not scored
        self.assertTrue(any("inglés avanzado" in w for w in hard["warnings"]))
        self.assertLess(hard["rank_score"], good["rank_score"])

    def test_rank_command_scores_only_the_waiting_ones(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch("sys.stdout", new=io.StringIO()):
            state, ficha = Path(tmp) / "seen.json", Path(tmp) / "ficha.json"
            state.write_text(json.dumps({"seen": {"a": {"title": "Practicante de Finanzas", "status": "new"},
                                                   "b": {"title": "Analista", "status": "ranked", "rank_score": 80}}}),
                             encoding="utf-8")
            ficha.write_text(json.dumps(self.FICHA), encoding="utf-8")
            self.assertEqual(pp.pe_lexico.main(["rank", "--state", str(state), "--ficha", str(ficha)]), 0)
            seen = json.loads(state.read_text(encoding="utf-8"))["seen"]
        self.assertEqual(seen["a"]["rank_method"], "lexico")
        self.assertEqual(seen["b"]["rank_score"], 80)  # /rank's own score is left alone


if __name__ == "__main__":
    unittest.main()
