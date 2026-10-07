import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("api_diff", ROOT / "api-diff/check.py")
api_diff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api_diff)


class DiffTests(unittest.TestCase):
    def test_paths_cannot_escape_checkouts(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertRaises(ValueError, api_diff.inside, directory, "../outside.json")
            self.assertRaises(ValueError, api_diff.inside, directory, str(Path(directory).resolve()))

    def test_missing_model_fails_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertRaises(ValueError, api_diff.prepare, directory, ".", "missing.json", "", Path(directory) / "out.json")

    def test_build_sets_export_path_and_does_not_interpolate_model_paths_into_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.json"
            model.write_text("{}", encoding="utf-8")
            with patch.object(api_diff.subprocess, "run") as run:
                api_diff.prepare(directory, ".", "model.json", "cargo build --locked", Path(directory) / "snapshot.json")
            self.assertEqual(run.call_args.args[0], ["bash", "-e", "-o", "pipefail", "-c", "cargo build --locked"])
            self.assertEqual(Path(run.call_args.kwargs["env"]["CRUCIBLE_MODEL_OUT"]), model.resolve())

    def test_breaking_report_is_preserved_and_returns_the_checker_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for side in ("baseline", "proposed"):
                (root / side).mkdir()
                (root / side / "model.json").write_text("{}", encoding="utf-8")
            (root / "proposed/crucible.toml").write_text("schema_version = 1", encoding="utf-8")
            report = {"findings": [{"rule": "compatibility.http", "shape": "example.api#Get", "severity": "error", "message": "<script>changed</script>", "exception": "approved <change>"}]}
            args = argparse.Namespace(cli="crucible", baseline=str(root / "baseline"), proposed=str(root / "proposed"), working_directory=".", model_path="model.json", build_command="", config_path="crucible.toml", service="", report_path=str(root / "report"))
            result = subprocess.CompletedProcess([], 1, json.dumps(report), "API policy check failed")
            with patch.object(api_diff.subprocess, "run", return_value=result), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(api_diff.run(args), 1)
            self.assertEqual(json.loads((root / "report/report.json").read_text()), report)
            summary = (root / "report/report.md").read_text()
            self.assertIn("&lt;script&gt;", summary)
            self.assertIn("Exception:", summary)
            self.assertNotIn("<script>", summary)

    @unittest.skipUnless(os.environ.get("CRUCIBLE_CLI"), "Set CRUCIBLE_CLI to exercise the real checker")
    def test_real_checker_accepts_identity_and_rejects_an_http_break(self):
        model = json.loads((ROOT / "tests/contract.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for side in ("baseline", "proposed"):
                (root / side).mkdir()
                (root / side / "model.json").write_text(json.dumps(model), encoding="utf-8")
            (root / "proposed/crucible.toml").write_text("schema_version = 1", encoding="utf-8")
            command = [os.environ["CRUCIBLE_CLI"], "diff", str(root / "baseline/model.json"), str(root / "proposed/model.json"), "--json"]
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
            model["shapes"]["example.api#GetItem"]["traits"]["smithy.api#http"]["uri"] = "/v2/items"
            (root / "proposed/model.json").write_text(json.dumps(model), encoding="utf-8")
            args = argparse.Namespace(cli=os.environ["CRUCIBLE_CLI"], baseline=str(root / "baseline"), proposed=str(root / "proposed"), working_directory=".", model_path="model.json", build_command="", config_path="crucible.toml", service="", report_path=str(root / "report"))
            self.assertEqual(api_diff.run(args), 1)


if __name__ == "__main__":
    unittest.main()
