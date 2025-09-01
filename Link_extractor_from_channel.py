#!/usr/bin/env python3
import os, re, json, asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import ChannelInvalidError, ChannelPrivateError, UsernameNotOccupiedError
from telethon.tl.types import Message, MessageEntityUrl, MessageEntityTextUrl
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# --- URL helpers ---
def normalize_url(url: str) -> str:
    """Lowercase scheme/host, drop fragments and utm_* params."""
    p = urlparse(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not k.lower().startswith("utm_")]
    cleaned = p._replace(
        fragment="",
        query=urlencode(q, doseq=True),
        scheme=p.scheme.lower(),
        netloc=p.netloc.lower(),
    )
    return urlunparse(cleaned)

# --- link extraction (entities + regex fallback) ---
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)
def extract_links(msg: Message) -> List[str]:
    urls: List[str] = []

    # Entities
    ents = msg.entities or []
    for e in ents:
        if isinstance(e, MessageEntityUrl):
            text = msg.message or ""
            urls.append(text[e.offset : e.offset + e.length])
        elif isinstance(e, MessageEntityTextUrl) and getattr(e, "url", None):
            urls.append(e.url)

    # Fallback regex over text/caption
    blob = msg.message or ""
    for u in _URL_RE.findall(blob):
        urls.append(u.strip().rstrip(".,);]"))

    # Dedup while preserving order within the message
    seen, out = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u); out.append(u)
    return out

# --- output path helper ---
def default_output_path(channel_hint: str, hours: int) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", channel_hint.strip())
    out_dir = Path("data/exports"); out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{safe}_links_last{hours}h_{ts}.json"

async def main():
    load_dotenv()
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if not api_id or not api_hash:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

    session_path = os.getenv("TELEGRAM_SESSION_PATH", ".sessions/job_finder.session")
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    default_hours = int(os.getenv("LOOKBACK_HOURS", "12"))

    # --- interactive inputs ---
    channel_input = input("Channel (@username or -100... or https://t.me/...): ").strip()
    if not channel_input:
        raise SystemExit("No channel provided.")
    hours_str = input(f"Look back how many hours? [default {default_hours}]: ").strip()
    hours = default_hours if hours_str == "" else int(hours_str)

    # Accept @username, bare username, -100..., or https://t.me/<username>
    chan = channel_input
    m = re.match(r"^https?://t\.me/([^/]+)", chan, flags=re.I)
    if m:
        uname = m.group(1)
        if not uname.startswith("@"):  # treat as username
            chan = "@" + uname
    elif chan and not chan.startswith(("@", "-")) and chan.isalnum():
        chan = "@" + chan

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out_path = default_output_path(channel_input, hours)

    async with TelegramClient(session_path, api_id, api_hash) as client:
        # First run will prompt for phone & login code
        try:
            entity = await client.get_entity(chan)
        except UsernameNotOccupiedError:
            raise SystemExit(f"Username not found: {chan}")
        except ChannelPrivateError:
            raise SystemExit(f"Channel is private and you are not a member: {chan}")
        except (ChannelInvalidError, ValueError) as e:
            raise SystemExit(f"Channel not found/invalid: {chan} ({e})")

        # Collect links newest -> older until cutoff
        links_ordered: List[str] = []
        seen = set()
        async for msg in client.iter_messages(entity, limit=None):
            if msg.date and msg.date < cutoff:
                break
            for u in extract_links(msg):
                nu = normalize_url(u)
                if nu not in seen:
                    seen.add(nu)
                    links_ordered.append(nu)

    # Write single JSON file (array of URLs)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(links_ordered, f, ensure_ascii=False, indent=2)

    print(f"\nFound {len(links_ordered)} unique links in last {hours}h.")
    print(f"Wrote: {out_path}")

if __name__ == "__main__":
    asyncio.run(main())
