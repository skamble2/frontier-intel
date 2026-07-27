-- Frontier Lab Intelligence — schema
--
-- Invariants live here, not in prompts:
--   every insight references evidence (NOT NULL FK)
--   raw documents are immutable and hash-deduped
--   affiliations are append-only dated observations
--   every fetch attempt is logged, including empty and failed ones

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY,
    source_type   TEXT NOT NULL CHECK (source_type IN ('blog','newsroom','arxiv','github','social')),
    lab_id        INTEGER REFERENCES labs(id),
    name          TEXT NOT NULL,
    url           TEXT NOT NULL UNIQUE,
    -- who operates the channel; numeric reliability weights are learned, not configured
    channel       TEXT NOT NULL DEFAULT 'official' CHECK (channel IN ('official','third_party')),
    -- 'content' feeds the insight pipeline; 'register' documents exist as
    -- entity evidence (lab pages, author queries) and are never extracted
    purpose       TEXT NOT NULL DEFAULT 'content' CHECK (purpose IN ('content','register')),
    active        BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS raw_documents (
    id            INTEGER PRIMARY KEY,
    source_type   TEXT NOT NULL,
    source_id     INTEGER NOT NULL REFERENCES sources(id),
    url           TEXT NOT NULL,
    content_hash  TEXT NOT NULL UNIQUE,
    -- charset-honored decode of the fetched bytes, never modified after
    -- insert; the decode itself is recorded in fetch_log.detail
    raw_content   TEXT NOT NULL,
    published_at  TIMESTAMP,
    retrieved_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence (
    id                 INTEGER PRIMARY KEY,
    document_id        INTEGER NOT NULL REFERENCES raw_documents(id),
    locator            TEXT NOT NULL,     -- JSON; shape varies by source_type
    verbatim_content   TEXT NOT NULL,
    verification       TEXT NOT NULL CHECK (verification IN ('exact','fuzzy','structural')),
    verification_score REAL
);

CREATE TABLE IF NOT EXISTS labs (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    is_public_company BOOLEAN,
    parent_ticker     TEXT               -- NULL for private labs
);

CREATE TABLE IF NOT EXISTS people (
    id             INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    seniority_tier TEXT CHECK (seniority_tier IN ('founder','research_lead','senior_ic','ic')),
    discovered_via TEXT CHECK (discovered_via IN
        ('seed','coauthor_expansion','manual','auto_approved')),
    first_seen_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS identities (
    id                INTEGER PRIMARY KEY,
    person_id         INTEGER NOT NULL REFERENCES people(id),
    platform          TEXT NOT NULL CHECK (platform IN ('arxiv','github','x','lab_page')),
    handle            TEXT NOT NULL,
    -- how strong the link is, as an ordered fact about how it was made:
    -- verbatim > corroborated > manual_approved > name_match_only
    confidence_tier   TEXT NOT NULL CHECK (confidence_tier IN
        ('verbatim','corroborated','manual_approved','name_match_only')),
    resolution_method TEXT NOT NULL CHECK (resolution_method IN ('self_link','coauthor_overlap','manual','exact')),
    -- NOT NULL: an identity without evidence is exactly what this system is
    -- built to prevent, so the database refuses it rather than a check finding
    -- it afterwards.
    evidence_id       INTEGER NOT NULL REFERENCES evidence(id),
    UNIQUE (platform, handle)
);

-- Append-only: re-observing is how the register stays current. A person with
-- rows at two labs inside a window is a mobility event. lab_id NULL records
-- "person observed, no lab evidence".
CREATE TABLE IF NOT EXISTS affiliations (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    lab_id      INTEGER REFERENCES labs(id),
    role        TEXT,
    -- how the lab link was made: name verbatim on a lab page, or inferred
    -- from corroborated co-authorship with a seed at that lab. Distinct
    -- tiers, never silently equal.
    basis       TEXT NOT NULL DEFAULT 'page_verbatim'
                CHECK (basis IN ('page_verbatim','coauthor_inference')),
    observed_at TIMESTAMP NOT NULL,
    evidence_id INTEGER NOT NULL REFERENCES evidence(id)
);

-- Co-author expansion approval queue. Nothing moves into people without an
-- explicit human decision. UNIQUE(name) merges same-named people across
-- labs; accepted at this scale and caught at manual review.
CREATE TABLE IF NOT EXISTS person_candidates (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,       -- exactly as it appears on arXiv
    discovered_via  TEXT NOT NULL CHECK (discovered_via IN ('coauthor_expansion')),
    paper_count     INTEGER NOT NULL DEFAULT 1,
    entry_ids       TEXT NOT NULL DEFAULT '[]', -- counted arXiv entry ids (idempotency)
    seed_person_ids TEXT NOT NULL,              -- tracked people they co-authored with
    seed_lab_ids    TEXT NOT NULL DEFAULT '[]', -- labs of those seeds (§22 F2; per-lab slates)
    lab_hint        TEXT,                       -- set only via a lab collective author
    evidence_id     INTEGER NOT NULL REFERENCES evidence(id),
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    reviewed_at     TIMESTAMP,
    created_at      TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS insights (
    id                   INTEGER PRIMARY KEY,
    evidence_id          INTEGER NOT NULL REFERENCES evidence(id),
    attributed_person_id INTEGER REFERENCES people(id),
    attributed_lab_id    INTEGER REFERENCES labs(id),
    event_type           TEXT NOT NULL CHECK (event_type IN
        ('research','personnel','release','infrastructure','benchmark','open_source','commercial','other')),
    claim                TEXT NOT NULL,
    cluster_id           INTEGER,
    score                REAL,
    score_components     TEXT,            -- JSON; rendered as a decomposition, never a bare number
    created_at           TIMESTAMP NOT NULL
);

-- An event (a row in `insights`) has 0..N attributed entities, each a person OR
-- a lab, each independently cited. This is the multi-author / multi-lab model
-- the singular attributed_*_id columns on `insights` cannot express; those stay
-- as a denormalized primary-entity cache. entity_kind pins which FK is set (XOR
-- enforced below); role names how the entity relates to the event. Same citation
-- invariant as insights: attribution is never uncited (evidence_id NOT NULL).
CREATE TABLE IF NOT EXISTS event_entities (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES insights(id),
    entity_kind TEXT NOT NULL CHECK (entity_kind IN ('person','lab')),
    person_id   INTEGER REFERENCES people(id),
    lab_id      INTEGER REFERENCES labs(id),
    role        TEXT NOT NULL CHECK (role IN
        ('author','releaser','subject','mover_from','mover_to')),
    -- how the lab/person link was made (§20.7): 'model_asserted' = the extractor
    -- named it and resolve_lab matched; 'source_inferred' = defaulted from the
    -- publisher of an official channel. Kept apart so scoring downweights the
    -- inferred kind and never treats a publisher default as a verbatim fact (P3).
    basis       TEXT NOT NULL DEFAULT 'model_asserted'
                CHECK (basis IN ('model_asserted','source_inferred')),
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    -- exactly one of person_id / lab_id, matching entity_kind
    CHECK ((entity_kind = 'person' AND person_id IS NOT NULL AND lab_id IS NULL)
        OR (entity_kind = 'lab'    AND lab_id IS NOT NULL AND person_id IS NULL))
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id           INTEGER PRIMARY KEY,
    insight_id   INTEGER NOT NULL REFERENCES insights(id),
    persona      TEXT NOT NULL CHECK (persona IN ('investment','ai_team')),
    hypothesis   TEXT NOT NULL,
    tickers      TEXT,
    direction    TEXT,
    confidence   TEXT,
    time_horizon TEXT,
    reasoning    TEXT NOT NULL            -- always shown to the reader
);

CREATE TABLE IF NOT EXISTS rejections (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER REFERENCES raw_documents(id),
    stage       TEXT NOT NULL CHECK (stage IN ('stage1','stage2','verification')),
    reason      TEXT NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id),
    attempted_at TIMESTAMP NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('ok','empty','error','rate_limited')),
    items_found  INTEGER,
    detail       TEXT
);

-- Newest stored version of each URL. Re-fetched pages whose bytes changed
-- are new immutable rows; this view answers "which one is current".
CREATE VIEW IF NOT EXISTS latest_documents AS
SELECT d.* FROM raw_documents d
WHERE d.id = (SELECT max(d2.id) FROM raw_documents d2 WHERE d2.url = d.url);

CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY,
    task          TEXT NOT NULL,
    model         TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL,
    created_at    TIMESTAMP
);

