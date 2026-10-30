#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable regression coverage for development image pinning and cleanup."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / 'tools/remote/images.py'
HELPER = ROOT / 'tools/remote/dev-image.sh'
CLIENT = ROOT / 'tools/remote-uut.sh'
spec = importlib.util.spec_from_file_location('netlab_images', IMAGES)
images = importlib.util.module_from_spec(spec)
spec.loader.exec_module(images)
ID1 = 'sha256:' + 'a' * 64
ID2 = 'sha256:' + 'b' * 64


class ImageSelectionTests(unittest.TestCase):
    def topology(self, parent):
        stage = parent / 'runs/upload.abc'
        scenario = stage / 'netlabs/uut/2R-internal/topology.yml'
        scenario.parent.mkdir(parents=True)
        scenario.write_text(yaml.safe_dump({'defaults': {'devices.frr.clab.image': 'old'},
                                            'nodes': {'oe': {'device': 'frr'},
                                                      'csr': {'device': 'csr'}}}))
        return stage, scenario, stage / 'artifacts/image.json'

    def test_dev_image_selected_and_pinned(self):
        with tempfile.TemporaryDirectory() as temp:
            stage, topology, evidence = self.topology(Path(temp))
            evidence.parent.mkdir()
            (stage / 'dev-image.txt').write_text('openeigrp:diivious-test\n' + ID1 + '\n')
            inspect = json.dumps([{'Id': ID1, 'Os': 'linux', 'Architecture': 'amd64',
                                   'Created': 'now', 'RepoDigests': []}])
            with mock.patch.object(images, 'docker', return_value=inspect) as docker:
                images.prepare(topology, evidence)
            docker.assert_called_once_with('image', 'inspect', 'openeigrp:diivious-test')
            self.assertEqual(yaml.safe_load(topology.read_text())['defaults']['devices.frr.clab.image'],
                             'openeigrp:diivious-test')
            self.assertEqual(json.loads(evidence.read_text())['source'], 'development')
            self.assertEqual(json.loads(evidence.read_text())['id'], ID1)

    def test_stale_remote_image_fails_before_topology_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            stage, topology, evidence = self.topology(Path(temp))
            (stage / 'dev-image.txt').write_text('openeigrp:diivious-test\n' + ID1 + '\n')
            before = topology.read_text()
            with mock.patch.object(images, 'docker', return_value=json.dumps([{'Id': ID2}])):
                with self.assertRaisesRegex(RuntimeError, 'changed after transfer'):
                    images.prepare(topology, evidence)
            self.assertEqual(topology.read_text(), before)

    def test_wrong_architecture_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            stage, topology, evidence = self.topology(Path(temp))
            (stage / 'dev-image.txt').write_text('openeigrp:test\n' + ID1 + '\n')
            with mock.patch.object(images, 'docker', return_value=json.dumps(
                [{'Id': ID1, 'Os': 'linux', 'Architecture': 'arm64'}])):
                with self.assertRaisesRegex(RuntimeError, 'linux/amd64'):
                    images.prepare(topology, evidence)

    def test_bad_stage_pin_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            stage, topology, evidence = self.topology(Path(temp))
            (stage / 'dev-image.txt').write_text('ghcr.io/diivious/openeigrp-frr:0.4.0\n' + ID1 + '\n')
            with self.assertRaisesRegex(RuntimeError, 'Invalid staged'):
                images.staged_image(topology)
            (stage / 'dev-image.txt').write_text('openeigrp:ok\nsha256:not-an-id\n')
            with self.assertRaisesRegex(RuntimeError, 'Invalid staged'):
                images.staged_image(topology)

    def test_default_release_selection_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            _, topology, evidence = self.topology(Path(temp))
            evidence.parent.mkdir()
            with mock.patch.object(images, 'installed', return_value='0.4.0'):
                with mock.patch.object(images, 'docker', return_value=json.dumps([{'Id': ID1}])):
                    images.prepare(topology, evidence)
            self.assertEqual(json.loads(evidence.read_text())['source'], 'release')
            self.assertEqual(json.loads(evidence.read_text())['image'],
                             'ghcr.io/diivious/openeigrp-frr:0.4.0')


class RemoteHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.bin = self.path / 'bin'
        self.bin.mkdir()
        self.data = self.path / 'netlab-data'
        self.state = self.path / 'state'
        self.data.mkdir()
        self.state.mkdir()
        self.env = {**os.environ, 'REMOTE_LAB_DATA': str(self.data),
                    'REMOTE_LAB_STATE': str(self.state),
                    'PATH': str(self.bin) + ':' + os.environ['PATH'],
                    'DOCKER_LOG': str(self.path/'docker.log'),
                    'DOCKER_STATE': str(self.path/'docker-state.json')}
        (self.path/'docker-state.json').write_text(json.dumps({'openeigrp:diivious-test': ID1,
                                                                'openeigrp:unused': ID2,
                                                                'ghcr.io/diivious/openeigrp-frr:0.4.0': ID2,
                                                                'vrnetlab/vr-csr:17.03.06': ID2}))
        stub = self.bin / 'docker'
        stub.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
path=Path(os.environ['DOCKER_STATE'])
state=json.loads(path.read_text())
with open(os.environ['DOCKER_LOG'],'a') as log: log.write(repr(args)+'\\n')
if args[:2]==['ps','-a']:
 print(os.getenv('MOCK_NODES','')); sys.exit(0)
if args[:2]==['image','ls']:
 for k in state: print(k)
elif args[:2]==['image','inspect']:
 name=args[-1]
 if name not in state: sys.exit(1)
 print('linux/amd64' if '{{.Os}}/{{.Architecture}}' in args else state[name])
elif args[:2]==['image','rm']:
 name=args[-1]
 if os.environ.get('MOCK_RMI_FAIL')==name: sys.exit(1)
 del state[name]
 path.write_text(json.dumps(state))
 print(name)
elif args==['load']:
 raw=sys.stdin.buffer.read().decode()
 assert raw.startswith('ARCHIVE:')
 state['openeigrp:diivious-test']=raw.split(':',1)[1]
 path.write_text(json.dumps(state))
 print('Loaded image')
