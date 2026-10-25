"""Public Prácticas.pe result parsing and URL boundary."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pe_practicas as practicas


class Practicas(unittest.TestCase):
    def test_search_extracts_one_posting_without_confusing_share_links_with_results(self):
        html = '''<section class="convocatorias"><article>
        <h3><a href="https://www.practicas.pe/oferta-convocatoria-economia-lima-70398.html">Practicante de Economía</a></h3>
        <div class="text-left"><p><span>Entidad:</span> Banco Central</p>
        <p><span>Se requiere:</span> Estudiante de Economía</p></div>
        <div class="badge badge--blue">Lima</div><div class="badge badge--green">Preprofesional</div>
        <div class="badge badge--cyan">Híbrido</div><div class="badge badge--orange">Finaliza el 05/11/2026</div>
        <a href="https://wa.me/?text=compartir">Compartir</a></article></section>
        <a href="?page=1&q=economia">1</a><a href="?page=3&q=economia">3</a>'''
        rows, pages = practicas.parse_search(html)
        self.assertEqual((len(rows), pages), (1, 3))
        self.assertEqual((rows[0]["id"], rows[0]["company"], rows[0]["modality"]),
                         ("70398", "Banco Central", "híbrido"))
        self.assertEqual(rows[0]["requirements"], "Estudiante de Economía")
        self.assertEqual(rows[0]["deadline"], "2026-11-05")
        self.assertIsNone(rows[0]["date"])

    def test_detail_rejects_an_external_or_non_posting_url_before_network(self):
        for url in ("https://example.com/oferta-convocatoria-70398.html",
                    "https://www.practicas.pe/resultados-busqueda.php?q=economia",
                    "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                practicas.detail(url)


if __name__ == "__main__":
    unittest.main()
