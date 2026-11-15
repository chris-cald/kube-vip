#!/usr/bin/env python3
"""Bind a Docker save archive to the inspected local image; no registry claims."""
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tarfile


def verify(archive, inspection, tag):
    local_image, = json.loads(Path(inspection).read_text())
    if (local_image['Os'], local_image['Architecture']) != ('linux', 'amd64'):
        raise ValueError('local image is not linux/amd64')
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        if len({member.name for member in members}) != len(members):
            raise ValueError('duplicate archive member')

        def open_member(name):
            member = tar.getmember(name)
            if not member.isfile():
                raise ValueError(f'not a regular file: {name}')
            return tar.extractfile(member)

        with open_member('manifest.json') as stream:
            manifest, = json.load(stream)
        if tag not in manifest['RepoTags']:
            raise ValueError('expected SHA tag missing from archive')
        with open_member(manifest['Config']) as stream:
            config_data = stream.read()
        config_digest = 'sha256:' + hashlib.sha256(config_data).hexdigest()
        if config_digest != local_image['Id']:
            raise ValueError('saved config differs from inspected local image')
        config = json.loads(config_data)
        if (config['os'], config['architecture']) != ('linux', 'amd64'):
            raise ValueError('saved config is not linux/amd64')
        if config['rootfs']['type'] != 'layers':
            raise ValueError('unsupported rootfs type')
        diff_ids = config['rootfs']['diff_ids']
        if diff_ids != local_image['RootFS']['Layers'] or len(diff_ids) != len(manifest['Layers']):
            raise ValueError('layer inventory differs from config/local image')
        for name, expected in zip(manifest['Layers'], diff_ids):
            with open_member(name) as raw:
                compressed = raw.read(2) == b'\x1f\x8b'
                raw.seek(0)
                # Classic saves use raw tar layers; containerd saves may use gzip.
                stream = gzip.GzipFile(fileobj=raw) if compressed else raw
                hasher = hashlib.sha256()
                try:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        hasher.update(chunk)
                finally:
                    if compressed:
                        stream.close()
                if 'sha256:' + hasher.hexdigest() != expected:
                    raise ValueError('layer diff ID mismatch: ' + name)
        return {'format': 'docker-save', 'tag': tag, 'platform': 'linux/amd64',
                'local_image_config_digest': config_digest, 'layer_diff_ids': diff_ids,
                'registry_manifest_digest': None, 'exact_oci_promotion_verified': False,
                'embedded_provenance_verified': False, 'reproducible_build_certified': False}


if __name__ == '__main__':
    print(json.dumps(verify(sys.argv[1], sys.argv[2], sys.argv[3]), indent=2))
