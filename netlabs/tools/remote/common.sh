#!/usr/bin/env bash
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Shared SSH transport for macOS and Linux remote-lab clients.
set -euo pipefail
REMOTE_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
REMOTE_CFG="${REMOTE_LAB_CONFIG:-$REMOTE_REPO_ROOT/netlabs/remote-lab.cfg}"
[[ -r "$REMOTE_CFG" ]] || { echo "Missing config: $REMOTE_CFG" >&2; exit 2; }
source "$REMOTE_CFG"
: "${REMOTE_HOST:=StormTrooper}" "${REMOTE_USER:=netlab}" \
  "${REMOTE_PASSWORD:=netlab}" "${REMOTE_SSH_PORT:=22}"
[[ "$REMOTE_HOST" =~ ^[A-Za-z0-9_.-]+$ && "$REMOTE_USER" =~ ^[A-Za-z0-9_-]+$ &&
   "$REMOTE_SSH_PORT" =~ ^[0-9]+$ ]] || exit 2
REMOTE_TARGET="${REMOTE_USER}@${REMOTE_HOST}"
REMOTE_RUNS='/home/netlab-data/runs'
REMOTE_MODE=''
# Report transport failures before any tests are marked BLOCKED. Authentication
# failures cannot reliably distinguish a bad username from a bad password.
remote_transport_error() {
    local phase="$1" rc="$2" detail="$3" reason
    case "$detail" in
        *'Could not resolve hostname'*|*'Name or service not known'*|\
        *'nodename nor servname provided'*|*'Temporary failure in name resolution'*)
            reason='hostname cannot be resolved' ;;
        *'Connection refused'*) reason='SSH connection refused' ;;
        *'Connection timed out'*|*'Operation timed out'*|*'Connection timeout'*)
            reason='SSH connection timed out' ;;
        *'Network is unreachable'*|*'No route to host'*)
            reason='remote host is not reachable' ;;
        *'REMOTE HOST IDENTIFICATION HAS CHANGED'*|*'Host key verification failed'*|\
        *'POSSIBLE DNS SPOOFING'*)
            reason='SSH host-key verification failed' ;;
        *'Permission denied'*|*'Authentication failed'*|*'Too many authentication failures'*)
            reason='SSH authentication failed (check username, password or key)' ;;
        *)
            if [[ "$phase" == password && "$rc" == 5 ]]; then
                reason='SSH username or password rejected'
            else
                reason='SSH connection check failed'
            fi ;;
    esac
    printf 'ERROR: Remote transport: %s\n' "$reason" >&2
    printf '  Target: %s (port %s)\n  Config: %s\n' \
        "$REMOTE_TARGET" "$REMOTE_SSH_PORT" "$REMOTE_CFG" >&2
    [[ -z "$detail" ]] || printf '  SSH: %s\n' "$detail" >&2
    printf '  Phase: %s (exit %s)\n' "$phase" "$rc" >&2
}

