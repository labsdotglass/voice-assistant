#!/bin/sh
# Next: run a branch of this checkout in the real Claude app, before it's merged (card #34; called staging until card #47).
#
#   ./next.sh on [branch]   voice runs the branch (default: the one checked out here)
#   ./next.sh pass          you tried what next runs and it works: GitHub lets that commit into main
#   ./next.sh off           back to the release, from the voice-assistant marketplace
#   ./next.sh status        which one the app loads
#
# The branch is installed the way users install voice, from a marketplace: a second one, voice-assistant-next,
# in ~/.voice-assistant/next, whose plugin is a worktree of the branch. Its version gets "-next.<commit>",
# so every commit is a new install and the start line in voice.log says what runs. Only one of the two is enabled at
# a time; both use your settings, voice prints and built app in ~/.voice-assistant. Then quit and reopen the app.
#
# The gate (card #43): main takes a PR only when its head commit has a green "next" commit status. `on` posts it
# pending, `pass` posts it green, and a ruleset on main requires it. Run `pass` only once someone has tried the build.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
DIR="$HOME/.voice-assistant/next"
TREE="$DIR/voice-mode"
RELEASE="voice-mode@voice-assistant"
MARKET="voice-assistant-next"
NEXT="voice-mode@$MARKET"
OLD_MARKET="voice-assistant-staging"  # this channel's name before card #47; `on` removes it
OLD_DIR="$HOME/.voice-assistant/staging"

enabled() {  # prints true or false: whether that plugin is installed and enabled
  claude plugin list --json 2>/dev/null | python3 -c '
import json, sys
print(str(any(p.get("id") == sys.argv[1] and p.get("enabled") for p in json.load(sys.stdin))).lower())' "$1"
}

installed() {
  claude plugin list --json 2>/dev/null | python3 -c '
import json, sys
sys.exit(0 if any(p.get("id") == sys.argv[1] for p in json.load(sys.stdin)) else 1)' "$1"
}

# owner/name of origin on GitHub, from its URL (git@github.com:o/n.git or https://github.com/o/n)
origin_repo() {
  git -C "$REPO" remote get-url origin | sed -E 's#^(git@github\.com:|https://github\.com/)##; s#\.git$##'
}

# mark <sha> <state> <description>: the "next" status on that commit; says so, and why not, if it can't
mark() {
  repo=$(origin_repo)
  if ! gh api "repos/$repo/commits/$1" --silent 2>/dev/null; then
    echo "note: $(git -C "$REPO" rev-parse --short "$1") isn't on GitHub yet, so it isn't marked; push it, then run ./next.sh ${4:-pass}" >&2
    return 1
  fi
  gh api -X POST "repos/$repo/statuses/$1" -f state="$2" -f context=next -f description="$3" --silent
}

# on_main <sha>: true when the commit is already on main. Nothing to mark then: the gate reads a PR's head, and
# main is never one (card #51). Fetched first, quietly; offline, the local origin/main answers.
on_main() {
  git -C "$REPO" fetch --quiet origin main 2>/dev/null || true
  git -C "$REPO" merge-base --is-ancestor "$1" origin/main 2>/dev/null
}

restart_note() {
  echo "Quit and reopen the Claude app (Cmd-Q) for it to take effect. The first start of a new install takes about a minute."
}

case "${1:-status}" in
on)
  branch=${2:-$(git -C "$REPO" rev-parse --abbrev-ref HEAD)}
  commit=$(git -C "$REPO" rev-parse --verify --quiet "$branch^{commit}") || { echo "no branch or commit '$branch' here" >&2; exit 1; }
  short=$(git -C "$REPO" rev-parse --short "$commit")
  if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] && [ "$branch" = "$(git -C "$REPO" rev-parse --abbrev-ref HEAD)" ]; then
    echo "note: uncommitted changes here aren't on next; only the commit $short is" >&2
  fi

  # The old staging install goes first, so the two never both run.
  if installed "voice-mode@$OLD_MARKET"; then
    claude plugin uninstall "voice-mode@$OLD_MARKET" >/dev/null
  fi
  if claude plugin marketplace list 2>/dev/null | grep -q "$OLD_MARKET"; then
    claude plugin marketplace remove "$OLD_MARKET" >/dev/null
  fi
  if [ -d "$OLD_DIR" ]; then
    [ -e "$OLD_DIR/voice-mode/.git" ] && git -C "$REPO" worktree remove --force "$OLD_DIR/voice-mode"
    rm -rf "$OLD_DIR"
    echo "Removed the old staging install (now called next)."
  fi

  # The branch, in a worktree of its own (detached, so the branch stays free to check out here).
  mkdir -p "$DIR/.claude-plugin"
  if [ -d "$TREE/.git" ] || [ -f "$TREE/.git" ]; then
    git -C "$TREE" checkout --quiet --force --detach "$commit"
  else
    git -C "$REPO" worktree prune
    git -C "$REPO" worktree add --quiet --force --detach "$TREE" "$commit"
  fi
  # Its version, marked: a new install per commit, and voice.log's start line names it.
  python3 - "$TREE/.claude-plugin/plugin.json" "$short" <<'EOF'
