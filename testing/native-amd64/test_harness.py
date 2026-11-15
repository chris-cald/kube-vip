"""Offline public CLI tests; no Docker daemon, network or Go compiler needed."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
MANDATORY_VIP_NAMES = (
    'TestDHCPv4ClientOptions',
    'TestDHCPv6StopReleasesManagerReferenceForParentInterface',
    'TestDHCPv6ClientManagerSharesOneClientPerParentInterface',
)


class HarnessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HERE)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def executable(self, name, body):
        path = self.root / name
        path.write_text('#!/usr/bin/env python3\n' + body)
        path.chmod(0o755)

    def test_container_boundary_and_failure_propagation(self):
        self.executable('docker', '''import json, os, sys
with open(os.environ['CALLS'], 'a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'run':
    print('fixture test output')
    sys.exit(1 if '/services.test' in sys.argv else 0)
''')
        self.executable('go', '''import sys
print(sys.stdin.read(), end='')
''')
        bins, evidence = self.root / 'bins', self.root / 'evidence'
        bins.mkdir()
        calls = self.root / 'calls.jsonl'
        env = dict(os.environ, PATH=f'{self.root}:{os.environ["PATH"]}', CALLS=str(calls))
        result = subprocess.run(['bash', str(HERE / 'run-tests.sh'), str(bins), str(evidence)],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertTrue(calls.exists(), result.stderr)
        requests = [json.loads(line) for line in calls.read_text().splitlines()]
        builds = [args for args in requests if args[:2] == ['buildx', 'build']]
        self.assertEqual(len(builds), 1)
        self.assertEqual(builds[0][builds[0].index('--builder') + 1], 'default')
        self.assertIn('--load', builds[0])
        runs = [args for args in requests if args[0] == 'run']
        self.assertEqual(len(runs), 3, result.stderr)  # failure must not hide instance results
        for args, package in zip(runs, ('vip', 'services', 'instance')):
            self.assertEqual(args[args.index('--network') + 1], 'none')
            self.assertIn('--read-only', args)
            self.assertEqual(args[args.index('--cap-drop') + 1], 'ALL')
            self.assertEqual(args[args.index('--security-opt') + 1], 'no-new-privileges=true')
            caps = [args[i + 1] for i, arg in enumerate(args) if arg == '--cap-add']
            self.assertEqual(caps, [] if package == 'instance' else ['NET_ADMIN', 'NET_RAW', 'NET_BIND_SERVICE'])
            self.assertNotIn('--privileged', args)
            self.assertNotIn('--volume', args)
            self.assertNotIn('-v', args)
            self.assertIn('/' + package + '.test', args)
            self.assertIn('-test.v=test2json', args)
            self.assertFalse(any(arg.startswith(('-test.run', '-test.skip')) for arg in args))
            self.assertIn('fixture test output', (evidence / f'{package}.log').read_text())
            self.assertEqual((evidence / f'{package}.exit').read_text().strip(), '1' if package == 'services' else '0')
            self.assertTrue((evidence / f'{package}.jsonl').exists())

    def test_default_driver_probe_is_read_only_and_fails_closed(self):
        self.executable('docker', '''import json, os, sys
args = sys.argv[1:]
with open(os.environ['CALLS'], 'a') as out:
    out.write(json.dumps(args) + '\\n')
if args == ['context', 'show']:
    print('default')
elif args == ['context', 'inspect', 'default', '--format', '{{.Endpoints.docker.Host}}']:
    print('unix:///var/run/docker.sock')
elif args == ['buildx', 'inspect', 'default', '--format', '{{.Driver}}']:
    print(os.environ['DRIVER'])
else:
    sys.exit(99)
''')
        for driver in ('docker', 'docker-container', 'remote'):
            with self.subTest(driver=driver):
                calls = self.root / (driver + '.jsonl')
                env = {k: v for k, v in os.environ.items() if k not in ('DOCKER_HOST', 'DOCKER_CONTEXT')}
                env.update(PATH=f'{self.root}:{os.environ["PATH"]}', CALLS=str(calls), DRIVER=driver)
                result = subprocess.run(['bash', str(HERE / 'verify-driver.sh')], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if driver == 'docker' else 1, result.stderr)
                self.assertTrue(calls.exists(), result.stderr)
                requests = [json.loads(line) for line in calls.read_text().splitlines()]
                self.assertEqual(requests, [
                    ['context', 'show'],
                    ['context', 'inspect', 'default', '--format', '{{.Endpoints.docker.Host}}'],
                    ['buildx', 'inspect', 'default', '--format', '{{.Driver}}'],
                ])
        for override in ('DOCKER_HOST', 'DOCKER_CONTEXT'):
            env[override] = 'unexpected'
            self.assertNotEqual(subprocess.run(['bash', str(HERE / 'verify-driver.sh')],
                                               env=env, capture_output=True).returncode, 0)
            del env[override]

    def report_fixture(self):
        source, evidence = self.root / 'source', self.root / 'evidence'
        evidence.mkdir()
        fixtures = {
            'vip/dhcpv4_backoff_test.go': 'TestDHCPv4Lease',
            'vip/dhcpv6_lifecycle_linux_test.go': 'TestDHCPv6Lease',
            'vip/dhcpv6_error_delivery_test.go': 'TestDHCPv6CancelledError',
            'services/dhcpv4_lifecycle_linux_test.go': 'TestDHCPServiceDelete',
        }
        names = {'vip': list(MANDATORY_VIP_NAMES), 'services': [], 'instance': ['TestInstance']}
        for file, name in fixtures.items():
            path = source / 'pkg' / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'func {name}(t *testing.T) {{}}\n')
            names[file.split('/')[0]].append(name)
        for package, tests in names.items():
            events = []
            for name in tests:
                events.extend([{'Action': 'run', 'Test': name}, {'Action': 'pass', 'Test': name}])
            if package == 'services':
                events.extend([
                    {'Action': 'run', 'Test': 'TestRecoverEgressAfterEndpointLoss'},
                    {'Action': 'output', 'Test': 'TestRecoverEgressAfterEndpointLoss', 'Output': 'network namespaces unavailable: operation not permitted\n'},
                    {'Action': 'skip', 'Test': 'TestRecoverEgressAfterEndpointLoss'},
                    {'Action': 'run', 'Test': 'TestDHCPServiceDelete/subcase'},
                    {'Action': 'pass', 'Test': 'TestDHCPServiceDelete/subcase'},
                ])
            events.append({'Action': 'pass'})
            (evidence / f'{package}.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
            (evidence / f'{package}.exit').write_text('0\n')
        return source, evidence

    def run_report(self, source, evidence):
        return subprocess.run(['python3', str(HERE / 'report.py'), str(source), str(evidence)],
                              capture_output=True, text=True)

    def test_report_counts_skips_and_separates_subtests(self):
        source, evidence = self.report_fixture()
        result = self.run_report(source, evidence)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((evidence / 'test-summary.json').read_text())
        services = report['packages']['services']
        self.assertEqual(services['top_level'], {'pass': 1, 'fail': 0, 'skip': 1})
        self.assertEqual(services['subtests'], {'pass': 1, 'fail': 0, 'skip': 0})
        self.assertIn('operation not permitted', services['skips']['TestRecoverEgressAfterEndpointLoss'])
        self.assertEqual(services['required_passes'], ['TestDHCPServiceDelete'])
        self.assertIn('not full coverage', (evidence / 'summary.md').read_text())

    def test_each_explicit_vip_gate_rejects_missing_and_skipped(self):
        for name in MANDATORY_VIP_NAMES:
            for fault in ('missing', 'skip'):
                with self.subTest(name=name, fault=fault):
                    original = self.root
                    self.root = original / (name + '-' + fault)
                    self.root.mkdir()
                    source, evidence = self.report_fixture()
                    self.root = original
                    path = evidence / 'vip.jsonl'
                    events = [json.loads(line) for line in path.read_text().splitlines()]
                    if fault == 'missing':
                        events = [event for event in events if event.get('Test') != name]
                    else:
                        for event in events:
                            if event.get('Test') == name and event['Action'] == 'pass':
                                event['Action'] = 'skip'
                    path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                    result = self.run_report(source, evidence)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    report = json.loads((evidence / 'test-summary.json').read_text())
                    self.assertIn(f'vip/{name}: required actual pass, got {fault}', report['errors'])

    def test_real_source_catalog_requires_exactly_45_without_namespace_gate(self):
        _, evidence = self.report_fixture()
        source = HERE.parents[1]
        allowed_skips = {'TestDHCPv4ClientSendsIdentityOptionsToServer',
                         'TestGetAddressRejectsIANAWithoutAddresses'}
        for package in ('vip', 'services', 'instance'):
            # Synthetic result events test reporting, not product execution.
            names = set()
            for path in (source / 'pkg' / package).glob('*_test.go'):
                names.update(re.findall(r'^func (Test\w+)\(t \*testing.T\)', path.read_text(), re.M))
            events = []
            for name in sorted(names):
                events.extend([{'Action': 'run', 'Test': name},
                               {'Action': 'skip' if name in allowed_skips else 'pass', 'Test': name}])
            events.append({'Action': 'pass'})
            (evidence / f'{package}.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
        result = self.run_report(source, evidence)
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = json.loads((evidence / 'test-summary.json').read_text())['packages']
        self.assertEqual({name: len(data['required_passes']) for name, data in packages.items()},
                         {'vip': 24, 'services': 21, 'instance': 0})
        self.assertTrue(set(MANDATORY_VIP_NAMES).issubset(packages['vip']['required_passes']))
        self.assertTrue(allowed_skips.issubset(packages['vip']['skips']))
        self.assertTrue(allowed_skips.isdisjoint(packages['vip']['required_passes']))

    def test_report_fails_closed_but_keeps_evidence(self):
        # No waiver for real failures, lifecycle skips, absent tests or truncated logs.
        for fault in ('skip', 'missing', 'exit', 'fail', 'truncated', 'empty'):
            with self.subTest(fault=fault):
                case = self.root / fault
                case.mkdir()
                original = self.root
                self.root = case
                source, evidence = self.report_fixture()
                self.root = original
                path = evidence / 'vip.jsonl'
                events = [json.loads(line) for line in path.read_text().splitlines()]
                if fault in ('skip', 'fail'):
                    events[1]['Action'] = fault
                elif fault == 'missing':
                    events = events[2:]
                elif fault == 'exit':
                    (evidence / 'services.exit').write_text('1\n')
                elif fault == 'truncated':
                    events = events[:-2]
                else:
                    events = []
                path.write_text(''.join(json.dumps(event) + '\n' for event in events))
                result = self.run_report(source, evidence)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertTrue((evidence / 'summary.md').exists(), result.stderr)
                self.assertTrue(json.loads((evidence / 'test-summary.json').read_text())['errors'])

    def archive_fixture(self, fault=None, compressed=False):
        tag = 'kube-vip:f86d86485743b3ee84626b50511495810c79839e'
        layer = b'synthetic uncompressed layer bytes'
        diff_id = 'sha256:' + hashlib.sha256(layer).hexdigest()
        config = json.dumps({'os': 'linux', 'architecture': 'amd64',
                             'rootfs': {'type': 'layers', 'diff_ids': [diff_id]}}).encode()
        config_id = 'sha256:' + hashlib.sha256(config).hexdigest()
        config_path = config_id.split(':')[1] + '.json'
        manifest = [{'Config': config_path, 'RepoTags': [tag], 'Layers': ['layer/layer.tar']}]
        inspection = [{'Id': config_id, 'Os': 'linux', 'Architecture': 'amd64',
                       'RootFS': {'Layers': [diff_id]}}]
        if fault == 'tag':
            manifest[0]['RepoTags'] = ['kube-vip:wrong']
        elif fault == 'config':
            config += b' '
        elif fault == 'layer':
            layer += b'tampered'
        elif fault == 'local-id':
            inspection[0]['Id'] = 'sha256:' + '0' * 64
        elif fault == 'layer-count':
            manifest[0]['Layers'] = []
        elif fault == 'platform':
            inspection[0]['Architecture'] = 'arm64'
        blobs = {config_path: config, 'manifest.json': json.dumps(manifest).encode(),
                 'layer/layer.tar': gzip.compress(layer) if compressed else layer}
        archive, inspect = self.root / 'image.tar', self.root / 'inspect.json'
        with tarfile.open(archive, 'w') as out:
            for name, data in blobs.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                out.addfile(member, io.BytesIO(data))
            if fault == 'duplicate':
                member = tarfile.TarInfo('manifest.json')
                member.size = 2
                out.addfile(member, io.BytesIO(b'[]'))
        inspect.write_text(json.dumps(inspection))
        return archive, inspect, tag, config_id, diff_id

    def test_saved_archive_identity_layers_and_digest_distinctions(self):
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                archive, inspect, tag, config_id, diff_id = self.archive_fixture(compressed=compressed)
                result = subprocess.run(['python3', str(HERE / 'verify-archive.py'), str(archive), str(inspect), tag],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(result.stdout)
                self.assertEqual(record['format'], 'docker-save')
                self.assertEqual(record['local_image_config_digest'], config_id)
                self.assertEqual(record['layer_diff_ids'], [diff_id])
                self.assertEqual(record['platform'], 'linux/amd64')
                self.assertIsNone(record['registry_manifest_digest'])
                self.assertFalse(record['exact_oci_promotion_verified'])
                self.assertFalse(record['embedded_provenance_verified'])
                self.assertNotIn('oci_digest', record)

    def test_saved_archive_rejects_tampering_and_identity_mismatch(self):
        for fault in ('tag', 'config', 'layer', 'local-id', 'layer-count', 'platform', 'duplicate'):
            with self.subTest(fault=fault):
                archive, inspect, tag, _, _ = self.archive_fixture(fault)
                result = subprocess.run(['python3', str(HERE / 'verify-archive.py'), str(archive), str(inspect), tag],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)


if __name__ == '__main__':
    unittest.main()
