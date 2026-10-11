#!/usr/bin/env python3
"""
Send N numbered test messages to a chat thread through the public API, to
check older-message loading / pagination in the app.

    python scripts/seed_chat_messages.py --thread 852e3295-7e8b-459f-b9fa-c86b5b843a09

You'll be asked for your email and password (the password is read with
getpass, so it isn't echoed, stored, or saved in shell history).

Heads-up: every message goes through the normal send flow, so the other
person in the thread receives it (and push notifications) like any message.

Options:
    --count 100         number of messages (default 100)
    --api URL           API base (default https://app.petnabor.com/api)
    --delay 0.15        seconds between messages (stay under rate limits)
    --prefix TEXT       message text prefix (default "Pagination test")
"""

import argparse
import getpass
import json
import sys
import time
import urllib.error
import urllib.request


def request(method, url, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as err:
        raw = err.read().decode()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, {"detail": raw[:300]}


def login(api, email, password):
    status, body = request(
        "POST", f"{api}/users/login/", body={"email_or_phone": email, "password": password}
    )
    token = (body.get("data") or {}).get("access_token") or body.get("access_token")
    if status != 200 or not token:
        sys.exit(f"Login failed ({status}): {body.get('message') or body}")
    return token


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--thread", required=True, help="Thread UUID")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--api", default="https://app.petnabor.com/api")
    parser.add_argument("--delay", type=float, default=0.15)
    parser.add_argument("--prefix", default="Pagination test")
    args = parser.parse_args()
    api = args.api.rstrip("/")

    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    token = login(api, email, password)
    del password

    url = f"{api}/messaging/threads/{args.thread}/messages/"
    width = len(str(args.count))
    sent = 0
    for i in range(1, args.count + 1):
        text = f"{args.prefix} #{i:0{width}d} of {args.count}"
        status, body = request(
            "POST", url, token=token, body={"message_type": "TEXT", "text_content": text}
        )
        if status == 429:
            wait = 5
            print(f"  rate limited, waiting {wait}s…")
            time.sleep(wait)
            status, body = request(
                "POST", url, token=token, body={"message_type": "TEXT", "text_content": text}
            )
        if status not in (200, 201):
            sys.exit(f"Stopped at #{i} ({status}): {body}")
        sent += 1
        if i % 10 == 0 or i == args.count:
            print(f"  sent {i}/{args.count}")
        time.sleep(args.delay)

    print(f"Done: {sent} messages sent to thread {args.thread}.")


if __name__ == "__main__":
    main()
