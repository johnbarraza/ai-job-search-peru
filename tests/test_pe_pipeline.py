"""Tests for tools/pe_pipeline.py: the search's mechanical steps without a model (Peru fork)."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
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
            {"portal": "linkedin", "q": "research assistant", "l": "Chile"},
            {"portal": "linkedin", "q": "practicante", "l": "Lima, Peru"}]}), encoding="utf-8")

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
        self.assertEqual(bolsa["enlaces"], [])  # each candidate adds their own board's URL

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
        self.assertEqual([c[0][-1] for c in self.calls], ["Lima, Peru"])

    def test_remote_only_runs_abroad_queries_remote_and_records_their_errors(self):
        self.with_abroad("remote only, for research assistant roles")
        self.assertEqual(self.run_cmd(), 0)
        chile = self.calls[0][0]
        self.assertEqual(chile[chile.index("--remote") + 1], "remote")
        self.assertNotIn("--remote", self.calls[1][0])
        cov = json.loads((self.tmp / "reports/busqueda/cobertura.json").read_text(encoding="utf-8"))
        li = next(p for p in cov["portales"] if p["id"] == "linkedin")
        self.assertEqual((li["ok"], li["fallidas"]), (1, 1))
        self.assertIn("429", li["errores"][0])

    def test_registrar_adds_new_postings_for_rank(self):
        self.assertEqual(self.run_cmd("--registrar"), 0)
        seen = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]
        added = [e for e in seen.values() if e.get("url") == NEW]
        self.assertEqual(len(added), 1)
        self.assertEqual((added[0]["status"], added[0]["portal"]), ("new", "bumeran-search"))

    def test_servir_keeps_each_convocatoria_and_does_not_treat_portal_expiry_as_deadline(self):
        (self.tmp / "q.json").write_text(json.dumps({"consultas": [
            {"portal": "talento-peru", "q": "", "l": "Lima", "tipo": "practicas"}]}), encoding="utf-8")
        listing = {"results": [{"id": "servir:a123", "title": "Práctica de Economía", "company": "Entidad",
                                "url": "https://app.servir.gob.pe/DifusionOfertasExterno/faces/consultas/modalidades_formativas.xhtml",
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
        seen = json.loads((self.tmp / "seen.json").read_text(encoding="utf-8"))["seen"]
        row = seen["servir_A123"]
        self.assertEqual(row["id"], "SERVIR:A123")
        self.assertIsNone(row["deadline"])
        self.assertEqual(row["validThrough"], "2026-10-30")
        self.assertTrue(Path(row["detail"]).is_file())
        self.assertIn("007 - 2026", row["how_to_apply"])
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


if __name__ == "__main__":
    unittest.main()
