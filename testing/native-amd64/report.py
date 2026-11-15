#!/usr/bin/env python3
"""Account for full-package test2json results; never turn a skip into a pass."""
import collections
import json
from pathlib import Path
import re
import sys

REQUIRED_FILES = {
    'vip': ('dhcpv4_backoff_test.go', 'dhcpv6_lifecycle_linux_test.go',
            'dhcpv6_error_delivery_test.go'),
    'services': ('dhcpv4_lifecycle_linux_test.go',),
    'instance': (),
}
# Only these additional functions are mandatory, not their entire source files.
REQUIRED_NAMES = {
    'vip': ('TestDHCPv4ClientOptions',
            'TestDHCPv6StopReleasesManagerReferenceForParentInterface',
            'TestDHCPv6ClientManagerSharesOneClientPerParentInterface'),
}


def main(source, evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    report = {'packages': {}, 'errors': []}
    for package, files in REQUIRED_FILES.items():
        try:
            required = list(REQUIRED_NAMES.get(package, ()))
            for file in files:
                names = re.findall(r'^func (Test\w+)\(t \*testing.T\)',
                                   (source / 'pkg' / package / file).read_text(), re.M)
                if not names:
                    raise ValueError(f'no required tests discovered in {file}')
                required.extend(names)
            events = [json.loads(line) for line in (evidence / f'{package}.jsonl').read_text().splitlines()]
            code = int((evidence / f'{package}.exit').read_text())
            terminals, started = {}, set()
            output = collections.defaultdict(str)
            package_result = None
            for event in events:
                action, name = event['Action'], event.get('Test')
                if not name:
                    if action in ('pass', 'fail', 'skip'):
                        package_result = action
                    continue
                if action == 'run':
                    started.add(name)
                elif action in ('pass', 'fail', 'skip'):
                    if name in terminals:
                        raise ValueError(f'duplicate terminal result: {name}')
                    terminals[name] = action
                elif action == 'output':
                    output[name] += event.get('Output', '')
            top = {action: sum('/' not in name and result == action for name, result in terminals.items())
                   for action in ('pass', 'fail', 'skip')}
            sub = {action: sum('/' in name and result == action for name, result in terminals.items())
                   for action in ('pass', 'fail', 'skip')}
            report['packages'][package] = {
                'exit': code, 'top_level': top, 'subtests': sub,
                'skips': {name: output[name] for name, result in terminals.items() if result == 'skip'},
                'required_passes': sorted(name for name in required if terminals.get(name) == 'pass'),
            }
            if code != 0 or package_result != 'pass' or top['fail'] or sub['fail']:
                report['errors'].append(f'{package}: exit={code}, package result={package_result}, failures={top["fail"] + sub["fail"]}')
            if not started or started != set(terminals):
                report['errors'].append(f'{package}: empty or incomplete test results')
            for name in required:
                if terminals.get(name) != 'pass':
                    report['errors'].append(f'{package}/{name}: required actual pass, got {terminals.get(name, "missing")}')
        except (OSError, ValueError, KeyError, TypeError) as error:
            report['errors'].append(f'{package}: {error}')

    (evidence / 'test-summary.json').write_text(json.dumps(report, indent=2) + '\n')
    lines = ['# Native amd64 package results', '',
             'Skips are coverage gaps, not full coverage. This is not deployment certification.', '',
             '| Package | Exit | Top-level pass/fail/skip | Subtest pass/fail/skip | Required passes |',
             '| --- | --- | --- | --- | --- |']
    for package, result in report['packages'].items():
        counts = ['/'.join(str(result[key][action]) for action in ('pass', 'fail', 'skip'))
                  for key in ('top_level', 'subtests')]
        lines.append(f'| {package} | {result["exit"]} | {counts[0]} | {counts[1]} | {len(result["required_passes"])} |')
    lines.extend(['', '## Skips (coverage gaps)'])
    for package, result in report['packages'].items():
        for name, reason in result['skips'].items():
            lines.extend(['', f'SKIP `{package}/{name}`:', '', '```text', reason.rstrip(), '```'])
    if report['errors']:
        lines.extend(['', '## Validation failures', ''] + ['- ' + error for error in report['errors']])
    summary = '\n'.join(lines) + '\n'
    (evidence / 'summary.md').write_text(summary)
    print(summary)
    return bool(report['errors'])


if __name__ == '__main__':
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
