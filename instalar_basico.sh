#!/usr/bin/env bash
# Instalación básica: entorno virtual, librerías de Python y buscadores de portales (los programas ya deben estar).
# Windows (Git Bash), macOS y Linux:  bash instalar_basico.sh
# No instala programas del sistema: si falta Python, Git o Bun, dice cómo instalarlo y se detiene. Para eso: bash instalar_full.sh
# Se puede repetir sin riesgo (por ejemplo, después de un git pull). Guía: docs/INSTALAR.md
set -eu
cd "$(dirname "$0")"
VENV="${VENV:-.venv}"
STEP=0 NAME=""
paso() { STEP=$((STEP + 1)) NAME="$1"; echo; echo "  ($STEP/3) $NAME"; }
# Opened with a double click the window closes at the end: wait for Enter so the result can be read.
pausa() { if [ -t 0 ] && [ -z "${NO_PAUSA:-}" ]; then echo; read -r -p "Presiona Enter para cerrar esta ventana..." _ || true; fi; }
trap 'echo; echo "ERROR en ($STEP/3) $NAME. Lee el mensaje de arriba, corrige y vuelve a correr: bash instalar_basico.sh" >&2; pausa' ERR

paso "buscar Python"
python=""
for cmd in python3 python py; do
  # The Windows Store alias answers "python" without being Python: ask it to run code.
  if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    python="$cmd"; break
  fi
done
if [ -z "$python" ]; then
  echo "Falta Python 3.10 o más nuevo. Windows: winget install --id Python.Python.3.12 -e · Mac: brew install python@3.12" >&2
  echo "Después cierra esta terminal, abre otra y vuelve a correr: bash instalar_basico.sh" >&2
  exit 1
fi
echo "Python: $("$python" --version)"

paso "librerías de Python en $VENV (1 a 3 minutos la primera vez)"
[ -d "$VENV" ] || "$python" -m venv "$VENV"
venv_python="$VENV/bin/python"
[ -x "$venv_python" ] || venv_python="$VENV/Scripts/python.exe"
"$venv_python" -m pip --version >/dev/null 2>&1 || "$venv_python" -m ensurepip --upgrade >/dev/null
"$venv_python" -m pip install --disable-pip-version-check -q -r requirements.txt
echo "Librerías de Python instaladas en $VENV (versiones de requirements.txt)"

paso "buscadores de portales (Bun)"
if command -v bun >/dev/null 2>&1; then
  "$venv_python" tools/install_portal_clis.py
else
  echo "Aviso: falta Bun, así que LinkedIn, Bumeran, Computrabajo y Get on Board no buscarán." >&2
  echo "  Windows: winget install --id Oven-sh.Bun -e · Mac: brew install oven-sh/bun/bun · luego repite: bash instalar_basico.sh" >&2
fi
command -v lualatex >/dev/null 2>&1 || echo "Aviso: falta LaTeX (lualatex); solo hace falta para generar los CV en PDF."

if [ -f "$VENV/bin/activate" ]; then activate="source $VENV/bin/activate"; else activate="$VENV\Scripts\Activate.ps1  (PowerShell)  o  source $VENV/Scripts/activate  (Git Bash)"; fi
cat <<EOF

Listo: quedó instalado. Todavía no busca nada; faltan 2 pasos (detalle en docs/INSTALAR.md, pasos 7 y 8):

  1. Crea tu perfil (tu CV y lo que buscas). Con un agente: abre Claude Code en esta carpeta y escribe /setup
     Sin agente:  python tools/perfiles.py crear <tu-nombre>   y completa perfiles/<tu-nombre>/CLAUDE.md
  2. Busca y abre la página:
       cd perfiles/<tu-nombre>
       python tools/pe_pipeline.py diario
     La página queda en perfiles/<tu-nombre>/reports/revision-ofertas.html (ábrela con doble clic).

En cada terminal nueva, activa antes el entorno:  $activate
EOF
pausa
