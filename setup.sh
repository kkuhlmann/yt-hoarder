#!/usr/bin/env bash
set -euo pipefail

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

# ── Upstream Sources ────────────────────────────────────────────────────────

# Where a run that isn't sitting in a checkout fetches what it needs. Overridable
# for the same reason YT_HOARDER_IMAGE is — a fork or a mirror.
RAW_BASE="${YT_HOARDER_RAW_BASE:-https://raw.githubusercontent.com/kkuhlmann/yt-hoarder}"
CLONE_URL="https://github.com/kkuhlmann/yt-hoarder.git"
# Human-readable docs, not RAW_BASE: a wget install has no local docs/ to point at.
DOCS_BASE="https://github.com/kkuhlmann/yt-hoarder/blob/main/docs"

# docker-compose.published.yml is useless on its own: every service in it uses
# `extends: file: docker-compose.common.yml`. config.sample.yml comes along as the
# reference for the settings this wizard doesn't prompt for.
BOOTSTRAP_FILES=(docker-compose.common.yml docker-compose.published.yml config.sample.yml)

DEFAULT_INSTALL_DIR="./yt-hoarder"

# ── Helpers ─────────────────────────────────────────────────────────────────

print_header() {
    echo ""
    echo -e "${CYAN}${BOLD}── $1 ──${RESET}"
    echo ""
}

print_success() {
    echo -e "${GREEN}$1${RESET}"
}

print_warn() {
    echo -e "${YELLOW}$1${RESET}"
}

print_error() {
    echo -e "${RED}$1${RESET}"
}

