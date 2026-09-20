#!/usr/bin/env bash
# Move a folder onto S4P (HIFIS/PUNCH dCache at DESY) from your own machine.
#
# Deliberately thin and readable: every command it runs is visible below, so
# you can stop using it the moment you would rather type them yourself. It
# wraps oidc-agent (for the token) and rclone (for the transfer) and adds
# nothing of its own except defaults and a check.
#
#   ./s4p-transfer.sh setup            one-off per machine
#   ./s4p-transfer.sh check            proves the token reaches your VO path
#   ./s4p-transfer.sh push SRC DEST    upload a folder
#
# HIFIS publishes its own helper scripts for the same job. Use theirs if you
# prefer; this one exists so the card can show the whole path in one file.

set -euo pipefail

ACCOUNT="${HELMHOLTZ_AAI_OIDC_AGENT_ACCOUNT:-punch-aai}"
BASE="${S4P_BASE:-https://dcache-doma-door01.desy.de/punch/physicsllm}"
ISSUER="${S4P_ISSUER:-https://login.helmholtz.de/oauth2}"

die() { echo "error: $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

token() {
  have oidc-token || die "oidc-token not found - run '$0 setup' first"
  oidc-token "$ACCOUNT" 2>/dev/null || die "no token for account '$ACCOUNT' - run '$0 setup'"
}

cmd_setup() {
  have oidc-gen || die "install oidc-agent first: https://indigo-dc.gitbook.io/oidc-agent/"
  have rclone   || die "install rclone first: https://rclone.org/install/"

  if oidc-add --loaded 2>/dev/null | grep -qx "$ACCOUNT"; then
    echo "account '$ACCOUNT' is already loaded - nothing to do"
    return
  fi

  echo "Registering an oidc-agent account named '$ACCOUNT'."
  echo "A browser will open for the Helmholtz AAI consent. That is the one"
  echo "human step in this flow, and it is meant to be."
  oidc-gen --iss "$ISSUER" \
           --scope "openid profile email offline_access eduperson_entitlement" \
           "$ACCOUNT"
  echo "done. Now run: $0 check"
}

cmd_check() {
  local t; t="$(token)"
  echo "token: acquired for '$ACCOUNT'"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -X PROPFIND \
         -H "Authorization: Bearer $t" -H 'Depth: 0' "$BASE/") || true
  case "$code" in
    207) echo "PROPFIND $BASE/ -> 207  VO membership is effective. You can upload." ;;
    401) echo "PROPFIND $BASE/ -> 401  token rejected. Re-run setup, or the token expired." ;;
    403) echo "PROPFIND $BASE/ -> 403  authenticated but not authorised."
         echo "     This is the VO approval step: punch AND physicsllm must both be granted." ;;
    404) echo "PROPFIND $BASE/ -> 404  path does not exist. Check S4P_BASE." ;;
    *)   echo "PROPFIND $BASE/ -> $code  unexpected; see the dCache door logs." ;;
  esac
}

cmd_push() {
  local src="${1:-}" dest="${2:-}"
  [ -n "$src" ] && [ -n "$dest" ] || die "usage: $0 push SRC_DIR DEST_REL_PATH"
  [ -d "$src" ] || die "not a directory: $src"
  local t; t="$(token)"

  # --webdav-bearer-token is refreshed per invocation, so a long transfer that
  # outlives the token needs a re-run; rclone skips what it already moved.
  rclone copy "$src" ":webdav:$dest" \
    --webdav-url "$BASE" \
    --webdav-vendor other \
    --webdav-bearer-token "$t" \
    --transfers 4 --checkers 8 \
    --progress --stats-one-line
  echo "uploaded to $BASE/$dest"
}

case "${1:-}" in
  setup) shift; cmd_setup "$@" ;;
  check) shift; cmd_check "$@" ;;
  push)  shift; cmd_push  "$@" ;;
  *) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