-- Day 5 (§10 scoring, §24.2 A1). The model's training surface: one row per
-- (event, feature). score_components (JSON on insights) stays the reader-facing
-- explanation; a model never parses JSON — it reads these numeric rows.
CREATE TABLE IF NOT EXISTS insight_features (
    event_id    INTEGER NOT NULL REFERENCES insights(id),
    feature     TEXT NOT NULL,
    value       REAL NOT NULL,
    computed_at TIMESTAMP NOT NULL,
    PRIMARY KEY (event_id, feature)
);

-- Pairwise judgements: "which of these two would you rather see?" — more
-- reliable to elicit than absolute scores. Ranking becomes classification on
-- feature differences.
--
-- NOT treated as ground truth. Nobody on this project is qualified to say what
-- matters to a portfolio manager, so a judgement is recorded together with WHO
-- made it, and their reliability is estimated from disagreement rather than
-- assumed (docs/scoring-without-ground-truth.md).
CREATE TABLE IF NOT EXISTS pairwise_labels (
    id             INTEGER PRIMARY KEY,
    event_a        INTEGER NOT NULL REFERENCES insights(id),
    event_b        INTEGER NOT NULL REFERENCES insights(id),
    winner         TEXT NOT NULL CHECK (winner IN ('a','b','tie')),
    -- 'llm:<model>' | 'human:<name>' | 'lf:<function>'. Every source of a
    -- judgement is just another labeler, so one table holds them all.
    labeler        TEXT NOT NULL,
    -- which transmission channel decided it (config/policy.yml), and why in
    -- one line. Audited against docs/labeling-rubric.md, not decorative.
    thesis_channel TEXT,
    reason         TEXT,
    labeled_at     TIMESTAMP NOT NULL,
    -- The load-bearing detail: one pair may carry an LLM label, a human audit,
    -- and N labeling functions. Inter-labeler agreement is then a join, not a
    -- second table.
    UNIQUE (event_a, event_b, labeler)
);

