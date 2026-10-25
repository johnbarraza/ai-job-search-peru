---
name: practicas-search
description: Search live Peruvian internships on Prácticas.pe without an LLM, including Lima and other regions; read an individual convocatoria by URL. Use when the user asks for Prácticas.pe results or public/private internships in Peru.
---

# Prácticas.pe search

From the profile folder, install the shared portal dependencies once:

```bash
pip install -r tools/requirements-servir.txt
```

The CLI checks robots.txt before any request, then reads the public HTML. It never signs in or applies.

```bash
python tools/pe_practicas.py search -q economia -l Lima --limit 100
python tools/pe_practicas.py detail 'https://www.practicas.pe/oferta-convocatoria-...html'
python tools/pe_pipeline.py buscar --portal practicas-pe --detalle
```

`search` returns JSON with `results` (`id`, `title`, `company`, `url`, `location`, `requirements`, `level`, `modality`, `deadline`, `date`). A listing often omits its posting date; `detail` reads it from the individual page. `--detalle` in the pipeline saves new pages and fills their dates. Always verify the deadline and application instructions against the linked convocatoria and official bases before applying.
