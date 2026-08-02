"""Event -> holding edges: which position an event touches, and how."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from fli import storage
from fli.core.policy import load_policy

_DIRECTION_BY_CHANNEL = {
    "competitive_displacement": "threat",
    "compute_memory": "unclear",
    "energy_datacenter": "unclear",
    "data_economics": "unclear",
    "talent_movement": "unclear",
}

_SUPPLIERS = {"MU", "TSM", "IREN", "RDDT"}


def channel_for_event(text: str, quote: str, policy) -> tuple[str | None, str]:
    """(channel, provenance) where provenance is 'classifier' | 'lexicon' | ''.
    (channel, provenance) where provenance is 'classifier' | 'lexicon' | ''."""
    try:
        from fli.knowledge.channels import _key, _load_cache
        from fli.ops.llm import MODEL_FOR_TASK
        cache = _load_cache()
        v = cache.get(_key(quote, policy.version, MODEL_FOR_TASK["channel"]))
    except Exception:
        v = None
    if v is not None:
        ch = v.get("channel")
        return (None, "classifier") if ch in (None, "none") else (ch, "classifier")
    lex = policy.channel_for(text)
    return (lex, "lexicon") if lex else (None, "")


def direction_for(ticker: str, channel: str | None,
                  provenance: str = "classifier") -> str:
    """threat | tailwind | unclear."""
    if not channel or provenance != "classifier":
        return "unclear"
    implied = _DIRECTION_BY_CHANNEL.get(channel, "unclear")
    if implied == "unclear":
        return "unclear"
    if implied == "tailwind" and ticker not in _SUPPLIERS:
        return "unclear"
    if implied == "threat" and ticker in _SUPPLIERS:
        return "unclear"
    return implied


def build(conn: sqlite3.Connection, rubric: str | None = None,
          verbose: bool = True) -> dict:
    """Populate event_positions for every scored event with a holding match."""
    policy = load_policy()
    if not policy.positions:
        raise SystemExit(
            "config/policy.yml has no `positions` block, so there are no "
            "holdings to link events to. Add one, or run the technical "
            "persona only.")

    rows = conn.execute(
        "SELECT i.id, i.claim, i.evidence_id, ev.verbatim_content quote"
        " FROM insights i JOIN evidence ev ON ev.id = i.evidence_id"
        " WHERE i.score IS NOT NULL").fetchall()

    conn.execute("DELETE FROM event_positions WHERE policy_version = ?",
                 (policy.version,))
    ts = storage.now_utc()
    made = 0
    by_ticker: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    by_prov: dict[str, int] = {}
    for r in rows:
        text = f"{r['claim']}\n{r['quote']}"
        tickers = policy.positions_for(text)
        if not tickers:
            continue
        channel, prov = channel_for_event(text, r["quote"], policy)
        for t in tickers:
            d = direction_for(t, channel, prov)
            holding = next(h for h in policy.positions if h.ticker == t)
            if channel is None and prov == "classifier":
                how = ("The classifier examined this text and found no "
                       "transmission channel. Exposure without a mechanism — "
                       "a candidate to watch, not a signal to act on.")
            elif prov == "classifier":
                how = (f"Reaches the position via {channel} "
                       f"(mechanism established by the channel classifier).")
            elif prov == "lexicon":
                how = (f"Keyword match on the {channel} vocabulary only. "
                       f"Topical, not a mechanism — direction withheld.")
            else:
                how = ("Exposure only: the text touches this holding's "
                       "vocabulary but no transmission channel was found.")
            rationale = f"{holding.name} ({t}) — {holding.thesis}. {how}"
            by_prov[prov or "none"] = by_prov.get(prov or "none", 0) + 1
            conn.execute(
                "INSERT OR REPLACE INTO event_positions (event_id, ticker,"
                " direction, channel, rationale, evidence_id, policy_version,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (r["id"], t, d, channel, rationale, r["evidence_id"],
                 policy.version, ts))
            made += 1
            by_ticker[t] = by_ticker.get(t, 0) + 1
            by_direction[d] = by_direction.get(d, 0) + 1
    conn.commit()

    if verbose:
        print(f"event_positions — {made} edge(s) over {len(rows)} scored events"
              f"  (policy v{policy.version}, holdings as of "
              f"{policy.positions_as_of})")
        print(f"  by direction: {by_direction}")
        print(f"  channel provenance: {by_prov}")
        if by_prov.get("lexicon", 0):
            print(f"  NOTE {by_prov['lexicon']} edge(s) fell back to the keyword "
                  f"lexicon because no classifier verdict exists for them yet.\n"
                  f"       Their direction is withheld. `python3 -m fli.cli "
                  f"channels` covers the gap.")
        for t, n in sorted(by_ticker.items(), key=lambda kv: -kv[1]):
            h = next(x for x in policy.positions if x.ticker == t)
            print(f"    {t:<6}{h.name:<16}{n:>4}")
        unclear = by_direction.get("unclear", 0)
        if made:
            print(f"  {unclear / made:.0%} are `unclear` — exposure without an "
                  f"established mechanism is the honest default, not a gap.")
    return {"edges": made, "by_ticker": by_ticker, "by_direction": by_direction}


def top_for_ticker(conn, ticker: str, k: int = 5) -> list[dict]:
    """The highest-scoring events touching one holding, for the digest."""
    return [dict(r) for r in conn.execute(
        "SELECT ep.ticker, ep.direction, ep.channel, ep.rationale,"
        " i.claim, i.score, ev.verbatim_content quote, d.url, d.published_at"
        " FROM event_positions ep"
        " JOIN insights i ON i.id = ep.event_id"
        " JOIN evidence ev ON ev.id = ep.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " WHERE ep.ticker = ? ORDER BY i.score DESC LIMIT ?", (ticker, k))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    build(conn)


if __name__ == "__main__":
    main()
