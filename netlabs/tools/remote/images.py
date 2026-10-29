#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Select installed OpenEIGRP releases, update from GHCR, and pin a staged UUT.

Only complete X.Y.Z release tags are eligible for default selection. A staged
--image run may select an explicitly verified local development image instead.
Never relabel development images as published releases.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import yaml

REPOSITORY = 'ghcr.io/diivious/openeigrp-frr'
VERSION = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+$')
DEV_IMAGE = re.compile(r'^openeigrp:[a-zA-Z0-9][a-zA-Z0-9_.-]*$')
IMAGE_ID = re.compile(r'^sha256:[0-9a-f]{64}$')
REGISTRY = 'https://ghcr.io'
TAGS_URL = REGISTRY + '/v2/diivious/openeigrp-frr/tags/list?n=100'
TOKEN_URL = REGISTRY + '/token?service=ghcr.io&scope=repository:diivious/openeigrp-frr:pull'


def version_key(tag):
    if not VERSION.fullmatch(tag):
        raise ValueError(f'Invalid release tag: {tag}')
    return tuple(int(part) for part in tag.split('.'))


def newest(tags):
    matching = [tag for tag in tags if VERSION.fullmatch(tag)]
    if not matching:
        raise RuntimeError('No versioned OpenEIGRP images found (expected X.Y.Z)')
    return max(matching, key=version_key)


def docker(*args):
    result = subprocess.run(['docker', *args], check=True, capture_output=True, text=True)
    return result.stdout


def installed():
    lines = docker('image', 'ls', '--format', '{{.Repository}}:{{.Tag}}').splitlines()
    tags = []
    for line in lines:
        prefix = REPOSITORY + ':'
        if line.startswith(prefix):
            tags.append(line[len(prefix):])
    return newest(tags)


def registry_tags():
    token_response = Request(TOKEN_URL, headers={'User-Agent': 'OpenEIGRP-netlabs'})
    with urlopen(token_response, timeout=25) as response:
        token_data = json.load(response)
    token = token_data.get('token') or token_data.get('access_token')
    if not token:
        raise RuntimeError('GHCR did not provide a registry access token')

    tags = []
    url = TAGS_URL
    seen = set()
    while url:
        # Restrict pagination links to the intended GHCR image repository.
        parts = urlsplit(url)
        if (parts.scheme, parts.netloc) != ('https', 'ghcr.io') or parts.path != '/v2/diivious/openeigrp-frr/tags/list':
            raise RuntimeError('Unexpected GHCR pagination URL')
        if url in seen:
            raise RuntimeError('GHCR pagination cycle detected')
        seen.add(url)
        req = Request(url, headers={'Authorization': 'Bearer ' + token,
                                    'User-Agent': 'OpenEIGRP-netlabs'})
        with urlopen(req, timeout=25) as response:
            data = json.load(response)
            link = response.headers.get('Link', '')
        tags.extend(data.get('tags') or [])
        url = None
        for entry in link.split(','):
            match = re.search(r'<([^>]+)>\s*;\s*rel="?next"?', entry)
            if match:
                url = urljoin(REGISTRY, match.group(1))
                break
    return tags


def update():
    release = newest(registry_tags())
    image = f'{REPOSITORY}:{release}'
    print(f'Latest published OpenEIGRP release: {release}', flush=True)
    print(f'Pulling {image}...', flush=True)
    subprocess.run(['docker', 'pull', image], check=True)
    docker('image', 'inspect', image)
    chosen = installed()
    print(f'Image update complete. Future UUT runs will use {REPOSITORY}:{chosen}', flush=True)
    return image


def staged_image(topology_path):
    """Read a development pin only from the current disposable test stage."""
    for parent in topology_path.resolve().parents:
        if parent.name.startswith('upload.') and parent.parent.name == 'runs':
            marker = parent / 'dev-image.txt'
            if not marker.exists():
                return None
            values = marker.read_text(encoding='utf8').splitlines()
            if len(values) != 2 or not DEV_IMAGE.fullmatch(values[0]) or not IMAGE_ID.fullmatch(values[1]):
                raise RuntimeError('Invalid staged development image selection')
            return values
    return None


def prepare(topology_path, artifact_path):
    """Pin a verified image in the staged topology, never the source fixture."""
    topology_path = Path(topology_path)
    dev = staged_image(topology_path)
    if dev:
        image, expected_id = dev
        repository, tag = image.split(':', 1)
        source = 'development'
    else:
        tag = installed()
        image = f'{REPOSITORY}:{tag}'
        repository = REPOSITORY
        expected_id = None
        source = 'release'
    image_data = json.loads(docker('image', 'inspect', image))[0]
    if expected_id is not None:
        if image_data['Id'] != expected_id:
            raise RuntimeError(f'Development image changed after transfer: {image}')
        if (image_data.get('Os'), image_data.get('Architecture')) != ('linux', 'amd64'):
            raise RuntimeError(f'Expected linux/amd64 development image: {image}')
    topology = yaml.safe_load(topology_path.read_text(encoding='utf8'))
    if not isinstance(topology, dict) or not isinstance(topology.get('defaults'), dict):
        raise RuntimeError('Invalid netlab topology defaults')
    if not any(node.get('device') == 'frr' for node in topology.get('nodes', {}).values()):
        raise RuntimeError('No FRR/OpenEIGRP node in netlab topology')
    topology['defaults']['devices.frr.clab.image'] = image
    topology_path.write_text(yaml.safe_dump(topology, sort_keys=False), encoding='utf8')
    evidence = {
        'repository': repository,
        'tag': tag,
        'image': image,
        'id': image_data['Id'],
        'source': source,
        'created': image_data.get('Created'),
        'repo_digests': image_data.get('RepoDigests') or [],
    }
    Path(artifact_path).write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf8')
    print(f"OpenEIGRP image: {image} (ID {image_data['Id'][:19]})", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('update')
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('topology')
    prepare_parser.add_argument('evidence')
    args = parser.parse_args()
    try:
        if args.action == 'update':
            update()
        else:
            prepare(args.topology, args.evidence)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: OpenEIGRP image {args.action} failed: {exc}', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
