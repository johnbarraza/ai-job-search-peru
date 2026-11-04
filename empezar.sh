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

paso "con IA o sin IA"
default_ia=lexico default_label="sin IA"
if command -v claude >/dev/null 2>&1; then default_ia=claude default_label="Claude Code"; fi
ia_modo=$(elegir "¿Tienes una suscripción de IA para usar con esto?" "$default_ia" "$default_label" \
  "claude|Sí, Claude Code (recomendado): arma tu perfil, evalúa cada oferta y adapta tu CV" \
  "codex|Sí, Codex (ChatGPT)" \
  "agy|Sí, Antigravity (agy)" \
  "lexico|No: modo sin IA, con preguntas (y tu CV si lo tienes) y puntaje por palabras")
ia_modo=${ia_modo%%,*}
[ "$ia_modo" = "<" ] && ia_modo=$default_ia
if [ "$ia_modo" = "claude" ] && ! command -v claude >/dev/null 2>&1; then
  echo "  No encuentro Claude Code en esta PC (cómo instalarlo: docs/AGENTES_IA.md). Sigo en modo sin IA."
  ia_modo=lexico
fi
case "$ia_modo" in
  claude)
    if grep -q "\[YOUR_NAME\]" "$PERFIL/CLAUDE.md" 2>/dev/null && [ -t 0 ]; then
      case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
          # Git Bash's window (mintty) is not a full Windows console and interactive programs can
          # misbehave there: Claude Code gets its own PowerShell window, and this one waits.
          ps_cmd="Start-Process powershell -WorkingDirectory '$(cygpath -w "$ROOT")' -ArgumentList '-NoExit -Command claude ''/setup --user $nombre'''"
          if powershell.exe -NoProfile -Command "$ps_cmd"; then
            echo "  Se abrió Claude Code en otra ventana con /setup: responde ahí (tu CV, estudios, experiencia...)."
            ask "  Cuando termine /setup, vuelve a esta ventana y presiona Enter para seguir... " >/dev/null
          else
            echo "  No pude abrir otra ventana. Abre PowerShell en esta carpeta, escribe  claude  y luego  /setup --user $nombre"
            ask "  Cuando termines, presiona Enter aquí para seguir... " >/dev/null
          fi ;;
        *)
          echo "  Se abre Claude Code con /setup. Responde sus preguntas; al terminar escribe /exit y esto sigue."
          claude "/setup --user $nombre" || echo "  Claude Code no abrió bien: ábrelo tú en esta carpeta con  claude  y escribe  /setup --user $nombre" ;;
      esac
    else
      echo "  Tu perfil ya tiene datos (o esta ventana no es interactiva): /setup no hace falta ahora."
    fi ;;
  codex|agy)
    echo "  Abre $ia_modo en la carpeta del proyecto y pégale el prompt de la «Forma 3» del README para armar tu perfil."
    echo "  Aquí sigo con las preguntas de búsqueda." ;;
  *)
    echo "  Modo sin IA: te hago unas preguntas para tu ficha. Si tienes tu CV, lo leo y se completan solas;"
    echo "  si no, igual funciona solo con las preguntas. Las ofertas se puntúan comparándolas con tu ficha." ;;
esac

paso "qué buscar"
cd "$PERFIL"
rc=0; run tools/pe_pipeline.py consultas --es-ejemplo || rc=$?
if [ "$rc" = 3 ]; then
  echo "  ya está: reports/busqueda/consultas.json (para cambiarla: python tools/pe_pipeline.py consultas --help)"
