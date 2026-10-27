#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Test installed and published version selection and native netlab pinning."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml

SCRIPT = Path(__file__).resolve().parents[2] / 'tools/remote/images.py'
spec = importlib.util.spec_from_file_location('netlabs_images', SCRIPT)
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)


class FakeResponse:
    def __init__(self, data, link=''):
        self.data = json.dumps(data).encode('utf8')
        self.headers = {'Link': link}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]


class ImageSelectionTests(unittest.TestCase):
    def test_numeric_order_and_ignore_nonrelease_tags(self):
        self.assertEqual(images.newest(['0.3.0', '0.10.0', '0.9.0',
                                        'current', '0.11.0-amd64', 'latest']), '0.10.0')
        with self.assertRaisesRegex(RuntimeError, 'No versioned'):
            images.newest(['latest', 'current', '0.4.0-arm64'])

    def test_only_exact_ghcr_repository_is_selected(self):
        rows = ('openeigrp-frr:current\n'
                'ghcr.io/diivious/openeigrp-frr:0.3.0\n'
                'ghcr.io/diivious/openeigrp-frr:0.4.0\n'
                'ghcr.io/other/openeigrp-frr:99.0.0\n'
                'ghcr.io/diivious/openeigrp-frr:0.5.0-arm64\n')
        with mock.patch.object(images, 'docker', return_value=rows):
            self.assertEqual(images.installed(), '0.4.0')

    def test_registry_pagination_and_release_filtering(self):
        calls = []
        def fake_urlopen(request, timeout):
            calls.append(request.full_url)
            if request.full_url == images.TOKEN_URL:
                return FakeResponse({'token': 'test-token'})
            if request.full_url == images.TAGS_URL:
                self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
                return FakeResponse({'tags': ['0.3.0', 'latest']},
                    '</v2/diivious/openeigrp-frr/tags/list?last=latest&n=100>; rel="next"')
            if 'last=latest' in request.full_url:
                return FakeResponse({'tags': ['0.4.0', '0.4.0-amd64', '0.10.0']})
            raise AssertionError(request.full_url)
        with mock.patch.object(images, 'urlopen', side_effect=fake_urlopen):
            self.assertEqual(images.newest(images.registry_tags()), '0.10.0')
        self.assertEqual(len(calls), 3)

    def test_reject_registry_pagination_outside_ghcr(self):
        def fake_urlopen(request, timeout):
            if request.full_url == images.TOKEN_URL:
                return FakeResponse({'token': 'test-token'})
            return FakeResponse({'tags': ['0.3.0']},
                                '<https://example.org/fake/tags>; rel="next"')
        with mock.patch.object(images, 'urlopen', side_effect=fake_urlopen):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected GHCR pagination'):
                images.registry_tags()

    def test_update_pulls_versioned_tag_and_preserves_older(self):
        with mock.patch.object(images, 'registry_tags', return_value=['0.3.0', '0.4.0']), \
             mock.patch.object(images, 'installed', return_value='0.4.0'), \
             mock.patch.object(images, 'docker', return_value='[]') as docker, \
             mock.patch.object(images.subprocess, 'run') as runner:
            self.assertEqual(images.update(), images.REPOSITORY + ':0.4.0')
        runner.assert_called_once_with(['docker', 'pull', images.REPOSITORY + ':0.4.0'], check=True)
        docker.assert_called_once_with('image', 'inspect', images.REPOSITORY + ':0.4.0')

    def test_prepare_rewrites_only_disposable_topology_and_records_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology_path = root / 'topology.yml'
            evidence_path = root / 'image.json'
            source = {'name': 'eigrp-neighbor',
                      'defaults': {'devices.frr.clab.image': images.REPOSITORY + ':0.3.0',
                                   'devices.csr.clab.image': 'vrnetlab/vr-csr:17.03.06'},
                      'nodes': {'oe': {'device': 'frr'}, 'csr': {'device': 'csr'}}}
            topology_path.write_text(yaml.safe_dump(source))
            inspect = [{'Id': 'sha256:abc', 'Created': '2026-10-26',
                        'RepoDigests': [images.REPOSITORY + '@sha256:123']}]
            with mock.patch.object(images, 'installed', return_value='0.4.0'), \
                 mock.patch.object(images, 'docker', return_value=json.dumps(inspect)):
                images.prepare(topology_path, evidence_path)
            updated = yaml.safe_load(topology_path.read_text())
            self.assertEqual(updated['defaults']['devices.frr.clab.image'], images.REPOSITORY + ':0.4.0')
            self.assertEqual(updated['defaults']['devices.csr.clab.image'], 'vrnetlab/vr-csr:17.03.06')
            self.assertEqual(updated['nodes'], source['nodes'])
            self.assertEqual(json.loads(evidence_path.read_text())['id'], 'sha256:abc')

    def test_prepare_failure_does_not_modify_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'topology.yml'
            content = 'name: test\ndefaults: {}\nnodes: {oe: {device: frr}}\n'
            p.write_text(content)
            with mock.patch.object(images, 'installed', side_effect=RuntimeError('No versioned images')):
                with self.assertRaisesRegex(RuntimeError, 'No versioned'):
                    images.prepare(p, Path(tmp) / 'image.json')
            self.assertEqual(p.read_text(), content)


if __name__ == '__main__':
    unittest.main()
