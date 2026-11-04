#!/usr/bin/env bash
# Instalación completa (recomendada): instala lo que falte de Python, Bun y LaTeX, y luego corre instalar_basico.sh.
# Windows (Git Bash, usa winget), macOS (usa Homebrew) y Linux (usa apt):  bash instalar_full.sh
# No lo corras como administrador: instala para tu usuario y, si un programa necesita permiso, Windows lo pide en una ventana.
# Solo instala lo que falta, así que se puede repetir sin riesgo: si algo falla, corrige y vuelve a correrlo.
# Todo lo que muestra queda también en instalar.log.
set -eu
cd "$(dirname "$0")"
exec 3>&1 4>&2  # the terminal itself, for empezar.sh (Claude Code needs it, not the log pipe)
exec > >(tee -a instalar.log) 2>&1
echo "== $(date '+%d/%m/%Y %H:%M') · instalar_full.sh =="

TOTAL=5 STEP=0 NAME=""
paso() {
  STEP=$((STEP + 1)) NAME="$1"
  bar=$(printf '%*s' "$STEP" '' | tr ' ' '#')$(printf '%*s' $((TOTAL - STEP)) '' | tr ' ' '-')
  echo; echo "[$bar] paso $STEP de $TOTAL: $NAME"
}
trap 'echo; echo "ERROR en el paso $STEP de $TOTAL ($NAME). El detalle está arriba y en instalar.log." >&2
      echo "Corrige lo que dice y vuelve a correr: bash instalar_full.sh (salta lo que ya quedó instalado)." >&2
      if [ -t 0 ]; then echo; read -r -p "Presiona Enter para cerrar esta ventana..." _ || true; fi' ERR

has_python() {
  for cmd in python3 python py; do
    # The Windows Store alias answers "python" without being Python: ask it to run code.
    command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null && return 0
  done
  return 1
}
missing() { ! command -v "$1" >/dev/null 2>&1; }
ya() { echo "  ya está: $1"; }

paso "revisar el sistema"
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) os=windows ;;
  Darwin) os=mac ;;
  *) os=linux ;;
esac
echo "  sistema: $os"
if [ "$os" = windows ]; then
  missing winget && { echo "Falta winget (App Installer de la Microsoft Store). Instálalo y repite." >&2; false; }
  get() { echo "  instalando $1 (puede tardar varios minutos)..."; winget install --id "$1" -e --silent --accept-package-agreements --accept-source-agreements || true; }
  # This shell does not see a new PATH until it is reopened: add the usual install folders by hand.
  local_app="$(cygpath -u "${LOCALAPPDATA:-$HOME/AppData/Local}")"
  export PATH="$local_app/Programs/Python/Python312:$local_app/Programs/Python/Python312/Scripts:/c/Program Files/Python312:$local_app/Microsoft/WinGet/Links:$HOME/.bun/bin:$local_app/Programs/MiKTeX/miktex/bin/x64:/c/Program Files/MiKTeX/miktex/bin/x64:$PATH"
elif [ "$os" = mac ]; then
  missing brew && { echo "Falta Homebrew. Instálalo con el comando de https://brew.sh y repite." >&2; false; }
  export PATH="$(brew --prefix)/bin:/Library/TeX/texbin:$PATH"
else
  missing apt-get && { echo "Este script instala con apt (Debian/Ubuntu). En otra distribución instala Python 3.10+, Bun y TeX Live (luatex, xetex) y corre: bash instalar_basico.sh" >&2; false; }
  sudo apt-get update -qq
  export PATH="$HOME/.bun/bin:$PATH"
fi

paso "Python 3.10 o más nuevo"
if has_python; then ya "Python"
elif [ "$os" = windows ]; then get Python.Python.3.12
elif [ "$os" = mac ]; then brew install python@3.12
else sudo apt-get install -y python3 python3-pip python3-venv
fi
[ "$os" = linux ] && sudo apt-get install -y -qq python3-venv curl unzip

paso "Bun (buscadores de LinkedIn, Bumeran, Computrabajo y Get on Board)"
if ! missing bun; then ya "Bun"
elif [ "$os" = windows ]; then get Oven-sh.Bun
elif [ "$os" = mac ]; then brew install oven-sh/bun/bun
else curl -fsSL https://bun.sh/install | bash
fi

paso "LaTeX (para los CV en PDF; es lo más pesado, varios GB)"
if ! missing lualatex; then ya "LaTeX"
elif [ "$os" = windows ]; then get MiKTeX.MiKTeX
elif [ "$os" = mac ]; then echo "  macOS pedirá tu contraseña"; brew install --cask mactex-no-gui
else sudo apt-get install -y texlive-luatex texlive-xetex texlive-latex-extra texlive-fonts-extra
fi
# MiKTeX asks in a window for each missing package; let it fetch them without asking.
if [ "$os" = windows ] && ! missing initexmf; then initexmf --set-config-value '[MPM]AutoInstall=1' >/dev/null 2>&1 || true; fi

paso "entorno de Python y buscadores (instalar_basico.sh)"
missing bun && echo "  Aviso: Bun aún no aparece; si acabas de instalarlo, abre una terminal nueva y repite." >&2
missing lualatex && echo "  Aviso: LaTeX aún no aparece; si acabas de instalarlo, abre una terminal nueva y repite." >&2
trap - ERR
NO_PAUSA=1 bash instalar_basico.sh
# Then straight on: profile, what to search, first search, the page, the daily run.
if [ -t 0 ]; then
  echo
  read -r -p "¿Seguir ahora con tu perfil y la primera búsqueda? [S/n] " seguir || seguir=n
  if [ "$seguir" != "n" ]; then exec bash empezar.sh 1>&3 2>&4; fi
  echo "Cuando quieras: bash empezar.sh"
  read -r -p "Presiona Enter para cerrar esta ventana..." _ || true
else
  echo "Siguiente: bash empezar.sh"
fi
