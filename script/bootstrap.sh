#!/usr/bin/env bash
#
# yt-hoarder — developer environment bootstrap
#
# Installs HOST-side tooling used for CONTRIBUTING to yt-hoarder:
#   - uv        + backend Python deps (`uv sync`)   -> pytest / ruff / IDE type-checking
#   - node deps (`npm ci` in frontend/)             -> lint / build / IDE support
#   - task      (Taskfile runner)                   -> the `task <name>` shortcuts
#   - deno                                          -> yt-dlp challenge solving (out-of-Docker only)
#
# None of this is required to RUN the app -- everything runs inside Docker
# (`task dev` / `task prod`). This is purely for local development.
#
# Safe to re-run: already-installed tools are detected and skipped. Every
# install is opt-in; declining prints the manual command and continues.
#
# Usage:  bash script/bootstrap.sh      (primary entry point)
#     or: task setup:dev                (if you already have `task`)

# NOTE: intentionally no `set -e` -- one failed/declined install must not abort
# the rest of the bootstrap. We use pipefail + explicit per-step return checks.
set -uo pipefail

# Resolve repo root from this script's location so it runs from any CWD.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# PATH as the user's shell actually has it, captured before we augment it below.
ORIGINAL_PATH="$PATH"

# ── Colors (respects NO_COLOR: https://no-color.org/) ──────────────────────

if [[ -z "${NO_COLOR:-}" ]] && [[ -t 1 ]]; then
    BOLD='\033[1m'
    DIM='\033[2m'
    GREEN='\033[0;32m'
    CYAN='\033[0;36m'
    YELLOW='\033[0;33m'
    RED='\033[0;31m'
    RESET='\033[0m'
else
    BOLD='' DIM='' GREEN='' CYAN='' YELLOW='' RED='' RESET=''
fi

# ── Helpers ─────────────────────────────────────────────────────────────────

print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}── $1 ──${RESET}"
    echo ""
}

print_success() { echo -e "${GREEN}$1${RESET}"; }
print_warn()    { echo -e "${YELLOW}$1${RESET}"; }
print_error()   { echo -e "${RED}$1${RESET}"; }

# Yes/no prompt. Returns 0 for yes, 1 for no.
prompt_yes_no() {
    local prompt_text="$1"
    local default="${2:-y}"
    local hint answer

    if [[ "$default" == "y" ]]; then
        hint="Y/n"
    else
        hint="y/N"
    fi

    while true; do
        echo -en "  ${prompt_text} ${DIM}[${hint}]${RESET}: "
        read -r answer
        answer="${answer:-$default}"
        answer=$(echo "$answer" | tr '[:upper:]' '[:lower:]')
        case "$answer" in
            y|yes) return 0 ;;
            n|no)  return 1 ;;
            *)     print_warn "  Please enter y or n." ;;
        esac
    done
}

command_exists() { command -v "$1" &>/dev/null; }

# Is $2 a directory already present on the PATH string $1?
path_contains() {
    case ":$1:" in
        *":$2:"*) return 0 ;;
        *)        return 1 ;;
    esac
}

# ── Summary tracking ─────────────────────────────────────────────────────────
# One line appended per tool; printed at the end. (Plain string, not an
# associative array, for Bash 3.2 / macOS compatibility.)
SUMMARY=""
add_summary() { SUMMARY+="  $1"$'\n'; }

# ── Welcome ─────────────────────────────────────────────────────────────────

welcome() {
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}║         yt-hoarder  Dev Environment Setup        ║${RESET}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo "  This installs host-side tooling for CONTRIBUTING to yt-hoarder:"
    echo "  uv + backend deps, frontend deps, the Task runner, and Deno."
    echo ""
    echo -e "  ${DIM}Running the app does NOT need any of this -- it all runs in"
    echo -e "  Docker (task dev / task prod). This is only for local development.${RESET}"
    echo ""
    echo "  Every install is opt-in, needs no sudo, and is safe to re-run."
    echo ""
}

# ── uv + backend deps ────────────────────────────────────────────────────────

setup_uv() {
    print_header "uv (Python package manager) + backend deps"

    if command_exists uv; then
        print_success "  uv found: $(uv --version 2>/dev/null | head -1)"
    else
        echo "  uv manages the backend's Python 3.14 env, tests (pytest) and linter (ruff)."
        echo ""
        if prompt_yes_no "Install uv now? (official installer, no sudo, -> ~/.local/bin)" "y"; then
            if curl -LsSf https://astral.sh/uv/install.sh | sh; then
                # Make uv usable for the rest of this script session.
                export PATH="$HOME/.local/bin:$PATH"
                if command_exists uv; then
                    print_success "  Installed uv: $(uv --version 2>/dev/null | head -1)"
                else
                    print_warn "  uv installed but not yet on PATH for this shell."
                fi
            else
                print_error "  uv install failed."
            fi
        else
            print_warn "  Skipped. Install later with:"
            echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
        fi
    fi

    if command_exists uv; then
        echo ""
        if prompt_yes_no "Run 'uv sync' in backend/ to install Python deps?" "y"; then
            if (cd "$REPO_ROOT/backend" && uv sync); then
                print_success "  Backend deps installed (backend/.venv)."
                add_summary "uv + backend deps ... ready"
            else
                print_error "  'uv sync' failed -- see output above."
                add_summary "uv ................. installed, 'uv sync' FAILED"
            fi
        else
            print_warn "  Skipped 'uv sync'. Run it later: cd backend && uv sync"
            add_summary "uv ................. installed ('uv sync' skipped)"
        fi
    else
        add_summary "uv ................. skipped / not installed"
    fi
}

# ── Frontend deps (npm) ──────────────────────────────────────────────────────

