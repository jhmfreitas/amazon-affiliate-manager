import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "discover_from_pinterest.py"

os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_KEY", "test-key")
sys.path.insert(0, str(SCRIPTS_DIR))

spec = importlib.util.spec_from_file_location("discover_from_pinterest", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def test_finalize_discovery_summary_handles_zero_results():
    result = module.finalize_discovery_summary(0, 0, 0)
    assert result == 0