print_usage() {
    cat <<EOF
Usage: ./setup.sh [OPTIONS]

Configure yt-hoarder by generating .env and config.yml, then start it.
Run with no options for the interactive wizard; pass flags to skip prompts.

Run from a source checkout, it uses that checkout. Downloaded on its own, it
creates ./yt-hoarder and fetches the compose files it needs into it.

Options:
  --audio-path <path>        Audio files storage path (default: ~/yt-hoarder/audio)
  --video-path <path>        Video files storage path (default: ~/yt-hoarder/video)
  --create-dirs               Auto-create missing storage directories
  --no-create-dirs             Don't create missing storage directories
  --whisper-model <name>      tiny.en | small.en | medium.en | large (default: tiny.en)
  --whisper-threads <n>       CPU threads for Whisper (default: detected CPU count, else 4)
  --cookie-secure             Send the auth cookie only over HTTPS. Use when you terminate
                              TLS in front of the app. Note this stops sign-in working over
                              plain http:// entirely, LAN included. (default: off)
  --mcp                       Enable the MCP server, so AI agents you run yourself (Claude
                              Code, LM Studio, Cursor...) can search your transcripts with a
                              personal access token (default: on)
  --no-mcp                    Turn the MCP server off
  --public-url <url>          The address you open the app at (e.g. https://hoarder.example.com),
                              used for the in-app links in MCP search results. Empty, the
                              default, uses whatever address each agent connects with.
  --overwrite                 Overwrite existing .env/config.yml (originals are backed up)
  --no-overwrite                Cancel setup if .env/config.yml already exist
  --launch <mode>             What to start after configuring (default: published)
                                published    pull the prebuilt image (fastest, recommended)
                                build-prod   compile from source, single container :8000
                                build-dev    compile from source, :3000 + :8000, hot reload
                                none         configure only
                              'prod' and 'dev' are accepted as synonyms for
                              build-prod and build-dev.
  --image-tag <tag>           Release to install with --launch published (default: latest,
                              e.g. v0.1.0). Saved to .env as YT_HOARDER_TAG.
  --install-dir <path>        Where to install when not run from a source checkout
                              (default: ./yt-hoarder). Ignored inside a checkout.
  --compose-ref <ref>         Git ref to fetch the compose files from (default: main, or
                              the matching tag when --image-tag names a version)
  -y, --yes                   Non-interactive: use defaults for any option not passed above
  -h, --help                  Show this help and exit

Examples:
  ./setup.sh -y
  ./setup.sh --launch published --image-tag v0.1.0
  ./setup.sh -y --install-dir ~/media/yt-hoarder
  ./setup.sh --audio-path /mnt/audio --video-path /mnt/video --whisper-model small.en
  ./setup.sh -y --public-url https://hoarder.example.com
EOF
}

# Prompt with a default value shown in brackets. Empty input returns the default.
prompt_with_default() {
    local prompt_text="$1"
    local default_value="$2"
    local user_input

    echo -e "  ${prompt_text} ${DIM}[${default_value}]${RESET}" >&2
    echo -en "  ${GREEN}>${RESET} " >&2
    read -r user_input
    echo "${user_input:-$default_value}"
}

# Yes/no prompt. Returns 0 for yes, 1 for no.
prompt_yes_no() {
    local prompt_text="$1"
    local default="${2:-y}"
    local hint

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

# An http(s) address, optionally with a path. Whitespace, quotes, backslashes, `?` and
# `#` are refused: the value lands inside double quotes in config.yml, and the backend
# rejects a query string or fragment anyway.
valid_public_url() {
    local re='^https?://[^[:space:]"\\?#]+$'
    [[ "$1" =~ $re ]]
}

# Expand ~ to $HOME in a path
expand_path() {
    local path="$1"
    if [[ "$path" == "~"* ]]; then
        path="${HOME}${path#\~}"
    fi
    echo "$path"
}

# Get CPU core count (cross-platform)
get_cpu_count() {
    if command -v nproc &>/dev/null; then
        nproc
    elif command -v sysctl &>/dev/null; then
        sysctl -n hw.ncpu 2>/dev/null || echo "unknown"
    else
        echo "unknown"
    fi
}

# Download to a temp file and move into place. A compose file truncated by a
# half-finished transfer fails far more confusingly than a missing one, and an
# existing copy is backed up first so a hand-edited file is never lost silently.
fetch_file() {
    local url="$1"
    local dest="$2"
    local ok=""

    if command -v curl &>/dev/null; then
        curl -fsSL --retry 3 -o "${dest}.tmp" "$url" && ok="1"
    elif command -v wget &>/dev/null; then
        wget -q -O "${dest}.tmp" "$url" && ok="1"
    fi

    if [[ -z "$ok" ]]; then
        rm -f "${dest}.tmp"
        return 1
    fi

    if [[ -f "$dest" ]] && ! cmp -s "$dest" "${dest}.tmp"; then
        cp "$dest" "${dest}.bak"
        print_warn "  Backed up modified ${dest} -> ${dest}.bak"
    fi
    mv "${dest}.tmp" "$dest"
}

# GNU and BSD stat share no compatible flags — `-c` is not even accepted on
# macOS, so the usage error plus `set -e` aborts the whole script. Try GNU
# first and fall back, rather than branching on `uname`: busybox stat is
# GNU-shaped, so the flag a platform accepts is the thing worth probing.
stat_field() {
    local gnu_fmt="$1" bsd_fmt="$2" path="$3"
    stat -c "$gnu_fmt" "$path" 2>/dev/null || stat -f "$bsd_fmt" "$path" 2>/dev/null
}

# ── Argument Parsing ───────────────────────────────────────────────────────

parse_args() {
    ARG_AUDIO_PATH=""
    ARG_VIDEO_PATH=""
    ARG_CREATE_DIRS=""
    ARG_OVERWRITE=""
    ARG_WHISPER_MODEL=""
    ARG_WHISPER_THREADS=""
    ARG_COOKIE_SECURE=""
    ARG_MCP=""
    ARG_PUBLIC_URL=""
    # Separate from the value, so an explicit `--public-url ''` is honoured.
    ARG_PUBLIC_URL_SET=""
    ARG_LAUNCH=""
    ARG_IMAGE_TAG=""
    ARG_INSTALL_DIR=""
    ARG_COMPOSE_REF=""
    NON_INTERACTIVE=""
    LAUNCH_SYNONYM=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --audio-path)
                [[ -n "${2:-}" ]] || { print_error "  --audio-path requires a value"; exit 1; }
                ARG_AUDIO_PATH="$2"
                shift 2
                ;;
            --video-path)
                [[ -n "${2:-}" ]] || { print_error "  --video-path requires a value"; exit 1; }
                ARG_VIDEO_PATH="$2"
                shift 2
                ;;
            --create-dirs)
                ARG_CREATE_DIRS="y"
                shift
                ;;
            --no-create-dirs)
                ARG_CREATE_DIRS="n"
                shift
                ;;
            --overwrite)
                ARG_OVERWRITE="y"
                shift
                ;;
            --no-overwrite)
                ARG_OVERWRITE="n"
                shift
                ;;
            --whisper-model)
                [[ -n "${2:-}" ]] || { print_error "  --whisper-model requires a value"; exit 1; }
                case "$2" in
                    tiny.en|small.en|medium.en|large) ARG_WHISPER_MODEL="$2" ;;
                    *)
                        print_error "  --whisper-model must be one of: tiny.en, small.en, medium.en, large"
                        exit 1
                        ;;
                esac
                shift 2
                ;;
            --whisper-threads)
                [[ -n "${2:-}" ]] || { print_error "  --whisper-threads requires a value"; exit 1; }
                if [[ "$2" =~ ^[0-9]+$ ]] && [[ "$2" -ge 1 ]]; then
                    ARG_WHISPER_THREADS="$2"
                else
                    print_error "  --whisper-threads must be a positive integer"
                    exit 1
                fi
                shift 2
                ;;
            --cookie-secure)
                ARG_COOKIE_SECURE="true"
                shift
                ;;
            --mcp)
                ARG_MCP="y"
                shift
                ;;
            --no-mcp)
                ARG_MCP="n"
                shift
                ;;
            --public-url)
                [[ $# -ge 2 ]] || { print_error "  --public-url requires a value"; exit 1; }
                if [[ -n "$2" ]] && ! valid_public_url "${2%/}"; then
                    print_error "  --public-url must be an http:// or https:// address with no query string"
                    exit 1
                fi
                ARG_PUBLIC_URL="${2%/}"
                ARG_PUBLIC_URL_SET="1"
                shift 2
                ;;
            --launch)
                [[ -n "${2:-}" ]] || { print_error "  --launch requires a value"; exit 1; }
                # Normalized here rather than at the use sites, so nothing
                # downstream has to know both spellings. The synonyms map to the
                # build modes they have always meant: a flag that silently starts
                # doing something else between versions is worse than a longer name.
                case "$2" in
                    published)  ARG_LAUNCH="published" ;;
                    build-prod) ARG_LAUNCH="build-prod" ;;
                    build-dev)  ARG_LAUNCH="build-dev" ;;
                    none)       ARG_LAUNCH="none" ;;
                    prod)       ARG_LAUNCH="build-prod"; LAUNCH_SYNONYM="prod" ;;
                    dev)        ARG_LAUNCH="build-dev";  LAUNCH_SYNONYM="dev" ;;
                    *)
                        print_error "  --launch must be one of: published, build-prod, build-dev, none"
                        exit 1
                        ;;
                esac
                shift 2
                ;;
            --image-tag)
                [[ -n "${2:-}" ]] || { print_error "  --image-tag requires a value"; exit 1; }
                # Rejected here rather than by the registry: an invalid tag
                # otherwise surfaces as an opaque pull error a minute later.
                if [[ "$2" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*$ ]]; then
                    ARG_IMAGE_TAG="$2"
                else
                    print_error "  --image-tag must be a valid Docker tag (letters, digits, . _ -)"
                    exit 1
                fi
                shift 2
                ;;
            --install-dir)
                [[ -n "${2:-}" ]] || { print_error "  --install-dir requires a value"; exit 1; }
                ARG_INSTALL_DIR="$2"
                shift 2
                ;;
            --compose-ref)
                [[ -n "${2:-}" ]] || { print_error "  --compose-ref requires a value"; exit 1; }
                ARG_COMPOSE_REF="$2"
                shift 2
                ;;
            -y|--yes)
                NON_INTERACTIVE="1"
                shift
                ;;
            -h|--help)
                print_usage
                exit 0
                ;;
            *)
                print_error "  Unknown option: $1"
                echo ""
                print_usage
                exit 1
                ;;
        esac
    done
}

# ── Step 0: Install Mode ───────────────────────────────────────────────────

