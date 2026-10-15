"""Regenerate the checked-in fictional price CSV when explicitly requested."""

from portfoliopulse.config import DEMO_CSV_PATH
from portfoliopulse.demo import generate_demo_prices


if __name__ == "__main__":
    if DEMO_CSV_PATH.exists():
        raise SystemExit(f"Refusing to overwrite {DEMO_CSV_PATH}; move it first if regeneration is intended.")
    DEMO_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    generate_demo_prices().to_csv(DEMO_CSV_PATH, index=False, float_format="%.8f")
    print(f"Created synthetic demo prices at {DEMO_CSV_PATH}")
