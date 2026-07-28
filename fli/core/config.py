"""Every tunable knob in one module.

Rationale: anyone asking "what threshold produced this number?" should have
exactly one file to open. Values that are *protocol* constants (XML namespaces,
OpenInference span attribute names, API URLs) deliberately stay next to the code
that speaks that protocol - they are not tuning knobs.

Any value changed here changes behaviour, so each one carries how it was chosen.
"""

# --- ingestion (L1) -------------------------------------------------------
HTTP_TIMEOUT_S = 20
MAX_ENTRIES_PER_FEED = 25       # per-run politeness cap
MAX_SITEMAP_PAGES = 8
ARXIV_DELAY_S = 3               # arXiv API politeness (their published guidance)
# Below this, the feed served a teaser, so hydrate the body from the article
# page (measured: OpenAI/DeepMind blogs ~250-430 chars vs Meta's full 27k).
BLOG_BODY_MIN = 1500

# --- stage-1 filter (L2) --------------------------------------------------
# Per source type: arXiv abstracts are short but dense, GitHub releases are long
# but often boilerplate. Lowered twice from observed false rejections. A post is
# capped at 280 characters, so the 400-char fallback floor would reject every
# tweet as too_short — 'social' needs its own entry.
MIN_CHARS = {"blog": 100, "newsroom": 100, "github": 400, "arxiv": 150,
             "social": 60}
# A release tag is signal regardless of length (DeepSeek-V3 v1.0.0 at 164 chars
# was being rejected). Commit feeds keep the higher github floor.
RELEASE_FEED_MIN_CHARS = 50

# --- X / social (L1) ------------------------------------------------------
# Pay-per-use, billed per RESOURCE RETURNED. Rates read from
# docs.x.com/x-api/getting-started/pricing on 2026-07-26. Resources are
# deduplicated within a 24h UTC window, so re-running the same day is free.
X_POST_COST_USD = 0.005         # Posts: Read
X_USER_COST_USD = 0.010         # User: Read (handle -> id, cached after once)
# These are a SPENDING control, not a tuning knob. The run stops when it hits
# them, so a pagination bug cannot drain the balance.
X_MAX_POSTS_PER_ACCOUNT = 20
# Raised from 400/$3.00 when researcher handles took the account list from 8 to
# ~50: at the old ceiling the post cap bound after roughly 20 accounts, silently
# skipping every researcher after that.
#   worst case: 50 users x $0.010 + 1000 posts x $0.005 = $5.50
X_MAX_POSTS_PER_RUN = 1000      # ceiling of $5.00 of posts in any single run
X_RUN_BUDGET_USD = 8.00         # abort before starting if projected spend exceeds this
X_MAX_USER_LOOKUPS = 60         # register seeding: one User: Read per candidate

# --- extraction (L2) ------------------------------------------------------
MAX_INSIGHTS_PER_DOC = 5        # length-proportional cap; fixed arXiv over-extraction

# --- register (L2) --------------------------------------------------------
# NOTE: slate_k moved to config/policy.yml (register.slate_k). How broadly to
# watch each lab is a business decision, not an engineering constant.
EXPANSION_WINDOW_DAYS = 365     # co-author discovery lookback

# --- clustering (L3) ------------------------------------------------------
# Jaccard on claim tokens, read off the similarity distribution rather than
# guessed — see `cluster --histogram`.
CLUSTER_THETA = 0.4

# --- features (L3) --------------------------------------------------------
RECENCY_SCALE_DAYS = 30.0       # exponential decay scale; documented, not tuned to labels
NEUTRAL_RECENCY = 0.5           # for documents with no published_at

# --- scoring (L3) ---------------------------------------------------------
TEST_FRAC = 0.30                # held-out fraction for the bake-off
RANDOM_SEED = 42                # every sampling/split in the repo uses this
