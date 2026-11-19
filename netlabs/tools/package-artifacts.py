#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Fetch the published Debian release used as the Netlab upgrade baseline.

The source for version A is a GitHub Release asset, never a Git tag checkout,
GHCR container, or locally rebuilt substitute. Before Debian packages are
published, the download returns null; upgrade tests are explicitly skipped.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO = 'diivious/openeigrp'
API = f'https://api.github.com/repos/{REPO}/releases'
ARCH = 'amd64'
RELEASE = re.compile(r'rel-(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z')
ASSET = re.compile(r'openeigrpd_([0-9]+\.[0-9]+)_(amd64|arm64)\.deb\Z')


def fetch(url):
    headers = {'User-Agent': 'OpenEIGRP-Netlab-package-test',
               'Accept': 'application/vnd.github+json'}
    if url.startswith(API):
        headers['X-GitHub-Api-Version'] = '2022-11-28'
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with urlopen(Request(url, headers=headers), timeout=45) as response:
            return response.read()
    except (HTTPError, URLError) as exc:
        raise ValueError(f'GitHub release download failed: {exc}') from exc


def latest(releases, requested=None):
    matches = []
    for release in releases:
        tag = release.get('tag_name', '')
        match = RELEASE.fullmatch(tag)
        if not match or release.get('draft') or release.get('prerelease'):
            continue
        if requested is not None and tag != 'rel-' + requested:
            continue
        version = f'{int(match[1])}.{int(match[2])}'
        matches.append(((int(match[1]), int(match[2])), version, release, tag))
    if not matches:
        return None
    # A rel-* tag can exist before Debian packages are published. Select
    # the newest release containing a complete native package pair; an
    # incomplete Debian publication is an error, not an upgrade skip.
    for _, version, release, tag in sorted(matches, key=lambda item: item[0], reverse=True):
        name = f'openeigrpd_{version}_{ARCH}.deb'
        assets = {a.get('name'): a for a in release.get('assets', [])
                  if a.get('state') == 'uploaded'}
        if name in assets and 'SHA256SUMS' in assets:
            return version, assets[name], assets['SHA256SUMS'], tag
        if any(asset == 'SHA256SUMS' or
               (isinstance(asset, str) and asset.startswith('openeigrpd_') and
                asset.endswith('.deb')) for asset in assets):
            raise ValueError(f'latest release {tag} is missing {name} or SHA256SUMS')
    return None


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def release_url(asset, tag, expected_name):
    url = asset.get('browser_download_url', '')
    prefix = f'https://github.com/{REPO}/releases/download/{tag}/'
    if url != prefix + expected_name:
        raise ValueError(f'unexpected release asset URL: {url!r}')
    return url


def checksum(lines, name):
    entries = {}
    for raw in lines.decode('utf-8').splitlines():
        match = re.fullmatch(r'([0-9a-fA-F]{64})[ \t]+\*?([^/\\\s]+)', raw)
        if not match or match[2] in entries:
            raise ValueError('invalid or duplicate SHA256SUMS entry')
        entries[match[2]] = match[1].lower()
    if name not in entries:
        raise ValueError(f'missing checksum for {name}')
    return entries[name]


def download_release(output, requested=None):
    # GitHub returns published releases newest-first. Choose the highest
    # numeric rel-MAJOR.MINOR version, not API ordering or a mutable GHCR tag.
    releases = []
    for page in range(1, 11):
        batch = json.loads(fetch(f'{API}?per_page=100&page={page}'))
        if not isinstance(batch, list):
            raise ValueError('unexpected GitHub Releases API response')
        releases.extend(batch)
        if len(batch) < 100:
            break
    else:
        raise ValueError('more than 1000 releases; cannot select the newest safely')
    selected = latest(releases, requested)
    if selected is None:
        # A published FRR image does not imply a published native package.
        return None
    version, deb_asset, sums_asset, tag = selected
    name = f'openeigrpd_{version}_{ARCH}.deb'
    deb_url = release_url(deb_asset, tag, name)
    sums_url = release_url(sums_asset, tag, 'SHA256SUMS')
    expected = checksum(fetch(sums_url), name)
    data = fetch(deb_url)
    digest = sha256(data)
    if digest != expected:
        raise ValueError(f'published SHA256SUMS mismatch for {name}')
    declared = deb_asset.get('digest')
    if declared and declared != 'sha256:' + digest:
        raise ValueError(f'GitHub asset digest mismatch for {name}')
    output.mkdir(parents=True, exist_ok=True)
    destination = output / name
    # Never overwrite an existing package with a differing payload; this
    # could silently change the baseline behind a previous manifest.
    if destination.exists() and sha256(destination.read_bytes()) != digest:
        raise ValueError(f'conflicting local artifact: {destination}')
    with tempfile.NamedTemporaryFile(dir=output, delete=False) as staged:
        staged.write(data)
        staged_name = staged.name
    try:
        os.replace(staged_name, destination)
    finally:
        if os.path.exists(staged_name):
            os.unlink(staged_name)
    return {'version': version, 'package': name, 'sha256': digest,
            'url': deb_url, 'release': tag}


