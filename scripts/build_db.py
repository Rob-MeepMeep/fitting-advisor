"""Build Pyfa's eve.db headlessly into data/eve.db from vendor/pyfa/staticdata."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYFA_ROOT = REPO_ROOT / "vendor" / "pyfa"
DEFAULT_OUTPUT = REPO_ROOT / "data" / "eve.db"


def build_db(output_path: Path = DEFAULT_OUTPUT) -> Path:
    """Build eve.db at ``output_path`` using Pyfa's bundled staticdata."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if str(PYFA_ROOT) not in sys.path:
        sys.path.insert(0, str(PYFA_ROOT))

    # Must be installed before anything from Pyfa is imported.
    from advisor import wxstub

    wxstub.install()

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    import eos.config

    eos.config.gamedata_connectionstring = f"sqlite:///{output_path.as_posix()}"
    eos.config.saveddata_connectionstring = "sqlite:///:memory:"

    db_update_script = PYFA_ROOT / "db_update.py"
    runpy.run_path(str(db_update_script), run_name="__main__")

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to build eve.db at {output_path}")
    return output_path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUTPUT
    result = build_db(out)
    print(f"Built gamedata DB: {result}")
