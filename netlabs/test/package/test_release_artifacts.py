#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Offline executable regressions for the published-release package gate."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[2] / 'tools/package-artifacts.py'
SPEC = importlib.util.spec_from_file_location('netlab_package_artifacts', HELPER)
ART = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ART)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def release(version, *, complete=True):
    tag = 'rel-' + version
    base = f'https://github.com/diivious/openeigrp/releases/download/{tag}/'
    name = f'openeigrpd_{version}_amd64.deb'
    assets = [{'name': name, 'state': 'uploaded',
               'browser_download_url': base + name},
              {'name': 'SHA256SUMS', 'state': 'uploaded',
               'browser_download_url': base + 'SHA256SUMS'}]
    return {'tag_name': tag, 'draft': False, 'prerelease': False,
            'assets': assets if complete else assets[:1]}


class ReleaseArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        self.contents = b'fixture release package\n'
        self.releases = [release('0.5'), release('0.6')]

    def mock_fetch(self, url):
        if url.startswith(ART.API):
            return json.dumps(self.releases).encode()
        if url.endswith('/SHA256SUMS'):
            version = url.split('/releases/download/rel-')[1].split('/')[0]
            return f'{digest(self.contents)}  openeigrpd_{version}_amd64.deb\n'.encode()
        if url.endswith('.deb'):
            return self.contents
        raise AssertionError(f'unknown download URL: {url}')

    def test_selects_highest_published_complete_release(self):
        self.releases = [release('0.5'), release('0.6'), release('0.5')]
        with patch.object(ART, 'fetch', self.mock_fetch):
            output = ART.download_release(self.dist)
        self.assertEqual(output['version'], '0.6')
        self.assertEqual(output['release'], 'rel-0.6')
        self.assertEqual((self.dist / output['package']).read_bytes(), self.contents)

    def test_requested_release_is_exact_not_latest_or_prefix(self):
        with patch.object(ART, 'fetch', self.mock_fetch):
            selected = ART.download_release(self.dist, '0.5')
            self.assertEqual(selected['release'], 'rel-0.5')
            self.assertIsNone(ART.download_release(self.dist, '0.50'))
        self.assertFalse((self.dist / 'openeigrpd_0.6_amd64.deb').exists())

    def test_no_published_debian_release_returns_no_baseline(self):
        self.releases = [
            {'tag_name': '0.5.0', 'draft': False, 'prerelease': False,
             'assets': []},
            {'tag_name': 'rel-0.6', 'draft': True, 'prerelease': False,
             'assets': []},
        ]
        with patch.object(ART, 'fetch', self.mock_fetch):
            self.assertIsNone(ART.download_release(self.dist))
        self.assertEqual(list(self.dist.iterdir()), [])

    def test_empty_releases_returns_no_baseline(self):
        self.releases = []
        with patch.object(ART, 'fetch', self.mock_fetch):
            self.assertIsNone(ART.download_release(self.dist))

    def test_api_failure_does_not_skip_upgrade(self):
        with patch.object(ART, 'fetch', side_effect=ValueError('GitHub API unavailable')):
            with self.assertRaisesRegex(ValueError, 'GitHub API unavailable'):
                ART.download_release(self.dist)

    def test_published_tag_without_debian_assets_is_not_package_baseline(self):
        tag = release('0.6')
        tag['assets'] = []
        self.releases = [release('0.5'), tag]
        with patch.object(ART, 'fetch', self.mock_fetch):
            selected = ART.download_release(self.dist)
        self.assertEqual(selected['version'], '0.5')

    def test_only_nonpackage_releases_have_no_baseline(self):
        tag = release('0.6')
        tag['assets'] = []
        self.releases = [tag]
        with patch.object(ART, 'fetch', self.mock_fetch):
            self.assertIsNone(ART.download_release(self.dist))

    def test_rejects_incomplete_latest_release(self):
        self.releases = [release('0.5'), release('0.6', complete=False)]
        with patch.object(ART, 'fetch', self.mock_fetch):
            with self.assertRaisesRegex(ValueError, 'latest release rel-0.6 is missing'):
                ART.download_release(self.dist)
        self.assertEqual(list(self.dist.iterdir()), [])

    def test_rejects_unverified_release_content(self):
        def tampered(url):
            if url.endswith('.deb'):
                return b'tampered'
            return self.mock_fetch(url)
        with patch.object(ART, 'fetch', tampered):
            with self.assertRaisesRegex(ValueError, 'SHA256SUMS mismatch'):
                ART.download_release(self.dist)
        self.assertEqual(list(self.dist.iterdir()), [])

    def test_rejects_conflicting_existing_archive(self):
        path = self.dist / 'openeigrpd_0.6_amd64.deb'
        path.write_bytes(b'not the release')
        with patch.object(ART, 'fetch', self.mock_fetch):
            with self.assertRaisesRegex(ValueError, 'conflicting local artifact'):
                ART.download_release(self.dist)
        self.assertEqual(path.read_bytes(), b'not the release')

    def test_manifest_without_release_checks_development_package(self):
        name = 'openeigrpd_0.5.10-test.dev_amd64.deb'
        (self.dist / name).write_bytes(self.contents)
        manifest = {'schema': 'openeigrp-netlabs-amd64/v1',
                    'image': 'openeigrp:test', 'image_id': 'sha256:' + 'a'*64,
                    'package_image': 'openeigrp-package:test',
                    'package_image_id': 'sha256:' + 'b'*64,
                    'old': None,
                    'new': {'version': '0.5.10-test.dev', 'package': name,
                            'sha256': digest(self.contents)}}
        path = self.dist / 'netlabs-amd64.json'
        path.write_text(json.dumps(manifest))
        self.assertEqual(ART.manifest_read(path, self.dist, 'test'), manifest)
        (self.dist / name).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'missing or modified new'):
            ART.manifest_read(path, self.dist)
        (self.dist / name).write_bytes(self.contents)
        manifest['old'] = {'unexpected': 'release'}
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'invalid old artifact identity'):
            ART.manifest_read(path, self.dist)

    def test_manifest_integrity_and_image_selection(self):
        old = release('0.5')['assets'][0]
        name_a = old['name']
        name_b = 'openeigrpd_0.5.10-diivious.dev_amd64.deb'
        (self.dist / name_a).write_bytes(self.contents)
        (self.dist / name_b).write_bytes(b'new')
        manifest = {'schema': 'openeigrp-netlabs-amd64/v1',
                    'image': 'openeigrp:diivious-test', 'image_id': 'sha256:' + 'a'*64,
                    'package_image': 'openeigrp-package:diivious-test',
                    'package_image_id': 'sha256:' + 'b'*64,
                    'old': {'version': '0.5', 'package': name_a,
                            'sha256': digest(self.contents), 'release': 'rel-0.5',
                            'url': old['browser_download_url']},
                    'new': {'version': '0.5.10-diivious.dev', 'package': name_b,
                            'sha256': digest(b'new')}}
        file = self.dist / 'netlabs-amd64.json'
        file.write_text(json.dumps(manifest))
        self.assertEqual(ART.manifest_read(file, self.dist, 'diivious-test'), manifest)
        with self.assertRaisesRegex(ValueError, 'not build tag wrong'):
            ART.manifest_read(file, self.dist, 'wrong')
        (self.dist / name_b).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'missing or modified new'):
            ART.manifest_read(file, self.dist)
        (self.dist / name_b).write_bytes(b'new')
        manifest['old']['url'] = 'https://elsewhere.invalid/replacement.deb'
        file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'does not reference its GitHub release'):
            ART.manifest_read(file, self.dist)

    def test_make_all_record_uses_exact_amd64_images(self):
        name = 'openeigrpd_0.5.10-test.dev_amd64.deb'
        (self.dist / name).write_bytes(self.contents)
        from unittest.mock import patch
        with patch.object(ART, 'docker_identity', side_effect=[
                'sha256:' + 'a'*64, 'sha256:' + 'b'*64]), \
                patch.object(ART, 'download_release', return_value=None):
            manifest = ART.record(self.dist, '0.5.10-test.dev', 'test')
        self.assertEqual(manifest['build_tag'], 'test')
        self.assertEqual(manifest['image'], 'openeigrp:0.5.10-test.dev-amd64')
        self.assertEqual(manifest['package_image'], 'openeigrp-package:netlab')
        self.assertIsNone(manifest['old'])
        self.assertEqual(ART.manifest_read(
            self.dist / 'netlabs-amd64.json', self.dist, 'test'), manifest)
        with self.assertRaisesRegex(ValueError, 'not build tag wrong'):
            ART.manifest_read(self.dist / 'netlabs-amd64.json', self.dist, 'wrong')
        (self.dist / name).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'missing or modified new'):
            ART.manifest_read(self.dist / 'netlabs-amd64.json', self.dist)

    def test_record_does_not_replace_index_without_both_images(self):
        name = 'openeigrpd_0.5.10-test.dev_amd64.deb'
        (self.dist / name).write_bytes(self.contents)
        from unittest.mock import patch
        with patch.object(ART, 'docker_identity', side_effect=[
                'sha256:' + 'a'*64, ValueError('missing Debian image')]):
            with self.assertRaisesRegex(ValueError, 'missing Debian image'):
                ART.record(self.dist, '0.5.10-test.dev', 'test')
        self.assertFalse((self.dist / 'netlabs-amd64.json').exists())

    def test_manifest_does_not_require_private_debian_image_build(self):
        name = 'openeigrpd_0.5.10-test.dev_amd64.deb'
        (self.dist / name).write_bytes(self.contents)
        base = {'schema': 'openeigrp-netlabs-amd64/v1',
                'image': 'openeigrp:test', 'image_id': 'sha256:' + 'a'*64,
                'old': None, 'new': {'version': '0.5.10-test.dev', 'package': name,
                                    'sha256': digest(self.contents)}}
        file = self.dist / 'netlabs-amd64.json'
        file.write_text(json.dumps(base))
        self.assertEqual(ART.manifest_read(file, self.dist), base)
        # Older build manifests may contain the obsolete Debian test image
        # identity; test image preparation is independent of developer output.
        base['package_image'] = 'openeigrp-package:old-test'
        base['package_image_id'] = 'sha256:' + 'b'*64
        file.write_text(json.dumps(base))
        self.assertEqual(ART.manifest_read(file, self.dist), base)


if __name__ == '__main__':
    unittest.main(verbosity=2)
