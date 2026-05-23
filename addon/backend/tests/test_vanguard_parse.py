"""Quick local test for VanguardFetcher._parse_xlsx.

Usage (no Python install needed — uv handles everything):

    cd addon/backend
    uv run --with pandas --with openpyxl tests/test_vanguard_parse.py \
        "C:/Users/NicolaMazza/Downloads/Holdings details - Vanguard FTSE Developed Europe UCITS ETF (EUR) Accumulating - 23_05_2026.xlsx"
"""
import sys
import logging
import pathlib

logging.basicConfig(level=logging.DEBUG,
                    format="%(levelname)-8s %(name)s  %(message)s")

from app.fetchers.vanguard import VanguardFetcher  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = pathlib.Path(sys.argv[1])
    if not path.exists():
        print(f"ERROR: file not found: {path}")
        sys.exit(1)

    raw = path.read_bytes()
    print(f"\nFile: {path.name}  ({len(raw):,} bytes)\n")

    fetcher = VanguardFetcher()
    holdings = fetcher._parse_xlsx("IE00BK5BQX27", raw)

    print(f"\n{'='*60}")
    print(f"Parsed {len(holdings)} holdings")
    print(f"{'='*60}")
    if holdings:
        total_w = sum(h.weight_pct for h in holdings)
        print(f"Total weight : {total_w:.4f}%")
        print(f"Sample (first 5):")
        for h in holdings[:5]:
            print(f"  {h.constituent_isin:<12}  {h.weight_pct:6.4f}%  "
                  f"{h.constituent_name or '':<40}  sector={h.sector or '-'}")
    else:
        print("ERROR: no holdings returned")
        sys.exit(1)


if __name__ == "__main__":
    main()
