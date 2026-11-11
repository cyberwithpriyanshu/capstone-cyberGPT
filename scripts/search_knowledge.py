# scripts/search_knowledge.py
#
# PURPOSE: Try the knowledge search from the terminal (no server needed).
#
# HOW TO RUN (from the project's top folder, after running ingest_knowledge.py):
#     python scripts/search_knowledge.py "how do I prevent SQL injection"
#     python scripts/search_knowledge.py CVE-2021-44228
#     python scripts/search_knowledge.py "kubernetes secrets" --domain cloud
#     python scripts/search_knowledge.py ransomware --source attack -n 8 --full

import argparse
import sys
from pathlib import Path

# So "import app..." works when running a script from the scripts/ folder (see ingest_knowledge.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge import DEFAULT_DATA_DIR, DOMAINS, SearchIndex, load_directory  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Search CyberGPT's knowledge base")
    parser.add_argument("query", nargs="+", help="your question or keywords")
    parser.add_argument("--domain", action="append", choices=DOMAINS, help="limit to a topic area (repeatable)")
    parser.add_argument("--source", action="append", choices=["kev", "attack", "owasp"], help="limit to a source")
    parser.add_argument("-n", "--limit", type=int, default=5, help="how many results")
    parser.add_argument("--full", action="store_true", help="print the whole passage")
    args = parser.parse_args()

    chunks = load_directory(DEFAULT_DATA_DIR)
    if not chunks:
        print("No knowledge found. Run first:  python scripts/ingest_knowledge.py")
        return 1

    query = " ".join(args.query)
    result = SearchIndex(chunks).search(query, limit=args.limit, domains=args.domain, sources=args.source)

    print(f"Query: {query}   ({len(chunks)} passages searched)")
    if result.ids_not_found:
        print(f"NOT IN OUR SOURCES: {', '.join(result.ids_not_found)} (this does not prove it doesn't exist)")
    if not result.hits:
        print("\nNo matches.")
        return 0

    for number, hit in enumerate(result.hits, start=1):
        c = hit.chunk
        how = "exact ID" if hit.exact_id else f"score {hit.score}"
        print(f"\n{number}. {c.title}")
        print(f"   [{c.source}, tier {c.tier}, {how}]  {c.url}")
        text = c.text if args.full else c.text[:300].replace("\n", " ") + (" ..." if len(c.text) > 300 else "")
        print(f"   {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())