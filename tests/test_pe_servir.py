"""SERVIR's JSF form and the fields carried into the offline pipeline."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import pe_servir as servir


def page(form, kind, page_number, total, view):
    title = "PRÁCTICA DE ECONOMÍA" if kind == "practicas" else "ANALISTA ECONÓMICO"
    date_label = "Inicio de Publicación" if kind == "practicas" else "Fecha Inicio de Publicación"
    end_label = "Cierre de Publicación" if kind == "practicas" else "Fecha Fin de Publicación"
    return f'''<form id="{form}"><div class="cuadro-vacantes">
      <div class="titulo-vacante"><label>{title}</label></div>
      <div class="nombre-entidad">Banco Público</div>
      <div><span class="sub-titulo">Ubicación:</span><span class="detalle-sp">LIMA - LIMA</span></div>
      <div><span class="sub-titulo">Número de Convocatoria:</span><span class="detalle-sp">123 - 2026</span></div>
      <div><span class="sub-titulo">{date_label}:</span><span class="detalle-sp">20/10/2026</span></div>
      <div><span class="sub-titulo">{end_label}:</span><span class="detalle-sp">30/10/2026</span></div>
      </div><button id="{form}:next">Sig.</button>Página {page_number} de {total}
      <input name="javax.faces.ViewState" value="{view}"></form>'''


class Servir(unittest.TestCase):
    def test_both_listing_types_keep_portal_expiry_separate_from_deadline(self):
        for kind, (_, form, _) in servir.KINDS.items():
            rows, paging, nxt, view = servir.parse_rows(page(form, kind, 1, 2, "v1"), kind, "https://example.org")
            self.assertEqual((paging, nxt, view), ((1, 2), form + ":next", "v1"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["date"], "2026-10-20")
            self.assertEqual(rows[0]["validThrough"], "2026-10-30")
            self.assertNotIn("deadline", rows[0])
            self.assertEqual(rows[0]["ref"], "123 - 2026")

    def test_pagination_uses_the_new_jsf_viewstate(self):
        kind = "laboral"
        form = servir.KINDS[kind][1]

        class Response:
            def __init__(self, body):
                self.text = body

            def raise_for_status(self):
                pass

        class Session:
            def __init__(self):
                self.posts = []

            def get(self, url, timeout):
                return Response(page(form, kind, 1, 2, "v0"))

            def post(self, url, data, headers, timeout):
                self.posts.append(data)
                n = len(self.posts)
                html = page(form, kind, n, 2, "unused")
                return Response(f'<partial-response><changes><update id="{form}"><![CDATA[{html}]]></update>'
                                f'<update id="javax.faces.ViewState"><![CDATA[v{n}]]></update></changes></partial-response>')

        session = Session()
        rows = servir.search_kind(session, kind, "economia", "Lima", 10, None)
        self.assertEqual(len(rows), 1)  # same convocatoria on both pages
        self.assertEqual(session.posts[0]["javax.faces.ViewState"], "v0")
        self.assertEqual(session.posts[1]["javax.faces.ViewState"], "v1")
        self.assertEqual(session.posts[1]["javax.faces.source"], form + ":next")


    def test_both_listings_get_their_own_limit(self):
        calls = []

        def fake(session, kind, query, location, limit, jobage):
            calls.append((kind, limit))
            return [{"id": kind + str(i)} for i in range(limit)]

        old_kind, old_gate = servir.search_kind, servir.robots_check.gate
        servir.search_kind, servir.robots_check.gate = fake, lambda url: (0, "")
        try:
            out = servir.search("economia", "Lima", "ambos", limit=3)
        finally:
            servir.search_kind, servir.robots_check.gate = old_kind, old_gate
        self.assertEqual(calls, [("laboral", 3), ("practicas", 3)])
        self.assertEqual(len(out["results"]), 6)


if __name__ == "__main__":
    unittest.main()
