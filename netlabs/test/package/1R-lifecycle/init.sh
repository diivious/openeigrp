#!/bin/sh
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
# Docker's private mount propagation can hide systemd LoadCredential mounts
# from the service namespace. Make the disposable guest's mount tree shared
# before systemd becomes PID 1, including on docker restart.
set -eu
mount --make-rshared /
exec "$@"