# Two independent facts, not one mode. A fetched install ends up holding a copy
# of this script beside the compose files, so "has the compose files" must not be
# read as "is a source checkout" — the build modes need Dockerfile.prod and the
# backend/frontend trees, which a fetched install has no way to produce.
detect_install_mode() {
    HAVE_SOURCE=""
    HAVE_COMPOSE=""
    SCRIPT_PATH=""

    # Empty under `curl ... | bash`, where there is no script file at all.
    local script_dir=""
    if [[ -f "${BASH_SOURCE[0]:-}" ]]; then
        script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        SCRIPT_PATH="${script_dir}/$(basename "${BASH_SOURCE[0]}")"
    fi

    if [[ -n "$script_dir" ]]; then
        if [[ -f "${script_dir}/Dockerfile.prod" ]] && [[ -d "${script_dir}/backend" ]]; then
            HAVE_SOURCE="1"
        fi
        if [[ -f "${script_dir}/docker-compose.published.yml" ]] \
            && [[ -f "${script_dir}/docker-compose.common.yml" ]]; then
            HAVE_COMPOSE="1"
        fi
    fi

    # Everything downstream is relative to the compose files, so an existing
    # install is configured where it lives rather than where it was invoked from.
    if [[ -n "$HAVE_SOURCE" ]] || [[ -n "$HAVE_COMPOSE" ]]; then
        cd "$script_dir"
    fi
    return 0
}

# Runs before anything is created, so a rejected build mode leaves no half-made
# install directory behind.
require_source_for_build_modes() {
    case "$ARG_LAUNCH" in
        build-prod|build-dev) ;;
        *) return 0 ;;
    esac
    [[ -z "$HAVE_SOURCE" ]] || return 0

    print_error "  --launch ${ARG_LAUNCH} builds from source, and there is no source here."
    echo ""
    echo "  The published image needs none of it. To build anyway:"
    echo ""
    echo -e "    ${BOLD}git clone ${CLONE_URL}${RESET}"
    echo -e "    ${BOLD}cd yt-hoarder && bash setup.sh --launch ${ARG_LAUNCH}${RESET}"
    echo ""
    exit 1
}

# `curl ... | bash` leaves stdin on the pipe, so every `read -r` would eat script
# text instead of an answer. /dev/tty is the way back to the keyboard; with no
# terminal at all there is nothing to prompt with and -y is the only way through.
ensure_interactive_stdin() {
    [[ -z "$NON_INTERACTIVE" ]] || return 0
    [[ ! -t 0 ]] || return 0

    # Opened in a subshell first: /dev/tty passes -r whenever the device node is
    # readable, but opening it fails outright with no controlling terminal — and a
    # failed redirection on `exec` kills a non-interactive shell before the message
    # below can explain why.
    if (exec </dev/tty) 2>/dev/null; then
        exec </dev/tty
        return 0
    fi

    print_error "  No terminal available for the interactive prompts."
    echo "  Download the script and run it, or pass -y to accept every default:"
    echo ""
    echo -e "    ${BOLD}wget ${RAW_BASE}/main/setup.sh${RESET}"
    echo -e "    ${BOLD}bash setup.sh${RESET}"
    echo ""
    exit 1
}

# ── Welcome ─────────────────────────────────────────────────────────────────

welcome() {
    echo ""
    echo '     _______________/\/\____________/\/\__/\/\______________/\/\__________________________________________________/\/\_________________________'
    echo '    _/\/\__/\/\__/\/\/\/\/\________/\/\__/\/\______________/\/\__________/\/\/\____/\/\/\______/\/\__/\/\________/\/\____/\/\/\____/\/\__/\/\_ '
    echo '   _/\/\__/\/\____/\/\________/\/\/\/\__/\/\____/\/\/\/\__/\/\/\/\____/\/\__/\/\______/\/\____/\/\/\/\______/\/\/\/\__/\/\/\/\/\__/\/\/\/\___  '
    echo '  ___/\/\/\/\____/\/\______/\/\__/\/\__/\/\______________/\/\__/\/\__/\/\__/\/\__/\/\/\/\____/\/\________/\/\__/\/\__/\/\________/\/\_______   '
    echo ' _______/\/\____/\/\/\______/\/\/\/\__/\/\/\____________/\/\__/\/\____/\/\/\____/\/\/\/\/\__/\/\__________/\/\/\/\____/\/\/\/\__/\/\_______    '
    echo '_/\/\/\/\_________________________________________________________________________________________________________________________________     '
    echo ""
    echo -e "${BOLD}╔══════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}║             yt-hoarder  Quick Setup              ║${RESET}"
    echo -e "${BOLD}╚══════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo "  This script configures yt-hoarder by creating your .env and"
    echo "  config.yml with sensible defaults, then starts it -- by default"
    echo "  from the prebuilt release image, so there is nothing to compile."
    echo ""
    echo -e "  Press ${BOLD}Enter${RESET} at any prompt to accept the default value."
    echo ""
}

# ── Step 1: Prerequisites ──────────────────────────────────────────────────

check_prerequisites() {
    print_header "Checking Prerequisites"

    local missing=0

    if command -v docker &>/dev/null; then
        print_success "  docker found: $(docker --version 2>/dev/null | head -1)"
    else
        print_error "  docker not found"
        echo "  Install Docker: https://docs.docker.com/get-docker/"
        missing=1
    fi

    if docker compose version &>/dev/null 2>&1; then
        print_success "  docker compose found: $(docker compose version 2>/dev/null | head -1)"
    else
        print_error "  docker compose not found"
        echo "  Docker Compose v2 is required (comes with Docker Desktop)."
        echo "  Install: https://docs.docker.com/compose/install/"
        missing=1
    fi

    if command -v openssl &>/dev/null; then
        print_success "  openssl found"
    else
        print_error "  openssl not found (needed to generate JWT secret)"
        echo "  Install openssl and re-run this script."
        missing=1
    fi

    if [[ -z "$HAVE_SOURCE" ]]; then
        if command -v curl &>/dev/null || command -v wget &>/dev/null; then
            print_success "  curl/wget found"
        else
            print_error "  neither curl nor wget found"
            echo "  One of them is needed to download the compose files."
            missing=1
        fi
    fi

    if [[ "$missing" -eq 1 ]]; then
        echo ""
        print_error "Please install the missing prerequisites and re-run this script."
        exit 1
    fi
}

