from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bootstrap


class BootstrapImportCheckTests(unittest.TestCase):
    def test_installed_package_scan_filters_names_and_keeps_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime = Path(temp_dir) / 'python'
            site_packages = runtime / 'Lib' / 'site-packages'
            site_packages.mkdir(parents=True)
            distributions = {
                'numpy-2.5.3.dist-info': ('numpy', '2.5.3'),
                'numpy-2.4.0.dist-info': ('numpy', '2.4.0'),
                'pyside6_fluent_widgets-1.11.3.dist-info': (
                    'pyside6-fluent-widgets',
                    '1.11.3',
                ),
                'legacy_pkg-1.0.egg-info': ('legacy-pkg', '1.0'),
                'pip-26.0.dist-info': ('pip', '26.0'),
            }
            for directory, (name, version) in distributions.items():
                metadata_dir = site_packages / directory
                metadata_dir.mkdir()
                metadata_file = (
                    'PKG-INFO' if directory.endswith('.egg-info') else 'METADATA'
                )
                (metadata_dir / metadata_file).write_text(
                    f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n',
                    encoding='utf-8',
                )
            (site_packages / 'unrelated-1.0.dist-info').mkdir()

            with patch.object(bootstrap.subprocess, 'run') as run:
                installed = bootstrap.getInstalledPackages(
                    runtime / 'python.exe',
                    names=['numpy', 'pyside6-fluent-widgets', 'legacy_pkg'],
                )

        self.assertEqual(
            {(package.name, package.version) for package in installed},
            {
                ('numpy', '2.5.3'),
                ('numpy', '2.4.0'),
                ('pyside6-fluent-widgets', '1.11.3'),
                ('legacy-pkg', '1.0'),
            },
        )
        run.assert_not_called()

    def test_skips_imports_for_packages_already_scheduled_for_install(self) -> None:
        required = [
            bootstrap.RequirementInfo('numpy', '2.5.3'),
            bootstrap.RequirementInfo('pillow', '12.3.0'),
        ]

        with patch.object(
            bootstrap,
            '_runImportCheck',
            return_value=(set(), [], ''),
        ) as run_check:
            missing = bootstrap.getMissingImports(
                Path('python.exe'),
                required,
                {'numpy': 'numpy', 'pillow': 'PIL'},
                already_unsatisfied=[required[0]],
            )

        self.assertEqual(missing, [])
        run_check.assert_called_once_with(Path('python.exe'), {'pillow': 'PIL'}, None)

    def test_checks_all_imports_when_no_packages_are_unsatisfied(self) -> None:
        required = [
            bootstrap.RequirementInfo('numpy', '2.5.3'),
            bootstrap.RequirementInfo('pillow', '12.3.0'),
        ]

        with patch.object(
            bootstrap,
            '_runImportCheck',
            return_value=(set(), [], ''),
        ) as run_check:
            missing = bootstrap.getMissingImports(
                Path('python.exe'),
                required,
                {'numpy': 'numpy', 'pillow': 'PIL'},
            )

        self.assertEqual(missing, [])
        run_check.assert_called_once_with(
            Path('python.exe'),
            {'numpy': 'numpy', 'pillow': 'PIL'},
            None,
        )


if __name__ == '__main__':
    unittest.main()
