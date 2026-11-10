# scripts/stream_test.py
#
# PURPOSE: A manual check that streaming works. It calls our /chat/stream address and
# prints the answer AS IT ARRIVES, which is exactly what the web UI will do in Step 2B.
#
# HOW TO RUN (the server must already be running in ANOTHER terminal):
#     python scripts/stream_test.py
#     python scripts/stream_test.py --mode analyze "Is port 23 open a problem?"
#
# If streaming works you will see the words appear gradually, not all at once.

import argparse
import json
import sys

import httpx

# Make sure special characters (like emojis in a model's answer) don't crash the
# Windows terminal. Older Python versions may not support reconfigure; that's fine.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the CyberGPT streaming endpoint")
    parser.add_argument("question", nargs="*", help="what to ask")
    parser.add_argument("--mode", default="learn", help="learn, analyze, pentest, defend or report")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="where the server runs")
    args = parser.parse_args()

    question = " ".join(args.question) or "Explain what a firewall does in three sentences."
    body = {"messages": [{"role": "user", "content": question}], "mode": args.mode}

    print(f"Mode: {args.mode}\nQuestion: {question}\n")
    print("Answer:")

    try:
        # httpx.stream keeps the connection open so we can read pieces as they arrive.
        with httpx.stream("POST", f"{args.url}/chat/stream", json=body, timeout=120) as resp:
            if resp.status_code != 200:
                resp.read()
                print(f"\nServer replied HTTP {resp.status_code}: {resp.text}")
                return 1

            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[len("data:"):])

                if event["type"] == "token":
                    # end="" keeps everything on one line; flush=True prints immediately.
                    print(event["text"], end="", flush=True)
                elif event["type"] == "error":
                    print(f"\n\n[ERROR] {event['message']}")
                    return 1
                elif event["type"] == "done":
                    print(f"\n\n[done] model={event['model']} provider={event['provider']}")
    except httpx.ConnectError:
        print(f"\nCould not connect to {args.url}. Is the server running (uvicorn app.main:app)?")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())