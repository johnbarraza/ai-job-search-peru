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
  read -r -p "  Número(s) (Enter = $label, < = volver a la anterior): " answer || true
  case "$answer" in "<"*) printf '<'; return ;; esac
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
nombre=""
for _ in 1 2 3; do
  raw=$(ask "  Tu nombre (una palabra, ej. ana; si ya tienes perfil, el mismo): ")
  nombre=$(run -c 'import re,sys,unicodedata as u; s=u.normalize("NFKD",sys.argv[1].strip().lower()); s="".join(c for c in s if not u.combining(c)); print(re.sub(r"[^a-z0-9]+","-",s).strip("-")[:40])' "$raw")
  [ -n "$nombre" ] && break
  echo "  Hace falta un nombre (es el de la carpeta de tu perfil)."
done
if [ -z "$nombre" ]; then nombre="mi-perfil"; raw="Mi perfil"; echo "  Uso «mi-perfil»."; fi
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
  echo "  Responde con el número; en las que dicen «varios», separa con comas (1,3)."
  echo "  Enter salta la pregunta; < vuelve a la anterior; al final puedes cambiar cualquier respuesta."
  echo "  Solo se busca lo que elijas: si eliges solo sector privado, no se busca en el público."
  sector="" etapa="" ciclo="" merito="" area="" modo="" lugar="" city="Lima" modo_busqueda="lista" kw="" cv_file=""
  pretty() { printf '%s' "${1:-$2}" | sed 's/,/, /g'; }
  # One question by number. Returns 1 when the answer is "<" (go back) and 2 when it does not apply.
  pregunta() {
    local v
    case "$1" in
      1) v=$(elegir "1. ¿En qué sector quieres trabajar? (varios)" "" "todos" \
           "privado|Sector privado (empresas, bancos, consultoras...)" \
           "publico|Sector público (SERVIR: ministerios, BCRP, SUNAT, SBS...)" \
           "investigacion|Investigación (research: universidades, centros de estudio)" \
           "otros|Otros (ONG, organismos internacionales)" \
           "privado,publico,investigacion,otros|Todos")
         [ "$v" = "<" ] && return 1; sector=$v ;;
      2) v=$(elegir "2. ¿Eres estudiante universitario?" "" "omitir" \
           "estudiante|Sí, estudiante: busco prácticas preprofesionales" \
           "egresado|Egresado o bachiller: prácticas profesionales, trainee, analista junior" \
           "profesional|No, ya trabajo: analista, asociado, junior")
         [ "$v" = "<" ] && return 1; etapa=${v%%,*}; [ "$etapa" = "estudiante" ] || ciclo="" ;;
      3) [ "$etapa" = "estudiante" ] || return 2
         v=$(elegir "3. ¿En qué ciclo estás?" "" "omitir" \
           "1-4|1.º a 4.º ciclo" "5-6|5.º o 6.º ciclo" "7-8|7.º u 8.º ciclo" "9+|9.º ciclo o más (último año)")
         [ "$v" = "<" ] && return 1; ciclo=${v%%,*} ;;
      4) v=$(elegir "4. ¿Tienes mérito académico? (muchas prácticas lo piden)" "" "omitir" \
           "decimo|Décimo superior" "quinto|Quinto superior" "tercio|Tercio superior" "medio|Medio superior" "ninguno|Ninguno")
         [ "$v" = "<" ] && return 1; merito=${v%%,*} ;;
      5) v=$(elegir "5. ¿Cómo armamos tus búsquedas? (sin IA)" "lista" "elegir áreas" \
           "lista|Eligiendo áreas de una lista" \
           "palabras|Escribiendo palabras clave (ej. practicante finanzas, riesgos)" \
           "cv|Desde tu CV (PDF, Word o texto): lee tus carreras, tu etapa y tu mérito")
         [ "$v" = "<" ] && return 1
         modo_busqueda=${v%%,*}; kw="" cv_file=""
         if [ "$modo_busqueda" = "palabras" ]; then
           kw=$(ask "  Palabras clave, separadas por comas: "); area=""
           [ -n "$kw" ] || modo_busqueda="lista"
         elif [ "$modo_busqueda" = "cv" ]; then
           cv_file=$(ask "  Arrastra tu CV a esta ventana (o escribe su ruta) y presiona Enter: ")
           cv_file=$(printf '%s' "$cv_file" | tr -d "\"'" | sed 's/^ *//; s/ *$//')
           if [ -f "$cv_file" ] && found=$(run tools/pe_pipeline.py leer-cv "$(cygpath -w "$cv_file" 2>/dev/null || printf '%s' "$cv_file")"); then
             cv_area="" cv_etapa="" cv_ciclo="" cv_merito=""
             eval "$found"  # cv_* assignments from a fixed vocabulary, shell-quoted by pe_pipeline.py
             area=$cv_area
             # The CV fills only what was skipped: an answer given above wins.
             [ -n "$etapa" ] || etapa=$cv_etapa
             [ "$etapa" != "estudiante" ] || [ -n "$ciclo" ] || ciclo=$cv_ciclo
             [ -n "$merito" ] || merito=$cv_merito
             mkdir -p documents/cv && cp "$cv_file" documents/cv/ 2>/dev/null || true
             echo "  Leí tu CV (sin IA): áreas $(pretty "$area" 'no claras'); etapa $(pretty "$etapa" '—'); mérito $(pretty "$merito" '—')."
             echo "  Lo copié a documents/cv/ de tu perfil. Revisa el resumen de abajo: puedes corregir cualquier cosa."
           else
             echo "  No encontré o no pude leer ese archivo. Elige las áreas de la lista."
             modo_busqueda="lista"
           fi
         fi
         if [ "$modo_busqueda" = "lista" ]; then
           v=$(elegir "   ¿De qué área? (varios)" "" "todas" \
             "economia|Economía" "finanzas|Finanzas" "contabilidad|Contabilidad y auditoría" \
             "administracion|Administración y negocios" "industrial|Ingeniería industrial" "datos|Datos y tecnología" \
             "marketing|Marketing y ventas" "derecho|Derecho" "rrhh|Recursos humanos y psicología" \
             "ingenieria|Otras ingenierías (civil, minas, sistemas)" "comunicaciones|Comunicaciones")
           [ "$v" = "<" ] && return 1; area=$v
         fi ;;
      6) v=$(elegir "6. ¿Qué modalidad? (varios)" "" "cualquiera" \
           "remoto|Remoto" "presencial|Presencial" "hibrido|Híbrido" "cualquiera|Cualquiera")
         [ "$v" = "<" ] && return 1; modo=$v ;;
      7) v=$(elegir "7. ¿Dónde?" "" "Lima" \
           "lima|Lima, Perú" "peru|Otra ciudad del Perú" \
           "afuera-remoto|Perú y también fuera, solo remoto" "afuera|Perú y también fuera, dispuesto a mudarme o viajar")
         [ "$v" = "<" ] && return 1; lugar=${v%%,*}; city="Lima"
         if [ "$lugar" = "peru" ]; then city=$(ask "  ¿Qué ciudad? (Enter = Lima): "); city=${city:-Lima}; fi ;;
    esac
    return 0
  }
  i=1 dir=1
  while [ "$i" -le 7 ]; do
    rc=0; pregunta "$i" || rc=$?
    case "$rc" in
      0) i=$((i + 1)); dir=1 ;;
      1) dir=-1; i=$((i > 1 ? i - 1 : 1)) ;;
      *) i=$((i + dir)); [ "$i" -ge 1 ] || { i=1; dir=1; } ;;
    esac
  done
  while true; do
    echo
    echo "  Tus respuestas:"
    echo "    1) Sector: $(pretty "$sector" todos)"
    echo "    2) Etapa: $(pretty "$etapa" '—')"
    [ "$etapa" = "estudiante" ] && echo "    3) Ciclo: $(pretty "$ciclo" '—')"
    echo "    4) Mérito académico: $(pretty "$merito" '—')"
    case "$modo_busqueda" in
      palabras) echo "    5) Búsqueda: palabras clave: $kw" ;;
      cv) echo "    5) Búsqueda: desde tu CV ($(basename "$cv_file")) · áreas: $(pretty "$area" 'no claras: busca en general')" ;;
      *) echo "    5) Búsqueda: áreas: $(pretty "$area" todas)" ;;
    esac
    echo "    6) Modalidad: $(pretty "$modo" cualquiera)"
    echo "    7) Dónde: $(pretty "$lugar" lima)$([ "$lugar" = "peru" ] && printf ' (%s)' "$city")"
    change=$(ask "  Enter = está bien · o el número de la que quieres cambiar: ")
    case "$change" in
      ""|s|S|si|sí) break ;;
      [1-7]) pregunta "$change" || true ;;
      *) echo "  Escribe un número del 1 al 7, o Enter." ;;
    esac
  done
  args=(--ciudad "$city" --sector "$sector" --modalidad "$modo" --area "$area")
  [ -n "$kw" ] && args+=(--q "$kw")
  [ -n "$lugar" ] && args+=(--lugar "$lugar")
  [ -n "$etapa" ] && args+=(--etapa "$etapa")
  [ -n "$ciclo" ] && args+=(--ciclo "$ciclo")
  [ -n "$merito" ] && args+=(--merito "$merito")
  run tools/pe_pipeline.py consultas "${args[@]}" \
    || echo "  No pude armar las búsquedas; sigue igual. Luego: python tools/pe_pipeline.py consultas --help"
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
