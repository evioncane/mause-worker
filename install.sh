#!/usr/bin/env bash
#
# Install `mause` as a command on your PATH.
#
#   ./install.sh              Symlink mause.sh into ~/.local/bin as `mause`
#   ./install.sh --uninstall  Remove that symlink
#
# Override the location with MAUSE_BIN_DIR=/somewhere/on/your/path ./install.sh
#
# The symlink points back at this checkout, so `git pull` updates the command
# and moving or deleting the project breaks it (by design — there is no copy to
# drift out of sync).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
WORKER_LAUNCHER="$SCRIPT_DIR/mause.sh"
TARGET_DIR="${MAUSE_BIN_DIR:-$HOME/.local/bin}"
TARGET_LINK="$TARGET_DIR/mause"
SHELL_RC="$HOME/.zshrc"
PATH_LINE="export PATH=\"\$HOME/.local/bin:\$PATH\""
# Some rc files pin a block that has to stay last; insert above it, not after.
SDKMAN_MARKER="#THIS MUST BE AT THE END OF THE FILE FOR SDKMAN TO WORK!!!"


uninstall() {
    if [[ -L "$TARGET_LINK" ]]; then
        rm -f "$TARGET_LINK"
        echo "Removed $TARGET_LINK"
    elif [[ -e "$TARGET_LINK" ]]; then
        echo "$TARGET_LINK exists but is not a symlink — leaving it alone." >&2
        return 1
    else
        echo "Nothing to remove at $TARGET_LINK"
    fi
    echo "The PATH line in $SHELL_RC was left in place; remove it by hand if you want it gone."
}


# Is the directory already reachable from PATH in this shell?
directory_on_path() {
    case ":$PATH:" in
        *":$TARGET_DIR:"*) return 0 ;;
        *)                 return 1 ;;
    esac
}


add_path_line() {
    if [[ ! -f "$SHELL_RC" ]]; then
        printf '%s\n' "$PATH_LINE" >"$SHELL_RC"
        echo "Created $SHELL_RC with the PATH line."
        return
    fi

    if grep -qF '$HOME/.local/bin' "$SHELL_RC" || grep -qF "$HOME/.local/bin" "$SHELL_RC"; then
        echo "$SHELL_RC already references ~/.local/bin — leaving it untouched."
        return
    fi

    local backup
    backup="$SHELL_RC.backup-$(date '+%Y%m%d%H%M%S')"
    cp "$SHELL_RC" "$backup"

    if grep -qF "$SDKMAN_MARKER" "$SHELL_RC"; then
        # Insert above the pinned trailing block.
        local temporary
        temporary="$(mktemp)"
        awk -v line="$PATH_LINE" -v marker="$SDKMAN_MARKER" '
            $0 == marker && !inserted {
                print "# Mause Worker — see " ENVIRON["MAUSE_SOURCE_DIR"]
                print line
                print ""
                inserted = 1
            }
            { print }
        ' "$SHELL_RC" >"$temporary"
        mv "$temporary" "$SHELL_RC"
        echo "Added the PATH line to $SHELL_RC (above the SDKMAN block)."
    else
        {
            echo
            echo "# Mause Worker — see $SCRIPT_DIR"
            printf '%s\n' "$PATH_LINE"
        } >>"$SHELL_RC"
        echo "Added the PATH line to the end of $SHELL_RC."
    fi
    echo "Backup of the original: $backup"
}


install() {
    if [[ ! -f "$WORKER_LAUNCHER" ]]; then
        echo "mause.sh not found next to this installer ($WORKER_LAUNCHER)." >&2
        return 1
    fi
    chmod +x "$WORKER_LAUNCHER"

    if [[ -e "$TARGET_LINK" && ! -L "$TARGET_LINK" ]]; then
        echo "$TARGET_LINK already exists and is a real file, not a symlink." >&2
        echo "Move it aside first, or pick another directory with MAUSE_BIN_DIR." >&2
        return 1
    fi

    mkdir -p "$TARGET_DIR"
    ln -sfn "$WORKER_LAUNCHER" "$TARGET_LINK"
    echo "Linked $TARGET_LINK -> $WORKER_LAUNCHER"

    local needs_new_shell=0
    if ! directory_on_path; then
        MAUSE_SOURCE_DIR="$SCRIPT_DIR" add_path_line
        needs_new_shell=1
    fi

    echo
    if [[ $needs_new_shell -eq 1 ]]; then
        echo "Open a new terminal (or run: source $SHELL_RC), then:"
    else
        echo "Ready to use:"
    fi
    echo "  mause           # start, with the menu bar icon"
    echo "  mause status    # is it running?"
    echo "  mause stop      # stop it"
    echo "  mause help      # everything else"
}


case "${1:-install}" in
    install|"")            install ;;
    --uninstall|uninstall) uninstall ;;
    -h|--help|help)
        awk 'NR > 2 { if (/^#/) { sub(/^# ?/, ""); print } else { exit } }' "${BASH_SOURCE[0]}"
        ;;
    *)
        echo "Unknown option: $1" >&2
        exit 2
        ;;
esac