# ── Step 2: Existing Files ─────────────────────────────────────────────────

clear_stale_mount_dir() {
    local path="$1"
    [[ -d "$path" ]] || return 0

    # Docker substitutes an empty directory for a missing bind-mount source, so a
    # `docker compose up` run before setup leaves ./config.yml as a *directory* —
    # which `-f` reports as absent, letting setup run to the `cat >` that then
    # fails. Only an empty one is Docker's placeholder; anything else is real data.
    if rmdir "$path" 2>/dev/null; then
        print_warn "  Removed empty ${path}/ — a placeholder directory left by a bind mount."
        return 0
    fi

    print_error "  ${path} is a non-empty directory, but setup must write it as a file."
    echo "  Move or remove it, then re-run setup."
    exit 1
}

check_existing_files() {
    clear_stale_mount_dir "config.yml"

    local env_exists=0
    local config_exists=0

    [[ -f ".env" ]] && env_exists=1
    [[ -f "config.yml" ]] && config_exists=1

    if [[ "$env_exists" -eq 0 ]] && [[ "$config_exists" -eq 0 ]]; then
        return 0
    fi

    print_header "Existing Configuration Detected"

    if [[ "$env_exists" -eq 1 ]]; then
        print_warn "  .env already exists"
    fi
    if [[ "$config_exists" -eq 1 ]]; then
        print_warn "  config.yml already exists"
    fi
    echo ""

    local overwrite
    if [[ "$ARG_OVERWRITE" == "y" ]]; then
        print_success "  Overwrite existing files: yes (from --overwrite)"
        overwrite=0
    elif [[ "$ARG_OVERWRITE" == "n" ]]; then
        print_success "  Overwrite existing files: no (from --no-overwrite)"
        overwrite=1
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        print_success "  Overwrite existing files: no (default)"
        overwrite=1
    elif prompt_yes_no "Overwrite existing files? (originals will be backed up)" "n"; then
        overwrite=0
    else
        overwrite=1
    fi

    if [[ "$overwrite" -eq 0 ]]; then
        # `if` rather than `[[ ]] && cmd`: a false guard on the last command of a
        # function makes the function return 1, which under `set -e` exits the
        # whole script silently from main().
        if [[ "$env_exists" -eq 1 ]]; then
            cp .env .env.bak
            print_success "  Backed up .env -> .env.bak"
        fi
        if [[ "$config_exists" -eq 1 ]]; then
            cp config.yml config.yml.bak
            print_success "  Backed up config.yml -> config.yml.bak"
        fi
    else
        echo ""
        echo "  Setup cancelled. Your existing files are unchanged."
        exit 0
    fi
}

# ── Step 3: Storage Paths ──────────────────────────────────────────────────

configure_storage() {
    print_header "Storage Paths"

    echo "  Where should downloaded media be stored on your machine?"
    echo "  These directories will be mounted into the Docker containers."
    echo ""

    if [[ -n "$ARG_AUDIO_PATH" ]]; then
        AUDIO_ONLY_PATH=$(expand_path "$ARG_AUDIO_PATH")
        print_success "  Audio files path: ${AUDIO_ONLY_PATH} (from --audio-path)"
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        AUDIO_ONLY_PATH=$(expand_path "$HOME/yt-hoarder/audio")
        print_success "  Audio files path: ${AUDIO_ONLY_PATH} (default)"
    else
        AUDIO_ONLY_PATH=$(expand_path "$(prompt_with_default "Audio files path" "$HOME/yt-hoarder/audio")")
    fi

    if [[ -n "$ARG_VIDEO_PATH" ]]; then
        VIDEO_PATH=$(expand_path "$ARG_VIDEO_PATH")
        print_success "  Video files path: ${VIDEO_PATH} (from --video-path)"
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        VIDEO_PATH=$(expand_path "$HOME/yt-hoarder/video")
        print_success "  Video files path: ${VIDEO_PATH} (default)"
    else
        VIDEO_PATH=$(expand_path "$(prompt_with_default "Video files path" "$HOME/yt-hoarder/video")")
    fi

    # Offer to create directories if they don't exist
    for dir_info in "Audio:$AUDIO_ONLY_PATH" "Video:$VIDEO_PATH"; do
        local label="${dir_info%%:*}"
        local dir="${dir_info#*:}"
        if [[ ! -d "$dir" ]]; then
            echo ""
            if [[ "$ARG_CREATE_DIRS" == "y" ]]; then
                mkdir -p "$dir"
                print_success "  Created: ${dir} (from --create-dirs)"
            elif [[ "$ARG_CREATE_DIRS" == "n" ]]; then
                print_warn "  ${label} directory doesn't exist: ${dir} (skipped, from --no-create-dirs)"
            elif [[ -n "$NON_INTERACTIVE" ]]; then
                mkdir -p "$dir"
                print_success "  Created: ${dir} (default)"
            elif prompt_yes_no "${label} directory doesn't exist: ${dir}. Create it?" "y"; then
                mkdir -p "$dir"
                print_success "  Created: ${dir}"
            fi
        fi
    done
}

# ── Step 4: Transcription ──────────────────────────────────────────────────

