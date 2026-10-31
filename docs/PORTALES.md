# Portales: cobertura, pruebas y errores

Qué portales cubre la búsqueda, cómo, y qué se sabe de cada uno. El estado de cada corrida no se escribe aquí: lo registra `python tools/pe_pipeline.py buscar` en `reports/busqueda/cobertura.json` y se ve con `python tools/pe_pipeline.py portales` o en la sección "Portales y cobertura" de la página. Este archivo guarda lo que no cambia entre corridas: método, pruebas y límites conocidos.

**Última revisión general:** 2026-10-25. **Bolsa UP:** 2026-10-30.

## Resumen

| Portal | Método | Estado comprobado | Para qué sirve |
|---|---|---|---|
| LinkedIn Jobs | CLI `linkedin-search` | Funciona; responde 429 a las ráfagas | Prácticas y analistas junior en bancos, consultoras y grandes empresas |
| Bolsa UP / Symplicity | CLI local optativo `tools/pe_symplicity.py` | Búsqueda, fichas, solicitudes, CV guardados y envío desde el formulario interno verificados | Oportunidades para estudiantes y egresados UP |
| Talento Perú (SERVIR) | CLI `tools/pe_servir.py` | Lista, detalle «Ver más» y Word DOCX probados | Ministerios, reguladores y entidades públicas |
| Convocatorias ONPE | Enlace manual | Portal de reclutamiento con cuenta | Procesos electorales y trabajo de campo; confirmar provincia exacta |
| Bumeran | CLI `bumeran-search` | Funciona | Prácticas, asistentes, analistas junior y finanzas |
| Laborum | Enlace · `jev-search` | Responde; resultados por JavaScript | Empresas privadas: administración, finanzas, análisis |
| Indeed Perú | Enlace manual · JobSpy opcional | Consulta de JobSpy probada el 25/10/2026 | Vacantes de muchas empresas y portales |
| Glassdoor Perú | Enlace manual | JobSpy omite Perú | Prácticas y puestos junior; sueldos y reseñas |
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

### Bolsa UP / Symplicity (CLI local optativo)

- Para la Bolsa UP hay un CLI de Chrome que usa la sesión de la cuenta indicada por el perfil; la persona introduce sus credenciales y el PIN en el navegador. Se documenta en [SYMPLICITY.md](SYMPLICITY.md). No se usan credenciales en la terminal ni se suben a Git. La sesión local caduca según `--remember-hours`; si el servidor la cierra antes, hay que repetir `login`.
- El pipeline ejecuta sus consultas solo cuando un perfil añade entradas `{"portal":"bolsa-universidad","q":"..."}` y `"symplicity_account":"mi-cuenta"` en su `consultas.json`. `q` puede ser vacío para ver todos los avisos por fecha; `paginas` permite avanzar. El CLI lee «Solicitudes» y marca los avisos ya postulados al registrar, incluso si no hay avisos nuevos. Con `--detalle` lee descripción, requisitos, modalidad y cierre de cada aviso nuevo. No conoce la fecha exacta de publicación, solo la antigüedad relativa que muestra el portal. Si no se configura, el portal conserva su enlace manual.
- El mismo CLI lista los CV guardados y los disponibles en el formulario. `upload-resume`, `delete-resume` y `submit-application` muestran un plan por defecto y requieren `--confirm` para cambiar la cuenta. Guardar un CV y enviar la solicitud son acciones distintas; `submit-application` verifica el envío en «Solicitudes» y evita duplicarlo. Solo cubre formularios que se abren dentro de Bolsa UP; algunas vacantes llevan al sitio del empleador. El cupo total de CV de la cuenta no está verificado.

### Talento Perú (SERVIR) (CLI)

