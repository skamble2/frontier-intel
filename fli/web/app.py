"""Read-only web surface over the existing database.

Browse the register, see scored insights and why they were flagged, read past
digests, and view the tracked-universe config. Deliberately thin: every list
it renders comes from the same calls the CLI uses (`scoring.top_events` for
the slate), so the web view can never disagree with the digest.

No writes. Configuration changes stay YAML edits (config/register_seeds.yml),
which is the intended workflow — the page links to the file it mirrors.

Run:  python -m fli.cli web            # http://127.0.0.1:5000
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from fli import storage
from fli.core.paths import ROOT, SEEDS_PATH

DIGEST_DIR = ROOT / "docs" / "digests"

_STYLE = """
body { font-family: -apple-system, Segoe UI, sans-serif; margin: 2rem auto;
       max-width: 60rem; color: #1a1a1a; line-height: 1.5; padding: 0 1rem; }
nav a { margin-right: 1.2rem; font-weight: 600; text-decoration: none; color: #0a4d8c; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.35rem 0.6rem; border-bottom: 1px solid #e3e3e3;
         vertical-align: top; }
th { background: #f5f7fa; }
.score { font-variant-numeric: tabular-nums; white-space: nowrap; }
.tag { background: #eef3f8; border-radius: 3px; padding: 0.05rem 0.4rem;
       font-size: 0.8rem; white-space: nowrap; }
.quote { color: #555; font-size: 0.85rem; font-style: italic; }
.comp { font-size: 0.8rem; color: #666; }
pre.report { background: #fafafa; border: 1px solid #e3e3e3; padding: 1rem;
             white-space: pre-wrap; font-size: 0.85rem; }
.muted { color: #777; font-size: 0.85rem; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
"""


def _page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)} — Frontier Lab Intelligence</title>"
            f"<style>{_STYLE}</style></head><body>"
            f"<nav><a href='/'>Overview</a><a href='/register'>Register</a>"
            f"<a href='/insights'>Insights</a><a href='/reports'>Reports</a>"
            f"<a href='/config'>Tracked universe</a></nav>"
            f"<h1>{html.escape(title)}</h1>{body}</body></html>")


def _e(v) -> str:
    return html.escape(str(v if v is not None else ""))


def _components(raw: str | None) -> str:
    """insights.score_components JSON -> one compact 'why flagged' line."""
    if not raw:
        return ""
    try:
        comps = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(comps, dict):
        return ""
    parts = []
    for k, v in comps.items():
        if k == "winner":
            continue
        if k == "top_factors" and isinstance(v, list):
            parts.extend(f"{_e(f.get('feature'))} {f.get('contribution', 0):+.2f}"
                         for f in v if isinstance(f, dict))
        elif isinstance(v, (int, float)):
            parts.append(f"{_e(k)}={v:g}")
        else:
            parts.append(f"{_e(k)}={_e(v)}")
    return " · ".join(parts)


def create_app():
    from flask import Flask, abort

    app = Flask(__name__)

    def db():
        return storage.connect()

    @app.get("/")
    def overview():
        conn = db()
        counts = {label: conn.execute(q).fetchone()[0] for label, q in [
            ("Tracked labs", "SELECT count(*) FROM labs"),
            ("Tracked people", "SELECT count(*) FROM people"),
            ("Resolved identities", "SELECT count(*) FROM identities"),
            ("Documents ingested", "SELECT count(*) FROM raw_documents"),
            ("Insights extracted", "SELECT count(*) FROM insights"),
            ("Insights scored", "SELECT count(*) FROM insights WHERE score IS NOT NULL"),
            ("Pairwise labels", "SELECT count(*) FROM pairwise_labels"),
            ("Alerts raised", "SELECT count(*) FROM alerts"),
        ]}
        conn.close()
        rows = "".join(f"<tr><td>{_e(k)}</td><td class='score'>{v}</td></tr>"
                       for k, v in counts.items())
        reports = sorted(DIGEST_DIR.glob("*.md"), reverse=True)[:2]
        latest = "".join(f"<li><a href='/reports/{_e(p.stem)}'>{_e(p.stem)}</a></li>"
                         for p in reports)
        return _page("Overview",
                     f"<table>{rows}</table>"
                     f"<h2>Latest reports</h2><ul>{latest or '<li>none yet</li>'}</ul>")

    @app.get("/register")
    def register():
        conn = db()
        labs = conn.execute(
            "SELECT l.id, l.name, l.parent_ticker,"
            " (SELECT count(*) FROM affiliations a WHERE a.lab_id = l.id) n_obs,"
            " (SELECT count(DISTINCT a.person_id) FROM affiliations a"
            "   WHERE a.lab_id = l.id) n_people"
            " FROM labs l ORDER BY l.name").fetchall()
        lab_rows = "".join(
            f"<tr><td>{_e(r['name'])}</td>"
            f"<td>{_e(r['parent_ticker'] or 'private')}</td>"
            f"<td class='score'>{r['n_people']}</td>"
            f"<td class='score'>{r['n_obs']}</td></tr>" for r in labs)

        people = conn.execute(
            "SELECT p.id, p.canonical_name, p.seniority_tier, p.discovered_via,"
            " (SELECT group_concat(i.platform || ':' || i.handle, '  ')"
            "   FROM identities i WHERE i.person_id = p.id) idents,"
            " (SELECT l.name FROM affiliations a JOIN labs l ON l.id = a.lab_id"
            "   WHERE a.person_id = p.id ORDER BY a.observed_at DESC LIMIT 1) lab"
            " FROM people p ORDER BY p.canonical_name").fetchall()
        ppl_rows = "".join(
            f"<tr><td>{_e(r['canonical_name'])}</td><td>{_e(r['lab'])}</td>"
            f"<td><span class='tag'>{_e(r['seniority_tier'])}</span></td>"
            f"<td>{_e(r['discovered_via'])}</td>"
            f"<td class='muted'>{_e(r['idents'])}</td></tr>" for r in people)

        pending = conn.execute(
            "SELECT count(*) FROM person_candidates WHERE status='pending'"
        ).fetchone()[0]
        conn.close()
        return _page("Register",
            f"<h2>Labs ({len(labs)})</h2><table><tr><th>Lab</th><th>Ticker</th>"
            f"<th>People</th><th>Observations</th></tr>{lab_rows}</table>"
            f"<h2>People ({len(people)})</h2><table><tr><th>Name</th><th>Lab</th>"
            f"<th>Tier</th><th>Via</th><th>Identities</th></tr>{ppl_rows}</table>"
            f"<p class='muted'>{pending} discovered candidates pending review "
            f"(<code>python -m fli.cli register review</code>).</p>")

    @app.get("/insights")
    def insights():
        from fli.intelligence.scoring import primary_rubric, top_events
        conn = db()
        items, dropped = top_events(conn, k=25, rubric=primary_rubric())
        conn.close()
        rows = "".join(
            f"<tr><td class='score'>{r['score']:.3f}</td>"
            f"<td><a href='/insights/{r['id']}'>{_e(r['claim'])}</a>"
            f"<div class='quote'>&ldquo;{_e((r['quote'] or '')[:220])}&rdquo;</div>"
            f"<div class='comp'>{_components(r['score_components'])}</div></td>"
            f"<td><span class='tag'>{_e(r['event_type'])}</span></td>"
            f"<td>{_e(r['lab'])}</td>"
            f"<td class='muted'>{_e((r['published_at'] or 'undated')[:10])}<br>"
            f"<a href='{_e(r['url'])}'>source</a></td></tr>" for r in items)
        drops = " · ".join(f"{_e(k)}: {v}" for k, v in dropped.items())
        return _page("Scored insights",
            f"<p class='muted'>Slate from the winning model on the primary rubric — "
            f"the same call the digest renders. Slate filter dropped: "
            f"{drops or 'nothing'}.</p>"
            f"<table><tr><th>Score</th><th>Claim / why flagged</th><th>Type</th>"
            f"<th>Lab</th><th>Source</th></tr>{rows}</table>")

    @app.get("/insights/<int:event_id>")
    def insight_detail(event_id: int):
        conn = db()
        r = conn.execute(
            "SELECT i.*, ev.verbatim_content quote, ev.verification,"
            " d.url, d.published_at, d.source_type,"
            " COALESCE(l.name,'(unattributed)') lab"
            " FROM insights i JOIN evidence ev ON ev.id = i.evidence_id"
            " JOIN raw_documents d ON d.id = ev.document_id"
            " LEFT JOIN labs l ON l.id = i.attributed_lab_id"
            " WHERE i.id = ?", (event_id,)).fetchone()
        if r is None:
            conn.close()
            abort(404)
        hyps = conn.execute("SELECT * FROM hypotheses WHERE insight_id = ?",
                            (event_id,)).fetchall()
        pos = conn.execute(
            "SELECT ticker, direction, channel, rationale FROM event_positions"
            " WHERE event_id = ? ORDER BY ticker", (event_id,)).fetchall()
        conn.close()

        hyp_html = "".join(
            f"<h2>{_e(h['persona'])} reading</h2><p>{_e(h['hypothesis'])}</p>"
            f"<p class='muted'>direction: {_e(h['direction'])} · "
            f"confidence: {_e(h['confidence'])} · horizon: {_e(h['time_horizon'])}"
            f"{' · tickers: ' + _e(h['tickers']) if h['tickers'] else ''}</p>"
            f"<p class='comp'>{_e(h['reasoning'])}</p>" for h in hyps)
        pos_rows = "".join(
            f"<tr><td>{_e(p['ticker'])}</td><td>{_e(p['direction'])}</td>"
            f"<td>{_e(p['channel'])}</td><td class='muted'>{_e(p['rationale'])}</td></tr>"
            for p in pos)
        pos_html = (f"<h2>Position exposure</h2><table><tr><th>Ticker</th>"
                    f"<th>Direction</th><th>Channel</th><th>Rationale</th></tr>"
                    f"{pos_rows}</table>") if pos_rows else ""
        return _page(f"Insight #{event_id}",
            f"<p><strong>{_e(r['claim'])}</strong></p>"
            f"<p><span class='tag'>{_e(r['event_type'])}</span> · {_e(r['lab'])} · "
            f"{_e((r['published_at'] or 'undated')[:10])} · "
            f"<a href='{_e(r['url'])}'>primary source</a> · "
            f"verification: {_e(r['verification'])}</p>"
            f"<blockquote class='quote'>{_e(r['quote'])}</blockquote>"
            f"<p class='comp'>score {r['score'] if r['score'] is not None else '—'}"
            f" · {_components(r['score_components'])}</p>"
            f"{hyp_html}{pos_html}")

    @app.get("/reports")
    def reports():
        files = sorted(DIGEST_DIR.glob("*.md"), reverse=True)
        items = "".join(f"<li><a href='/reports/{_e(p.stem)}'>{_e(p.stem)}</a></li>"
                        for p in files)
        return _page("Reports", f"<ul>{items or '<li>none yet — run '
                     '<code>python -m fli.cli digest --all</code></li>'}</ul>")

    @app.get("/reports/<name>")
    def report(name: str):
        path = DIGEST_DIR / f"{Path(name).name}.md"
        if not path.is_file():
            abort(404)
        return _page(path.stem,
                     f"<pre class='report'>{_e(path.read_text(encoding='utf-8'))}</pre>")

    @app.get("/config")
    def config():
        seeds = SEEDS_PATH.read_text(encoding="utf-8")
        return _page("Tracked universe",
            f"<p class='muted'>The tracked universe lives in "
            f"<code>config/register_seeds.yml</code> — editing it is a config "
            f"change, not a code change. This page mirrors the file; seeding "
            f"re-verifies every entry against live pages before writing.</p>"
            f"<pre class='report'>{_e(seeds)}</pre>")

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="read-only web UI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    create_app().run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
