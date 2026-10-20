"""Only session collection and serving are exposed through the Python CLI."""
import subprocess
import sys
import unittest


class CommandTests(unittest.TestCase):
    def test_manual_card_commands_are_unavailable(self):
        help_text = subprocess.check_output([sys.executable, '-m', 'kanban', '--help'], text=True)
        self.assertIn('{serve,collect}', help_text)
        for command in ('board', 'boards', 'run', 'start', 'recover'):
            result = subprocess.run([sys.executable, '-m', 'kanban', command], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('invalid choice', result.stderr)


if __name__ == '__main__':
    unittest.main()
