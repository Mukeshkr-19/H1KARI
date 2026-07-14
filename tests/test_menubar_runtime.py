"""Menu-bar support must remain importable without its optional dependency."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import patch


def test_menubar_imports_without_rumps_and_points_to_real_entrypoint():
    module_path = Path(__file__).parents[1] / "core" / "menubar.py"

    with patch.dict(sys.modules, {"rumps": None}):
        namespace = runpy.run_path(str(module_path))

    assert namespace["RUMPS_AVAILABLE"] is False
    assert Path(namespace["HIKARI_ENTRYPOINT"]) == Path(__file__).parents[1] / "hikari.py"
    assert Path(namespace["HIKARI_ENTRYPOINT"]).is_file()
