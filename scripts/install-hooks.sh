#!/bin/sh
# Install a git pre-commit hook that runs the commit-stage checks (without the slow test suites) and stops the
# commit when any check FAILs. The hook runs the staged scripts/check.py (copied out of the index to a temporary
# directory, removed afterwards), so a commit is checked by the checker it contains. Re-running replaces an
# earlier Argus hook; another hook is kept as .bak.
set -eu

top=$(git rev-parse --show-toplevel)
hooks=$(cd "$top" && git rev-parse --git-path hooks)
case "$hooks" in /*) ;; *) hooks="$top/$hooks" ;; esac
hook="$hooks/pre-commit"
marker="# argus-check pre-commit hook"

mkdir -p "$hooks"
if [ -f "$hook" ] && ! grep -q "$marker" "$hook"; then
    mv "$hook" "$hook.bak"
    echo "kept the existing hook as $hook.bak"
fi

# Git GUIs and some shells start hooks without nvm on PATH: remember where node is now.
node_dir=""
if command -v node >/dev/null 2>&1; then
    node_dir=$(dirname "$(command -v node)")
fi

cat > "$hook" <<HOOK
#!/bin/sh
$marker (installed by scripts/install-hooks.sh)
# Runs the STAGED scripts/check.py, the version this commit will contain, not the working-tree copy: the checks
# read the index, so the checker must come from the index too, whether the commit changes it or not.
top=\$(git rev-parse --show-toplevel) || exit 1
cd "\$top" || exit 1
if [ ! -x backend/.venv/bin/python ]; then
    echo "pre-commit: backend/.venv is missing, so the checks cannot run. Run 'make setup' first." >&2
    exit 1
fi
if ! git cat-file -e :scripts/check.py 2>/dev/null; then
    echo "pre-commit: scripts/check.py is not staged (not in the index), so the checks cannot run." >&2
    exit 1
fi
staged=\$(mktemp -d "\${TMPDIR:-/tmp}/argus-check.XXXXXX") || exit 1
trap 'rm -rf "\$staged"' EXIT
trap 'exit 1' HUP INT TERM
git show :scripts/check.py > "\$staged/check.py" || exit 1
ARGUS_NODE_DIR="\${ARGUS_NODE_DIR:-$node_dir}" backend/.venv/bin/python "\$staged/check.py" commit --fast
HOOK
chmod +x "$hook"
echo "installed $hook (runs: the staged scripts/check.py commit --fast; node from ${node_dir:-PATH})"