setup_node() {
    print_header "Frontend deps (npm ci)"

    if command_exists npm; then
        print_success "  npm found: $(npm --version 2>/dev/null)"
        echo ""
        # npm ci, not npm install: this installs exactly what package-lock.json
        # pins, and cannot silently rewrite the lockfile out from under a
        # contributor the way `npm install` can.
        if prompt_yes_no "Run 'npm ci' in frontend/?" "y"; then
            if (cd "$REPO_ROOT/frontend" && npm ci); then
                print_success "  Frontend deps installed (frontend/node_modules)."
                add_summary "node deps .......... ready"
            else
                print_error "  'npm ci' failed -- see output above."
                add_summary "node deps .......... 'npm ci' FAILED"
            fi
        else
            print_warn "  Skipped. Run it later: cd frontend && npm ci"
            add_summary "node deps .......... skipped"
        fi
    else
        # Installing a Node runtime cross-platform is messy; we check-and-instruct
        # rather than auto-install it.
        print_warn "  npm/Node not found."
        echo "  Install Node 24 first (matches the pinned Docker build), then re-run:"
        echo "    - nvm:  https://github.com/nvm-sh/nvm"
        echo "    - fnm:  https://github.com/Schniz/fnm"
        echo "    - or your system package manager (e.g. 'brew install node')"
        add_summary "node deps .......... skipped (install Node first)"
    fi
}

# ── task (Taskfile runner) ───────────────────────────────────────────────────

setup_task() {
    print_header "task (Taskfile runner)"

    if command_exists task; then
        print_success "  task found: $(task --version 2>/dev/null | head -1)"
        add_summary "task ............... already installed"
        return
    fi

    echo "  'task' runs the shortcuts in Taskfile.yml (task dev, task backend:test, ...)."
    echo ""
    if prompt_yes_no "Install task now? (official installer, no sudo, -> ~/.local/bin)" "y"; then
        mkdir -p "$HOME/.local/bin"
        if sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b "$HOME/.local/bin"; then
            export PATH="$HOME/.local/bin:$PATH"
            print_success "  Installed task: $(task --version 2>/dev/null | head -1)"
            add_summary "task ............... installed"
        else
            print_error "  task install failed."
            add_summary "task ............... install FAILED"
        fi
    else
        print_warn "  Skipped. Install later (see also TASKFILE_GUIDE.md):"
        echo "    sh -c \"\$(curl --location https://taskfile.dev/install.sh)\" -- -d -b \"\$HOME/.local/bin\""
        add_summary "task ............... skipped"
    fi
}

# ── deno ─────────────────────────────────────────────────────────────────────

setup_deno() {
    print_header "deno"

    if command_exists deno; then
        print_success "  deno found: $(deno --version 2>/dev/null | head -1)"
        add_summary "deno ............... already installed"
        return
    fi

    echo "  Deno lets yt-dlp solve YouTube sig/n challenges."
    echo -e "  ${DIM}Only needed if you run the backend OUTSIDE Docker; inside Docker it's already present.${RESET}"
    echo ""
    if prompt_yes_no "Install deno now? (official installer, no sudo, -> ~/.deno)" "n"; then
        if curl -fsSL https://deno.land/install.sh | sh; then
            export PATH="$HOME/.deno/bin:$PATH"
            print_success "  Installed deno: $(deno --version 2>/dev/null | head -1)"
            add_summary "deno ............... installed"
        else
            print_error "  deno install failed."
            add_summary "deno ............... install FAILED"
        fi
    else
        print_warn "  Skipped. Install later with:"
        echo "    curl -fsSL https://deno.land/install.sh | sh"
        add_summary "deno ............... skipped"
    fi
}

# ── PATH guidance ────────────────────────────────────────────────────────────
# We run in a subshell and cannot change the parent shell's PATH; advise instead.

path_guidance() {
    local needed=()
    local dir
    for dir in "$HOME/.local/bin" "$HOME/.deno/bin"; do
        if [[ -d "$dir" ]] && ! path_contains "$ORIGINAL_PATH" "$dir"; then
            needed+=("$dir")
        fi
    done

    [[ ${#needed[@]} -eq 0 ]] && return

    print_header "Finish PATH setup"
    echo "  Some tools installed to directories not on your PATH. Add them to your"
    echo "  shell rc (~/.bashrc or ~/.zshrc), then open a new shell:"
    echo ""
    for dir in "${needed[@]}"; do
        echo -e "    ${BOLD}export PATH=\"${dir}:\$PATH\"${RESET}"
    done
    echo ""
    echo -e "  ${DIM}(uv, task, and deno installers may also offer to update your rc for you.)${RESET}"
}

# ── Completion ───────────────────────────────────────────────────────────────

print_completion() {
    print_header "Dev Setup Summary"
    echo -en "$SUMMARY"
    echo ""
    echo "  Next steps:"
    echo -e "    ${BOLD}cd backend && uv run pytest${RESET}      # backend tests ${DIM}(needs Docker running -- spins up a temp Postgres)${RESET}"
    echo -e "    ${BOLD}cd frontend && npm run lint${RESET}      # frontend lint"
    echo -e "    ${BOLD}task help${RESET}                        # all Taskfile shortcuts"
    echo -e "    ${BOLD}task dev${RESET}                         # run the app (Docker)"
    echo ""
    echo -e "  See ${BOLD}CONTRIBUTING.md${RESET} for the full development workflow."
    echo ""
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    if ! command_exists curl; then
        print_error "curl is required to run this bootstrap. Please install curl and re-run."
        exit 1
    fi

    welcome
    setup_uv
    setup_node
    setup_task
    setup_deno
    path_guidance
    print_completion
}

main