configure_transcription() {
    print_header "Transcription Settings"

    echo "  yt-hoarder can transcribe audio using OpenAI's Whisper model."
    echo "  Larger models are more accurate but require more RAM."
    echo ""
    echo "    1) tiny.en   - Fastest, ~1GB RAM (recommended for most users)"
    echo "    2) small.en  - Better accuracy, ~2GB RAM"
    echo "    3) medium.en - Good accuracy, ~5GB RAM"
    echo "    4) large     - Best accuracy, ~10GB RAM, supports all languages"
    echo ""

    if [[ -n "$ARG_WHISPER_MODEL" ]]; then
        WHISPER_MODEL="$ARG_WHISPER_MODEL"
        print_success "  Selected: ${WHISPER_MODEL} (from --whisper-model)"
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        WHISPER_MODEL="tiny.en"
        print_success "  Selected: ${WHISPER_MODEL} (default)"
    else
        local model_choice
        while true; do
            model_choice=$(prompt_with_default "Select model (1-4)" "1")
            case "$model_choice" in
                1) WHISPER_MODEL="tiny.en";   break ;;
                2) WHISPER_MODEL="small.en";  break ;;
                3) WHISPER_MODEL="medium.en"; break ;;
                4) WHISPER_MODEL="large";     break ;;
                *) print_warn "  Please enter a number between 1 and 4." ;;
            esac
        done
        print_success "  Selected: ${WHISPER_MODEL}"
    fi

    echo ""
    local cpu_count
    cpu_count=$(get_cpu_count)
    # Leave a core for everything else, matching the code default in config.py.
    local default_threads="3"
    if [[ "$cpu_count" != "unknown" ]]; then
        echo -e "  Your system has ${BOLD}${cpu_count}${RESET} CPU cores."
        default_threads=$(( cpu_count > 1 ? cpu_count - 1 : 1 ))
    fi

    if [[ -n "$ARG_WHISPER_THREADS" ]]; then
        WHISPER_THREADS="$ARG_WHISPER_THREADS"
        print_success "  CPU threads: ${WHISPER_THREADS} (from --whisper-threads)"
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        WHISPER_THREADS="$default_threads"
        print_success "  CPU threads: ${WHISPER_THREADS} (default)"
    else
        local threads_input
        while true; do
            threads_input=$(prompt_with_default "CPU threads for Whisper transcription" "$default_threads")
            if [[ "$threads_input" =~ ^[0-9]+$ ]] && [[ "$threads_input" -ge 1 ]]; then
                WHISPER_THREADS="$threads_input"
                break
            else
                print_warn "  Please enter a positive number."
            fi
        done
    fi
}

configure_mcp() {
    print_header "AI Agent Access (MCP)"

    echo "  yt-hoarder can serve an MCP endpoint, so AI agents you run yourself --"
    echo "  Claude Code, LM Studio, Cursor and others -- can search your transcripts and"
    echo "  summarise them with links to the exact moment. Each user connects their own"
    echo "  agent with a personal access token. The app itself never calls an AI service,"
    echo "  and nothing can connect until a user creates a token."
    echo ""

    MCP_ENABLED="true"
    MCP_PUBLIC_URL=""
    if [[ "$ARG_MCP" == "y" ]]; then
        print_success "  MCP server: enabled (from --mcp)"
    elif [[ "$ARG_MCP" == "n" ]]; then
        MCP_ENABLED="false"
        print_success "  MCP server: off (from --no-mcp)"
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        print_success "  MCP server: enabled (default)"
    elif ! prompt_yes_no "Enable the MCP server so AI agents can search your transcripts?" "y"; then
        MCP_ENABLED="false"
    fi

    [[ "$MCP_ENABLED" == "true" ]] || return 0

    echo ""
    echo "  Search results link back into the app. Leave this blank to use whatever"
    echo "  address each agent connects with; set it if you reach the app through a"
    echo "  reverse proxy or under a different name than the agents use."
    if [[ -n "$ARG_PUBLIC_URL_SET" ]]; then
        MCP_PUBLIC_URL="$ARG_PUBLIC_URL"
        print_success "  Public URL: ${MCP_PUBLIC_URL:-auto-detect} (from --public-url)"
    elif [[ -n "$NON_INTERACTIVE" ]]; then
        print_success "  Public URL: auto-detect (default)"
    else
        local answer
        while true; do
            answer=$(prompt_with_default "Address you open yt-hoarder at (blank = auto-detect)" "")
            answer="${answer%/}"
            if [[ -z "$answer" ]] || valid_public_url "$answer"; then
                MCP_PUBLIC_URL="$answer"
                break
            fi
            print_warn "  Enter an http:// or https:// address, e.g. https://hoarder.example.com"
        done
        print_success "  Public URL: ${MCP_PUBLIC_URL:-auto-detect}"
    fi
}

# ── Step 5: Generate JWT Secret ────────────────────────────────────────────

generate_jwt_secret() {
    JWT_SECRET=$(openssl rand -hex 32)
}

# ── Step 5b: Release Image Tag ─────────────────────────────────────────────

# Must run before write_config_files: the tag is persisted to .env, so that
# `docker compose pull` later picks up the same release without re-running setup.
resolve_image_tag() {
    IMAGE_TAG="${ARG_IMAGE_TAG:-latest}"
    IMAGE_REF="ghcr.io/kkuhlmann/yt-hoarder:${IMAGE_TAG}"
}

# ── Step 5c: Bootstrap Install Directory ───────────────────────────────────

# Only for a script downloaded on its own; a checkout already has everything.
# Runs after resolve_image_tag because the ref to fetch from follows the image
# tag, and before check_existing_files so that check looks at the right directory.
bootstrap_install_files() {
    # A checkout's compose files are tracked source. Refreshing them from a ref
    # would discard whatever the developer is working on.
    if [[ -n "$HAVE_SOURCE" ]]; then
        if [[ -n "$ARG_INSTALL_DIR" ]] || [[ -n "$ARG_COMPOSE_REF" ]]; then
            echo ""
            print_warn "  --install-dir/--compose-ref ignored: running from a source checkout."
        fi
        return 0
    fi

    print_header "Preparing Install Directory"

    # Only a script arriving on its own picks a directory. An existing install is
    # refreshed where it stands, or a re-run would nest a second install inside it.
    if [[ -z "$HAVE_COMPOSE" ]]; then
        local target
        target=$(expand_path "${ARG_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}")
        mkdir -p "$target"
        cd "$target"
    elif [[ -n "$ARG_INSTALL_DIR" ]]; then
        print_warn "  --install-dir ignored: already inside an install directory."
    fi
    print_success "  Installing into $(pwd)"

    # The compose files describe the image they configure, so a pinned release
    # takes its own; anything else (latest, a branch build, a sha) tracks main.
    local ref="$ARG_COMPOSE_REF"
    if [[ -z "$ref" ]]; then
        if [[ "$IMAGE_TAG" =~ ^v[0-9] ]]; then
            ref="$IMAGE_TAG"
        else
            ref="main"
        fi
    fi

    echo ""
    local file
    for file in "${BOOTSTRAP_FILES[@]}"; do
        if fetch_file "${RAW_BASE}/${ref}/${file}" "$file"; then
            print_success "  Downloaded ${file}"
        else
            print_error "  Could not download ${file} from ${RAW_BASE}/${ref}/"
            echo "  Check your connection, or name an existing ref with --compose-ref."
            exit 1
        fi
    done

    # Leaves the install able to reconfigure itself without a second download.
    if [[ -n "$SCRIPT_PATH" ]] && [[ ! -f setup.sh ]]; then
        cp "$SCRIPT_PATH" setup.sh
        chmod +x setup.sh
    fi
    return 0
}

