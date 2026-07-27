"""LAYER 1 — X (Twitter) as a source.

Why this source exists: labs and researchers announce things on X hours to days
before a blog post exists, and personnel moves ("excited to share I'm joining…")
often appear ONLY here. The corpus currently has 2 personnel events out of 406;
this is the channel most likely to change that.

PRICING (docs.x.com/x-api/getting-started/pricing, read 2026-07-26)
    pay-per-use, billed per RESOURCE RETURNED — not per request
        Posts: Read   $0.005 each
        User:  Read   $0.010 each
    Resources are deduplicated within a 24h UTC window, so re-running the same
    day costs nothing for posts already seen. No subscription, no minimum.

Every run therefore prints a cost ledger, and the caps in core/config.py are a
hard stop rather than a suggestion — see `_budget_guard`.

ATTRIBUTION RULE, and it matters:
    Lab accounts (@OpenAI, @AnthropicAI, …) are `channel='official'` WITH a
    lab_id — those are the lab speaking, so a source_inferred attribution is
    legitimate (check C12).
    Researcher accounts are `channel='third_party'` with lab_id NULL. A person
    tweeting is NOT their employer announcing. Without this, every personal
    post would be attributed to the lab as though it were official, which is
    exactly the kind of quiet over-claim C12 exists to prevent.

Run:  python3 -m fli.cli x --dry-run     # cost estimate, spends nothing
      python3 -m fli.cli x               # fetch, capped
"""
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

# Official lab accounts: the lab speaking for itself -> lab_id set.
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
    """Token from the environment or .env.

    BUG THIS FIXES: this read os.environ directly and never loaded .env, so
    `fli.cli x` reported "X_BEARER_TOKEN not set" while the token sat in .env
    the whole time. fli/ops/llm.py had always called load_dotenv() for the
    Anthropic key; this path never did.
    """
    from fli.ops.llm import load_dotenv
    load_dotenv()
    return os.environ.get("X_BEARER_TOKEN")


class XClient:
    """Thin API client that counts what it spends.

    Cost is derived from resources RETURNED, mirroring how X bills, so the
    ledger is an accounting of the same quantity the invoice will show.
    """

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
        body, _ = http_get(url, headers=self._headers)   # token never logged
        return json.loads(body)

    def user_id(self, handle: str) -> tuple[str, str] | None:
        """Resolve a handle to (id, name). Billed as one User: Read."""
        data = self._get(f"/users/by/username/{handle}", {"user.fields": "name"})
        self.users_read += 1
        u = data.get("data")
        return (u["id"], u.get("name", handle)) if u else None

    def user_profile(self, handle: str) -> dict | None:
        """Full profile: id, name, bio, verified. Billed as one User: Read.

        Separate from `user_id` on purpose. That method is called once per
        account on every ingest run and needs only the id, so widening it would
        pull a bio nobody reads on every run. This one is called once per
        candidate handle during register seeding, where the BIO IS THE POINT —
        a self-declared "Research scientist @AnthropicAI" is the evidence that
        makes the identity row admissible.
        """
        data = self._get(f"/users/by/username/{handle}", {
            "user.fields": "name,description,verified,public_metrics,url"})
        self.users_read += 1
        return data.get("data")

    def recent_posts(self, user_id: str, limit: int) -> list[dict]:
        """Most recent original posts. Retweets and replies are excluded: a
        retweet carries no new claim and would pollute the evidence base."""
        data = self._get(f"/users/{user_id}/tweets", {
            "max_results": max(5, min(limit, 100)),
            "exclude": "retweets,replies",
            "tweet.fields": "created_at,public_metrics,entities",
        })
        posts = data.get("data", []) or []
        self.posts_read += len(posts)
        return posts[:limit]


def _budget_guard(n_accounts: int, per_account: int) -> float:
    """Refuse to start a run whose worst case exceeds the budget.

    Checked BEFORE any call: a spend cap that only triggers mid-run has already
    spent the money it was meant to protect.
    """
    worst = (n_accounts * X_USER_COST_USD
             + min(n_accounts * per_account, X_MAX_POSTS_PER_RUN) * X_POST_COST_USD)
    if worst > X_RUN_BUDGET_USD:
        raise SystemExit(
            f"projected worst case ${worst:.2f} exceeds X_RUN_BUDGET_USD "
            f"${X_RUN_BUDGET_USD:.2f}. Lower X_MAX_POSTS_PER_RUN or raise the "
            f"budget in fli/core/config.py — deliberately, not by accident.")
    return worst


def as_document(handle: str, post: dict) -> tuple[str, str]:
    """(url, body). The body keeps the same header convention as every other
    source so readable_text and the extractor need no special case."""
    url = f"https://x.com/{handle}/status/{post['id']}"
    return url, f"@{handle}\n{url}\n\n{post['text']}"


def accounts_to_track(conn) -> list[tuple[str, str | None, str]]:
    """(handle, lab_name_or_None, channel).

    Lab accounts carry their lab. Researcher handles come from `identities`
    (platform='x') and deliberately carry no lab — see the module docstring.
    """
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
            # 429 is the expected failure mode; it is logged like any other
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
