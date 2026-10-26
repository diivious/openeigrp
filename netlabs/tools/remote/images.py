#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Select installed OpenEIGRP releases, update from GHCR, and pin a staged UUT.

Only complete X.Y.Z release tags are eligible. Never use a mutable `latest`
alias or relabel an old image under a newer version number.
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


def prepare(topology_path, artifact_path):
    """Pin the highest locally installed release in a disposable staged topology."""
    tag = installed()
    image = f'{REPOSITORY}:{tag}'
    image_data = json.loads(docker('image', 'inspect', image))[0]
    topology_path = Path(topology_path)
    topology = yaml.safe_load(topology_path.read_text(encoding='utf8'))
    if not isinstance(topology, dict) or not isinstance(topology.get('defaults'), dict):
        raise RuntimeError('Invalid netlab topology defaults')
    if not any(node.get('device') == 'frr' for node in topology.get('nodes', {}).values()):
        raise RuntimeError('No FRR/OpenEIGRP node in netlab topology')
    topology['defaults']['devices.frr.clab.image'] = image
    topology_path.write_text(yaml.safe_dump(topology, sort_keys=False), encoding='utf8')
    evidence = {
        'repository': REPOSITORY,
        'tag': tag,
        'image': image,
        'id': image_data['Id'],
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