# ── Step 6: Write Config Files ─────────────────────────────────────────────

write_config_files() {
    print_header "Generating Configuration"

    COOKIE_SECURE="${ARG_COOKIE_SECURE:-false}"
    if [[ "$COOKIE_SECURE" == "true" ]]; then
        print_success "  Auth cookie: HTTPS only (from --cookie-secure)"
        print_warn "  Sign-in over plain http:// will no longer work, LAN included."
    fi

    # Write .env
    cat > .env <<EOF
# Generated by setup.sh on $(date -u +"%Y-%m-%d %H:%M:%S UTC")

# Docker Compose's variable file. Application settings live in config.yml —
# nothing here reaches the backend except FORWARDED_ALLOW_IPS.

# Host paths for media storage (mounted into Docker containers)
AUDIO_ONLY_PATH=${AUDIO_ONLY_PATH}
VIDEO_PATH=${VIDEO_PATH}

# The prebuilt release image, read only by docker-compose.published.yml.
# 'latest' follows the newest release; pin a version (e.g. v0.1.0) to hold one.
# Update with: docker compose -f docker-compose.published.yml pull && ... up -d
YT_HOARDER_IMAGE=ghcr.io/kkuhlmann/yt-hoarder
YT_HOARDER_TAG=${IMAGE_TAG}

# Where the dev-mode frontend looks for the API. Leave empty: it then follows
# whatever address you browsed to, so one dev server answers on localhost, a LAN
# IP and a Tailscale name alike. Set it only if the API is not on :8000 of that
# host -- behind a reverse proxy, or with TLS terminated in front.
NEXT_PUBLIC_BACKEND_API=

# Extra hostnames allowed to load Next.js dev resources (hot reload). Every
# dotted host is already allowed; this is for the ones no wildcard can match --
# a single-label hostname (http://nas:3000) or an IPv6 literal. Comma-separated.
ALLOWED_DEV_ORIGINS=

# Set to your reverse proxy's address if one fronts the app, so per-client
# sign-in rate limits don't collapse into a single shared budget.
# FORWARDED_ALLOW_IPS=127.0.0.1
EOF

    print_success "  Created .env"

    # Write config.yml
    cat > config.yml <<EOF
# Generated by setup.sh on $(date -u +"%Y-%m-%d %H:%M:%S UTC")
# See config.sample.yml for all available options and documentation.

database:
  url: postgresql://ytdl:ytdl@postgres:5432/yt_hoarder

# How often subscriptions are checked and how many jobs each lane runs at once
# are not set here — both live in the Settings tab, so they can be changed
# without a restart.
tasks:
  purge_on_startup: false

transcription:
  whisper_model: ${WHISPER_MODEL}
  whisper_cpu_threads: ${WHISPER_THREADS}
  whisper_num_workers: 1

auth:
  secret_key: ${JWT_SECRET}
  jwt_expiry_days: 30
  algorithm: HS256
  # Set true if you terminate HTTPS in front of the app. This stops sign-in from
  # working over plain http:// entirely, LAN included. It governs the cookie only:
  # offline mode on phones needs an HTTPS origin and a production install, not
  # this flag. See docs/OFFLINE.md.
  cookie_secure: ${COOKIE_SECURE}

logging:
  level: INFO

# AI agents (MCP). Each user creates a personal access token from the account menu.
# public_url builds the in-app links in search results; empty uses the address the
# agent connected with.
mcp:
  enabled: ${MCP_ENABLED}
  public_url: "${MCP_PUBLIC_URL}"
EOF

    # config.yml carries the JWT signing key; at the default umask it would be
    # world-readable, letting any local user forge admin tokens. But the backend
    # container (uid/gid 1000) reads it through a bind mount that preserves host
    # ownership, so a blind chmod 600 under a different uid locks the app out.
    if [[ "$(id -u)" == "1000" ]]; then
        chmod 600 config.yml
    elif chgrp 1000 config.yml 2>/dev/null || sudo -n chgrp 1000 config.yml 2>/dev/null; then
        chmod 640 config.yml
    else
        chmod 644 config.yml
        print_warn "  Could not restrict config.yml to uid/gid 1000 — left it world-readable."
        print_warn "  Tighten it with: sudo chgrp 1000 config.yml && chmod 640 config.yml"
    fi

    print_success "  Created config.yml"

    # Ensure data directory exists and is writable by the container user (UID 1000)
    if [[ ! -d "data" ]]; then
        mkdir -p data
        print_success "  Created data/ directory"
    fi

    # The backend container runs as uid 1000 and must write admin-recovery.txt,
    # cookies, backups and backgrounds under data/. Prefer giving it ownership;
    # fall back to world-writable only if we can't chown. Every attempt is
    # non-fatal: a data/ already owned by someone else (a root-owned dir Docker
    # created on an earlier run) refuses both chmod and chown, and under
    # `set -e` that would abort setup instead of reporting the one fixable thing.
    if [[ "$(id -u)" == "1000" ]]; then
        chmod 755 data 2>/dev/null || true
    elif chown 1000:1000 data 2>/dev/null || sudo -n chown 1000:1000 data 2>/dev/null; then
        chmod 775 data 2>/dev/null || true
    else
        chmod 777 data 2>/dev/null || true
        print_warn "  Could not chown data/ to uid 1000 — left it world-writable."
        print_warn "  Tighten it with: sudo chown 1000:1000 data/ && sudo chmod 775 data/"
    fi

    # `|| true` for the same reason the chmod/chown attempts above carry it: this
    # only decides whether to print a warning, so a stat that can't answer must
    # not take the install down with it.
    local data_owner data_mode
    data_owner="$(stat_field '%u' '%u' data || true)"
    data_mode="$(stat_field '%a' '%Lp' data || true)"
    if [[ -n "$data_mode" ]] && [[ "$data_owner" != "1000" ]] && (( (8#$data_mode & 2) == 0 )); then
        print_warn "  data/ is not writable by the container's user (uid 1000)."
        print_warn "  Fix it with: sudo chown 1000:1000 data/ && sudo chmod 775 data/"
    fi
}

# ── Step 7: Launch ─────────────────────────────────────────────────────────

# Sets LAUNCH_MODE (what was asked for) rather than echoing it: print_success
# writes to stdout, so a `mode=$(resolve_launch_mode)` would swallow every
# "Launch mode:" line into the variable instead of showing it.
resolve_launch_mode() {
    if [[ -n "$ARG_LAUNCH" ]]; then
        LAUNCH_MODE="$ARG_LAUNCH"
        print_success "  Launch mode: ${LAUNCH_MODE} (from --launch)"
        if [[ -n "$LAUNCH_SYNONYM" ]]; then
            print_warn "  Note: --launch ${LAUNCH_SYNONYM} builds from source. Use --launch published"
            print_warn "  to install the prebuilt release image instead."
        fi
        return 0
    fi

    if [[ -n "$NON_INTERACTIVE" ]]; then
        LAUNCH_MODE="published"
        print_success "  Launch mode: published (default)"
        return 0
    fi

    echo "  How would you like to proceed?"
    echo ""

    local choice
    # The build options are omitted rather than shown-and-rejected: without a
    # checkout there is nothing here for them to compile.
    if [[ -z "$HAVE_SOURCE" ]]; then
        echo "    1) Published release  - pull the prebuilt image (recommended)"
        echo "    2) Don't start        - I'll start it myself later"
        echo ""
        echo -e "  ${DIM}Building from source needs a git clone; see --help.${RESET}"
        echo ""
        while true; do
            choice=$(prompt_with_default "Select option (1-2)" "1")
            case "$choice" in
                1) LAUNCH_MODE="published"; break ;;
                2) LAUNCH_MODE="none";      break ;;
                *) print_warn "  Please enter 1 or 2." ;;
            esac
        done
        return 0
    fi

    echo "    1) Published release  - pull the prebuilt image (fastest, recommended)"
    echo "    2) Build prod locally - compile from source, single container :8000"
    echo "    3) Build dev locally  - frontend :3000, API :8000, hot reload"
    echo "    4) Don't start        - I'll start it myself later"
    echo ""

    while true; do
        choice=$(prompt_with_default "Select option (1-4)" "1")
        case "$choice" in
            1) LAUNCH_MODE="published";  break ;;
            2) LAUNCH_MODE="build-prod"; break ;;
            3) LAUNCH_MODE="build-dev";  break ;;
            4) LAUNCH_MODE="none";       break ;;
            *) print_warn "  Please enter a number between 1 and 4." ;;
        esac
    done
    return 0
}

