#!/usr/bin/env bash
# Read-only checks: never create, switch, bootstrap, or repair a builder/daemon.
set -euo pipefail
test -z "${DOCKER_HOST:-}"
test -z "${DOCKER_CONTEXT:-}"
test "$(docker context show)" = default
test "$(docker context inspect default --format '{{.Endpoints.docker.Host}}')" = unix:///var/run/docker.sock
driver=$(docker buildx inspect default --format '{{.Driver}}')
printf 'default builder driver: %s\n' "$driver"
test "$driver" = docker
