"""Local-only Symplicity session and result normalization tests."""
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("pe_symplicity", REPO / "tools" / "pe_symplicity.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)


class SessionSafety(unittest.TestCase):
    def test_account_names_cannot_escape_the_local_root(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ps, "SESSION_ROOT", Path(temp)):
            for bad in ("../john", "john/other", "john\\other", "", ".", "john@example.com"):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    ps.account_dir(bad)
            self.assertEqual(ps.account_dir("john"), Path(temp) / "john")

    def test_expiry_and_logout_remove_only_the_selected_account(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(ps, "SESSION_ROOT", Path(temp) / "app"):
            john = ps.account_dir("john")
            cesar = ps.account_dir("cesar")
            for path in (john, cesar):
                path.mkdir(parents=True)
                (path / "session.json").write_text(json.dumps({"expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}), encoding="utf-8")
            self.assertEqual(ps.session_state(john), "valid")
            self.assertEqual(ps.main(["logout", "--account", "john"]), 0)
            self.assertFalse(john.exists())
            self.assertTrue(cesar.exists())
            (cesar / "session.json").write_text(json.dumps({"expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}), encoding="utf-8")
            self.assertEqual(ps.session_state(cesar), "expired")
            with self.assertRaises(ValueError):
                ps._safe_remove(Path(temp))
            self.assertTrue(cesar.exists())


class Results(unittest.TestCase):
    def test_resume_list_keeps_document_ids_but_never_signed_pdf_urls(self):
        rows = [{"id": "4a6b632c3f985dc297412878c88a2e75", "name": "santander_riesgos",
                 "is_default": True, "pdf_url": "https://up-csm.symplicity.com/utils/view.php?key=secret"},
                {"id": "bad", "name": "invalid"}]
        result = ps.normalize_resumes(rows)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["is_default"])
        self.assertNotIn("pdf_url", result[0])

    def test_upload_validation_accepts_only_existing_pdf_under_site_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            valid = Path(temp) / "cv.pdf"
            valid.write_bytes(b"%PDF-1.7\nexample")
            self.assertEqual(ps.validate_resume_file(str(valid))[1], valid.stat().st_size)
            invalid = Path(temp) / "not-pdf.pdf"
            invalid.write_bytes(b"not a pdf")
            with self.assertRaises(ValueError):
                ps.validate_resume_file(str(invalid))
            with self.assertRaises(ValueError):
                ps.validate_resume_file(str(Path(temp) / "missing.pdf"))

    def test_only_up_jobs_with_real_ids_enter_the_portal_contract(self):
        rows = [
            {"title": "Practicante de Finanzas", "company": "Banco", "url":
             "https://up-csm.symplicity.com/students/app/jobs/search?currentJobId=95ccb8cb1e172c3af0d07d346638aab4"},
            {"title": "duplicada", "url":
             "https://up-csm.symplicity.com/students/app/jobs/search?currentJobId=95ccb8cb1e172c3af0d07d346638aab4"},
            {"title": "Inicio", "url": "https://up-csm.symplicity.com/students/app/home"},
            {"title": "otro sitio", "url": "https://evil.example/jobs/detail/id"},
        ]
        out = ps.normalize_results(rows, 20)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "up:95ccb8cb1e172c3af0d07d346638aab4")
        self.assertEqual(out[0]["portal"], "bolsa-universidad")

    def test_deadline_and_employer_are_normalized_from_listing(self):
        rows = [{"title": "Practicante", "company": "Banco (20100053455) - Híbrido",
                 "deadline_text": "Postularse antes del Nov 2",
                 "url": "https://up-csm.symplicity.com/students/app/jobs/search?currentJobId=abc"}]
        with patch.object(ps, "date") as today:
            today.today.return_value = datetime(2026, 10, 30).date()
            today.side_effect = lambda *a: datetime(*a).date()
            result = ps.normalize_results(rows, 1)[0]
        self.assertEqual(result["company"], "Banco")
        self.assertEqual(result["modality"], "híbrido")
        self.assertEqual(result["deadline"], "2026-11-02")

    def test_dpapi_roundtrip(self):
        import os
        if os.name != "nt":
            self.skipTest("Windows DPAPI only")
        secret = b"test-cookie-value"
        protected = ps._dpapi(secret)
        self.assertNotIn(secret, protected)
        self.assertEqual(ps._dpapi(protected, decrypt=True), secret)

    def test_only_sent_application_links_are_recorded(self):
        rows = [
            {"title": "Puesto", "url": "https://up-csm.symplicity.com/students/app/jobs/detail/abc?expired=1", "applied": True},
            {"title": "Duplicado", "url": "https://up-csm.symplicity.com/students/app/jobs/detail/abc", "applied": True},
            {"title": "Guardado", "url": "https://up-csm.symplicity.com/students/app/jobs/detail/def", "applied": False},
        ]
        result = ps.normalize_applications(rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "up:abc")

    def test_detail_extracts_formal_level_and_exact_deadline(self):
        ident = "2dfc7f54f27be627979ea2c5564e28ac"
        raw = {"title": "Practicante de Planeamiento Financiero", "company": "Interbank (20100053455)",
               "description": "Apoyar el presupuesto. Excel avanzado indispensable.",
               "additional": "Detalles adicionales del empleo\nTipo de Oferta\nPráctica Preprofesional\nRemoto/Presencial\nHíbrido",
               "requirements": "Requisitos\nNivel Académico\nAlumno de pregrado",
               "process": "Proceso de solicitud de empleo\nFecha límite de las solicitudes\nNovember 12, 2026"}
        result = ps.normalize_detail(raw, ident)
        self.assertEqual(result["deadline"], "2026-11-12")
        self.assertEqual(result["academic_level"], "Alumno de pregrado")
        self.assertEqual(result["modality"], "Híbrido")
        self.assertEqual(result["company"], "Interbank")
        self.assertEqual(ps._detail_id("up:" + ident), ident)
        with self.assertRaises(ValueError):
            ps._detail_id("https://evil.example/jobs/detail/" + ident)


if __name__ == "__main__":
    unittest.main()
