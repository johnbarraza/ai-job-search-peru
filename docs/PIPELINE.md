# Flujo sin LLM: `tools/pe_pipeline.py`

Los pasos mecánicos de la búsqueda (buscar, cruzar, fechar, armar la página) corren solos. Lo que pide juicio queda con el agente que uses: el encaje (`/rank`), los CV (`/apply`) y la entrevista (`/interview`). Cada paso escribe un archivo para que Claude, Codex u otro agente lo lea en vez de rehacerlo.

Todo se corre desde la carpeta del perfil (por ejemplo `perfiles/<nombre>`). Necesita Python 3.10+ y [Bun](https://bun.sh). Para SERVIR y Prácticas.pe, instala `pip install -r tools/requirements-servir.txt`.

## El ciclo

```bash
python tools/pe_pipeline.py buscar --detalle                  # refresca la lista
#    (agente) /rank                                             # evalúa las nuevas
python tools/pe_pipeline.py actualizar --titulo "Tu Nombre"   # página y JSON al día
python tools/pe_pipeline.py estado                            # qué hacer hoy
```

Luego abre `reports/revision-ofertas.html`.

## Comandos

### `buscar`: refrescar la lista

Corre cada consulta de `reports/busqueda/consultas.json` con la CLI de su portal y se queda con las ofertas que aún no están en `job_scraper/seen_jobs.json` (cruza por el id del portal, no por el título).

| Opción | Qué hace |
|---|---|
| `--detalle` | Guarda el detalle de cada oferta nueva en `reports/detalles/<li\|bm\|ct\|sv\|pr>_<id>.json`. SERVIR solo entrega los datos visibles en el listado; sus bases completas están en el Word del portal. |
| `--registrar` | Agrega las nuevas a `seen_jobs.json` con `status: "new"`, para que `/rank` las encuentre. Sin esta opción no toca el estado. |
| `--portal linkedin` | Solo las consultas de ese portal; también acepta `talento-peru` y `practicas-pe`. |
| `--jobage 7` | Cambia la antigüedad máxima (días) del archivo de consultas. |
| `--perfil CLAUDE.md` | De dónde lee la línea `Abroad:`. |

Escribe:
- `reports/busqueda/<fecha>_<hora>/<portal>_<consulta>.json`: cada resultado, en una carpeta por corrida; nunca pisa una corrida anterior;
- `reports/busqueda/<fecha>_<hora>/nuevas.json` y `nuevas-anteriores.json`: resultados nuevos de esa corrida y copia de la lista que estaba activa antes de reemplazarla;
- `reports/busqueda/nuevas.json`: las nuevas, con portal, título, empresa, lugar, fecha, modalidad, nivel y la consulta que las trajo;
- `reports/busqueda/cobertura.json` (y una copia en la carpeta de la corrida): por portal, cuántas consultas corrieron, cuántas fallaron, cuántos resultados hubo y los mensajes de error.

**LinkedIn** responde `429 Too Many Requests` si recibe muchas consultas seguidas. `buscar` espera 6 segundos entre una y otra y, tras dos 429 seguidos, deja de consultarlo en esa corrida y anota las omitidas como fallidas. Vuelve a correr más tarde con `--portal linkedin`.

### `portales`: cobertura y enlaces

Sin usar la red, rehace `cobertura.json` con el registro de portales y la última corrida de cada CLI. Si nunca corriste `buscar`, toma la cobertura de lo que dejaron las búsquedas anteriores en `reports/busqueda*/` (un `.json` por consulta, un `.json.err` por consulta fallida). Imprime la tabla y, para los portales sin CLI, los enlaces de búsqueda de cada consulta manual. `--json` la imprime como datos.

### `actualizar`: página y JSON al día

1. cobertura de portales;
2. duplicados entre portales (`tools/pe_duplicados.py --write`, anota `duplicate_of` en `seen_jobs.json`);
3. frescura (`tools/pe_frescura.py` → `reports/frescura.json`);
4. lugar y rubro (`tools/pe_contexto.py` → `reports/contexto.json`);
5. logos, solo con `--logos` (descarga);
6. página (`tools/pe_revision.py` → `reports/revision-ofertas.html`) y su gemelo `reports/revision-ofertas.json`;
7. con `--artifact`, la copia para claude.ai (`reports/revision-ofertas-artifact.html`) y la lista de PDF a adjuntar (`reports/cv_manifest.json`).

Si un paso falla, los demás siguen y el comando termina con código 1.

### `estado`: qué hacer hoy

Lee `reports/revision-ofertas.json` y muestra lo que cierra en 7 días (con cómo postular y si ya hay CV a medida) y las primeras ofertas para postular. `--json` imprime lo mismo como datos, junto con las que aún no tienen CV.

## `consultas.json`

```json
{
 "jobage": 14,
 "excluir_titulo": ["senior", "jefe", "gerente"],
 "consultas_manuales": ["practicante finanzas", "asistente de investigación"],
 "consultas": [
  {"portal": "linkedin", "q": "practicante finanzas", "l": "Lima, Peru"},
  {"portal": "bumeran", "q": "practicante economia", "l": "Lima"},
  {"portal": "computrabajo", "q": "practicante de riesgos", "l": "Lima"},
  {"portal": "talento-peru", "q": "", "l": "Lima", "tipo": "practicas"},
  {"portal": "talento-peru", "q": "economia", "l": "Lima", "tipo": "laboral"},
  {"portal": "practicas-pe", "q": "economia", "l": "Lima"},
  {"portal": "linkedin", "q": "research assistant", "l": "Chile"}
 ],
 "portales": [
  {"id": "otra-bolsa", "nombre": "Bolsa de mi universidad", "metodo": "manual", "home": "https://<la URL de tu bolsa>"}
 ]
}
```

- `portal`: `linkedin`, `bumeran`, `computrabajo`, `talento-peru` o `practicas-pe` (las CLIs instaladas).
- Prácticas.pe consulta por carrera (`q`) y región (`l`: Lima o Perú). Su listado no indica cuándo se publicó cada aviso: `--detalle` guarda esa fecha para las ofertas nuevas. El filtro `jobage` no se aplica en ese portal hasta leer las fichas.
- `campo` para SERVIR (laboral): `perfil` (por defecto) busca la **carrera** que pide el aviso (`economia`, `psicologia`); `puesto` busca palabras del **cargo** (`supervisor`, `analista de recursos humanos`). No son intercambiables: el 25/10, "supervisor" dio 0 como perfil y 13 como puesto, y "psicologia" dio 30 como perfil y 0 como puesto.
- `paginas` (opcional, por consulta o en la raíz del archivo): cuántas páginas leer en Computrabajo y Bumeran (20 avisos cada una) y LinkedIn (10). Por defecto 1, máximo 10; se detiene cuando una página viene vacía o repite la anterior. `paginas_linkedin` en la raíz fija aparte el valor de LinkedIn, que limita las ráfagas. Sin páginas extra, una consulta amplia se queda con lo publicado ese día.
- `tipo` para SERVIR: `laboral`, `practicas` o `ambos`. Una consulta vacía de prácticas obtiene todas las de Lima. SERVIR identifica cada aviso por entidad, título y número de convocatoria, pues no publica URL individual.
- `l`: lugar. Para LinkedIn, `"Lima, Peru"`; para Bumeran y Computrabajo, una región o un distrito (`"San Isidro"`).
- `remote` (opcional): `remote` o `hybrid`, para forzar la modalidad.
- `excluir_titulo`: palabras que descartan una oferta por su título, sin Claude.
- `consultas_manuales`: las consultas para las que se arman enlaces en los portales sin CLI. Si falta, se usan las primeras consultas en Perú.
- `portales` (opcional): cambia o agrega entradas al registro de portales, por `id` (por ejemplo, la URL de la bolsa de tu universidad: `{"id": "bolsa-universidad", "home": "https://..."}`). Esas bolsas exigen iniciar sesión y se buscan a mano.

## Dónde se busca: la línea `Abroad`

La búsqueda es en tu ciudad y país (Lima, Perú). Una consulta con lugar en otro país solo corre si `CLAUDE.md` tiene la línea `Abroad:`:

| Línea en `CLAUDE.md` | `buscar` | La página |
|---|---|---|
| (sin línea) | omite las consultas fuera del Perú | oculta las ofertas del extranjero |
| `- **Abroad:** remote only` (puede nombrar roles) | las corre con `--remote remote` | oculta las del extranjero presenciales o híbridas |
| `- **Abroad:** open to relocating` u `open to travel` | las corre tal cual | muestra todas |

La página dice qué regla aplica y tiene un interruptor para ver lo que deja fuera (se recuerda en ese navegador). `/rank` aplica la misma regla en la dimensión de lugar (`04-job-evaluation.md`, sección 4), y `/setup` la pregunta.

## Cómo lo usa cualquier agente

| Archivo | Qué tiene | Pídele |
|---|---|---|
| `reports/revision-ofertas.json` | Cada oferta: acción, encaje, cierre, frescura, lugar, si es del extranjero, cómo postular, CV, fortalezas, brechas, calificaciones | "lee reports/revision-ofertas.json y dime qué hago hoy" |
| `reports/busqueda/nuevas.json` | Lo nuevo de la última búsqueda, sin evaluar | "/rank sobre reports/busqueda/nuevas.json" |
| `reports/busqueda/cobertura.json` | Portales, errores y enlaces | "¿qué portales fallaron en la última búsqueda?" |
| `python tools/pe_pipeline.py estado --json` | El resumen del día | (Claude lo corre) |

## La página

`reports/revision-ofertas.html` es un solo archivo, sin conexiones a internet: se abre con doble clic, en el teléfono o en cualquier hosting estático. Tiene:

- "Cierran en los próximos 7 días" en un acordeón, con cómo postular, el CV y "Ver ficha";
- **Para decidir**, en lista por defecto, con carrusel opcional (‹ ›, teclas ← →, deslizar, una cinta de colores por acción para saltar); filtros por texto, acción, modalidad, frescura, zona, cierre y revisadas, más accesos rápidos por modalidad, distrito, rubro y tipo de empresa; orden por acción, encaje, cierre, frescura, reputación o puesto;
- por oferta: encaje, fortalezas, brechas, **frescura vacante**, cómo postular, **CV sugerido** cuando ya hay un PDF a medida (descargable desde la página), contactos, personas del área, lugar y rubro, reputación;
- las ofertas cuya fecha de cierre ya pasó, aparte ("Cerradas"), igual que las descartadas;
- la regla de `Abroad` y la cobertura de portales;
- tema claro u oscuro (sigue al sistema; ◐ para cambiarlo).

"Revisada" y las preferencias de vista se guardan solo en ese navegador. `--no-embed-cvs` enlaza los PDF en el disco en vez de incrustarlos (archivo más liviano, pero solo funciona en esa computadora).
