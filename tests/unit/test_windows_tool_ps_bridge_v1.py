"""Native-argv bridge contract; no installation, credentials or application."""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path


def test_powershell_bridge_has_no_double_quotes_for_windows51_to_strip(tmp_path):
    source = Path(__file__).resolve().parents[2] / "scripts/Install-CTCC-0058.ps1"
    text = source.read_text(encoding="utf8")
    matches = re.findall(r"^\$Bridge = '((?:[^']|'')*)'$", text, re.MULTILINE)
    assert len(matches) == 1
    bridge = matches[0].replace("''", "'")
    assert '"' not in bridge and "`" not in bridge and "$" not in bridge
    parsed = ast.parse(bridge)
    call = parsed.body[-1].value
    assert isinstance(call, ast.Call)
    assert len(call.keywords) == 1 and call.keywords[0].arg == "run_name"
    assert isinstance(call.keywords[0].value, ast.Constant)
    assert call.keywords[0].value.value == "__main__"
    root = tmp_path / "source with spaces & $literal"
    root.mkdir()
    (root / "bridge_data.py").write_text(
        "import json,sys;print(json.dumps({'name':__name__,'args':sys.argv}))",
        encoding="utf8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            bridge,
            str(root),
            "bridge_data",
            "two words",
            "$literal:arg",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "name": "__main__",
        "args": ["bridge_data", "two words", "$literal:arg"],
    }
