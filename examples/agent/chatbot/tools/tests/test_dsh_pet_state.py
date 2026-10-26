# SPDX-License-Identifier: Apache-2.0
"""Compile and execute the actual firmware arbitration logic on the host."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class NativePetStateTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('g++'), 'g++ is needed for the native character view test')
    def test_character_view_commands_transitions_and_arbitration(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'character-view-test.exe'
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-finput-charset=UTF-8', '-fexec-charset=UTF-8',
                            '-I', str(root / 'main'), str(Path(__file__).with_name('character_view_state_test.cpp')),
                            '-o', str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn('checks passed', result.stdout)

    @unittest.skipUnless(shutil.which('g++'), 'g++ is needed for the native firmware state test')
    def test_native_arbitration_and_expiration(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'pet-state-test.exe'
            subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-I', str(root / 'main'), str(Path(__file__).with_name('dsh_pet_state_test.cpp')),
                            '-o', str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn('checks passed', result.stdout)

    @unittest.skipUnless(shutil.which('g++'), 'g++ is needed for the native receiver test')
    def test_actual_receiver_with_idf_doubles(self):
        compiler = Path(shutil.which('g++')).resolve()
        prefix = compiler.parent.parent
        include = next((path for path in (prefix / 'include', Path('/usr/include'), Path('/usr/local/include'))
                        if (path / 'boost/json.hpp').exists()), None)
        if include is None:
            self.skipTest('Boost.JSON headers and library are needed for the native receiver test')
        tests = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'pet-receiver-test.exe'
            subprocess.run([str(compiler), '-std=c++23', '-Wall', '-Wextra', '-Werror',
                            '-I', str(tests / 'dsh_sync_stubs'), '-I', str(include),
                            str(tests / 'dsh_pet_receiver_test.cpp'), '-L', str(prefix / 'lib'),
                            '-lboost_json', '-o', str(executable)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
            self.assertIn('checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
