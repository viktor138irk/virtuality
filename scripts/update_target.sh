#!/usr/bin/env bash
# Sourced by auto_update_check.sh and apply_github_update.sh: which git ref a node updates to.
#   stable (default) — the latest release tag vX.Y.Z; main until the first release is tagged
#   main             — every change on the main branch
# Needs SOURCE_DIR, REMOTE and BRANCH; sets UPDATE_CHANNEL, TARGET_REF, TARGET_LABEL, TARGET_ZIP_URL.

update_channel() {
  local value="${VIRTUALITY_UPDATE_CHANNEL:-}"
  if [[ -z "$value" && -f /var/lib/virtuality/config/web.env ]]; then
    value="$(sed -n 's/^VIRTUALITY_UPDATE_CHANNEL=//p' /var/lib/virtuality/config/web.env | tail -n1 | tr -d "\"'")"
  fi
  [[ "$value" == "main" ]] && echo main || echo stable
}

resolve_update_target() {
  local tag=""
  UPDATE_CHANNEL="$(update_channel)"
  TARGET_REF="${REMOTE}/${BRANCH}"
  TARGET_LABEL="${BRANCH}"
  TARGET_ZIP_URL="https://github.com/viktor138irk/virtuality/archive/refs/heads/${BRANCH}.zip"
  if [[ "$UPDATE_CHANNEL" == "stable" && -d "${SOURCE_DIR}/.git" ]]; then
    tag="$(git -C "$SOURCE_DIR" tag --list 'v[0-9]*' --sort=-v:refname | grep -E '^v[0-9]+(\.[0-9]+)*$' | head -n1 || true)"
    if [[ -n "$tag" ]]; then
      TARGET_REF="refs/tags/${tag}"
      TARGET_LABEL="$tag"
      TARGET_ZIP_URL="https://github.com/viktor138irk/virtuality/archive/refs/tags/${tag}.zip"
    fi
  fi
}
