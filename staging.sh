#!/bin/sh
# Staging: run a branch of this checkout in the real Claude app, before it's merged (card #34).
#
#   ./staging.sh on [branch]   voice runs the branch (default: the one checked out here)
#   ./staging.sh off           back to the release, from the voice-assistant marketplace
#   ./staging.sh status        which one the app loads
#
# The branch is installed the way users install voice, from a marketplace: a second one, voice-assistant-staging,
# in ~/.voice-assistant/staging, whose plugin is a worktree of the branch. Its version gets "-staging.<commit>",
# so every commit is a new install and the start line in voice.log says what runs. Only one of the two is enabled at
# a time; both use your settings, voice prints and built app in ~/.voice-assistant. Then quit and reopen the app.
set -eu

REPO=$(cd "$(dirname "$0")" && pwd)
STAGE="$HOME/.voice-assistant/staging"
TREE="$STAGE/voice-mode"
RELEASE="voice-mode@voice-assistant"
MARKET="voice-assistant-staging"
STAGING="voice-mode@$MARKET"

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

restart_note() {
  echo "Quit and reopen the Claude app (Cmd-Q) for it to take effect. The first start of a new install takes about a minute."
}

case "${1:-status}" in
on)
  branch=${2:-$(git -C "$REPO" rev-parse --abbrev-ref HEAD)}
  commit=$(git -C "$REPO" rev-parse --verify --quiet "$branch^{commit}") || { echo "no branch or commit '$branch' here" >&2; exit 1; }
  short=$(git -C "$REPO" rev-parse --short "$commit")
  if [ -n "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] && [ "$branch" = "$(git -C "$REPO" rev-parse --abbrev-ref HEAD)" ]; then
    echo "note: uncommitted changes here aren't staged; only the commit $short is" >&2
  fi

  # The branch, in a worktree of its own (detached, so the branch stays free to check out here).
  mkdir -p "$STAGE/.claude-plugin"
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
plugin["version"] = plugin["version"].split("-")[0] + f"-staging.{short}"
open(path, "w").write(json.dumps(plugin))
EOF
  printf '%s\n' "$branch" > "$STAGE/branch"
  cat > "$STAGE/.claude-plugin/marketplace.json" <<EOF
{"name": "$MARKET", "description": "Voice mode, from a branch of a local checkout (staging.sh).",
 "owner": {"name": "Glass Labs"},
 "plugins": [{"name": "voice-mode", "source": "./voice-mode", "description": "Voice mode, staging: $branch at $short."}]}
EOF

  if claude plugin marketplace list 2>/dev/null | grep -q "$MARKET"; then
    claude plugin marketplace update "$MARKET" >/dev/null
  else
    claude plugin marketplace add "$STAGE" >/dev/null
  fi
  [ "$(enabled "$RELEASE")" = true ] && claude plugin disable "$RELEASE" >/dev/null
  if installed "$STAGING"; then
    claude plugin update "$STAGING" >/dev/null
    [ "$(enabled "$STAGING")" = true ] || claude plugin enable "$STAGING" >/dev/null
  else
    claude plugin install "$STAGING" >/dev/null
  fi
  echo "Staging: voice runs $branch at $short."
  restart_note
  ;;
off)
  if installed "$STAGING" && [ "$(enabled "$STAGING")" = true ]; then
    claude plugin disable "$STAGING" >/dev/null
  fi
  [ "$(enabled "$RELEASE")" = true ] || claude plugin enable "$RELEASE" >/dev/null
  echo "Release: voice runs the voice-assistant marketplace's version again."
  restart_note
  ;;
status)
  if [ "$(enabled "$STAGING")" = true ]; then
    echo "staging: $(cat "$STAGE/branch" 2>/dev/null || echo '?') at $(git -C "$TREE" rev-parse --short HEAD 2>/dev/null || echo '?')"
  elif [ "$(enabled "$RELEASE")" = true ]; then
    echo "release: $(claude plugin list --json 2>/dev/null | python3 -c 'import json,sys; print(next((p["version"] for p in json.load(sys.stdin) if p.get("id") == "voice-mode@voice-assistant"), "?"))')"
  else
    echo "neither: voice isn't enabled (./staging.sh off brings the release back)"
  fi
  ;;
*)
  sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
  ;;
esac
