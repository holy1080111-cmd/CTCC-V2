"""Real metadata fixtures + mocked isolated snapshots; no Docker or trading calls."""

import copy
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release_dependencies as v

VERIFIER = Path(v.__file__).resolve()


class DependencyScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "app"
        self.site = Path(self.temp.name) / "site-packages"
        self.root.mkdir()
        self.site.mkdir()
        self.egg = self.root / "ctcc_v2.egg-info"
        self.make_metadata(self.egg, "ctcc-v2", "1.6.9", "PKG-INFO")
        self.make_metadata(self.site / "ctcc_v2-1.6.9.dist-info", "ctcc-v2", "1.6.9")
        self.make_metadata(
            self.site / "example_dep-2.0.dist-info", "example-dep", "2.0"
        )
        self.contract = {
            "dependencies": [["ctcc-v2", "1.6.9"], ["example-dep", "2.0"]],
            "python": sys.version,
        }
        self.original_distributions = importlib.metadata.distributions
        self.enterContext(
            patch.object(
                v.importlib.metadata,
                "distributions",
                lambda: self.original_distributions(
                    path=[str(self.root), str(self.site)]
                ),
            )
        )
        self.isolated = {"python": sys.version, "rows": self.inventory(self.site)}
        self.isolated_mock = self.enterContext(
            patch.object(
                v, "_isolated_dependency_inventory", return_value=self.isolated
            )
        )

    @staticmethod
    def make_metadata(directory, name, version, filename="METADATA"):
        directory.mkdir(exist_ok=True)
        (directory / filename).write_text(
            "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n"
        )

    def inventory(self, directory):
        return [
            {
                "name": d.metadata["Name"],
                "version": d.version,
                "metadata_path": str(d._path),
            }
            for d in self.original_distributions(path=[str(directory)])
        ]

    def rejected(self):
        with self.assertRaises(v.DependencyMismatch) as raised:
            v.verify_dependencies(self.root, self.contract)
        self.assertEqual(
            str(raised.exception), "DEPENDENCIES_DIFFER_FROM_FULL_VALIDATION"
        )
        self.assertIsInstance(raised.exception.details, dict)
        return raised.exception.details

    def test_original_comparison_reproduces_false_mismatch(self):
        current = sorted(
            (d.metadata["Name"], d.version)
            for d in v.importlib.metadata.distributions()
        )
        self.assertEqual(current.count(("ctcc-v2", "1.6.9")), 2)
        self.assertNotEqual(current, sorted(map(tuple, self.contract["dependencies"])))
        self.assertTrue(
            v.verify_dependencies(self.root, self.contract)["source_metadata_excluded"]
        )

    def test_exact_match_preserves_original_behavior_without_subprocess(self):
        self.enterContext(
            patch.object(
                v, "_dependency_inventory", return_value=self.inventory(self.site)
            )
        )
        result = v.verify_dependencies(self.root, self.contract)
        self.assertFalse(result["source_metadata_excluded"])
        self.isolated_mock.assert_not_called()

    def test_real_source_metadata_duplicate_passes_both_inventories(self):
        result = v.verify_dependencies(self.root, self.contract)
        self.assertTrue(result["source_metadata_excluded"])
        self.isolated_mock.assert_called_once()
        self.assertTrue((self.egg / "PKG-INFO").is_file())

    def test_conflicting_source_version_rejected(self):
        self.make_metadata(self.egg, "ctcc-v2", "9.9.9", "PKG-INFO")
        self.assertIn("9.9.9", json.dumps(self.rejected()))

    def test_other_package_extra_rejected(self):
        self.make_metadata(self.site / "extra-1.dist-info", "extra", "1")
        self.assertIn("extra", json.dumps(self.rejected()))

    def test_changed_installed_version_rejected(self):
        self.make_metadata(
            self.site / "example_dep-2.0.dist-info", "example-dep", "3.0"
        )
        self.assertIn("3.0", json.dumps(self.rejected()))

    def test_missing_installed_dependency_rejected(self):
        (self.site / "example_dep-2.0.dist-info" / "METADATA").unlink()
        self.rejected()

    def test_unrelated_duplicate_installation_rejected(self):
        self.make_metadata(self.site / "duplicate-1.dist-info", "example-dep", "2.0")
        self.rejected()

    def test_extra_project_distribution_elsewhere_rejected(self):
        self.make_metadata(self.site / "duplicate-1.dist-info", "ctcc-v2", "1.6.9")
        self.rejected()

    def test_duplicate_discovery_of_same_source_path_rejected(self):
        rows = (
            self.inventory(self.root)
            + self.inventory(self.root)
            + self.inventory(self.site)
        )
        self.enterContext(patch.object(v, "_dependency_inventory", return_value=rows))
        self.rejected()

    @unittest.skipIf(
        os.name == "nt",
        "Native symlink proof runs in Linux acceptance; no Windows ACL changes",
    )
    def test_egg_directory_symlink_rejected(self):
        target = self.root / "actual-project-metadata"
        self.egg.rename(target)
        self.egg.symlink_to(target, target_is_directory=True)
        self.rejected()

    @unittest.skipIf(
        os.name == "nt",
        "Native symlink proof runs in Linux acceptance; no Windows ACL changes",
    )
    def test_pkg_info_symlink_rejected(self):
        original = self.egg / "PKG-INFO"
        target = self.root / "actual-pkg-info"
        original.rename(target)
        original.symlink_to(target)
        self.rejected()

    def test_isolated_version_difference_rejected(self):
        changed = copy.deepcopy(self.isolated)
        changed["rows"][0]["version"] = "different"
        self.isolated_mock.return_value = changed
        self.rejected()

    def test_isolated_python_difference_rejected(self):
        self.isolated_mock.return_value = {**self.isolated, "python": "different"}
        self.rejected()

    def test_isolated_probe_timeout_rejected_without_raw_error(self):
        self.isolated_mock.side_effect = subprocess.TimeoutExpired(
            "DO_NOT_PRINT_COMMAND", 15
        )
        details = self.rejected()
        self.assertNotIn("DO_NOT_PRINT_COMMAND", json.dumps(details))

    def test_relative_source_path_requires_matching_working_directory(self):
        rows = self.inventory(self.root) + self.inventory(self.site)
        rows[0]["metadata_path"] = "ctcc_v2.egg-info"
        self.enterContext(patch.object(v, "_dependency_inventory", return_value=rows))
        self.rejected()

    def test_relative_traversal_is_not_admitted(self):
        rows = self.inventory(self.root) + self.inventory(self.site)
        rows[0]["metadata_path"] = "../app/ctcc_v2.egg-info"
        self.enterContext(patch.object(v, "_dependency_inventory", return_value=rows))
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            self.rejected()
        finally:
            os.chdir(previous)

    def test_real_stdin_discovery_recognizes_source_path_and_passes(self):
        code = """
import importlib.util,json,pathlib,sys
spec=importlib.util.spec_from_file_location('v',VERIFIER_PATH)
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
sys.path[:]=[p for p in sys.path if 'site-packages' not in p]
sys.path.insert(1,SITE_PATH)
rows=v._dependency_inventory()
source=[r for r in rows if pathlib.Path(r['metadata_path']).resolve()==(pathlib.Path.cwd()/'ctcc_v2.egg-info').resolve()]
assert len(source)==1,rows
installed=[r for r in rows if r not in source]
contract={'dependencies':[[r['name'],r['version']] for r in installed],'python':sys.version}
v._isolated_dependency_inventory=lambda:{'python':sys.version,'rows':installed}
result=v.verify_dependencies(pathlib.Path.cwd(), contract)
assert result['source_metadata_excluded'],result
print('REAL_STDIN_RELATIVE_METADATA_CASE_PASSED=1')
""".replace("VERIFIER_PATH", repr(str(VERIFIER))).replace(
            "SITE_PATH", repr(str(self.site))
        )
        child = subprocess.run(
            [sys.executable, "-B", "-"],
            input=code,
            text=True,
            capture_output=True,
            cwd=self.root,
            timeout=15,
            check=False,
        )
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("REAL_STDIN_RELATIVE_METADATA_CASE_PASSED=1", child.stdout)

    def test_unknown_metadata_path_rejected(self):
        rows = self.inventory(self.root) + self.inventory(self.site)
        rows[0]["metadata_path"] = str(self.root / "unexpected.egg-info")
        self.enterContext(patch.object(v, "_dependency_inventory", return_value=rows))
        self.rejected()


if __name__ == "__main__":
    unittest.main(verbosity=2)
