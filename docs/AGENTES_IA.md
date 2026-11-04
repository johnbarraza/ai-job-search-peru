# Instalar un agente de IA (opcional)

La búsqueda, la página de revisión y la búsqueda diaria funcionan **sin IA**. Un agente suma tres cosas: evaluar el encaje con tu perfil (`/rank`), adaptar el CV y la carta (`/apply`) y preparar entrevistas (`/interview`). Basta con **uno**. Cada uno pide su propia cuenta o plan.

| Agente | Comando | Cuenta que necesitas | Para qué sirve mejor aquí |
|---|---|---|---|
| **Claude Code** (recomendado) | `claude` | Plan Claude Pro, Max, Team o Enterprise, o créditos de API en Claude Console | Todo: `/setup`, `/rank`, `/apply`, `/interview` están escritos para él |
| **Antigravity CLI** | `agy` | Cuenta de Google (plan Free, Pro o Ultra) o una API key de Gemini | `/rank` en la búsqueda diaria (`--ia agy`) y tareas sueltas |
| **Codex CLI** | `codex` | Cuenta de ChatGPT o API key de OpenAI | Tareas sueltas siguiendo `AGENTS.md` |

Después de instalar cualquiera, **cierra la terminal y abre una nueva**, y comprueba con el comando de versión.

## Claude Code

Guía oficial: [code.claude.com/docs/es/quickstart](https://code.claude.com/docs/es/quickstart)

| Sistema | Comando |
|---|---|
| Windows (PowerShell) | `irm https://claude.ai/install.ps1 \| iex` |
| Windows (winget) | `winget install Anthropic.ClaudeCode` |
| Mac / Linux | `curl -fsSL https://claude.ai/install.sh \| bash` |
| Mac (Homebrew) | `brew install --cask claude-code` |

- **Comprobar:** `claude --version`
- **Iniciar sesión:** entra a la carpeta del proyecto (o de tu perfil, `perfiles/<nombre>`) y escribe `claude`. La primera vez abre el navegador para iniciar sesión.
- **Primer uso:** dentro de Claude Code escribe `/setup` para crear tu perfil.
- En Windows, Claude Code usa Git Bash si Git está instalado, que ya es requisito de este proyecto.
- La instalación nativa se actualiza sola. Con winget o Homebrew, actualiza con `winget upgrade Anthropic.ClaudeCode` o `brew upgrade claude-code`.

## Antigravity CLI (`agy`)

Guía oficial: [antigravity.google/docs/getting-started](https://antigravity.google/docs/getting-started?tab=cli)

| Sistema | Comando |
|---|---|
| Windows (PowerShell) | `irm https://antigravity.google/cli/install.ps1 \| iex` |
| Mac / Linux | `curl -fsSL https://antigravity.google/cli/install.sh \| bash` |

- **Comprobar:** `agy --version`
- **Iniciar sesión:** escribe `agy` en la carpeta del perfil. La primera vez pide tema de colores, modo de pantalla y confianza en la carpeta, y luego la cuenta.
- **Búsqueda diaria con `/rank`:** `python tools/pe_pipeline.py programar --hora "8:00 am" --ia agy`. Antes, dale los permisos que explica [docs/PIPELINE.md](PIPELINE.md#diario-y-programar-la-búsqueda-todos-los-días-sola), porque sin supervisión no puede preguntarte.

## Codex CLI

Guía oficial: [learn.chatgpt.com/docs/codex/cli](https://learn.chatgpt.com/docs/codex/cli)

| Sistema | Comando |
|---|---|
| Mac / Linux | `curl -fsSL https://chatgpt.com/codex/install.sh \| sh` |
| Cualquiera, con Node.js | `npm install -g @openai/codex` |

- **Comprobar:** `codex --version`
- **Iniciar sesión:** escribe `codex` en la carpeta del perfil y elige *Sign in with ChatGPT*.
- Codex lee `AGENTS.md` y las skills de `.agents/skills/`. Para `/rank` o `/apply`, pídele que siga `.claude/commands/rank.md` o `.claude/commands/apply.md`.

## Seguridad

- Ningún agente postula ni envía nada sin que lo apruebes.
- No le des permisos amplios (`--dangerously-skip-permissions`, modo "auto" sin límites): este proyecto no los necesita.
- Abre el agente dentro de `perfiles/<nombre>/`, así trabaja sobre los datos de esa persona y nada de eso se sube a GitHub.
