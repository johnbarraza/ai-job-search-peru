# Instalación y uso diario

No hace falta haber usado Python, una terminal ni un entorno virtual. Los ejemplos son para **Windows** con PowerShell; las diferencias en macOS o Linux van marcadas con **(Mac/Linux)**. Ningún paso requiere Claude Code, Codex ni otro modelo: la búsqueda, la página de revisión y los CV funcionan sin IA.

## Programas necesarios

| Programa | Uso en el proyecto | ¿Obligatorio? |
|---|---|---|
| **Git** | Descargar el proyecto y recibir sus actualizaciones | Sí |
| **Python 3.10 o más nuevo** | Corre la búsqueda, la página de revisión y los CV | Sí |
| **Bun** | Corre los buscadores de LinkedIn, Bumeran, Computrabajo y Get on Board | Sí, para buscar en esos portales |
| **MiKTeX** (LaTeX) | Convierte los CV `.tex` en PDF | Solo si vas a generar CV |
| **Google Chrome** | Bolsa UP (inicias sesión tú) | Solo para la Bolsa UP |
| **Claude Code o Codex** | `/setup`, `/rank`, `/apply`: evaluar ofertas y adaptar el CV | No: la búsqueda y la página funcionan sin IA |

## 1. Terminal

La terminal es una ventana donde escribes comandos y presionas Enter.

- **Windows:** tecla Windows, escribe `PowerShell`, Enter.
- **(Mac)** Cmd + Espacio, escribe `Terminal`, Enter.

Cada bloque gris de esta guía es un comando: cópialo, pégalo en la terminal y presiona Enter.

## 2. Instalación de programas

