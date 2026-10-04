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


def test_repin_winners_main_returns_zero_when_no_variations_are_created(monkeypatch):
    import importlib.util
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    repin_path = root / "scripts" / "repin_winners.py"
    mod_spec = importlib.util.spec_from_file_location("repin_winners", repin_path)
    repin_module = importlib.util.module_from_spec(mod_spec)
    sys.modules[mod_spec.name] = repin_module
    mod_spec.loader.exec_module(repin_module)

    monkeypatch.setattr(repin_module, "find_opportunity_products", lambda: [{
        "product": {"name": "Test product", "category": "fashion", "price": 25, "pinterest_keywords": ["women fashion"], "image_url": "https://example.com/image.jpg", "asin": "B123456789", "affiliate_url": "https://example.com", "id": 1},
        "impressions": 100,
        "clicks": 0,
        "ctr": 0.0,
        "existing_pin_count": 1,
        "existing_titles": ["Old angle"],
    }])
    monkeypatch.setattr(repin_module, "generate_fresh_variation", lambda *args, **kwargs: None)

    assert repin_module.main() == 0
