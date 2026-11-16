# Fork-only native amd64 validation

[Workflow](../../.github/workflows/native-amd64.yaml) for **only**
`chris-cald/kube-vip`, branch `ci/plex-kube-vip-f86d864`.
The source under test/build is always
`f86d86485743b3ee84626b50511495810c79839e`, **not** the workflow commit.
The validation harness comes from the event's immutable commit in a separate
checkout. No existing source, tests, Dockerfiles or workflows are changed.
This branch must not be merged into, or pushed over, the upstream PR1860 branch.

## Start a run (parent-owned operation)

1. Parent verifies `fix/dhcp-lease-safety-proof` still identifies the expected
   source SHA, then uses GitHub MCP to create **only**
   `ci/plex-kube-vip-f86d864` at that SHA.
2. Parent uses GitHub MCP `push_files` to commit all files in this directory and
   `.github/workflows/native-amd64.yaml` together on that new branch. Include a
   DCO `Signed-off-by` trailer using the configured contributor identity.
3. This **content push**, not branch creation alone, triggers the workflow.
   Both the branch and changed-path filters must match. The workflow need not
   exist on the default branch. Do not change the default branch or open a PR
   merely to trigger validation. A later content change in these paths can
   trigger another run; an empty commit need not pass the path filter.
4. `workflow_dispatch` is optional and subject to GitHub's default-branch
   workflow registration rules. It is not the bootstrap mechanism. Dispatch
   is also restricted by the job guard to the dedicated branch.

The parent is responsible for the GitHub mutation, run inspection, and evidence
review. This implementation does not push or create GitHub resources. If the
provider lacks workflow-file write permission, or fork Actions are disabled,
stop and surface that capability/settings gate. Do not request credentials in
chat, bypass policy, change settings, or alter another branch.

## What runs

- A fresh GitHub-hosted `ubuntu-24.04` runner, with Linux/x86_64 runner, Docker
  daemon and native Go host architecture checks. No QEMU setup or self-hosted
  runner. Go is fixed to `1.27.3`, matching the upstream Dockerfile's toolchain.
- The exact source commit is checked before compilation. Test binaries for the
  **entire** `pkg/vip`, `pkg/services`, and `pkg/instance` packages are built with
  `CGO_ENABLED=0`, `-mod=readonly`; `go mod verify` and source cleanliness are
  checked. These are non-race builds, not a substitute for race testing.
- Each package gets a fresh scratch container, network `none`, read-only root
  filesystem, `--cap-drop ALL`, and `no-new-privileges`. Only VIP and services
  add `NET_ADMIN`, `NET_RAW`, `NET_BIND_SERVICE` and the explicit isolated-DHCP
  fixture opt-in. Instance adds **zero** capabilities. IPv6 sysctls apply only
  inside the disposable VIP/services network namespaces. A private noexec,
  nosuid, nodev `/tmp` tmpfs supports Go temporary files. Tests have no source,
  credentials, Docker socket, host directories or network attached.
- No test selection or skip flags are supplied. The upstream egress recovery
  test remains unchanged and runs as part of services. Docker's default
  seccomp/AppArmor constraints remain; no SYS_ADMIN, privileged mode, host
  networking or security-profile relaxation is used to remove skips.
- The runner collects all three suites even if one fails, preserves each Docker
  exit code and raw output, and converts output with Go `test2json`. Reporting
  fails on nonzero exits, package/test failures, empty/incomplete output, or
  missing/skipped required lifecycle tests. Top-level and subtest counts are
  separate (do not add them and call the result independent tests).
- All test functions in the immutable source's VIP `dhcpv4_backoff_test.go`,
  `dhcpv6_lifecycle_linux_test.go`, `dhcpv6_error_delivery_test.go`, and services
  `dhcpv4_lifecycle_linux_test.go` must actually **pass**. Three additional VIP
  functions are explicitly mandatory: `TestDHCPv4ClientOptions`,
  `TestDHCPv6StopReleasesManagerReferenceForParentInterface`, and
  `TestDHCPv6ClientManagerSharesOneClientPerParentInterface`.
  That is exactly **24 VIP + 21 services = 45** required functions at this SHA,
  including the blocked DHCPv6 error-delivery regression. This does not include
  `TestGetAddressRejectsIANAWithoutAddresses` or require a pass from the known
  namespace-skipping `TestDHCPv4ClientSendsIdentityOptionsToServer`; both still
  run in the full suite, with failures enforced and skips accounted for.
  The real DHCP lifecycle fixtures use native interfaces/packets and synthetic
  peers; these gates prevent an accidental fixture opt-out from appearing green.

