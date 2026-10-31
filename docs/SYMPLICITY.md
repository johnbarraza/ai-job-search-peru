# Bolsa UP (Symplicity)

La Bolsa UP requiere una cuenta personal. `tools/pe_symplicity.py` usa Chrome mediante Playwright: la persona introduce usuario, contraseña y PIN directamente en el navegador. El CLI no acepta credenciales como argumentos, no exporta cookies y no requiere LLM ni servidor MCP. La sesión local caduca según el plazo elegido o antes si Symplicity la cierra.

## Preparar la sesión

Desde la raíz del repositorio, en Windows:

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r tools/requirements-symplicity.txt
.venv/Scripts/python.exe tools/pe_symplicity.py login --account mi-cuenta --remember-hours 24
.venv/Scripts/python.exe tools/pe_symplicity.py status --account mi-cuenta
```

`--account` es un alias local que eliges tú. Usa el mismo valor en todos los comandos y en `symplicity_account` del perfil. `--remember-hours` admite de 1 a 720 horas; si el PIN tarda, `login --timeout 900` amplía la espera. El estado autenticado queda fuera del repositorio, en `%LOCALAPPDATA%\ai-job-search-pe\symplicity\mi-cuenta`: `auth.dpapi` está cifrado para la cuenta de Windows y `chrome/` contiene datos de navegador sensibles. No copies ni sincronices esa carpeta. `logout --account mi-cuenta` la elimina.

Si aparece `LOGIN_REQUIRED`, repite `login`. El CLI no intenta mantener viva la sesión ni saltarse el PIN. Ejecuta los comandos de una misma cuenta **uno por vez** porque comparten el perfil de Chrome.

## Buscar y leer ofertas

```powershell
.venv/Scripts/python.exe tools/pe_symplicity.py search --account mi-cuenta
.venv/Scripts/python.exe tools/pe_symplicity.py search --account mi-cuenta -q "practicante finanzas" --page 2
.venv/Scripts/python.exe tools/pe_symplicity.py detail ID_OFERTA --account mi-cuenta
.venv/Scripts/python.exe tools/pe_symplicity.py applications --account mi-cuenta
```

`search` sin `-q` muestra todas las ofertas por fecha; cada página contiene hasta 20. El resultado JSON incluye ID, puesto, empresa, enlace y cierre cuando la lista lo informa. `detail` lee los requisitos, plazo y el campo **Remoto/Presencial** de la ficha: «En las instalaciones», «Híbrido» o «Remoto». No infieras modalidad o distrito a partir de la dirección de la empresa. La antigüedad relativa (`2d`) no equivale a una fecha exacta de publicación. `applications` comprueba «Solicitudes» y permite verificar un envío.

Para guardar las búsquedas de un perfil, añade a su archivo privado `reports/busqueda/consultas.json`:

```json
{
  "symplicity_account": "mi-cuenta",
  "consultas": [
    {"portal": "bolsa-universidad", "q": "", "paginas": 2},
    {"portal": "bolsa-universidad", "q": "practicante finanzas", "paginas": 2}
  ]
}
```

Conserva también las otras consultas existentes. Desde la carpeta de ese perfil ejecuta:

```powershell
../../.venv/Scripts/python.exe tools/pe_pipeline.py buscar --portal bolsa-universidad --detalle --registrar
python tools/pe_pipeline.py actualizar --titulo "Tu Nombre"
```

El primer comando usa Playwright instalado en `.venv` de la raíz; adapta la ruta si usas otro Python. `--detalle` guarda fichas en `reports/detalles/up_<id>.json`. `--registrar` añade avisos nuevos a `seen_jobs.json`, lee «Solicitudes» y marca las postulaciones verificadas, incluso cuando no haya ofertas nuevas. La corrida conserva `up_solicitudes.json` dentro de `reports/busqueda/<fecha_hora>/`. Más opciones y salidas: [PIPELINE.md](PIPELINE.md).

## CV y postulación

Antes de postular, lee la ficha completa y verifica los requisitos formales, el ciclo académico, el cierre y la modalidad. Prepara un PDF específico para el puesto y muéstralo a la persona junto con la vacante y cualquier requisito pendiente de confirmar. El envío requiere su decisión sobre ese PDF y esa vacante; si ya los revisó y pidió enviarlo, continúa. Conserva una copia del archivo exacto que enviarás. El CV usado en una solicitud anterior no cambia cuando editas el PDF local.

```powershell
.venv/Scripts/python.exe tools/pe_symplicity.py resumes --account mi-cuenta
.venv/Scripts/python.exe tools/pe_symplicity.py application-options ID_OFERTA --account mi-cuenta
.venv/Scripts/python.exe tools/pe_symplicity.py upload-resume ID_OFERTA "ruta/al/cv.pdf" --label "CV_puesto" --account mi-cuenta
.venv/Scripts/python.exe tools/pe_symplicity.py submit-application ID_OFERTA --resume "CV_puesto" --account mi-cuenta
```

`resumes` lista los CV sin mostrar los enlaces firmados a sus PDF. `application-options` lista los nombres disponibles en el formulario. `upload-resume` y `submit-application` **solo muestran un plan** en los ejemplos anteriores. Cuando ya esté revisado el PDF y se haya decidido postular, repite **cada comando por separado** con `--confirm`. La carga pulsa «Guardar» y comprueba el CV en «Mis documentos»; no envía la solicitud. El envío selecciona el nombre exacto del CV, pulsa «Enviar» una vez y comprueba el puesto en «Solicitudes». Si la comprobación inmediata falla, consulta `applications` antes de reintentar para evitar duplicados.

Solo se automatiza el formulario interno de Bolsa UP. Si «Solicitar» abre el sitio del empleador, completa allí la postulación. La interfaz muestra un máximo de 64 MB por PDF; el cupo total de CV de la cuenta no está verificado. Si necesitas borrar uno, usa `delete-resume --name "CV_antiguo" --account mi-cuenta` para ver el plan y luego `--confirm` tras elegirlo expresamente. No se sabe si borrar un CV afecta a solicitudes anteriores, por lo que no se hace limpieza automática.

La ruta canónica para agentes está en [apply.md](../.claude/commands/apply.md); la búsqueda general está en [job-scraper/SKILL.md](../.claude/skills/job-scraper/SKILL.md). La [skill portátil](../.agents/skills/bolsa-up-search/SKILL.md) es una referencia breve y queda desactivada para búsquedas genéricas porque requiere una cuenta personal.
