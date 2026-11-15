#!/usr/bin/env bash
# Run only on the disposable native Linux runner, never against a lab host.
set -euo pipefail
bins=$(cd "$1" && pwd)
mkdir -p "$2"
evidence=$(cd "$2" && pwd)

# No base image, network, credentials, source checkout, or host sockets in tests.
docker buildx build --builder default --load --provenance=false --network none --tag kube-vip-native-tests --file - "$bins" <<'DOCKERFILE' 2>&1 | tee "$evidence/test-image-build.log"
FROM scratch
COPY *.test /
DOCKERFILE

failed=0
for package in vip services instance; do
  caps=()
  if [[ "$package" != instance ]]; then
    caps=(--cap-add NET_ADMIN --cap-add NET_RAW --cap-add NET_BIND_SERVICE
          --env KUBE_VIP_ISOLATED_DHCP_TEST=1
          --sysctl net.ipv6.conf.all.disable_ipv6=0
          --sysctl net.ipv6.conf.default.disable_ipv6=0)
  fi
  # A private writable tmpfs supports Go t.TempDir; rootfs remains read-only.
  # Keep Docker's default seccomp/AppArmor. Namespace-related skips stay visible.
  set +e
  docker run --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges=true \
    --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m \
    "${caps[@]}" kube-vip-native-tests "/$package.test" -test.v=test2json \
    2>&1 | tee "$evidence/$package.log"
  statuses=("${PIPESTATUS[@]}")
  set -e
  printf '%s\n' "${statuses[0]}" > "$evidence/$package.exit"
  if [[ "${statuses[0]}" != 0 || "${statuses[1]}" != 0 ]]; then
    failed=1
  fi
  if ! go tool test2json -t -p "github.com/kube-vip/kube-vip/pkg/$package" \
    < "$evidence/$package.log" > "$evidence/$package.jsonl"; then
    failed=1
  fi
done
exit "$failed"
