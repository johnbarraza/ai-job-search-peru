#!/usr/bin/env bash
# Instalación completa (recomendada): instala lo que falte de Python, Bun y LaTeX, y luego corre instalar_basico.sh.
# Windows (Git Bash, usa winget), macOS (usa Homebrew) y Linux (usa apt):  bash instalar_full.sh
# Solo instala lo que falta. Se puede repetir sin riesgo. Git ya debe estar: en Windows trae el Git Bash que corre esto.
set -eu
cd "$(dirname "$0")"

has_python() {
  for cmd in python3 python py; do
    # The Windows Store alias answers "python" without being Python: ask it to run code.
    command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null && return 0
  done
  return 1
}
missing() { command -v "$1" >/dev/null 2>&1 && return 1 || return 0; }

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    missing winget && { echo "Falta winget (App Installer de la Microsoft Store). Instálalo y repite: bash instalar_full.sh" >&2; exit 1; }
    get() { echo "Instalando $1..."; winget install --id "$1" -e --silent --accept-package-agreements --accept-source-agreements || true; }
    has_python || get Python.Python.3.12
    missing bun && get Oven-sh.Bun
    if missing lualatex; then get MiKTeX.MiKTeX; fi
    # This shell does not see the new PATH until it is reopened: add the usual install folders by hand.
    local_app="$(cygpath -u "${LOCALAPPDATA:-$HOME/AppData/Local}")"
    export PATH="$local_app/Programs/Python/Python312:$local_app/Programs/Python/Python312/Scripts:/c/Program Files/Python312:$local_app/Microsoft/WinGet/Links:$HOME/.bun/bin:$local_app/Programs/MiKTeX/miktex/bin/x64:/c/Program Files/MiKTeX/miktex/bin/x64:$PATH"
    # MiKTeX asks in a window for each missing package; let it fetch them without asking.
    command -v initexmf >/dev/null 2>&1 && initexmf --set-config-value '[MPM]AutoInstall=1' >/dev/null 2>&1 || true
    ;;
  Darwin)
    if missing brew; then
      echo "Falta Homebrew. Instálalo con el comando de https://brew.sh y repite: bash instalar_full.sh" >&2; exit 1
    fi
    has_python || brew install python@3.12
    missing bun && brew install oven-sh/bun/bun
    missing lualatex && [ ! -x /Library/TeX/texbin/lualatex ] && brew install --cask mactex-no-gui  # pide tu contraseña
    export PATH="$(brew --prefix)/bin:/Library/TeX/texbin:$PATH"
    ;;
  *)
    if missing apt-get; then
      echo "Este script instala con apt (Debian/Ubuntu). En otra distribución instala Python 3.10+, Bun y TeX Live (luatex, xetex) y corre: bash instalar_basico.sh" >&2; exit 1
    fi
    pkgs=""
    has_python || pkgs="python3 python3-pip"
    pkgs="$pkgs python3-venv curl unzip"
    missing lualatex && pkgs="$pkgs texlive-luatex texlive-xetex texlive-latex-extra texlive-fonts-extra"
    sudo apt-get update && sudo apt-get install -y $pkgs
    missing bun && [ ! -x "$HOME/.bun/bin/bun" ] && curl -fsSL https://bun.sh/install | bash
    export PATH="$HOME/.bun/bin:$PATH"
    ;;
esac

missing bun && echo "Aviso: Bun no quedó instalado; abre una terminal nueva y repite: bash instalar_full.sh" >&2
missing lualatex && echo "Aviso: LaTeX no quedó instalado; abre una terminal nueva y repite: bash instalar_full.sh" >&2
exec bash instalar_basico.sh