import json, sys
path, short = sys.argv[1], sys.argv[2]
plugin = json.load(open(path))
plugin["version"] = plugin["version"].split("-")[0] + f"-next.{short}"
open(path, "w").write(json.dumps(plugin))
EOF
  printf '%s\n' "$branch" > "$DIR/branch"
  cat > "$DIR/.claude-plugin/marketplace.json" <<EOF
{"name": "$MARKET", "description": "Voice mode, from a branch of a local checkout (next.sh).",
 "owner": {"name": "Glass Labs"},
 "plugins": [{"name": "voice-mode", "source": "./voice-mode", "description": "Voice mode, next: $branch at $short."}]}
EOF

  if claude plugin marketplace list 2>/dev/null | grep -q "$MARKET"; then
    claude plugin marketplace update "$MARKET" >/dev/null
  else
    claude plugin marketplace add "$DIR" >/dev/null
  fi
  [ "$(enabled "$RELEASE")" = true ] && claude plugin disable "$RELEASE" >/dev/null
  if installed "$NEXT"; then
    claude plugin update "$NEXT" >/dev/null
    [ "$(enabled "$NEXT")" = true ] || claude plugin enable "$NEXT" >/dev/null
  else
    claude plugin install "$NEXT" >/dev/null
  fi
  echo "Next: voice runs $branch at $short."
  if on_main "$commit"; then
    echo "GitHub: $short is on main already, so it isn't marked."
  else
    mark "$commit" pending "on next, not tried yet" on && echo "GitHub: $short is marked pending; ./next.sh pass once you've tried it."
  fi
  restart_note
  ;;
pass)
  [ "$(enabled "$NEXT")" = true ] || { echo "next isn't on: ./next.sh on [branch] first, then try it" >&2; exit 1; }
  commit=$(git -C "$TREE" rev-parse HEAD)
  short=$(git -C "$TREE" rev-parse --short HEAD)
  branch=$(cat "$DIR/branch" 2>/dev/null || echo '?')
  if on_main "$commit"; then
    echo "GitHub: $short is on main already: nothing to mark."
    exit 0
  fi
  mark "$commit" success "tried live in next" pass || exit 1
  echo "GitHub: $branch at $short is marked tried live; a PR whose head is $short can merge into main."
  head=$(git -C "$REPO" rev-parse --verify --quiet "$branch^{commit}" || true)
  [ -n "$head" ] && [ "$head" != "$commit" ] && echo "note: $branch has moved on since ($(git -C "$REPO" rev-parse --short "$head")); ./next.sh on it too" >&2
  true
  ;;
off)
  if installed "$NEXT" && [ "$(enabled "$NEXT")" = true ]; then
    claude plugin disable "$NEXT" >/dev/null
  fi
  [ "$(enabled "$RELEASE")" = true ] || claude plugin enable "$RELEASE" >/dev/null
  echo "Release: voice runs the voice-assistant marketplace's version again."
  restart_note
  ;;
status)
  if [ "$(enabled "$NEXT")" = true ]; then
    echo "next: $(cat "$DIR/branch" 2>/dev/null || echo '?') at $(git -C "$TREE" rev-parse --short HEAD 2>/dev/null || echo '?')"
  elif [ "$(enabled "$RELEASE")" = true ]; then
    echo "release: $(claude plugin list --json 2>/dev/null | python3 -c 'import json,sys; print(next((p["version"] for p in json.load(sys.stdin) if p.get("id") == "voice-mode@voice-assistant"), "?"))')"
  else
    echo "neither: voice isn't enabled (./next.sh off brings the release back)"
  fi
  ;;
*)
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
  ;;
esac