else: sys.exit(2)
''')
        stub.chmod(0o755)

    def run_helper(self, *args, data=None, **env):
        return subprocess.run(['bash', str(HELPER), *args], input=data,
                              capture_output=True, env={**self.env, **env})

    def test_import_exact_id(self):
        import gzip
        data = gzip.compress(('ARCHIVE:' + ID2).encode())
        r = self.run_helper('import', 'openeigrp:diivious-test', ID2, data=data)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn(ID2, r.stdout.decode())
        self.assertEqual(json.loads((self.path/'docker-state.json').read_text())
                         ['openeigrp:diivious-test'], ID2)

    def test_import_mismatch_fails(self):
        import gzip
        data = gzip.compress(('ARCHIVE:' + ID1).encode())
        r = self.run_helper('import', 'openeigrp:diivious-test', ID2, data=data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b'mismatch', r.stderr)

    def test_import_truncated_stream_fails(self):
        r = self.run_helper('import', 'openeigrp:diivious-test', ID2, data=b'not gzip')
        self.assertNotEqual(r.returncode, 0)

    def test_import_does_not_modify_active_lab(self):
        (self.state / 'session').write_text('mode=uut\n')
        r = self.run_helper('import', 'openeigrp:diivious-test', ID2)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b'BUSY', r.stderr)
        self.assertNotIn('load', (self.path/'docker.log').read_text() if
                         (self.path/'docker.log').exists() else '')

    def test_import_does_not_modify_orphaned_lab(self):
        r = self.run_helper('import', 'openeigrp:diivious-test', ID2,
                            MOCK_NODES='clab-eigrp-neighbor-oe')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b'BUSY', r.stderr)

    def test_inspect_missing_image_is_empty(self):
        r = self.run_helper('inspect', 'openeigrp:missing')
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b'')

    def test_purge_only_netlabs_artifacts(self):
        reports = self.data/'results'
        reports.mkdir()
        (reports/'uut-20260101').mkdir()
        (reports/'uut-20260101'/'log').write_text('a')
        (reports/'batch-all-20260101.json').write_text('{}')
        (reports/'unrelated').write_text('keep')
        r = self.run_helper('purge')
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        state = json.loads((self.path/'docker-state.json').read_text())
        self.assertNotIn('openeigrp:unused', state)
        self.assertNotIn('openeigrp:diivious-test', state)
        self.assertIn('ghcr.io/diivious/openeigrp-frr:0.4.0', state)
        self.assertIn('vrnetlab/vr-csr:17.03.06', state)
        self.assertFalse((reports/'uut-20260101').exists())
        self.assertFalse((reports/'batch-all-20260101.json').exists())
        self.assertTrue((reports/'unrelated').exists())

    def test_purge_busy_keeps_evidence(self):
        reports = self.data/'results'
        reports.mkdir()
        (reports/'uut-123').mkdir()
        (self.state/'owner').write_text('pid=123\n')
        r = self.run_helper('purge')
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue((reports/'uut-123').exists())

    def test_purge_rejects_unrelated_image_name(self):
        r = self.run_helper('import', 'ghcr.io/diivious/openeigrp-frr:0.5.0', ID1)
        self.assertNotEqual(r.returncode, 0)
        self.assertNotIn('load', (self.path/'docker.log').read_text() if
                         (self.path/'docker.log').exists() else '')


class ClientArgumentTests(unittest.TestCase):
    def test_bad_images_rejected_before_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'netlabs/tools/remote').mkdir(parents=True)
            script = root/'netlabs/tools/remote-uut.sh'
            script.write_bytes(CLIENT.read_bytes())
            # The transport loader is deliberately a stub: arguments must be
            # rejected before any remote or Docker access occurs.
            (root/'netlabs/tools/remote/common.sh').write_text(
                'REMOTE_REPO_ROOT="' + str(root) + '"\n'
                'remote_transport_prepare() { echo network-access >&2; return 90; }\n')
            for invalid in ('../../evil', 'ghcr.io/diivious/release:0.4.0', 'a b'):
                r = subprocess.run(['bash', str(script), '--scenario', '2R-internal',
                                    '--image', invalid], capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertNotIn('network-access', r.stderr)
            r = subprocess.run(['bash', str(script), '--image', 'okay', '--purge'],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stderr)


class ClientTransferTests(unittest.TestCase):
    """Run the actual macOS-compatible client against isolated SSH/Docker stubs."""

    def test_changed_image_transfers_and_unchanged_skips(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            remote = root / 'netlabs/tools/remote'
            remote.mkdir(parents=True)
            (root/'netlabs/tools/remote-uut.sh').write_bytes(CLIENT.read_bytes())
            (remote/'dev-image.sh').write_bytes(HELPER.read_bytes())
            fixture = root/'netlabs/uut/2R-neighbor'
            fixture.mkdir(parents=True)
            (fixture/'scenario.yaml').write_text('name: 2R-neighbor\n')
            (fixture/'topology.yml').write_text('name: eigrp-neighbor\n')
            (root/'netlabs/uut/2R-neighbor.py').write_text('pass\n')
            temp = Path(tmp)
            stage_root = temp/'netlab-data/runs'
            stage_root.mkdir(parents=True)
            (temp/'netlab-data/results').mkdir()
            (temp/'remote-state').mkdir()
            (temp/'local.id').write_text(ID1)
            (temp/'remote.id').write_text('')
            (temp/'client-events').write_text('')
            (temp/'test-report').mkdir()
            (temp/'test-report/result.json').write_text('{"cleanup": "PASS"}')
            stub = temp/'bin/docker'
            stub.parent.mkdir()
            stub.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:]
base=Path(os.environ['MOCK_TMP'])
context=os.getenv('MOCK_CONTEXT','local')
idfile=base/('remote.id' if context=='remote' else 'local.id')
identity=idfile.read_text().strip()
with (base/'client-events').open('a') as log: log.write(context+' '+repr(args)+'\\n')
if args[:2]==['image','inspect']:
 if not identity: sys.exit(1)
 if '{{.Os}}/{{.Architecture}}' in args: print('linux/amd64')
 else: print(identity)
elif args[:2]==['ps','-a']: print('')
elif args[:2]==['image','ls']: print('openeigrp:diivious-test')
elif args[:2]==['image','rm']:
 idfile.write_text('')
elif args[:1]==['save']:
 sys.stdout.write('ARCHIVE:'+identity)
elif args[:1]==['load']:
 raw=sys.stdin.read()
 assert raw.startswith('ARCHIVE:'),raw
 idfile.write_text(raw.split(':',1)[1])
 print('Loaded image')
else: sys.exit(2)
''')
            stub.chmod(0o755)
            # This is the existing common.sh contract with SSH replaced by
            # execution in an isolated local environment for this test.
            common = '''REMOTE_REPO_ROOT="''' + str(root) + '''"
REMOTE_RUNS="''' + str(stage_root) + '''"
remote_transport_prepare() { return 0; }
remote_new_stage() { mktemp -d "$REMOTE_RUNS/upload.XXXXXXXX"; }
remote_stage() {
  local stage="$1"; shift
  mkdir -p "$stage/netlabs/tools/remote"
  cp "$REMOTE_REPO_ROOT/netlabs/tools/remote/dev-image.sh" "$stage/netlabs/tools/remote/dev-image.sh"
}
remote_ssh() { MOCK_CONTEXT=remote bash -c "$1"; }
remote_install_runner() { return 0; }
remote_run() { printf 'REPORT_ID=uut-test1\\n'; }
remote_fetch_report() {
  mkdir -p "$2/$1"
  cp "$MOCK_TMP/test-report/result.json" "$2/$1/result.json"
}
'''
            (remote/'common.sh').write_text(common)
            env = {**os.environ, 'PATH': str(stub.parent) + ':' + os.environ['PATH'],
                   'MOCK_TMP': str(temp), 'REMOTE_LAB_DATA': str(temp/'netlab-data'),
                   'REMOTE_LAB_STATE': str(temp/'remote-state')}
            script = root/'netlabs/tools/remote-uut.sh'
            for index, (value, expected_saves) in enumerate(((ID1, 1), (ID1, 1), (ID2, 2))):
                (temp/'local.id').write_text(value)
                proc = subprocess.run(['bash', str(script), '--scenario', '2R-neighbor',
                                       '--image', 'diivious-test'], env=env,
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                events = (temp/'client-events').read_text().splitlines()
                saves = [e for e in events if "['save', 'openeigrp:diivious-test']" in e]
                self.assertEqual(len(saves), expected_saves)
                self.assertEqual((temp/'remote.id').read_text(), value)
                self.assertTrue((root/'netlabs/results/uut-test1/result.json').exists())
                if index == 1:
                    self.assertIn('transfer skipped', proc.stdout)
            remote_report = temp/'netlab-data/results/uut-archived'
            remote_report.mkdir()
            (remote_report/'report.json').write_text('{}')
            purge = subprocess.run(['bash', str(script), '--purge'], env=env,
                                   capture_output=True, text=True)
            self.assertEqual(purge.returncode, 0, purge.stderr + purge.stdout)
            self.assertEqual((temp/'remote.id').read_text(), '')
            self.assertEqual((temp/'local.id').read_text(), '')
            self.assertFalse((root/'netlabs/results/uut-test1').exists())
            self.assertFalse(remote_report.exists())


if __name__ == '__main__':
    unittest.main()
