#!/usr/bin/env bash
# Después de instalar (instalar_full.sh lo llama al final): crea tu perfil, define qué buscar,
# hace la primera búsqueda, abre la página y, si quieres, deja la búsqueda diaria programada.
# Windows (Git Bash), macOS y Linux:  bash empezar.sh   · Se puede repetir: salta lo que ya está.
set -eu
cd "$(dirname "$0")"
ROOT="$PWD"
STEP=0 NAME=""
paso() { STEP=$((STEP + 1)) NAME="$1"; echo; echo "[$STEP/5] $NAME"; }
pausa() { if [ -t 0 ] && [ -z "${NO_PAUSA:-}" ]; then echo; read -r -p "Presiona Enter para cerrar esta ventana..." _ || true; fi; }
trap 'echo; echo "ERROR en el paso $STEP/5 ($NAME). Lee el mensaje de arriba, corrige y vuelve a correr: bash empezar.sh" >&2; pausa' ERR
ask() { local answer=""; read -r -p "$1" answer || true; printf '%s' "$answer"; }
# elegir "question" "default keys" "default label" "key|text"...: numbered options; prints the chosen keys.
elegir() {
  local question="$1" default="$2" label="$3" answer="" out="" n i=1
  shift 3
  local keys=()
  echo "  $question" >&2
  for option in "$@"; do keys+=("${option%%|*}"); echo "    $i) ${option#*|}" >&2; i=$((i + 1)); done
  read -r -p "  Número(s), separados por comas (Enter = $label): " answer || true
  for n in $(printf '%s' "$answer" | tr ',;' '  '); do
    case "$n" in *[!0-9]*) continue ;; esac
    if [ "$n" -ge 1 ] && [ "$n" -le "${#keys[@]}" ]; then out="$out,${keys[$((n - 1))]}"; fi
  done
  printf '%s' "${out#,}"
  [ -n "$out" ] || printf '%s' "$default"
}

py="$ROOT/.venv/Scripts/python.exe"; [ -x "$py" ] || py="$ROOT/.venv/bin/python"
if [ ! -x "$py" ]; then echo "Falta instalar: corre primero  bash instalar_full.sh" >&2; pausa; exit 1; fi
run() { "$py" -X utf8 "$@"; }

paso "tu perfil"
existing=$(run tools/perfiles.py lista 2>/dev/null | awk 'NF {print $1}' | grep -E '^[a-z0-9][a-z0-9-]*$' || true)
[ -n "$existing" ] && echo "  perfiles que ya hay: $(echo $existing)"
raw=$(ask "  Tu nombre (una palabra, ej. ana; si ya tienes perfil, el mismo): ")
nombre=$(run -c 'import re,sys,unicodedata as u; s=u.normalize("NFKD",sys.argv[1].strip().lower()); s="".join(c for c in s if not u.combining(c)); print(re.sub(r"[^a-z0-9]+","-",s).strip("-")[:40])' "$raw")
if [ -z "$nombre" ]; then echo "  Hace falta un nombre." >&2; false; fi
PERFIL="$ROOT/perfiles/$nombre"
if [ -d "$PERFIL" ]; then echo "  ya existe: perfiles/$nombre"; else run tools/perfiles.py crear "$nombre"; fi

paso "tus datos y tu CV (con un agente de IA, opcional)"
if grep -q "\[YOUR_NAME\]" "$PERFIL/CLAUDE.md" 2>/dev/null; then
  if command -v claude >/dev/null 2>&1 && [ -t 0 ]; then
    if [ "$(ask '  ¿Abrir Claude Code ahora para armar tu perfil con /setup? [S/n] ')" != "n" ]; then
      case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
          # Git Bash's window (mintty) is not a full Windows console and interactive programs can
          # misbehave there: Claude Code gets its own PowerShell window, and this one waits.
          ps_cmd="Start-Process powershell -WorkingDirectory '$(cygpath -w "$ROOT")' -ArgumentList '-NoExit -Command claude ''/setup --user $nombre'''"
          if powershell.exe -NoProfile -Command "$ps_cmd"; then
            echo "  Se abrió Claude Code en otra ventana. Responde sus preguntas ahí."
            ask "  Cuando termine /setup, vuelve a esta ventana y presiona Enter para seguir... " >/dev/null
          else
            echo "  No pude abrir otra ventana. Abre PowerShell en esta carpeta, escribe  claude  y luego  /setup --user $nombre"
            ask "  Cuando termines, presiona Enter aquí para seguir... " >/dev/null
          fi ;;
        *)
          echo "  Se abre Claude Code. Responde sus preguntas; al terminar escribe /exit y esto sigue."
          claude "/setup --user $nombre" || echo "  Claude Code no abrió bien: ábrelo tú en esta carpeta con  claude  y escribe  /setup --user $nombre" ;;
      esac
    fi
  elif command -v agy >/dev/null 2>&1; then
    echo "  Después abre agy en perfiles/$nombre y pídele: «sigue .claude/commands/setup.md para armar mi perfil»."
  else
    echo "  Sin agente: completa perfiles/$nombre/CLAUDE.md a mano (docs/INSTALAR.md, paso 7) o instala uno (docs/AGENTES_IA.md)."
    echo "  La búsqueda funciona igual sin eso."
  fi