**Windows** (con `winget`, que ya viene en Windows 10 y 11):

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
winget install --id Oven-sh.Bun -e
winget install --id MiKTeX.MiKTeX -e   # solo si vas a generar CV
```

**(Mac)** instala [Homebrew](https://brew.sh) y luego `brew install git python@3.12 oven-sh/bun/bun`; para los CV, `brew install --cask mactex-no-gui`.

**Cierra la terminal y abre una nueva** (si no, no reconoce los programas recién instalados). Comprueba:

```powershell
git --version
python --version
bun --version
```

Cada uno debe responder con un número de versión. Python debe ser 3.10 o mayor. **(Mac/Linux)** usa `python3` en lugar de `python` en toda la guía.

## 3. Descarga del proyecto

Elige una carpeta (por ejemplo, Documentos) y descarga el proyecto ahí:

```powershell
cd ~\Documents
git clone --recurse-submodules https://github.com/johnbarraza/ai-job-search-peru.git
cd ai-job-search-peru
```

Desde aquí, **todos los comandos se escriben dentro de la carpeta `ai-job-search-peru`**. Si cierras la terminal, vuelve a ella con `cd ~\Documents\ai-job-search-peru`.

## 4. Entorno virtual (una sola vez)

Un **entorno virtual** es una carpeta (`.venv`) donde se instalan las librerías de Python de este proyecto, separadas del resto de tu computadora. Así no se mezclan con otros programas y siempre usas las versiones exactas que el proyecto probó.

```powershell
python -m venv .venv
```

## 5. Activación del entorno (en cada terminal nueva)

```powershell
.venv\Scripts\Activate.ps1
```

**(Mac/Linux)** `source .venv/bin/activate`

Funcionó si al inicio de la línea aparece `(.venv)`. **Hay que activarlo cada vez que abres una terminal nueva**; si no, Python dirá que le faltan librerías.

Si Windows responde *"la ejecución de scripts está deshabilitada en este sistema"*, corre esto una vez y vuelve a activar:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## 6. Librerías y buscadores (una sola vez, con el entorno activo)

```powershell
python -m pip install -r requirements.txt
python tools/install_portal_clis.py
```

La primera línea instala las librerías de Python listadas en `requirements.txt`, con versión fija. La segunda instala los buscadores de portales (usa Bun).

## Alternativa: instalación con un script

Los pasos 2 y 4 a 6 se pueden hacer con un solo comando (recomendado), después del paso 3. En Windows se corre desde **Git Bash** (viene con Git: clic derecho en la carpeta del proyecto → *Open Git Bash here*); en Mac o Linux, desde la terminal:

```bash
bash instalar_full.sh
```

`instalar_full.sh` instala lo que falte de Python, Bun y LaTeX (con `winget`, Homebrew o `apt`) y luego corre `instalar_basico.sh`, que crea `.venv`, instala `requirements.txt` y los buscadores. Córrelo como usuario normal, no como administrador. Muestra el avance paso a paso, guarda todo en `instalar.log` y, si algo falla, dice en qué paso: corrige y vuelve a correrlo. Si ya tienes los programas, basta con `bash instalar_basico.sh`: no instala programas del sistema; si falta Python, muestra el comando y se detiene. Se puede repetir sin riesgo, por ejemplo después de un `git pull`. La activación del paso 5 sigue haciendo falta en cada terminal nueva.

## 7. Perfil

Cada persona tiene su propia carpeta en `perfiles/<nombre>/`, con su CV, sus consultas y sus resultados. Esa carpeta no se sube a internet.

- **Con Claude Code:** abre Claude Code en la carpeta del proyecto y escribe `/setup --user tunombre`. Te hace preguntas y llena tu perfil.
- **Sin IA:** `python tools/perfiles.py crear tunombre` y luego completa a mano `perfiles/tunombre/CLAUDE.md` y `perfiles/tunombre/reports/busqueda/consultas.json` (formato en [PIPELINE.md](PIPELINE.md#consultasjson)).

## 8. Uso diario

Con el entorno activo (paso 5):

```powershell
cd perfiles\tunombre
python tools/pe_pipeline.py buscar --detalle --registrar
python tools/pe_pipeline.py actualizar --titulo "Tu Nombre"
python tools/pe_pipeline.py estado
```

1. `buscar` revisa los portales y guarda las ofertas nuevas.
2. `actualizar` arma la página de revisión.
3. `estado` dice qué cierra pronto.

Abre `reports\revision-ofertas.html` con doble clic: es una página que funciona sin internet. Sin IA, la página abre la tabla «Nuevas de la última búsqueda»: fecha, cierre, lugar, modalidad, sueldo cuando el aviso lo publica y el enlace a cada aviso, con filtros por portal y por consulta. Con un agente, `/rank` agrega el encaje con tu perfil y `/apply` prepara el CV. Más opciones en [PIPELINE.md](PIPELINE.md).

LinkedIn se consulta como máximo una vez cada 20 horas, para que no bloquee tu conexión. Si una corrida dice que LinkedIn está "en espera", es normal: los demás portales se buscan igual.

## 9. Actualizaciones

No hace falta desinstalar nada. Desde la carpeta principal del proyecto:

```powershell
git pull
python -m pip install -r requirements.txt
python tools/install_portal_clis.py
python tools/perfiles.py actualizar --todos
```

`git pull` trae lo nuevo, las dos líneas siguientes instalan las librerías que hayan cambiado y la última pasa las mejoras a cada perfil. Tus datos personales no se tocan.

## Errores frecuentes

| Mensaje | Qué hacer |
|---|---|
| `python` / `bun` / `git` *no se reconoce como nombre de un cmdlet* | Cierra la terminal y abre otra. Si sigue, reinstala ese programa (paso 2). |
| Escribir `python` abre la Microsoft Store | Configuración → Aplicaciones → Configuración avanzada de aplicaciones → Alias de ejecución: apaga los de `python.exe` y `python3.exe`. |
| `No module named 'requests'` (o `pypdf`, `bs4`, `playwright`) | El entorno no está activo: paso 5. Si sí lo está, repite el paso 6. |
| *La ejecución de scripts está deshabilitada* | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (paso 5). |
| `lualatex` no se encuentra | Instala MiKTeX (paso 2) y abre una terminal nueva. La primera compilación descarga paquetes: acepta lo que pida. |
| LinkedIn: `429 Too Many Requests` | LinkedIn pidió una pausa. El proyecto la respeta solo: espera 48 horas y no lo vuelve a consultar antes. |

## Instrucciones para un asistente de IA

Si le pides a Claude Code, Codex u otro agente que instale o ejecute el proyecto, pégale esto:

> Instala y ejecuta este proyecto siguiendo `docs/INSTALAR.md`, en este orden:
> 1. Verifica `git --version`, `python --version` (≥ 3.10) y `bun --version`. Si falta alguno, dime el comando de instalación del paso 2 y espera a que lo instale: no instales programas del sistema sin preguntar.
> 2. En la raíz del repositorio, si no existe `.venv`, créalo con `python -m venv .venv`. Usa siempre el Python de `.venv` (`.venv\Scripts\python.exe` en Windows, `.venv/bin/python` en Mac/Linux); nunca instales paquetes en el Python global.
> 3. Instala con `.venv\Scripts\python.exe -m pip install -r requirements.txt` y luego `python tools/install_portal_clis.py`. No cambies versiones de `requirements.txt` ni agregues paquetes sin preguntarme.
> 4. Comprueba con `.venv\Scripts\python.exe -m pytest tests/test_pe_pipeline.py -q` (si pytest no está instalado, omite este paso y dímelo).
> 5. Para buscar, trabaja dentro de `perfiles/<nombre>/` (paso 8). No borres `reports/busqueda/linkedin-control.json`: es la pausa que evita que LinkedIn bloquee la conexión.
> 6. No hagas `git push`, no envíes postulaciones y no corras comandos con `--confirm` sin mi aprobación explícita.
> 7. Al terminar, dime qué instalaste, qué comandos corriste y qué falló, con el mensaje de error exacto.

Las reglas completas para agentes están en [AGENTS.md](../AGENTS.md).
