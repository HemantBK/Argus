#!/bin/sh
# Install a git pre-commit hook that runs the commit-stage checks (without the slow test suites) and stops the
# commit when any check FAILs. Re-running replaces an earlier Argus hook; another hook is kept as .bak.
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
top=\$(git rev-parse --show-toplevel) || exit 1
cd "\$top" || exit 1
if [ ! -x backend/.venv/bin/python ]; then
    echo "pre-commit: backend/.venv is missing, so the checks cannot run. Run 'make setup' first." >&2
    exit 1
fi
ARGUS_NODE_DIR="\${ARGUS_NODE_DIR:-$node_dir}" exec backend/.venv/bin/python scripts/check.py commit --fast
HOOK
chmod +x "$hook"
echo "installed $hook (runs: scripts/check.py commit --fast; node from ${node_dir:-PATH})"