- `tools/pe_servir.py` consulta el formulario JSF y conserva el estado de vista en cada página. No necesita navegador ni LLM. Instala sus dependencias con `pip install -r tools/requirements-servir.txt`.
- Ejemplos: `python tools/pe_servir.py search -q economia -l Lima --tipo laboral` (carrera), `python tools/pe_servir.py search -q supervisor -l Lima --tipo laboral --campo puesto` (cargo) y `python tools/pe_servir.py search -l Lima --tipo practicas`.
- **Perfil o puesto:** el formulario laboral tiene dos campos. `--campo perfil` (por defecto) filtra por la carrera que pide la convocatoria; `--campo puesto`, por el nombre del cargo. Una palabra de cargo buscada como perfil devuelve 0 sin error. `buscar --portal talento-peru` ejecuta las consultas guardadas del perfil.
- SERVIR no ofrece una URL individual estable: el resultado guarda número de convocatoria, entidad y `search_url` del formulario. `url` queda vacío, y la página muestra «Buscar en SERVIR» por separado; así el título no promete abrir una ficha que no existe. Si la entidad publica una ficha oficial exacta, se puede registrar su enlace en `reports/busqueda/enlaces_oficiales.json` del perfil (`{"SERVIR:ID":"https://..."}`); entonces el título sí la abre. `python tools/pe_servir.py detail SERVIR:ID --titulo "TÍTULO" --tipo laboral --ref "NÚMERO"` abre «Ver más» y extrae los requisitos y enlaces de la entidad. El Word es un resumen opcional: se descarga solo al añadir `--word reports/detalles/SERVIR_ID.docx`. **Fecha Fin/Cierre de Publicación** es el vencimiento en el portal (`validThrough`), no la fecha de postulación.

### Convocatorias ONPE (manual)

- [Portal de reclutamiento de ONPE](https://reclutamiento.onpe.gob.pe/convocatorias). Algunas convocatorias electorales son trabajo de campo, pero la sede puede estar fuera de Lima Metropolitana aunque un agregador las etiquete como «Lima». Elige provincia y verifica plazo en la ficha oficial. El portal de acceso requiere cuenta; `buscar` no entra en ella.

### Bumeran (CLI)

- API JSON propia del sitio. Filtros por región, distrito, modalidad, nivel y antigüedad del lado del servidor.
- Sin errores registrados. Ojo: una búsqueda de 7 días puede volver vacía en carreras con poco movimiento; 14 días es lo habitual.

### Laborum (enlace)

- `laborum.pe` corre en otra plataforma (Krowdy), no en la de Bumeran. La búsqueda por URL (`/search-jobs?q=...`) responde 200, pero los resultados se cargan con JavaScript: no vienen en el HTML.
- `robots.txt` no bloquea `/search-jobs`.
- **Para darle CLI:** ubicar la API JSON que usa la página. Candidato para `/add-portal`.

### Indeed Perú y Glassdoor Perú (enlaces)

- `pe.indeed.com/jobs?q=...&l=Lima` responde 200 con resultados en el HTML. También probé una consulta pequeña con JobSpy: devolvió cinco ofertas de Indeed Perú. Es una herramienta opcional iniciada por la persona, fuera de las corridas programadas de `buscar`:

  ```bash
  uvx --from python-jobspy jobspy "practicante economía" --site indeed --location Lima --country-indeed Peru -n 5 --hours-old 336 -o reports/busqueda/indeed.jsonl
  ```

  El archivo JSONL se puede abrir sin un LLM o entregar a Claude/Codex para revisar las ofertas. `uvx` instala JobSpy en su caché; usa `uv tool install python-jobspy` si prefieres instalar el comando `jobspy` de forma permanente. La prueba confirmó que el conector responde, no que todas las ofertas estén vigentes o sean relevantes: verifica cada aviso antes de postular.
- Glassdoor muestra ofertas para Lima y datos salariales. El enlace de la cobertura abre la página principal; busca el puesto y Lima dentro del portal. Distingue el sueldo publicado por el empleador de las estimaciones de Glassdoor por puesto o empresa: una estimación no es una oferta salarial.
- JobSpy no declara soporte para Glassdoor Perú. La prueba con `--site glassdoor --country-indeed Peru` respondió `country_indeed peru isn't supported, skipping it`; no produjo ofertas. Usa el enlace manual de Glassdoor para consultar puestos y sueldos.
- Los términos de [Indeed](https://pe.indeed.com/legal) y [Glassdoor](https://www.glassdoor.com/about/terms/) restringen la extracción automatizada sin permiso. Glassdoor también restringe los enlaces directos a secciones internas. JobSpy queda como una consulta opcional a criterio del usuario; no se ejecuta en `buscar` ni se recomienda `jev-search` para estos dos portales.
- Glassdoor indica que sus vacantes se apoyan en los anuncios de Indeed. Al registrar una oferta encontrada allí, comprueba si ya figura en Indeed u otro portal. Sus estimaciones salariales combinan datos aportados por usuarios y otras fuentes; guarda la fuente, moneda, período y fecha si anotas una cifra manualmente.

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
