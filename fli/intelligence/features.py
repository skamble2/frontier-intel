"""Build insight_features — the numeric surface models train on.

score_components (JSON on insights) stays the reader-facing explanation; models
never parse JSON — they read these numeric rows. Every feature is derivable from
the current schema (no new data), and every insight gets the SAME fixed feature
set (one-hots are 0/1), so there are no NULLs. Absent inputs get an explicit,
documented neutral value.

Lab identity is NEVER a feature: no is_openai. A DeepMind release and an
OpenAI release compete on merits.

Pure function of the DB except `recency`, which decays from the wall clock at
compute time — re-running on the same day reproduces identical values; across
days only recency moves (by design).

Run:  python -m fli.cli features
"""
from __future__ import annotations

import argparse
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from fli import storage
from fli.knowledge.extraction import EVENT_TYPES
from fli.core.config import NEUTRAL_RECENCY, RECENCY_SCALE_DAYS

SOURCE_TYPES = ["blog", "newsroom", "arxiv", "github"]


def _recency(published_at: str | None, now: datetime) -> float:
    if not published_at:
        return NEUTRAL_RECENCY
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return NEUTRAL_RECENCY
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    days = (now - pub).days
    return math.exp(-max(0, days) / RECENCY_SCALE_DAYS)


def _specificity(quote: str) -> float:
    """Concreteness proxy: numeric/version tokens plus $ and % signs.
    '$5 per million tokens' scores high; 'frontier intelligence' scores 0."""
    return float(len(re.findall(r"\d[\d.,]*", quote)) + quote.count("$") + quote.count("%"))


def compute_features(conn: sqlite3.Connection) -> dict:
    now = datetime.now(timezone.utc)
    cluster_size = {r["cluster_id"]: r["n"] for r in conn.execute(
        "SELECT cluster_id, count(*) n FROM insights GROUP BY cluster_id")}
    # contributor_lab_depth: registered layer-below people per lab — the honest
    # lab-level stand-in for a per-person feature (person attribution is 1/406).
    lab_depth = {r["lab_id"]: r["n"] for r in conn.execute(
        "SELECT a.lab_id, count(DISTINCT a.person_id) n FROM affiliations a"
        " JOIN people p ON p.id=a.person_id WHERE a.lab_id IS NOT NULL"
        " AND p.discovered_via IN ('coauthor_expansion','auto_approved')"
        " GROUP BY a.lab_id")}

    # mechanism_channel: 1.0 if the mechanism classifier assigned the quote a
    # transmission channel, 0.0 otherwise (including quotes it has never seen
    # — an unclassified quote has not demonstrated a mechanism). This is the
    # investment rubric's rule 1 ("channel over no channel") given a column to
    # land in: the f16 slate review showed the score rewarding official-channel
    # engineering posts and vendor case studies the reader cuts, because the
    # judges encode the rule in labels but no feature could express it. Read
    # from the committed verdict cache only — the feature builder stays free,
    # offline and deterministic; `python -m fli.cli channels` is the paid step
    # that fills the cache.
    from fli.knowledge.channels import cached_verdicts
    all_quotes = [r[0] for r in conn.execute(
        "SELECT ev.verbatim_content FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id")]
    mech = {t: v for t, v in cached_verdicts(all_quotes).items()
            if v["channel"] != "none"}

    # basis via a scalar subquery, not a LEFT JOIN: event_entities is 0..N per
    # event, so a join fans one insight into several feature-row inserts the
    # moment an event carries two lab entities. First entity row (lowest id)
    # is the deterministic representative.
    rows = conn.execute(
        "SELECT i.id, i.event_type, i.cluster_id, i.attributed_lab_id,"
        " d.published_at, d.source_type, s.channel,"
        " ev.verbatim_content AS quote,"
        " (SELECT ee.basis FROM event_entities ee"
        "   WHERE ee.event_id = i.id AND ee.entity_kind = 'lab'"
        "   ORDER BY ee.id LIMIT 1) AS basis"
        " FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id"
        " JOIN raw_documents d ON d.id = ev.document_id"
        " JOIN sources s ON s.id = d.source_id"
    ).fetchall()

    conn.execute("DELETE FROM insight_features")
    ts = storage.now_utc()
    n_feat = 0
    for r in rows:
        quote = r["quote"] or ""
        feats = {
            "recency": _recency(r["published_at"], now),
            "corroboration": float(cluster_size.get(r["cluster_id"], 1)),
            "channel_official": 1.0 if r["channel"] == "official" else 0.0,
            # attribution tiering made numeric ONLY here, at model input
            "attribution_confidence": 1.0 if r["basis"] == "model_asserted" else 0.5,
            "specificity": _specificity(quote),
            "quote_len_words": float(len(quote.split())),
            "contributor_lab_depth": float(lab_depth.get(r["attributed_lab_id"], 0)),
            "mechanism_channel": 1.0 if quote in mech else 0.0,
        }
        for st in SOURCE_TYPES:
            feats[f"source_type_{st}"] = 1.0 if r["source_type"] == st else 0.0
        for et in EVENT_TYPES:
            feats[f"event_type_{et}"] = 1.0 if r["event_type"] == et else 0.0
        n_feat = len(feats)
        for feature, value in feats.items():
            conn.execute(
                "INSERT INTO insight_features (event_id, feature, value, computed_at)"
                " VALUES (?,?,?,?)", (r["id"], feature, value, ts))
    conn.commit()
    return {"insights": len(rows), "features_per_insight": n_feat}


def feature_matrix(conn):
    """(event_ids, feature_names, matrix) — the dense matrix models train on.
    Rows are insights ordered by id; columns are features in stable name order."""
    names = [r["feature"] for r in conn.execute(
        "SELECT DISTINCT feature FROM insight_features ORDER BY feature")]
    ids = [r["id"] for r in conn.execute("SELECT id FROM insights ORDER BY id")]
    idx = {f: j for j, f in enumerate(names)}
    X = np.zeros((len(ids), len(names)))
    row_of = {iid: i for i, iid in enumerate(ids)}
    for r in conn.execute("SELECT event_id, feature, value FROM insight_features"):
        X[row_of[r["event_id"]], idx[r["feature"]]] = r["value"]
    return ids, names, X


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(storage.DEFAULT_DB))
    args = ap.parse_args()
    conn = storage.connect(Path(args.db))
    storage.init_db(conn)
    stats = compute_features(conn)
    print(f"features: {stats['features_per_insight']} per insight"
          f" x {stats['insights']} insights"
          f" = {stats['features_per_insight'] * stats['insights']} rows")


if __name__ == "__main__":
    main()