else
  echo "  ya está"
fi

paso "qué buscar"
cd "$PERFIL"
if ! run tools/pe_pipeline.py consultas --es-ejemplo; then
  echo "  ya está: reports/busqueda/consultas.json"
else
  sector=$(elegir "¿En qué sector quieres trabajar? (puedes elegir varios)" "privado,publico,investigacion,otros" "todos" \
    "privado|Sector privado (empresas, bancos, consultoras...)" \
    "publico|Sector público (SERVIR: ministerios, BCRP, SUNAT, SBS...)" \
    "investigacion|Investigación (research: universidades, centros de estudio)" \
    "otros|Otros (ONG, organismos internacionales)")
  modo=$(elegir "¿Qué modalidad?" "cualquiera" "cualquiera" \
    "remoto|Remoto" "presencial|Presencial" "hibrido|Híbrido" "cualquiera|Cualquiera")
  lugar=$(elegir "¿Dónde?" "lima" "Lima" \
    "lima|Lima, Perú" "peru|Otra ciudad del Perú" \
    "afuera-remoto|Perú y también fuera, solo remoto" "afuera|Perú y también fuera, dispuesto a mudarme o viajar")
  lugar=${lugar%%,*}
  city="Lima"
  [ "$lugar" = "peru" ] && city=$(ask "  ¿Qué ciudad? ") && city=${city:-Lima}
  etapa=$(elegir "¿En qué etapa estás?" "estudiante" "estudiante" \
    "estudiante|Estudiante universitario: busco prácticas preprofesionales" \
    "egresado|Egresado o bachiller: prácticas profesionales, trainee, analista junior" \
    "profesional|Ya trabajo: analista, asociado, junior")
  etapa=${etapa%%,*}
  q=$(ask "  ¿Qué puestos buscas? Separa con comas (ej. practicante finanzas, analista de riesgos): ")
  run tools/pe_pipeline.py consultas --q "$q" --ciudad "$city" --sector "$sector" --modalidad "$modo" \
    --lugar "$lugar" --etapa "$etapa"
fi

paso "primera búsqueda (5 a 15 minutos; puedes dejarla corriendo)"
run tools/pe_pipeline.py diario --titulo "$raw" || echo "  Algún paso falló; el detalle está arriba y en reports/busqueda/diario.log. Lo que sí terminó quedó guardado."
page="$PERFIL/reports/revision-ofertas.html"
if [ -f "$page" ]; then
  echo "  Página: $page"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) start "" "$page" 2>/dev/null || true ;;
    Darwin) open "$page" || true ;;
    *) xdg-open "$page" >/dev/null 2>&1 || true ;;
  esac
fi

paso "búsqueda diaria (opcional)"
hora=$(ask "  ¿A qué hora buscar sola todos los días? (ej. 8:00 am; Enter = no programar): ")
if [ -n "$hora" ]; then
  ia=()
  if command -v claude >/dev/null 2>&1 && [ "$(ask '  ¿Que además Claude Code las evalúe (/rank) cada día? [s/N] ')" = "s" ]; then ia=(--ia claude); fi
  run tools/pe_pipeline.py programar --hora "$hora" ${ia[@]+"${ia[@]}"} --titulo "$raw"
else
  echo "  Sin programar. Cuando quieras: cd perfiles/$nombre && python tools/pe_pipeline.py programar --hora \"8:00 am\""
fi

cat <<EOF

Listo. Tu espacio es perfiles/$nombre. Para el día a día (desde esa carpeta):
  python tools/pe_pipeline.py diario             busca y rehace la página
  python tools/pe_pipeline.py evaluar --ia claude pasa las nuevas por /rank (o con el botón de la página)
Antes, en cada terminal nueva:  activa el entorno (.venv) como dice docs/INSTALAR.md, paso 5.
EOF
pausa