## Image and evidence

The unchanged upstream [Dockerfile](../../Dockerfile) builds the binary using
its [Makefile](../../Makefile) and public Alpine `ca-certificates`, then copies
both into a scratch image. No private CA or protected input is needed. We do
**not** use the Makefile's image-publishing targets.

Both builds explicitly select `--builder default --load`. Before any build,
`verify-driver.sh` performs read-only checks: no Docker host/context overrides,
active context `default`, its local `/var/run/docker.sock` endpoint, and
`docker buildx inspect default` succeeding with exactly one `Driver:` field
whose entire value is `docker` (display whitespace is tolerated). Missing,
duplicate or other driver fields, extra values, and command failures are rejected.
Plain inspection avoids the unsupported `inspect --format` option on older
hosted Buildx versions; the context/socket checks are unchanged.
There is no bootstrap, builder creation/switch, daemon configuration change,
setup-buildx action, docker-container builder, Docker-in-Docker, additional
privileged container, or fallback driver. A mismatch fails closed. The image
build repeats the driver check and records it separately.

**Hosted baseline:** the disposable GitHub VM already has a privileged Docker
daemon and integrated BuildKit. This workflow uses that existing control plane;
it does not make Docker rootless or sandbox malicious build recipes. Regression
containers still run with the restrictions above, never in the build stage.
No privilege expansion beyond the approved existing hosted-daemon baseline is
introduced. Runner tool/driver incompatibility is a stop-and-review gate, not
permission to install another daemon or enable a more privileged builder.

The unchanged image is built once with SHA tag
`kube-vip:f86d86485743b3ee84626b50511495810c79839e`, loaded locally and exported
using `docker image save`. This deliberately uses the portable **Docker save**
format rather than assuming the existing driver's store supports OCI export or
embedded attestations. The image's `version` command runs with network none,
read-only root and no capabilities. Exact expected output:

```text
Version:  v1.3.1
Build:    f86d86485743b3ee84626b50511495810c79839e
```

`verify-archive.py` requires one tagged archive image, hashes its config against
the inspected local image ID, checks linux/amd64, and verifies every layer's
uncompressed diff ID against both config and local inspection. Raw and gzip
layers are supported; unexpected formats fail closed. Duplicate archive entries
and non-regular referenced members are rejected; nothing is extracted to disk.
This ties the saved content to the version-checked local image, **not** to a
registry manifest. The report explicitly has no registry manifest digest and
sets exact OCI promotion and embedded provenance verification to false.

`BUILDX_METADATA_PROVENANCE=max` requests the existing Buildx client's maximum
available build metadata in `build-metadata.json`. `--provenance=false` avoids
requesting embedded attestations unsupported by classic Docker stores. Retained
metadata, input checksums, source/workflow identity and build logs are **unsigned
local provenance evidence**, not a bound SLSA/GitHub/Sigstore attestation. Inspect
the actual metadata fields; availability varies by hosted Buildx/Engine version.
No embedded attestation retention or exact OCI promotion is promised. Any later
publication must establish and verify the resulting registry digest separately.

Only `contents: read` is granted; there is no registry login/push, packages-write,
OIDC or attestation-write permission. Both checkouts disable persisted
credentials; no credentials or build secrets are passed to builds or tests.

The always-run artifact upload preserves whatever evidence exists, including
on failed validation. An image can be present even if tests failed: **artifact
presence is not approval**. Review the complete job result and summary.

- `preflight.log`: source/workflow/run identity, kernel/Docker versions and
  source input checksums.
- `compile.log`: Go version, module verification and test-binary checksums.
- `test-image-build.log`, `{vip,services,instance}.{log,jsonl,exit}`: unfiltered
  suite logs, machine-readable events and original process exits.
- `test-summary.json`, `summary.md`: counts, each skip and its output/reason,
  required-pass names, and validation errors. Summary is emitted even on failure.