else
  [ "$rc" = 0 ] || echo "  Aviso: no pude revisar tus búsquedas (detalle arriba); igual te pregunto."
  echo "  Responde con el número; en las que dicen «varios», separa con comas (1,3)."
  echo "  Enter deja lo que está (o salta la pregunta); < vuelve a la anterior; al final puedes cambiar cualquier respuesta."
  echo "  Solo se busca lo que elijas: si eliges solo sector privado, no se busca en el público."
  sector="" etapa="" ciclo="" merito="" area="" modo="" lugar="" city="Lima" modo_busqueda="lista" kw="" cv_file=""
  universidad="" experiencia="" ingles="" herramientas="" certificaciones=""
  pretty() { printf '%s' "${1:-$2}" | sed 's/,/, /g'; }
  lexico() { [ "$ia_modo" = "lexico" ]; }
  # Read a CV and fill only what is still empty: an answer given wins.
  usar_cv() {
    local file found
    file=$(printf '%s' "$1" | tr -d "\"'" | sed 's/^ *//; s/ *$//')
    if [ -f "$file" ] && found=$(run tools/pe_pipeline.py leer-cv "$(cygpath -w "$file" 2>/dev/null || printf '%s' "$file")"); then
      cv_area="" cv_etapa="" cv_ciclo="" cv_merito="" cv_universidad="" cv_ingles="" cv_herramientas="" cv_certificaciones=""
      eval "$found"  # cv_* assignments from a fixed vocabulary, shell-quoted by pe_lexico.py
      cv_file=$file
      [ -n "$area" ] || area=$cv_area
      [ -n "$etapa" ] || etapa=$cv_etapa
      [ "$etapa" != "estudiante" ] || [ -n "$ciclo" ] || ciclo=$cv_ciclo
      [ -n "$merito" ] || merito=$cv_merito
      [ -n "$universidad" ] || universidad=$cv_universidad
      [ -n "$ingles" ] || ingles=$cv_ingles
      [ -n "$herramientas" ] || herramientas=$cv_herramientas
      [ -n "$certificaciones" ] || certificaciones=$cv_certificaciones
      mkdir -p documents/cv && cp "$file" documents/cv/ 2>/dev/null || true
      echo "  Leí tu CV (sin IA, por palabras) y lo copié a documents/cv/. Lo que leí aparece como respuesta"
      echo "  por defecto en cada pregunta: Enter lo deja, o elige otra cosa si lo leí mal."
      return 0
    fi
    echo "  No encontré o no pude leer ese archivo; sigo con las preguntas."
    return 1
  }
  # One question by number. Returns 1 when the answer is "<" (go back) and 2 when it does not apply.
  pregunta() {
    local v
    case "$1" in
      1) lexico || return 2
         v=$(ask "  ¿Tienes tu CV? Arrastra el archivo (PDF o Word) a esta ventana y Enter; si no, solo Enter: ")
         [ "$v" = "<" ] && return 1
         [ -z "$v" ] || usar_cv "$v" || true ;;
      2) v=$(elegir "¿En qué sector quieres trabajar? (varios)" "$sector" "$(pretty "$sector" todos)" \
           "privado|Sector privado (empresas, bancos, consultoras...)" \
           "publico|Sector público (SERVIR: ministerios, BCRP, SUNAT, SBS...)" \
           "investigacion|Investigación (research: universidades, centros de estudio)" \
           "otros|Otros (ONG, organismos internacionales)" \
           "privado,publico,investigacion,otros|Todos")
         [ "$v" = "<" ] && return 1; sector=$v ;;
      3) v=$(elegir "¿Eres estudiante universitario?" "$etapa" "$(pretty "$etapa" omitir)" \
           "estudiante|Sí, estudiante: busco prácticas preprofesionales" \
           "egresado|Egresado o bachiller: prácticas profesionales, trainee, analista junior" \
           "profesional|No, ya trabajo: analista, asociado, junior")
         [ "$v" = "<" ] && return 1; etapa=${v%%,*}; [ "$etapa" = "estudiante" ] || ciclo="" ;;
      4) [ "$etapa" = "estudiante" ] || return 2
         v=$(elegir "¿En qué ciclo estás?" "$ciclo" "$(pretty "$ciclo" omitir)" \
           "1-4|1.º a 4.º ciclo" "5-6|5.º o 6.º ciclo" "7-8|7.º u 8.º ciclo" "9+|9.º ciclo o más (último año)")
         [ "$v" = "<" ] && return 1; ciclo=${v%%,*} ;;
      5) v=$(elegir "¿Tienes mérito académico? (muchas prácticas lo piden)" "$merito" "$(pretty "$merito" omitir)" \
           "decimo|Décimo superior" "quinto|Quinto superior" "tercio|Tercio superior" "medio|Medio superior" "ninguno|Ninguno")
         [ "$v" = "<" ] && return 1; merito=${v%%,*} ;;
      6) v=$(elegir "¿Cómo armamos tus búsquedas?" "lista" "por área" \
           "lista|Por área (de tu carrera)" \
           "palabras|Con palabras clave tuyas (ej. practicante finanzas, riesgos)" \
           "cv|Desde tu CV (PDF, Word o texto)")
         [ "$v" = "<" ] && return 1
         modo_busqueda=${v%%,*}
         if [ "$modo_busqueda" = "palabras" ]; then
           kw=$(ask "  Palabras clave, separadas por comas: ")
           [ -n "$kw" ] || modo_busqueda="lista"
         elif [ "$modo_busqueda" = "cv" ]; then
           usar_cv "$(ask "  Arrastra tu CV a esta ventana (o escribe su ruta) y presiona Enter: ")" || true
           modo_busqueda="lista"
         fi
         if [ "$modo_busqueda" = "lista" ]; then
           kw=""
           v=$(elegir "   ¿De qué área? (varios)" "$area" "$(pretty "$area" todas)" \
             "economia|Economía" "finanzas|Finanzas" "contabilidad|Contabilidad y auditoría" \
             "administracion|Administración y negocios" "industrial|Ingeniería industrial" "datos|Datos y tecnología" \
             "marketing|Marketing y ventas" "derecho|Derecho" "rrhh|Recursos humanos y psicología" \
             "ingenieria|Otras ingenierías (civil, minas, sistemas)" "comunicaciones|Comunicaciones")
           [ "$v" = "<" ] && return 1; area=$v
         fi ;;
      7) lexico || return 2
         v=$(ask "  ¿Universidad? (Enter = $(pretty "$universidad" omitir)): ")
         [ "$v" = "<" ] && return 1; [ -z "$v" ] || universidad=$v ;;
      8) lexico || return 2
         v=$(elegir "¿Cuánta experiencia laboral tienes?" "$experiencia" "$(pretty "$experiencia" omitir)" \
           "ninguna|Ninguna" "practicas|Prácticas previas" "menos1|Menos de 1 año" \
           "1-3|1 a 3 años" "3-5|3 a 5 años" "5+|Más de 5 años")
         [ "$v" = "<" ] && return 1; experiencia=${v%%,*} ;;
      9) lexico || return 2
         v=$(elegir "¿Tu nivel de inglés?" "$ingles" "$(pretty "$ingles" omitir)" \
           "basico|Básico" "intermedio|Intermedio" "avanzado|Avanzado" "nativo|Nativo o bilingüe")
         [ "$v" = "<" ] && return 1; ingles=${v%%,*} ;;
      10) lexico || return 2
         v=$(elegir "¿Qué herramientas manejas? (varios; las que más piden)" "$herramientas" "$(pretty "$herramientas" omitir)" \
           "excel|Excel" "powerbi|Power BI" "tableau|Tableau" "sql|SQL" "python|Python" "r|R" "stata|Stata" \
           "eviews|EViews" "bloomberg|Bloomberg" "spss|SPSS" "sap|SAP" "ia|Herramientas de IA (ChatGPT, Copilot...)")
         [ "$v" = "<" ] && return 1; herramientas=$v ;;
      11) lexico || return 2
         v=$(elegir "¿Certificaciones? (varios)" "$certificaciones" "$(pretty "$certificaciones" ninguna)" \
           "cfa1|CFA nivel I" "cfa2|CFA nivel II" "cfa3|CFA nivel III" "frm|FRM" "pmp|PMP" "bmc|Bloomberg Market Concepts")
         [ "$v" = "<" ] && return 1; certificaciones=$v ;;
      12) v=$(elegir "¿Qué modalidad? (varios)" "$modo" "$(pretty "$modo" cualquiera)" \
           "remoto|Remoto" "presencial|Presencial" "hibrido|Híbrido" "cualquiera|Cualquiera")
         [ "$v" = "<" ] && return 1; modo=$v ;;
      13) v=$(elegir "¿Dónde?" "$lugar" "$(pretty "$lugar" Lima)" \
           "lima|Lima, Perú" "peru|Otra ciudad del Perú" \
           "afuera-remoto|Perú y también fuera, solo remoto" "afuera|Perú y también fuera, dispuesto a mudarme o viajar")
         [ "$v" = "<" ] && return 1; lugar=${v%%,*}; city="Lima"
         if [ "$lugar" = "peru" ]; then city=$(ask "  ¿Qué ciudad? (Enter = Lima): "); city=${city:-Lima}; fi ;;
    esac
    return 0
  }
  i=1 dir=1
  while [ "$i" -le 13 ]; do
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
    lexico && echo "     1) CV: $(pretty "$(basename "${cv_file:-}")" 'no usado')"
    echo "     2) Sector: $(pretty "$sector" todos)"
    echo "     3) Etapa: $(pretty "$etapa" '—')"
    [ "$etapa" = "estudiante" ] && echo "     4) Ciclo: $(pretty "$ciclo" '—')"
    echo "     5) Mérito académico: $(pretty "$merito" '—')"
    if [ -n "$kw" ]; then echo "     6) Búsqueda: palabras clave: $kw"; else echo "     6) Búsqueda por área: $(pretty "$area" todas)"; fi
    if lexico; then
      echo "     7) Universidad: $(pretty "$universidad" '—')"
      echo "     8) Experiencia: $(pretty "$experiencia" '—')"
      echo "     9) Inglés: $(pretty "$ingles" '—')"
      echo "    10) Herramientas: $(pretty "$herramientas" '—')"
      echo "    11) Certificaciones: $(pretty "$certificaciones" ninguna)"
    fi
    echo "    12) Modalidad: $(pretty "$modo" cualquiera)"
    echo "    13) Dónde: $(pretty "$lugar" lima)$([ "$lugar" = "peru" ] && printf ' (%s)' "$city")"
    change=$(ask "  Enter = está bien · o el número de la que quieres cambiar: ")
    case "$change" in
      ""|s|S|si|sí) break ;;
      [1-9]|1[0-3]) pregunta "$change" || true ;;
      *) echo "  Escribe un número de la lista, o Enter." ;;
    esac
  done
  args=(--ciudad "$city" --sector "$sector" --modalidad "$modo" --area "$area" --herramientas "$herramientas"
        --certificaciones "$certificaciones" --universidad "$universidad")
  [ -n "$kw" ] && args+=(--q "$kw")
  [ -n "$lugar" ] && args+=(--lugar "$lugar")
  [ -n "$etapa" ] && args+=(--etapa "$etapa")
  [ -n "$ciclo" ] && args+=(--ciclo "$ciclo")
  [ -n "$merito" ] && args+=(--merito "$merito")
  [ -n "$experiencia" ] && args+=(--experiencia "$experiencia")
  [ -n "$ingles" ] && args+=(--ingles "$ingles")
  run tools/pe_pipeline.py consultas "${args[@]}" \
    || echo "  No pude armar las búsquedas; sigue igual. Luego: python tools/pe_pipeline.py consultas --help"
