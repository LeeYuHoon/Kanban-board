"""Installer safety without sudo, launchd changes, or user data writes."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from unittest.mock import patch, Mock

from kanban import installation as install
from kanban.store import Store


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.registry = self.root / 'system'
        self.app = self.root / 'app'
        (self.app / '.local').mkdir(parents=True)
        self.data = dict(mode='main', uid=os.getuid(), root=str(self.app),
                         db=str(self.app / '.local/kanban.sqlite3'), python='/usr/bin/python3', id='a'*32)

    def test_main_has_no_alias_and_duplicate_roles_paths_users_rejected(self):
        install.write_registration(self.data, self.registry)
        with patch.dict(os.environ, {}, clear=True):
            saved = install.check(self.app, 'main', directory=self.registry)
            self.assertNotIn('alias', saved)
            for data in [dict(self.data, mode='collector', alias='remote'), dict(self.data, root=str(self.root/'other')),
                         dict(self.data, uid=os.getuid()+1)]:
                with self.assertRaisesRegex(ValueError, '중복 설치'):
                    install.write_registration(data, self.registry)
            with self.assertRaises(ValueError):
                install.check(self.app, 'collector', directory=self.registry)
            with self.assertRaises(ValueError):
                install.check(self.app, directory=self.registry, uid=os.getuid()+1)
            with self.assertRaises(ValueError):
                install.check(self.root/'other', directory=self.registry)
        with patch.dict(os.environ, {'KANBAN_DB': str(self.root/'other.sqlite3')}):
            with self.assertRaisesRegex(ValueError, 'DB'):
                install.check(self.app, directory=self.registry)

    def test_concurrent_install_only_one_wins(self):
        def attempt(index):
            try:
                install.write_registration(dict(self.data, id=str(index)), self.registry)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=6) as workers:
            self.assertEqual(sum(workers.map(attempt, range(6))), 1)

    def test_only_owner_can_unregister_and_endpoint_quotes_paths(self):
        data = dict(self.data, mode='collector', alias='home-macStudio', root=str(self.root/"O'Name folder"))
        install.write_registration(data, self.registry)
        endpoint = (self.registry/'remote').read_text()
        self.assertIn('id -u', endpoint)
        self.assertIn('-m kanban.remote_rpc', endpoint)
        self.assertEqual((self.registry/'remote').stat().st_mode & 0o777, 0o755)
        with self.assertRaises(ValueError):
            install.remove_registration(data['root'], data['uid']+1, self.registry)
        install.remove_registration(data['root'], data['uid'], self.registry)
        self.assertIsNone(install.registration(self.registry))
        self.assertFalse((self.registry/'remote').exists())
        install.write_registration(self.data, self.registry)

    def test_symlink_registry_and_invalid_alias_refused(self):
        real = self.root/'real'
        real.mkdir()
        self.registry.symlink_to(real, target_is_directory=True)
        with self.assertRaises(ValueError):
            install.write_registration(self.data, self.registry)
        for alias in ['', '  ', 'a\nb', 'x'*81]:
            with self.assertRaises(ValueError):
                install.validate_alias(alias)
        self.assertEqual(install.validate_alias(' home-macStudio '), 'home-macStudio')

    def test_collector_lock_and_heartbeat(self):
        store = Store(self.data['db'])
        self.assertFalse(install.collector_alive(store))
        with install.collector_lock(self.data):
            with self.assertRaisesRegex(ValueError, '이미 실행'):
                with install.collector_lock(self.data):
                    pass
        with install.collector_lock(self.data):
            pass

    def test_main_install_does_not_prompt_for_alias(self):
        # Service/administrator operations are mocked; all writes stay in the temp app.
        (self.app/'bin').mkdir()
        (self.app/'bin/kanban').write_text('fixture')
        with patch.object(install, 'ROOT', self.app), patch.object(install, 'registration', return_value=None), \
             patch.object(install, 'privileged') as privileged, patch.object(install.subprocess, 'run') as run, \
             patch.object(install.sys, 'platform', 'darwin'), patch('builtins.input', side_effect=['1']) as prompt, \
             patch.dict(os.environ, {}, clear=True):
            run.return_value = Mock(returncode=0, stdout='')
            install.install()
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(privileged.call_args.args[0], 'register')
        self.assertEqual(privileged.call_args.args[1]['mode'], 'main')
        self.assertNotIn('alias', privileged.call_args.args[1])

    def test_collector_does_not_open_web_or_accept_port(self):
        data = dict(self.data, mode='collector', alias='office')
        with patch.object(install, 'check', return_value=data), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, '웹 화면'):
                install.remote_command('open', [])
            with self.assertRaisesRegex(ValueError, '포트'):
                install.remote_command('restart', ['KANBAN_PORT=31229'])

    def test_failed_service_keeps_registration_to_prevent_second_role(self):
        with patch.object(install, 'ROOT', self.app), patch.object(install, 'registration', return_value=None), \
             patch.object(install, 'check_legacy_processes'), patch.object(install, 'privileged') as privileged, \
             patch.object(install, 'start_collector', side_effect=ValueError('service failed')), \
             patch.object(install.sys, 'platform', 'darwin'), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, '설치는 등록됐지만'):
                install.install('collector', 'home')
        self.assertEqual([call.args[0] for call in privileged.call_args_list], ['register'])
        self.assertTrue((self.app/'.local/db-path').is_file())

    def test_foreground_collector_can_be_stopped_without_web_server(self):
        data = dict(self.data, mode='collector', alias='fixture')
        env = dict(os.environ, HOME=str(self.root), KANBAN_SESSION_ROOTS='{"codex":[],"claude":[]}')
        code = 'import json,sys; from kanban.remote_rpc import serve_collector; serve_collector(json.loads(sys.argv[1]))'
        process = subprocess.Popen([sys.executable, '-c', code, json.dumps(data)], env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stderr.close()
        self.addCleanup(cleanup)
        store = Store(data['db'])
        for _ in range(100):
            if install.collector_alive(store):
                break
            if process.poll() is not None:
                self.fail(process.stderr.read().decode())
            time.sleep(.05)
        self.assertTrue(install.collector_alive(store))
        install.stop_foreground_collector(data)
        process.wait(timeout=5)
        self.assertEqual(process.returncode, 0)
        self.assertFalse(install.collector_alive(store))


if __name__ == '__main__':
    unittest.main()