launch_published() {
    echo "  Pulling ${IMAGE_REF} ..."
    echo ""

    # `if !` rather than a bare call: under `set -e` a failed pull would kill the
    # script before the fallback below could run. Compose's own stderr is left
    # alone -- "denied" vs "manifest unknown" is the actual diagnostic.
    if docker compose -f docker-compose.published.yml pull backend; then
        echo ""
        echo "  Starting from the published release..."
        echo ""
        docker compose -f docker-compose.published.yml up -d --remove-orphans
        LAUNCHED_MODE="published"
        echo ""
        print_success "  Application started from ${IMAGE_REF}!"
        echo ""
        echo "  Application: http://localhost:8000"
        return 0
    fi

    echo ""
    print_error "  Could not pull ${IMAGE_REF}."
    echo "  Either that release isn't published yet, the tag doesn't exist, or"
    echo "  this machine can't reach ghcr.io (offline, proxy, DNS)."
    echo ""

    if [[ -z "$HAVE_SOURCE" ]]; then
        print_warn "  Nothing was started. Everything else is configured, so retry with:"
        echo -e "    ${BOLD}docker compose -f docker-compose.published.yml up -d${RESET}"
        echo ""
        echo "  Or build it from source instead -- expect 20-45 minutes:"
        echo -e "    ${BOLD}git clone ${CLONE_URL}${RESET}"
        echo -e "    ${BOLD}cd yt-hoarder && bash setup.sh --launch build-prod${RESET}"
        LAUNCH_FAILED="1"
        return 0
    fi

    echo "  You can build the same thing from this checkout instead -- expect"
    echo "  20-45 minutes and several GB of disk."
    echo ""

    if [[ -n "$NON_INTERACTIVE" ]]; then
        print_warn "  Not building automatically in non-interactive mode (-y)."
        echo "  To build from source instead, run:"
        echo -e "    ${BOLD}docker compose -f docker-compose.prod.yml up -d --build${RESET}"
        LAUNCH_FAILED="1"
        return 0
    fi

    # Defaults to no: a stray Enter must not kick off a 45-minute build, and
    # re-running this script after fixing the cause costs seconds.
    if prompt_yes_no "Build it locally now?" "n"; then
        echo ""
        launch_build_prod
        return 0
    fi

    echo ""
    print_warn "  Nothing was started. Retry the pull later with:"
    echo -e "    ${BOLD}docker compose -f docker-compose.published.yml up -d${RESET}"
    LAUNCH_FAILED="1"
    return 0
}

launch_build_prod() {
    echo "  Building and starting in prod mode..."
    echo ""
    docker compose -f docker-compose.prod.yml up -d --build --remove-orphans
    LAUNCHED_MODE="build-prod"
    echo ""
    print_success "  Application started in prod mode!"
    echo ""
    echo "  Application: http://localhost:8000"
    return 0
}

launch_build_dev() {
    echo "  Starting in dev mode..."
    echo ""
    docker compose -f docker-compose.dev.yml up -d --remove-orphans
    LAUNCHED_MODE="build-dev"
    echo ""
    print_success "  Application started in dev mode!"
    echo ""
    echo "  Frontend:  http://localhost:3000"
    echo "  API Docs:  http://localhost:8000/docs"
    echo ""
    echo "  Both are reachable at this machine's other addresses too -- a LAN IP,"
    echo "  a Tailscale name, a domain -- with nothing further to configure."
    return 0
}

