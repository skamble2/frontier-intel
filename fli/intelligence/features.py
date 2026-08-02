"""Build insight_features — the numeric surface models train on."""
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
    """Concreteness proxy: numeric/version tokens plus $ and % signs. """
    return float(len(re.findall(r"\d[\d.,]*", quote)) + quote.count("$") + quote.count("%"))


def compute_features(conn: sqlite3.Connection) -> dict:
    now = datetime.now(timezone.utc)
    cluster_size = {r["cluster_id"]: r["n"] for r in conn.execute(
        "SELECT cluster_id, count(*) n FROM insights GROUP BY cluster_id")}
    lab_depth = {r["lab_id"]: r["n"] for r in conn.execute(
        "SELECT a.lab_id, count(DISTINCT a.person_id) n FROM affiliations a"
        " JOIN people p ON p.id=a.person_id WHERE a.lab_id IS NOT NULL"
        " AND p.discovered_via IN ('coauthor_expansion','auto_approved')"
        " GROUP BY a.lab_id")}

    from fli.knowledge.channels import cached_verdicts
    all_quotes = [r[0] for r in conn.execute(
        "SELECT ev.verbatim_content FROM insights i"
        " JOIN evidence ev ON ev.id = i.evidence_id")]
    mech = {t: v for t, v in cached_verdicts(all_quotes).items()
            if v["channel"] != "none"}

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
    (event_ids, feature_names, matrix) — the dense matrix models train on."""
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
