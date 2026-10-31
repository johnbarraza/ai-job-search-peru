<p align="center">
  <img src="assets/peru/banner.svg" alt="AI Job Search, edición Perú" width="100%">
</p>

# AI Job Search · Edición Perú

Herramientas para buscar ofertas, revisar requisitos, preparar un CV por puesto y llevar el estado de las postulaciones. Es una adaptación para Perú de [AI Job Search](https://github.com/ai-job-search-dev/ai-job-search), iniciado por Mads Lorentzen.

La búsqueda y la página de revisión funcionan **sin LLM**. Claude Code, Codex y otros agentes pueden usar los mismos JSON y seguir las instrucciones canónicas de [`.claude/`](.claude/) y el perfil de cada persona. Las instrucciones para agentes están en [AGENTS.md](AGENTS.md).

## Empezar

Requiere Python 3.10+; algunos portales también usan [Bun](https://bun.sh). Para instalación completa y configuración del perfil, lee [SETUP.md](SETUP.md).

### 1. Fork and clone

```powershell
gh repo fork johnbarraza/ai-job-search-peru --clone
cd ai-job-search-pe
git submodule update --init --recursive
```

**Privacy:** A fork of this public repository is public. `/setup` writes personal data to tracked files. If you need a private remote, follow [SETUP.md section 8](SETUP.md#8-pulling-upstream-updates-into-your-fork) before running `/setup`.

Si solo necesitas una copia local, clona el repositorio directamente:

```powershell
git clone --recurse-submodules https://github.com/johnbarraza/ai-job-search-peru.git
cd ai-job-search-pe
```

Carga las dependencias de los portales que vayas a usar con `python tools/install_portal_clis.py`.

Crea un perfil con `/setup --user <nombre>` desde Claude Code, o sigue [SETUP.md](SETUP.md) para preparar los archivos a mano. Ejecuta la búsqueda desde la carpeta del perfil; `perfiles/` está ignorada por Git.

```powershell
cd perfiles/<nombre>
python tools/pe_pipeline.py buscar --detalle --registrar
python tools/pe_pipeline.py actualizar --titulo "Tu Nombre"
python tools/pe_pipeline.py estado
```

Abre `reports/revision-ofertas.html` en el navegador. La página muestra ofertas, filtros, vencimientos, cobertura de portales y los PDF ya preparados mediante **CV sugerido**. Los datos equivalentes quedan en `reports/revision-ofertas.json`; las ofertas nuevas están en `reports/busqueda/nuevas.json`. Un agente puede leer esos JSON y continuar con `/rank` y `/apply` sin repetir la búsqueda.

Las consultas, los archivos de salida y cada opción del CLI se explican en [docs/PIPELINE.md](docs/PIPELINE.md).

## Portales

| Fuente | Acceso en este proyecto |
|---|---|
| LinkedIn, Bumeran, Computrabajo, Get on Board | CLI de búsqueda pública en `.agents/skills/` |
| Talento Perú / SERVIR, Prácticas.pe | CLI local, también integrado en el pipeline |
| Bolsa UP / Symplicity | CLI optativo con Chrome y sesión personal; búsqueda, fichas, solicitudes y formulario interno de postulación |
| Laborum, Empleos Perú, Indeed, Glassdoor | Enlaces o herramientas optativas según el portal; cobertura y límites documentados |

Para ver qué portales se consultaron realmente en tu última corrida, usa `python tools/pe_pipeline.py portales`. La lista detallada, errores y límites están en [docs/PORTALES.md](docs/PORTALES.md).

**Bolsa UP:** configura `symplicity_account` y una consulta `bolsa-universidad` en el perfil; inicia sesión tú mismo en Chrome. El pipeline busca y sincroniza «Solicitudes». La carga de un CV y el envío por el formulario interno son comandos separados que requieren `--confirm`. La guía de comandos, privacidad y caducidad de la sesión está en [docs/SYMPLICITY.md](docs/SYMPLICITY.md). La ruta para agentes es [bolsa-up-search](.agents/skills/bolsa-up-search/SKILL.md).

## CV y privacidad

El botón **CV sugerido** descarga un PDF que ya fue preparado y asociado a esa oferta; no genera contenido al pulsarlo. Las reglas de selección por rol, evidencia, estudios y formato están en [10-peru-market.md](.claude/skills/job-application-assistant/10-peru-market.md). Las instrucciones de postulación están en [apply.md](.claude/commands/apply.md). **La persona revisa el PDF final y decide si se envía**; el agente no interpreta `--confirm` como sustituto de esa decisión.

Los CV, historiales de búsqueda y sesiones autenticadas pertenecen al perfil local. La Bolsa UP guarda su sesión fuera del repositorio, bajo `%LOCALAPPDATA%`, y nunca recibe contraseñas como argumentos del CLI. Antes de publicar cambios, revisa [SETUP.md](SETUP.md) y ejecuta el guardián de privacidad del repositorio.

## Guías

| Documento | Contenido |
|---|---|
| [SETUP.md](SETUP.md) | Instalación y perfiles |
| [docs/PIPELINE.md](docs/PIPELINE.md) | Búsqueda, actualización, JSON y opciones del CLI |
| [docs/PORTALES.md](docs/PORTALES.md) | Cobertura y límites de cada fuente |
| [docs/SYMPLICITY.md](docs/SYMPLICITY.md) | Bolsa UP: sesión, búsqueda, CV y solicitudes |
| [10-peru-market.md](.claude/skills/job-application-assistant/10-peru-market.md) | Reglas del mercado peruano y CV |

Como referencias externas para comparar un CV con una vacante o explorar otros flujos están [Career-Ops](https://github.com/career-ops-hq/career-ops), [Resume Matcher](https://github.com/srbhr/Resume-Matcher) y [Applire](https://github.com/Applire/Applire). No forman parte de este proyecto ni se ha comprobado su eficacia en procesos de selección peruanos.

## Agradecimientos

Gracias al proyecto original [AI Job Search](https://github.com/ai-job-search-dev/ai-job-search) por el flujo de evaluación y CV por vacante. [latex-document-skill](https://github.com/ndpvt-web/latex-document-skill) sirvió de referencia para reforzar la revisión de PDF, la legibilidad para ATS y la vista previa visual. No se incorporaron sus plantillas ni su código.
