"""X (Twitter) as an ingestion source."""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from pathlib import Path

from fli import storage
from fli.core.config import (X_MAX_POSTS_PER_ACCOUNT, X_MAX_POSTS_PER_RUN,
                             X_POST_COST_USD, X_RUN_BUDGET_USD, X_USER_COST_USD)
from fli.core.http import FetchError, http_get

API = "https://api.x.com/2"

LAB_ACCOUNTS = {
    "OpenAI": "OpenAI",
    "AnthropicAI": "Anthropic",
    "GoogleDeepMind": "Google DeepMind",
    "MistralAI": "Mistral",
    "deepseek_ai": "DeepSeek",
    "Alibaba_Qwen": "Qwen",
    "AIatMeta": "Meta AI",
    "xai": "xAI",
}


def bearer_token() -> str | None:
    """Token from the environment or .env."""
    from fli.ops.llm import load_dotenv
    load_dotenv()
    return os.environ.get("X_BEARER_TOKEN")


class XClient:
    """Thin API client that counts what it spends."""

    def __init__(self, token: str):
        self._headers = {"Authorization": f"Bearer {token}"}
        self.posts_read = 0
        self.users_read = 0

    @property
    def spend_usd(self) -> float:
        return (self.posts_read * X_POST_COST_USD
                + self.users_read * X_USER_COST_USD)

    def _get(self, path: str, params: dict) -> dict:
        url = f"{API}{path}?{urllib.parse.urlencode(params)}"
        body, _ = http_get(url, headers=self._headers)
        return json.loads(body)

    def user_id(self, handle: str) -> tuple[str, str] | None:
        """Resolve a handle to (id, name). Billed as one User: Read."""
        data = self._get(f"/users/by/username/{handle}", {"user.fields": "name"})
        self.users_read += 1
        u = data.get("data")
        return (u["id"], u.get("name", handle)) if u else None

    def user_profile(self, handle: str) -> dict | None:
        """Full profile: id, name, bio, verified. """
        data = self._get(f"/users/by/username/{handle}", {
            "user.fields": "name,description,verified,public_metrics,url"})
        self.users_read += 1
        return data.get("data")

    def recent_posts(self, user_id: str, limit: int) -> list[dict]:
        """Most recent original posts. """
        data = self._get(f"/users/{user_id}/tweets", {
            "max_results": max(5, min(limit, 100)),
            "exclude": "retweets,replies",
            "tweet.fields": "created_at,public_metrics,entities",
        })
        posts = data.get("data", []) or []
        self.posts_read += len(posts)
        return posts[:limit]


def _budget_guard(n_accounts: int, per_account: int) -> float:
    """Refuse to start a run whose worst case exceeds the budget."""
    worst = (n_accounts * X_USER_COST_USD
             + min(n_accounts * per_account, X_MAX_POSTS_PER_RUN) * X_POST_COST_USD)
    if worst > X_RUN_BUDGET_USD:
        raise SystemExit(
            f"projected worst case ${worst:.2f} exceeds X_RUN_BUDGET_USD "
            f"${X_RUN_BUDGET_USD:.2f}. Lower X_MAX_POSTS_PER_RUN or raise the "
            f"budget in fli/core/config.py — deliberately, not by accident.")
    return worst


def as_document(handle: str, post: dict) -> tuple[str, str]:
    """(url, body). """
    url = f"https://x.com/{handle}/status/{post['id']}"
    return url, f"@{handle}\n{url}\n\n{post['text']}"


def accounts_to_track(conn) -> list[tuple[str, str | None, str]]:
    """(handle, lab_name_or_None, channel)."""
    out = [(h, lab, "official") for h, lab in LAB_ACCOUNTS.items()]
    for r in conn.execute(
            "SELECT handle FROM identities WHERE platform='x' ORDER BY handle"):
        out.append((r["handle"].lstrip("@"), None, "third_party"))
    return out


def ingest(conn, dry_run: bool = False, per_account: int = X_MAX_POSTS_PER_ACCOUNT):
    accounts = accounts_to_track(conn)
    worst = _budget_guard(len(accounts), per_account)

    print(f"X ingest — {len(accounts)} account(s), <= {per_account} posts each")
    print(f"  rates: post ${X_POST_COST_USD}/read, user ${X_USER_COST_USD}/read")
    print(f"  worst case this run: ${worst:.2f}  (cap {X_MAX_POSTS_PER_RUN} posts)")
    print("  note: X dedupes resources within a 24h UTC window, so a re-run "
          "today re-reads already-seen posts for free.\n")
    if dry_run:
        print("DRY RUN — no requests made, nothing spent.")
        return {"dry_run": True, "worst_case_usd": worst, "accounts": len(accounts)}

    token = bearer_token()
    if not token:
        raise SystemExit(
            "X_BEARER_TOKEN not set. Add it to .env:\n"
            "    X_BEARER_TOKEN=AAAA...\n"
            "Get it from console.x.com -> your app -> Keys and tokens.")

    client = XClient(token)
    stored = new_docs = 0
    for handle, lab_name, channel in accounts:
        if client.posts_read >= X_MAX_POSTS_PER_RUN:
            print(f"  [cap] {X_MAX_POSTS_PER_RUN} posts read; stopping early.")
            break
        lab_id = None
        if lab_name:
            row = conn.execute("SELECT id FROM labs WHERE name=?", (lab_name,)).fetchone()
            lab_id = row["id"] if row else None
        sid = storage.upsert_source(conn, "social", f"@{handle}",
                                    f"https://x.com/{handle}", lab_id, channel)
        try:
            resolved = client.user_id(handle)
            if not resolved:
                storage.log_fetch(conn, sid, "error", 0, "no such user")
                continue
            uid, _name = resolved
            budget_left = X_MAX_POSTS_PER_RUN - client.posts_read
            posts = client.recent_posts(uid, min(per_account, budget_left))
        except FetchError as e:
            storage.log_fetch(conn, sid, "error", 0, str(e)[:200])
            print(f"  {handle:<18} FETCH ERROR {e}")
            continue

        added = 0
        for p in posts:
            url, body = as_document(handle, p)
            _doc, is_new = storage.store_document(conn, sid, "social", url, body,
                                                  p.get("created_at"))
            stored += 1
            added += is_new
        new_docs += added
        storage.log_fetch(conn, sid, "ok" if posts else "empty", len(posts),
                          f"{added} new; spend so far ${client.spend_usd:.3f}")
        print(f"  {handle:<18} {len(posts):>3} posts, {added:>3} new")

    print(f"\ncost ledger: {client.posts_read} posts x ${X_POST_COST_USD}"
          f" + {client.users_read} users x ${X_USER_COST_USD}"
          f" = ${client.spend_usd:.3f}")
    print(f"documents: {stored} seen, {new_docs} new")
    return {"posts_read": client.posts_read, "users_read": client.users_read,
            "spend_usd": round(client.spend_usd, 4), "new_documents": new_docs}


def main() -> None:
    ap = argparse.ArgumentParser(description="Ingest posts from X. Costs money.")
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true",
                    help="print the cost estimate and exit without spending")
    ap.add_argument("--per-account", type=int, default=X_MAX_POSTS_PER_ACCOUNT)
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    ingest(conn, dry_run=args.dry_run, per_account=args.per_account)


if __name__ == "__main__":
    main()
