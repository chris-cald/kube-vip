#!/usr/bin/env bash
# Read-only checks: never create, switch, bootstrap, or repair a builder/daemon.
set -euo pipefail
test -z "${DOCKER_HOST:-}"
test -z "${DOCKER_CONTEXT:-}"
test "$(docker context show)" = default
test "$(docker context inspect default --format '{{.Endpoints.docker.Host}}')" = unix:///var/run/docker.sock
inspection=$(docker buildx inspect default)
# Older hosted Buildx versions do not support inspect --format.
# Accept exactly one Driver field, with only the docker value.
printf '%s\n' "$inspection" | LC_ALL=C awk '
  /^[[:space:]]*Driver:/ { fields++ }
  /^[[:space:]]*Driver:[[:space:]]+docker[[:space:]]*$/ { valid++ }
  END { exit !(fields == 1 && valid == 1) }
'
printf 'default builder driver: docker\n'