-- The bake-off: every model scores the identical event set so comparison is
-- fair. Baselines, a hand-weighted sum (the red flag to beat), and learned models.
CREATE TABLE IF NOT EXISTS event_scores (
    id             INTEGER PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES insights(id),
    model          TEXT NOT NULL,
    score          REAL NOT NULL,
    rank           INTEGER,
    components     TEXT,
    -- Which config/policy.yml version produced this score. A ranking is only
    -- explicable if the editorial policy behind it is identifiable, so this is
    -- NOT NULL and is asserted by check C17.
    policy_version INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMP NOT NULL,
    UNIQUE (event_id, model)
);

-- The bridge from a frontier-lab event to a public-equity position — the thing
-- the brief calls "connecting lab developments to where they actually land for
-- a public-equity investor". Positions themselves are NOT stored: they live in
-- config/policy.yml, dated and sourced, because which holdings exist is a
-- business fact that changes quarterly, not system state.
--
-- Same citation invariant as everything else: an exposure claim is never
-- uncited. `channel` records the MECHANISM (from policy.yml channels) and is
-- nullable on purpose — an event can touch a holding without the text making
-- the mechanism clear, and recording that honestly is better than inventing one.
CREATE TABLE IF NOT EXISTS event_positions (
    id          INTEGER PRIMARY KEY,
    event_id    INTEGER NOT NULL REFERENCES insights(id),
    ticker      TEXT NOT NULL,              -- matches policy.yml positions.holdings
    direction   TEXT NOT NULL CHECK (direction IN ('threat','tailwind','unclear')),
    channel     TEXT,                       -- NULL = exposure found, mechanism not established
    rationale   TEXT NOT NULL,              -- one line a PM can check against the quote
    evidence_id INTEGER NOT NULL REFERENCES evidence(id),
    -- which policy version supplied the holdings list; a stale exposure claim
    -- must be identifiable as stale
    policy_version INTEGER NOT NULL,
    created_at  TIMESTAMP NOT NULL,
    UNIQUE (event_id, ticker)
);

-- Indexes last, so every table they reference exists. Only where a hot path
-- needs one, each named with the query it serves.
--   latest_documents runs a correlated subquery on url for every row, and the
--   dedup pass hits that view on every pipeline run.
CREATE INDEX IF NOT EXISTS ix_raw_documents_url      ON raw_documents(url);
--   joined per event when features are built and when scores are rendered.
CREATE INDEX IF NOT EXISTS ix_event_entities_event   ON event_entities(event_id);
CREATE INDEX IF NOT EXISTS ix_insight_features_event ON insight_features(event_id);
--   the rejection funnel groups by reason in docs/metrics.sql.
CREATE INDEX IF NOT EXISTS ix_rejections_reason      ON rejections(reason);
--   agreement between labelers is a self-join on the pair.
CREATE INDEX IF NOT EXISTS ix_pairwise_labels_pair   ON pairwise_labels(event_a, event_b);
--   the investment persona reads exposures per event and per ticker.
CREATE INDEX IF NOT EXISTS ix_event_positions_event  ON event_positions(event_id);
CREATE INDEX IF NOT EXISTS ix_event_positions_ticker ON event_positions(ticker);