- `image-build.log`, `build-driver.log`, `buildx-version.log`,
  `build-metadata.json`: driver verification, build logs and available metadata.
- `version.log`, `image-inspect.json`, `archive-verification.json`: actual
  embedded version/build, local config ID and verified uncompressed layer IDs.
- `kube-vip.docker.tar` and its `.sha256`: tagged Docker save image and archive
  checksum. Archive checksum, local config ID, layer diff IDs and any digest in
  raw Buildx metadata identify different objects. None is certified here as a
  published registry digest; do not relabel them as one.

## Coverage and reproducibility limits

Passing under these capability constraints is **not all tests exercised**.
Namespace-creating tests can skip. The unchanged egress recovery test is
expected to hit EPERM/EACCES on native amd64 without SYS_ADMIN; any different
result must be inspected, not patched around or waived. Parent-reported prior
results were VIP/instance exit 0 with namespace skips; services 116 PASS/1 FAIL
under amd64 emulation (egress re-exec `invalid argument`), with an ARM-native
EPERM skip diagnostic. Those are historical observations, **not results of this
workflow**. Native counts, skip reasons and image digests remain pending an
actual run. No live cluster, rollout, rollback or deployment is exercised.

Actions reuse this revision's immutable action pins, source uses the exact
commit, Go uses a fixed version, and modules use `go.sum`. However, upstream
`golang:1.27.3-alpine3.23`, the experimental Dockerfile frontend, APK repository
packages/public CA bundle, BuildKit defaults and hosted-runner tools are not
all content-pinned. Network fetches are needed during compilation/build.
Logs/provenance record resolved materials where available; **this is not a
hermetic or certified reproducible build**. If upstream inputs become
unavailable, fail and request a separately reviewed recipe change—do not
silently substitute another toolchain, source SHA, CA or base image.

Publication, deployment, privilege expansion and changing the source SHA are
separate decision gates, outside this workflow's approval. Actual CA package
and bundle build provenance remains a future publication gate, not a current
workflow guarantee.

## Offline validation and TDD

From repository root, without Docker/network access:

```sh
ruby testing/native-amd64/contract_test.rb
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s testing/native-amd64 -v
bash -n testing/native-amd64/run-tests.sh
bash -n testing/native-amd64/verify-driver.sh
shellcheck testing/native-amd64/*.sh
git diff --check
```

The Ruby test uses YAML + Minitest, with no repository dependency installation.
Python uses only the standard library. Temporary fixtures stay inside this
worktree and are removed. Fake Docker/Go CLIs test orchestration only; they do
not claim to exercise Linux/DHCP behavior. The actual GitHub runner executes the
source's unmodified Go tests.

Red/green sequence: absent workflow failed its YAML contract, then passed;
absent runner failed the container-boundary/continued-collection test, then
passed; absent reporter failed count/skip and six fail-closed cases, then
passed; absent OCI verifier failed archive checks, then passed. A further red
case showed that a mismatching local config was incorrectly accepted; binding
it to the OCI config made that check pass. Export/reporting contract additions
also failed before the compatibility/failure-summary implementation.

The separate default-driver amendment preserves that original commit. Its new
contract first rejected setup-buildx/OCI export, then passed after removal.
The runtime CLI tests first failed for unpinned test-image builds and a missing
read-only driver guard, then passed with exact `docker` acceptance and rejection
of alternate drivers/host overrides. Replacement Docker archive tests failed
before implementation, then passed for raw/gzip layers and seven rejection
cases (tag, config, layer, local ID, layer count, platform and duplicates).
Native GitHub validation remains pending; offline tests do not exercise Docker.

Run `43674026655`, attempt 1 (job `132690101334`), failed preflight before
compilation/tests/build. Parent-retrieved logs identified `unknown flag:
--format` from `docker buildx inspect`, exit 125. That failed attempt is retained.
The compatibility regression fixture now reproduces that unsupported flag
instead of accepting it. It failed before the plain-inspection fix and passes
with nine cases covering valid output/spacing and fail-closed rejection paths.
The original fake CLI missed this runner-version incompatibility; native
revalidation remains a separate parent-owned action, not an offline-test claim.
