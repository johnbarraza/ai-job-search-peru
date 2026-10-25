# Portales: cobertura, pruebas y errores

Qué portales cubre la búsqueda, cómo, y qué se sabe de cada uno. El estado de cada corrida no se escribe aquí: lo registra `python tools/pe_pipeline.py buscar` en `reports/busqueda/cobertura.json` y se ve con `python tools/pe_pipeline.py portales` o en la sección "Portales y cobertura" de la página. Este archivo guarda lo que no cambia entre corridas: método, pruebas y límites conocidos.

**Última revisión:** 2026-10-24.

## Resumen

| Portal | Método | Estado al 24/10/2026 | Para qué sirve |
|---|---|---|---|
| LinkedIn Jobs | CLI `linkedin-search` | Funciona; responde 429 a las ráfagas | Prácticas y analistas junior en bancos, consultoras y grandes empresas |
| Bolsa de tu universidad | A mano (con tu cuenta) | Pide iniciar sesión | Oportunidades para estudiantes y egresados |
| Talento Perú (SERVIR) | CLI `tools/pe_servir.py` | Formulario JSF probado para laborales y prácticas | Ministerios, reguladores y entidades públicas |
| Bumeran | CLI `bumeran-search` | Funciona | Prácticas, asistentes, analistas junior y finanzas |
| Laborum | Enlace · `jev-search` | Responde; resultados por JavaScript | Empresas privadas: administración, finanzas, análisis |
| Indeed Perú | Enlace manual | Búsqueda pública | Vacantes de muchas empresas y portales |
| Glassdoor Perú | Enlace manual | Ofertas en Lima | Prácticas y puestos junior; reseñas de empresas |
| Computrabajo | CLI `computrabajo-search` | Funciona | Mucho volumen; hay que filtrar |
| Empleos Perú (MTPE) | Enlace · `jev-search` | Responde | Bolsa pública del Ministerio de Trabajo |
| Prácticas.pe | CLI `tools/pe_practicas.py` | Búsqueda por `q` y `dep`, fichas individuales | Prácticas públicas y privadas en Lima |

**Métodos:**
- **CLI**: `buscar` lo consulta solo.
- **Enlace**: `portales` y la página dan un enlace de búsqueda por consulta (o la portada, si el portal no tiene búsqueda por URL). Las ofertas que encuentres se pegan a Claude para evaluarlas.
- **`jev-search`**: opcional y de pago; abre un navegador real (ver el README).

## Detalle por portal

### LinkedIn Jobs (CLI)

- Páginas públicas, sin iniciar sesión. Con `-l "Lima, Peru"`; para el extranjero, con `--remote remote`.
- **Error conocido: `429 Too Many Requests`.** Aparece cuando se lanzan muchas consultas seguidas. Por eso `buscar` espera 6 s entre consultas y deja de consultar LinkedIn tras dos 429 seguidos. Esas consultas se repiten después con `--portal linkedin`.
- La modalidad solo viene cuando la búsqueda usa `--remote`.

### Bolsa de tu universidad (a mano)

- Las bolsas universitarias (muchas en Symplicity o Handshake) piden iniciar sesión con la cuenta de estudiante, así que no hay búsqueda automática: el pipeline deja un enlace manual.
- Agrega la URL de tu bolsa en `consultas.json` → `"portales": [{"id": "bolsa-universidad", "home": "https://..."}]` para verla en la página. Si una oferta viene de ahí, anota su id de la bolsa en `how_to_apply`.

### Talento Perú (SERVIR) (CLI)

- `tools/pe_servir.py` consulta el formulario JSF y conserva el estado de vista en cada página. No necesita navegador ni LLM. Instala sus dependencias con `pip install -r tools/requirements-servir.txt`.
- Ejemplos: `python tools/pe_servir.py search -q economia -l Lima --tipo laboral` (carrera), `python tools/pe_servir.py search -q supervisor -l Lima --tipo laboral --campo puesto` (cargo) y `python tools/pe_servir.py search -l Lima --tipo practicas`.
- **Perfil o puesto:** el formulario laboral tiene dos campos. `--campo perfil` (por defecto) filtra por la carrera que pide la convocatoria; `--campo puesto`, por el nombre del cargo. Una palabra de cargo buscada como perfil devuelve 0 sin error. `buscar --portal talento-peru` ejecuta las consultas guardadas del perfil.
- Los avisos no tienen URL individual: el resultado guarda el número de convocatoria, la entidad y la URL del buscador. Abre esa página, busca el número y descarga el Word para comprobar bases y plazo. **Fecha Fin/Cierre de Publicación** es el vencimiento en el portal (`validThrough`), no la fecha de postulación.