def manifest_read(path, dist, image_tag=None):
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('schema') != 'openeigrp-netlabs-amd64/v1':
        raise ValueError('unknown Netlab build manifest schema')
    image_id = manifest.get('image_id')
    if not isinstance(image_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
        raise ValueError('invalid Docker image ID in manifest')
    image = manifest.get('image')
    if not isinstance(image, str) or not re.fullmatch(r'openeigrp:[A-Za-z0-9][A-Za-z0-9_.-]*', image):
        raise ValueError('invalid Docker image in manifest')
    if image_tag:
        build_tag = manifest.get('build_tag', image.removeprefix('openeigrp:'))
        valid_tags = (build_tag, build_tag if build_tag.endswith('-test') else build_tag + '-test')
        if image_tag not in valid_tags:
            raise ValueError(f'build manifest is for {image}, not build tag {image_tag}')
    if 'package_image' in manifest:
        pkg_image = manifest['package_image']
        pkg_id = manifest.get('package_image_id')
        if not isinstance(pkg_image, str) or not re.fullmatch(
                r'openeigrp-package:[A-Za-z0-9][A-Za-z0-9_.-]*', pkg_image):
            raise ValueError('invalid Debian systemd image in manifest')
        if not isinstance(pkg_id, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', pkg_id):
            raise ValueError('invalid Debian systemd image ID in manifest')
    # The Debian systemd test image is assembled on the Netlab appliance.
    # Its identity is checked against the running image at scenario startup,
    # not embedded in the development package manifest. Old manifests may
    # contain unused package_image keys; they are intentionally ignored.
    names = []
    old = manifest.get('old')
    if old is not None and not isinstance(old, dict):
        raise ValueError('invalid published package metadata')
    for key in ('old', 'new'):
        if key == 'old' and old is None:
            continue
        item = manifest.get(key)
        if not isinstance(item, dict):
            raise ValueError(f'missing {key} package')
        name, digest, version = item.get('package'), item.get('sha256'), item.get('version')
        match = ASSET.fullmatch(name or '')
        # The new package's version may be a development version.
        if key == 'new':
            match = re.fullmatch(r'openeigrpd_([A-Za-z0-9.+~_-]+)_amd64\.deb', name or '')
        if not match or not re.fullmatch(r'[0-9a-f]{64}', digest or '') or not version:
            raise ValueError(f'invalid {key} artifact identity')
        if name != f'openeigrpd_{version}_amd64.deb':
            raise ValueError(f'inconsistent {key} artifact version')
        if key == 'old':
            if item.get('release') != 'rel-' + version or \
               item.get('url') != f'https://github.com/{REPO}/releases/download/rel-{version}/{name}':
                raise ValueError('old package does not reference its GitHub release')
        path = dist / name
        if not path.is_file() or sha256(path.read_bytes()) != digest:
            raise ValueError(f'missing or modified {key} artifact: {path}')
        names.append(name)
    if len(names) == 2 and names[0] == names[1]:
        raise ValueError('upgrade package versions are identical')
    return manifest


def docker_identity(name):
    result = subprocess.run(['docker', 'image', 'inspect', '--format',
                             '{{.Id}} {{.Os}}/{{.Architecture}}', name],
                            capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'missing locally built image {name}; run make all')
    identity = result.stdout.strip().split()
    if len(identity) != 2 or not re.fullmatch(r'sha256:[a-f0-9]{64}', identity[0]) or \
            identity[1] != 'linux/amd64':
        raise ValueError(f'{name} is not a valid linux/amd64 image')
    return identity[0]


def record(dist, version, tag):
    # make all builds both architecture-specific images and the native .deb.
    # Only index a complete build; never use a stale, native ARM64 alias.
    if not re.fullmatch(r'[A-Za-z0-9.+~_-]+', version) or not re.fullmatch(
            r'[A-Za-z0-9][A-Za-z0-9_.-]*', tag):
        raise ValueError('invalid development version or build tag')
    image = f'openeigrp:{version}-amd64'
    image_id = docker_identity(image)
    pkg_image = 'openeigrp-package:netlab'
    pkg_id = docker_identity(pkg_image)
    package = f'openeigrpd_{version}_amd64.deb'
    path = dist / package
    if not path.is_file():
        raise ValueError(f'missing locally built Debian package: {path}')
    old = download_release(dist)
    if old:
        result = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/amd64',
                                 '--entrypoint', 'dpkg', pkg_image,
                                 '--compare-versions', version, 'gt', old['version']])
        if result.returncode:
            raise ValueError(f'{version} must be newer than published {old["version"]}')
    manifest = {'schema': 'openeigrp-netlabs-amd64/v1', 'build_tag': tag,
                'image': image, 'image_id': image_id,
                'package_image': pkg_image, 'package_image_id': pkg_id,
                'old': old,
                'new': {'version': version, 'package': package,
                        'sha256': sha256(path.read_bytes())}}
    # The manifest is written last so an interrupted build cannot authorize
    # mismatched images/packages. make all clears the old manifest first.
    dist.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=dist, prefix='.netlabs-amd64.',
                                     delete=False, encoding='utf-8') as tmp:
        json.dump(manifest, tmp, indent=2, sort_keys=True)
        tmp.write('\n')
        staged = tmp.name
    os.replace(staged, dist / 'netlabs-amd64.json')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    down = sub.add_parser('download')
    down.add_argument('--dist', type=Path, required=True)
    down.add_argument('--release', help='Exact rel-MAJOR.MINOR release to download')
    recording = sub.add_parser('record')
    recording.add_argument('--dist', type=Path, required=True)
    recording.add_argument('--version', required=True)
    recording.add_argument('--tag', required=True)
    check = sub.add_parser('validate')
    check.add_argument('--dist', type=Path, required=True)
    check.add_argument('--manifest', type=Path, required=True)
    check.add_argument('--image-tag')
    args = parser.parse_args()
    if args.action == 'download':
        if args.release is not None and not re.fullmatch(r'[0-9]+\.[0-9]+', args.release):
            raise ValueError('release must be X.Y')
        print(json.dumps(download_release(args.dist, args.release), sort_keys=True))
    elif args.action == 'record':
        print(json.dumps(record(args.dist, args.version, args.tag), sort_keys=True))
    else:
        print(json.dumps(manifest_read(args.manifest, args.dist, args.image_tag), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser_msg = f'package-artifacts: {exc}'
        raise SystemExit(parser_msg)