print_start_later_hints() {
    if [[ -z "$HAVE_SOURCE" ]]; then
        echo "  To start later, from $(pwd):"
        echo ""
        echo -e "    ${BOLD}docker compose -f docker-compose.published.yml up -d${RESET}"
        return 0
    fi

    echo "  To start later, run one of:"
    echo ""
    echo -e "    ${BOLD}docker compose -f docker-compose.published.yml up -d${RESET}  # published release"
    echo -e "    ${BOLD}docker compose -f docker-compose.prod.yml up -d --build${RESET}  # build prod"
    echo -e "    ${BOLD}docker compose -f docker-compose.dev.yml up -d${RESET}   # build dev"
    echo ""
    echo "  All three are reachable from any device on your network at whatever"
    echo "  address this machine answers on -- there is nothing to configure for it."
    return 0
}

prompt_launch() {
    print_header "Launch Application"

    echo "  Your configuration is ready!"
    echo ""

    resolve_launch_mode
    echo ""

    case "$LAUNCH_MODE" in
        published)  launch_published ;;
        build-prod) launch_build_prod ;;
        build-dev)  launch_build_dev ;;
        none)       print_start_later_hints ;;
    esac
    return 0
}

# ── Completion ──────────────────────────────────────────────────────────────

print_completion() {
    print_header "Setup Complete"

    echo "  The first user to register will automatically become the admin."
    echo "  Subsequent users will need admin approval before they can log in."
    echo ""
    echo -e "  Install dir:   ${BOLD}$(pwd)${RESET}"
    echo -e "  Config files:  ${BOLD}.env${RESET}  ${BOLD}config.yml${RESET}"

    local samples=()
    if [[ -f .env.sample ]]; then samples+=(".env.sample"); fi
    if [[ -f config.sample.yml ]]; then samples+=("config.sample.yml"); fi
    if [[ "${#samples[@]}" -gt 0 ]]; then
        echo -e "  Sample files:  ${DIM}${samples[*]}${RESET}"
    fi
    echo ""

    # Keyed on what actually started, not what was requested: a failed pull that
    # fell back to a local build leaves LAUNCH_MODE=published but runs prod.
    local compose_file=""
    case "$LAUNCHED_MODE" in
        published)  compose_file="docker-compose.published.yml" ;;
        build-prod) compose_file="docker-compose.prod.yml" ;;
        build-dev)  compose_file="docker-compose.dev.yml" ;;
    esac

    if [[ -n "$compose_file" ]]; then
        echo -e "  View logs:     ${BOLD}docker compose -f ${compose_file} logs -f${RESET}"
        echo -e "  Stop:          ${BOLD}docker compose -f ${compose_file} down${RESET}"
        if [[ "$LAUNCHED_MODE" == "published" ]]; then
            echo -e "  Update:        ${BOLD}docker compose -f ${compose_file} pull && docker compose -f ${compose_file} up -d${RESET}"
        fi
    fi
    echo ""

    print_offline_mode_hint
    print_mcp_hint
}

# Offline downloads on a phone need an HTTPS origin, and nothing this script writes
# can supply one -- TLS terminates in front of the app, not inside it. Without a
# pointer here the feature is undiscoverable: its controls hide themselves on an
# insecure origin, so there is nothing on screen to prompt the question.
print_offline_mode_hint() {
    if [[ "$LAUNCHED_MODE" == "build-dev" ]]; then
        echo "  Offline mode on phones is not available in dev mode. It needs a production"
        echo "  install (published or build-prod) reached over HTTPS."
    elif [[ "${COOKIE_SECURE:-false}" == "true" ]]; then
        echo "  Offline mode on phones (download media, play it with no connection) needs"
        echo "  this app reached over HTTPS. The auth cookie is already set to HTTPS-only,"
        echo "  so put a trusted certificate in front of :8000 before signing in again --"
        echo "  Caddy, nginx, Traefik, Cloudflare Tunnel, or 'tailscale serve --bg 8000'."
    else
        echo "  Offline mode on phones (download media, play it with no connection) needs"
        echo "  this app reached over HTTPS. Put any trusted certificate in front of :8000 --"
        echo "  Caddy, nginx, Traefik, Cloudflare Tunnel, or 'tailscale serve --bg 8000' --"
        echo "  then re-run with --cookie-secure, or set auth.cookie_secure in config.yml."
    fi
    echo -e "  ${DIM}${DOCS_BASE}/OFFLINE.md${RESET}"
    echo ""
}

print_mcp_hint() {
    [[ "${MCP_ENABLED:-false}" == "true" ]] || return 0

    # Dev serves the API (and so /mcp, unprefixed) on :8000 while the UI is on :3000,
    # so a public_url -- the UI's address -- is no use for the endpoint there.
    local endpoint="${MCP_PUBLIC_URL:-http://localhost:8000}/api/mcp"
    if [[ "$LAUNCHED_MODE" == "build-dev" ]]; then
        endpoint="http://localhost:8000/mcp"
    fi

    echo "  AI agents (MCP) are enabled. To connect one:"
    echo "    1. Sign in, open the account menu, choose MCP Access Tokens, create a token."
    echo "    2. Point your MCP client at the endpoint, sending the token as a header:"
    echo -e "         Endpoint:  ${BOLD}${endpoint}${RESET}"
    echo -e "         Header:    ${BOLD}Authorization: Bearer <token>${RESET}"
    echo "       The token dialog also shows this as ready-to-paste client setup."
    if [[ -z "$MCP_PUBLIC_URL" || "$LAUNCHED_MODE" == "build-dev" ]]; then
        echo "    From another machine, use this host's address instead of localhost."
    fi
    echo -e "  ${DIM}${DOCS_BASE}/CONFIGURATION.md${RESET}"
    echo ""
}

# ── Main ────────────────────────────────────────────────────────────────────

main() {
    LAUNCH_MODE="none"
    LAUNCHED_MODE=""
    LAUNCH_FAILED=""

    parse_args "$@"
    detect_install_mode
    require_source_for_build_modes
    ensure_interactive_stdin
    welcome
    check_prerequisites
    resolve_image_tag
    bootstrap_install_files
    check_existing_files
    configure_storage
    configure_transcription
    configure_mcp
    generate_jwt_secret
    write_config_files
    prompt_launch
    print_completion

    # A one-shot install that didn't install must not report success to a script.
    if [[ -n "$LAUNCH_FAILED" ]]; then
        return 1
    fi
    return 0
}

main "$@"