remote_transport_prepare() {
    local ssh_base=(ssh -p "$REMOTE_SSH_PORT" -o ConnectTimeout=8
        -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30
        -o ServerAliveCountMax=3)
    local key_error='' pass_error='' rc=0
    REMOTE_MODE=''
    if ! command -v ssh >/dev/null 2>&1; then
        echo 'ERROR: Remote transport: ssh executable not found' >&2
        return 2
    fi
    # Preserve SSH diagnostics. A DNS/network failure must never fall back
    # to password authentication just because sshpass is installed.
    if key_error="$("${ssh_base[@]}" -n -o BatchMode=yes \
        "$REMOTE_TARGET" true 2>&1 >/dev/null)"; then
        REMOTE_MODE=key
        return 0
    else
        rc=$?
    fi
    case "$key_error" in
        *'Permission denied'*|*'Authentication failed'*|*'Too many authentication failures'*) ;;
        *) remote_transport_error key "$rc" "$key_error"; return 2 ;;
    esac
    if ! command -v sshpass >/dev/null 2>&1; then
        remote_transport_error key "$rc" "$key_error"
        echo '  Password fallback unavailable: install sshpass or configure SSH key access.' >&2
        return 2
    fi
    # Verify credentials before declaring password mode usable. Do not put
    # REMOTE_PASSWORD on the command line or include it in diagnostics.
    if pass_error="$(SSHPASS="$REMOTE_PASSWORD" sshpass -e "${ssh_base[@]}" \
        -n -o BatchMode=no -o NumberOfPasswordPrompts=1 \
        -o PreferredAuthentications=password,keyboard-interactive \
        -o PubkeyAuthentication=no "$REMOTE_TARGET" true 2>&1 >/dev/null)"; then
        REMOTE_MODE=password
        return 0
    else
        rc=$?
    fi
    remote_transport_error password "$rc" "$pass_error"
    return 2
}
remote_ssh() {
    local tty=''
    if [[ "${1:-}" == '--tty' ]]; then tty='-tt'; shift; fi
    local args=(-p "$REMOTE_SSH_PORT" -o ConnectTimeout=8
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3
        -o StrictHostKeyChecking=accept-new)
    [[ -z "$tty" ]] || args+=("$tty")
    if [[ "$REMOTE_MODE" == key ]]; then
        ssh -o BatchMode=yes "${args[@]}" "$REMOTE_TARGET" "$@"
    else
        SSHPASS="$REMOTE_PASSWORD" sshpass -e ssh "${args[@]}" \
            -o NumberOfPasswordPrompts=1 -o BatchMode=no \
            -o PreferredAuthentications=password,keyboard-interactive \
            -o PubkeyAuthentication=no "$REMOTE_TARGET" "$@"
    fi
}
remote_new_stage() {
    remote_ssh "mkdir -p '$REMOTE_RUNS' && mktemp -d '$REMOTE_RUNS/upload.XXXXXXXX'"
}
remote_stage() {
    local stage="$1"; shift
    [[ "$stage" =~ ^/home/netlab-data/runs/upload\.[A-Za-z0-9]+$ ]] || return 2
    COPYFILE_DISABLE=1 tar --format=ustar --no-xattrs -C "$REMOTE_REPO_ROOT" -cf - "$@" | \
        remote_ssh "tar -C '$stage' -xf -"
}
remote_run() {
    local stage="$1" action="$2"; shift 2
    local command='bash' part
    # A separate process group lets --abort signal only this runner and its
    # children. Do not change the process group for persistent lab sessions.
    case "$action" in
        uut-start|regressions) command='setsid --wait bash' ;;
    esac
    printf -v part '%q' "$stage/netlabs/tools/remote/runner.sh"
    command+=" $part"
    printf -v part '%q' "$action"
    command+=" $part"
    case "$action" in
        lab-start|uut-start|image-update|regressions)
            printf -v part '%q' "$stage"
            command+=" $part" ;;
    esac
    for part in "$@"; do
        printf -v part '%q' "$part"
        command+=" $part"
    done
    remote_ssh "$command"
}
remote_control() {
    local action="$1"; shift
    local command="bash /home/netlab/.remote-lab/runner.sh '$action'"
    remote_ssh "$command" "$@"
}
remote_install_runner() {
    local stage="$1"
    remote_ssh "mkdir -m 700 -p /home/netlab/.remote-lab && \
        cp '$stage/netlabs/tools/remote/runner.sh' /home/netlab/.remote-lab/runner.sh.new && \
        chmod 700 /home/netlab/.remote-lab/runner.sh.new && \
        mv -f /home/netlab/.remote-lab/runner.sh.new /home/netlab/.remote-lab/runner.sh"
}
remote_fetch_report() {
    local name="$1" output="$2"
    [[ "$name" =~ ^uut-[A-Za-z0-9_.-]+$ ]] || return 2
    mkdir -p "$output"
    remote_ssh "tar -C /home/netlab-data/results -cf - '$name'" |
        tar -C "$output" -xf -
}
