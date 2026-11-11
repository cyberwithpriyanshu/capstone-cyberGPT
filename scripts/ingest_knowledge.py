# scripts/ingest_knowledge.py
#
# PURPOSE: Downloads the free security sources and turns them into CyberGPT's knowledge files.
#
# HOW TO RUN (from the project's top folder; needs internet, takes about a minute):
#     python scripts/ingest_knowledge.py              # all three sources
#     python scripts/ingest_knowledge.py --only kev   # just one: kev, attack or owasp
#
# WHAT IT PRODUCES (inside data/knowledge/):
#     kev.jsonl, attack.jsonl, owasp.jsonl   the knowledge chunks, one per line
#     NOTICE.md                              credits and licenses for each source
#
# You run this again whenever you want fresh data (KEV changes almost daily).

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import httpx

# Python looks for "import app..." starting from the folder of the script being run (here:
# scripts/), where there is no "app". So we add the project's top folder to the search path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge.parsers import parse_attack, parse_kev, parse_owasp  # noqa: E402
from app.knowledge.store import DEFAULT_DATA_DIR, save_chunks  # noqa: E402

try:  # keep the Windows terminal from crashing on special characters
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KEV_URL = "https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json"
ATTACK_URL = "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/master/enterprise-attack/enterprise-attack.json"
OWASP_RAW = "https://raw.githubusercontent.com/OWASP/CheatSheetSeries/master"

NOTICE = """# Knowledge sources, credits and licenses

CyberGPT's knowledge files are derived from the public sources below. Keep this notice with
the data, and credit these sources in the paper and presentation.

## CISA Known Exploited Vulnerabilities (KEV) catalog
- Source: https://github.com/cisagov/kev-data (https://www.cisa.gov/known-exploited-vulnerabilities-catalog)
- License: CC0 1.0 (public domain dedication).

## MITRE ATT&CK(R)
- Source: https://github.com/mitre-attack/attack-stix-data (https://attack.mitre.org)
- (c) The MITRE Corporation. This work is reproduced and distributed with the permission of
  The MITRE Corporation.
- The MITRE Corporation grants a non-exclusive, royalty-free license to use ATT&CK for
  research, development and commercial purposes, provided any copy reproduces MITRE's
  copyright designation and this license. Full terms: https://attack.mitre.org/resources/terms-of-use/
- MITRE does not claim ATT&CK enumerates all possibilities of attacker behavior.

## OWASP Cheat Sheet Series
- Source: https://github.com/OWASP/CheatSheetSeries (https://cheatsheetseries.owasp.org)
- License: Creative Commons Attribution-ShareAlike 4.0 (CC BY-SA 4.0).
  Material derived from it (owasp.jsonl) must stay under the same license and credit OWASP.
"""


def download(url: str) -> bytes:
    print(f"  downloading {url}")
    # follow_redirects: some download addresses forward to another address; follow them.
    response = httpx.get(url, follow_redirects=True, timeout=180.0)
    response.raise_for_status()
    print(f"  got {len(response.content) / 1_000_000:.1f} MB")
    return response.content


def ingest_kev(out: Path) -> int:
    import json

    chunks = parse_kev(json.loads(download(KEV_URL)))
    save_chunks(out / "kev.jsonl", chunks)
    return len(chunks)


def ingest_attack(out: Path) -> int:
    import json

    chunks = parse_attack(json.loads(download(ATTACK_URL)))
    save_chunks(out / "attack.jsonl", chunks)
    return len(chunks)


def ingest_owasp(out: Path) -> int:
    # OWASP's own Index.md lists every cheat sheet. We read that list, then fetch each sheet's
    # Markdown file one by one (about 140 small files; this takes under a minute).
    # (We do not download the whole repository as a zip because some networks block that.)
    index = download(f"{OWASP_RAW}/Index.md").decode("utf-8", errors="replace")
    names = sorted(set(re.findall(r"cheatsheets/([A-Za-z0-9_\-]+\.md)", index)))
    if not names:
        raise RuntimeError("could not find any cheat sheets listed in OWASP's Index.md")
    print(f"  {len(names)} cheat sheets listed")

    files: dict[str, str] = {}
    failed: list[str] = []
    # One shared connection is faster than opening a new one for every file.
    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        for number, name in enumerate(names, start=1):
            try:
                response = client.get(f"{OWASP_RAW}/cheatsheets/{name}")
                response.raise_for_status()
                files[name] = response.text
            except httpx.HTTPError as err:
                failed.append(name)
                print(f"  could not fetch {name}: {err}")
            if number % 25 == 0:
                print(f"  fetched {number}/{len(names)}")

    if len(failed) > len(names) // 10:  # more than 10% missing: do not save a half-empty file
        raise RuntimeError(f"{len(failed)} of {len(names)} cheat sheets failed to download")

    chunks = parse_owasp(files, as_of=date.today().isoformat())
    save_chunks(out / "owasp.jsonl", chunks)
    return len(chunks)


SOURCES = {"kev": ingest_kev, "attack": ingest_attack, "owasp": ingest_owasp}


def main() -> int:
    parser = argparse.ArgumentParser(description="Download security sources into CyberGPT's knowledge base")
    parser.add_argument("--only", choices=sorted(SOURCES), help="ingest just one source")
    parser.add_argument("--out", default=str(DEFAULT_DATA_DIR), help="where to write the files")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    failures = 0
    for name in [args.only] if args.only else list(SOURCES):
        print(f"[{name}]")
        try:
            count = SOURCES[name](out)
            print(f"  saved {count} chunks to {out / (name + '.jsonl')}")
        except Exception as err:  # one failing source must not stop the others
            failures += 1
            print(f"  FAILED: {type(err).__name__}: {err}")

    (out / "NOTICE.md").write_text(NOTICE, encoding="utf-8", newline="\n")
    print("\nDone." if not failures else f"\nFinished with {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())