### Bumeran (CLI)

- API JSON propia del sitio. Filtros por región, distrito, modalidad, nivel y antigüedad del lado del servidor.
- Sin errores registrados. Ojo: una búsqueda de 7 días puede volver vacía en carreras con poco movimiento; 14 días es lo habitual.

### Laborum (enlace)

- `laborum.pe` corre en otra plataforma (Krowdy), no en la de Bumeran. La búsqueda por URL (`/search-jobs?q=...`) responde 200, pero los resultados se cargan con JavaScript: no vienen en el HTML.
- `robots.txt` no bloquea `/search-jobs`.
- **Para darle CLI:** ubicar la API JSON que usa la página. Candidato para `/add-portal`.

### Indeed Perú y Glassdoor Perú (enlaces)

- `pe.indeed.com/jobs?q=...&l=Lima` responde 200 con resultados en el HTML.
- Glassdoor muestra ofertas para Lima, por ejemplo en su [búsqueda de `practicante`](https://www.glassdoor.com/Job/lima-practicante-jobs-SRCH_IL.0%2C4_IC3033968_KO5%2C16.htm). El enlace de la cobertura abre esa búsqueda; cambia las palabras clave dentro del portal.
- JobSpy declara soporte para Indeed Perú, pero no marca Glassdoor Perú entre los países compatibles de su conector de Glassdoor. Que Glassdoor tenga anuncios peruanos no demuestra que ese conector funcione allí.
- Los términos de [Indeed](https://pe.indeed.com/legal) y [Glassdoor](https://www.glassdoor.com/about/terms-2022-12-01/) restringen el scraping automatizado sin permiso. Ambos quedan como enlaces para búsqueda manual; no se incluyen en `buscar` ni se recomiendan por `jev-search`.

### Computrabajo (CLI)

- Sin errores registrados. Mucho volumen y muchas consultoras de selección: la página marca "Recluta para un cliente" y el rubro.
- La fecha visible es la de la última renovación; `pe_frescura.py` lo trata como mínimo ("≥").

### Empleos Perú (MTPE) (enlace)

- `www.empleosperu.gob.pe/portal-mtpe/` responde 200. La búsqueda se hace dentro del portal; no se encontró un enlace de búsqueda estable.

### Prácticas.pe (CLI)

- `resultados-busqueda.php?q=economia&dep=15` muestra convocatorias en Lima y pagina con `page=N`. `tools/pe_practicas.py search -q economia -l Lima` lee título, entidad, requisitos breves, modalidad, ubicación y fecha de fin cuando aparece. `detail <URL>` lee el texto completo y la fecha de publicación.
- `python tools/pe_pipeline.py buscar --portal practicas-pe --detalle` ejecuta las consultas guardadas, evita repetir URLs ya conocidas y conserva el detalle de los avisos nuevos. La búsqueda no aplica `jobage`: esa fecha no está en el listado; el detalle sí la trae.
- La ficha es una fuente secundaria. Antes de postular, comprueba plazo, requisitos y medio de envío en las bases o en la entidad que convoca.

## Cómo se registra la cobertura

`buscar` guarda, por cada portal con CLI, en `reports/busqueda/cobertura.json`:

```json
{"id": "linkedin", "consultas": 45, "ok": 43, "fallidas": 2, "resultados": 410,
 "errores": ["research assistant · Chile: Request failed: 429 Too Many Requests"], "fecha": "2026-10-24"}
```

Los portales sin CLI llevan sus `enlaces` (`{"q": consulta, "url": búsqueda}`). Antes de la primera corrida de `buscar`, `portales` arma la cobertura de lo que dejaron las búsquedas anteriores en `reports/busqueda*/`.

## Agregar o cambiar un portal

- **Con CLI nueva:** `/add-portal` genera el skill en `.agents/skills/<portal>-search/`. Después agrega su prefijo en `PORTALS` y su entrada en `REGISTRY` de `tools/pe_pipeline.py` (con `"metodo": "cli"`). SERVIR usa directamente `tools/pe_servir.py`.
- **Solo enlace:** agrégalo en `REGISTRY` o, para un solo perfil, en `consultas.json` → `"portales"`, con `url` (usa `{q}` para la consulta y `{qs}` para la consulta con guiones) o solo `home`.
- Actualiza la tabla de este archivo y la del README.
