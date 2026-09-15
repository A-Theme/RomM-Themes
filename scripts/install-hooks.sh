#!/usr/bin/env bash
# Install this repo's git hooks.
#
# Git never copies hooks when a repository is cloned, so every fresh clone
# starts with none. Run this once after cloning:
#
#   ./scripts/install-hooks.sh
#
# Re-running is safe; it overwrites whatever is there with the current hook.
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

HOOK_DIR=$(git rev-parse --git-path hooks)
mkdir -p "$HOOK_DIR"

GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; RST=$'\033[0m'

for hook in pre-commit; do
    src="scripts/$hook"
    dest="$HOOK_DIR/$hook"
    [[ -f "$src" ]] || { echo "no $src to install"; exit 1; }

    if [[ -e "$dest" && ! -L "$dest" ]]; then
        cp "$dest" "$dest.backup"
        echo "${YELLOW}note${RST}: kept your existing $hook as $hook.backup"
    fi

    # Symlink where the platform supports it so the hook tracks the repo, and
    # fall back to a copy (Windows without developer mode) where it does not.
    if ln -sf "../../$src" "$dest" 2>/dev/null; then
        method="linked"
    else
        cp "$src" "$dest"
        method="copied"
    fi
    chmod +x "$dest" 2>/dev/null || true
    echo "${GREEN}[ ok ]${RST} $hook $method"
done

echo
echo "Hooks installed. The pre-commit hook regenerates manifest.json when you"
echo "change a theme, and blocks commits carrying tokens or private keys."