fi

paso "primera búsqueda (5 a 15 minutos; puedes dejarla corriendo)"
primera=(--titulo "$raw")
[ "$ia_modo" = "lexico" ] && primera+=(--ia lexico)  # sin IA: each new posting gets its word-based score
run tools/pe_pipeline.py diario "${primera[@]}" || echo "  Algún paso falló; el detalle está arriba y en reports/busqueda/diario.log. Lo que sí terminó quedó guardado."
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
evaluar="lexico"
if [ -n "$hora" ]; then
  ia=()
  case "$ia_modo" in
    lexico) ia=(--ia lexico); echo "  Cada día también puntuará las nuevas por palabras (sin IA)." ;;
    claude|agy)
      if [ "$(ask "  ¿Que además $ia_modo las evalúe (/rank) cada día? [s/N] ")" = "s" ]; then ia=(--ia "$ia_modo"); fi ;;
  esac
  run tools/pe_pipeline.py programar --hora "$hora" ${ia[@]+"${ia[@]}"} --titulo "$raw" || echo "  No se pudo programar; inténtalo luego con: python tools/pe_pipeline.py programar --hora \"$hora\""
else
  echo "  Sin programar. Cuando quieras: cd perfiles/$nombre && python tools/pe_pipeline.py programar --hora \"8:00 am\""
fi
case "$ia_modo" in claude|agy) evaluar=$ia_modo ;; esac

cat <<FIN

Listo. Tu espacio es perfiles/$nombre. Para el día a día (desde esa carpeta):
  python tools/pe_pipeline.py diario                busca y rehace la página
  python tools/pe_pipeline.py evaluar --ia $evaluar  puntúa las nuevas (o con el botón de la página)
Antes, en cada terminal nueva:  activa el entorno (.venv) como dice docs/INSTALAR.md, paso 5.
FIN
pausa
