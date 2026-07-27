-- Mechanical evaluation metrics.
-- Pure functions of the DB: no network, no LLM, no human. Safe to re-run.
--
-- Run:  sqlite3 data/fli.db < docs/metrics.sql
-- Save: sqlite3 data/fli.db < docs/metrics.sql > docs/metrics-out.txt
--
-- EVIDENCE SCOPE — the correction that matters.
-- The evidence table holds two populations verified by different mechanisms:
--   * INSIGHT evidence  : extraction quotes  (evidence.id IN (SELECT evidence_id FROM insights))
--   * REGISTER evidence : author/person spans from lab pages and arXiv author
--                         elements (identities, affiliations, person_candidates)
-- Mixing them corrupts every extraction metric: register spans are 2–3-word
-- names, so they swamped the quote-length histogram, and they inflated the D1
-- denominator. Every extraction metric below is scoped to insight evidence.

.mode box
.headers on

.print ''
.print '#############################################################'
.print '#  G · REGRESSION GUARDS — did the four fixes land?         #'
.print '#############################################################'

.print ''
.print '--- G1 · resolve_lab hyphen fix: pct_inferred should collapse for DeepSeek ---'
.print '    (was: DeepSeek 87.1% source_inferred because "DeepSeek-AI"/"DeepSeek-V4"'
.print '     tokenised as one hyphenated token and never matched "DeepSeek")'
SELECT COALESCE(l.name,'(unattributed)') AS lab,
       count(*) AS insights,
       SUM(ee.basis='model_asserted')  AS model_asserted,
       SUM(ee.basis='source_inferred') AS source_inferred,
       ROUND(100.0*SUM(ee.basis='source_inferred')/count(*),1) AS pct_inferred
FROM insights i
LEFT JOIN labs l ON l.id=i.attributed_lab_id
LEFT JOIN event_entities ee ON ee.event_id=i.id AND ee.entity_kind='lab'
GROUP BY 1 ORDER BY insights DESC;

.print ''
.print '--- G2 · per-insight verification logging: quote_unverified MUST appear ---'
.print '    (was: failed quotes silently `continue`d, so D1 read 99.8% because'
.print '     failures were no longer counted, not because they stopped happening)'
SELECT stage, reason, count(*) AS n
FROM rejections
WHERE stage='verification' OR reason LIKE 'quote%' OR reason LIKE '%duplicate%'
GROUP BY 1,2 ORDER BY n DESC;

.print ''
.print '--- G3 · evidence populations, kept apart ---'
SELECT
  (SELECT count(*) FROM evidence WHERE id IN (SELECT evidence_id FROM insights))
                                                                AS insight_evidence,
  (SELECT count(*) FROM evidence WHERE id NOT IN (SELECT evidence_id FROM insights))
                                                                AS register_evidence,
  (SELECT count(*) FROM evidence)                               AS total_evidence,
  (SELECT count(*) FROM insights)                               AS insights;

.print ''
.print '--- G4 · cap binding: is n=5 concentrated in LONG docs (legit) or SHORT (splitting)? ---'
SELECT d.source_type, x.n AS insights_in_doc, count(*) AS docs,
       ROUND(AVG(length(d.raw_content))) AS avg_chars,
       MIN(length(d.raw_content))        AS min_chars
FROM (SELECT e.document_id AS did, count(*) AS n
      FROM insights i JOIN evidence e ON e.id=i.evidence_id GROUP BY 1) x
JOIN raw_documents d ON d.id=x.did
GROUP BY 1,2 ORDER BY 1,2;

.print ''
.print '--- G5 · splitting signature: >1 insight of the SAME event_type in one doc ---'
SELECT (SELECT count(DISTINCT e.document_id) FROM insights i
          JOIN evidence e ON e.id=i.evidence_id)                       AS docs_with_insights,
       (SELECT count(*) FROM (SELECT e.document_id FROM insights i
          JOIN evidence e ON e.id=i.evidence_id
          GROUP BY e.document_id, i.event_type HAVING count(*)>1))     AS same_type_groups,
       (SELECT count(DISTINCT document_id) FROM (SELECT e.document_id FROM insights i
          JOIN evidence e ON e.id=i.evidence_id
          GROUP BY e.document_id, i.event_type HAVING count(*)>1))     AS docs_affected;

.print ''
.print '--- G5b · clustering: insights vs distinct clusters (cluster_id populated?) ---'
SELECT count(*)                                    AS insights,
       SUM(cluster_id IS NULL)                     AS cluster_id_null,
       count(DISTINCT cluster_id)                  AS distinct_clusters,
       ROUND(1.0*count(*)/NULLIF(count(DISTINCT cluster_id),0),2) AS insights_per_cluster
FROM insights;

.print ''
.print '#############################################################'
.print '#  M · STANDING METRICS                                     #'
.print '#############################################################'

.print ''
.print '================ M1a · FUNNEL ================'
SELECT
  (SELECT count(*) FROM raw_documents d JOIN sources s ON s.id=d.source_id
     WHERE s.purpose='content')                                  AS content_docs,
  (SELECT count(DISTINCT document_id) FROM rejections WHERE stage='stage1')
                                                                 AS stage1_rejected,
  (SELECT count(DISTINCT document_id) FROM rejections WHERE stage='stage2')
                                                                 AS stage2_rejected,
  (SELECT count(DISTINCT e.document_id) FROM insights i
     JOIN evidence e ON e.id=i.evidence_id)                      AS docs_with_insight,
  (SELECT count(*) FROM insights)                                AS insights;

.print ''
.print '================ M1b · REJECTION REASONS (noise-floor appendix) ================'
SELECT stage, reason, count(*) AS n
FROM rejections GROUP BY 1,2 ORDER BY n DESC;

.print ''
.print '================ M1c · YIELD BY SOURCE TYPE ================'
SELECT d.source_type,
       count(DISTINCT d.id) AS docs,
       count(i.id)          AS insights,
       ROUND(1.0*count(i.id)/NULLIF(count(DISTINCT d.id),0),2) AS insights_per_doc
FROM raw_documents d
JOIN sources s ON s.id=d.source_id AND s.purpose='content'
LEFT JOIN evidence e ON e.document_id=d.id
LEFT JOIN insights i ON i.evidence_id=e.id
GROUP BY 1 ORDER BY insights DESC;

.print ''
.print '================ M1d · YIELD PER *SURVIVING* DOC (the honest ratio) ================'
.print '    M1c divides by all docs incl. stage-1 rejects; this divides by docs that'
.print '    actually reached extraction.'
SELECT d.source_type,
       count(DISTINCT d.id) AS surviving_docs,
       (SELECT count(*) FROM insights i2 JOIN evidence e2 ON e2.id=i2.evidence_id
          JOIN raw_documents d2 ON d2.id=e2.document_id
          WHERE d2.source_type=d.source_type)                        AS insights,
       ROUND(1.0*(SELECT count(*) FROM insights i2 JOIN evidence e2 ON e2.id=i2.evidence_id
          JOIN raw_documents d2 ON d2.id=e2.document_id
          WHERE d2.source_type=d.source_type)/NULLIF(count(DISTINCT d.id),0),2)
                                                                     AS per_surviving_doc
FROM latest_documents d JOIN sources s ON s.id=d.source_id
WHERE s.purpose='content'
  AND d.id NOT IN (SELECT document_id FROM rejections
                   WHERE stage='stage1' AND document_id IS NOT NULL)
GROUP BY 1 ORDER BY insights DESC;

.print ''
.print '================ M2a · D1 HALLUCINATION CONTROL (insight evidence only) ================'
.print '    D1 = kept / (kept + per-insight verification failures).'
.print '    quote_unverified = a proposed insight whose quote did not verify (per insight).'
.print '    no_verified_insight / quote_not_found = whole-document failures.'
SELECT
  (SELECT count(*) FROM evidence
     WHERE id IN (SELECT evidence_id FROM insights))                    AS kept_insights,
  (SELECT count(*) FROM rejections WHERE reason='quote_unverified')     AS per_insight_failed,
  (SELECT count(*) FROM rejections
     WHERE reason IN ('no_verified_insight','quote_not_found'))         AS whole_doc_failed,
  ROUND(100.0*(SELECT count(*) FROM evidence WHERE id IN (SELECT evidence_id FROM insights))
        / NULLIF((SELECT count(*) FROM evidence WHERE id IN (SELECT evidence_id FROM insights))
               + (SELECT count(*) FROM rejections WHERE reason='quote_unverified'),0),1)
                                                                        AS d1_pct_verified;

.print ''
.print '================ M2b · VERIFICATION METHOD, BY POPULATION ================'
SELECT CASE WHEN id IN (SELECT evidence_id FROM insights) THEN 'insight (extraction)'
            ELSE 'register (entity)' END AS population,
       verification, count(*) AS n
FROM evidence GROUP BY 1,2 ORDER BY 1,2;

.print ''
.print '================ M3a · INSIGHTS PER DOCUMENT (distribution) ================'
SELECT n_insights, count(*) AS documents FROM (
  SELECT e.document_id, count(i.id) AS n_insights
  FROM insights i JOIN evidence e ON e.id=i.evidence_id
  GROUP BY e.document_id
) GROUP BY n_insights ORDER BY n_insights;

.print ''
.print '================ M3b · QUOTE LENGTH IN WORDS — EXTRACTION ONLY (spec: 10-60) ================'
SELECT bucket, count(*) AS n,
       ROUND(100.0*count(*)/(SELECT count(*) FROM evidence
                             WHERE id IN (SELECT evidence_id FROM insights)),1) AS pct
FROM (
  SELECT CASE
    WHEN w < 10  THEN '1. under 10 (under-spec)'
    WHEN w < 20  THEN '2. 10-19'
    WHEN w < 30  THEN '3. 20-29'
    WHEN w < 40  THEN '4. 30-39'
    WHEN w < 50  THEN '5. 40-49'
    WHEN w <= 60 THEN '6. 50-60'
    ELSE              '7. 61+ (over-spec)' END AS bucket
  FROM (SELECT length(verbatim_content)
               - length(replace(verbatim_content,' ','')) + 1 AS w
        FROM evidence WHERE id IN (SELECT evidence_id FROM insights))
) GROUP BY bucket ORDER BY bucket;

.print ''
.print '--- M3b2 · single headline compliance number ---'
SELECT count(*) AS insight_quotes,
       SUM(w BETWEEN 10 AND 60) AS in_spec,
       ROUND(100.0*SUM(w BETWEEN 10 AND 60)/count(*),1) AS pct_in_spec
FROM (SELECT length(verbatim_content)-length(replace(verbatim_content,' ',''))+1 AS w
      FROM evidence WHERE id IN (SELECT evidence_id FROM insights));

.print ''
.print '================ M3c · CLAIM LENGTH IN WORDS ================'
SELECT MIN(w) AS min_w, ROUND(AVG(w),1) AS avg_w, MAX(w) AS max_w FROM (
  SELECT length(claim) - length(replace(claim,' ','')) + 1 AS w FROM insights);

.print ''
.print '================ M3d · RECENCY PROFILE (docs per month) ================'
SELECT substr(published_at,1,7) AS month, count(*) AS docs
FROM latest_documents d JOIN sources s ON s.id=d.source_id
WHERE s.purpose='content' AND published_at IS NOT NULL
GROUP BY 1 ORDER BY 1 DESC LIMIT 12;

.print ''
.print '================ M3e · EVENT TYPE DISTRIBUTION (D2) ================'
SELECT event_type, count(*) AS n,
       ROUND(100.0*count(*)/(SELECT count(*) FROM insights),1) AS pct
FROM insights GROUP BY 1 ORDER BY n DESC;

.print ''
.print '================ M4b · REGISTER BALANCE (seeds / layer-below / insights per lab) ================'
SELECT l.name AS lab,
  (SELECT count(*) FROM affiliations a JOIN people p ON p.id=a.person_id
     WHERE a.lab_id=l.id AND p.discovered_via='seed')                 AS seeds,
  (SELECT count(DISTINCT a.person_id) FROM affiliations a
     JOIN people p ON p.id=a.person_id
     WHERE a.lab_id=l.id
       AND p.discovered_via IN ('coauthor_expansion','auto_approved')) AS layer_below,
  (SELECT count(*) FROM insights i WHERE i.attributed_lab_id=l.id)    AS insights
FROM labs l ORDER BY insights DESC;

.print ''
.print '================ M4c · PERSON-LEVEL ATTRIBUTION COVERAGE (W1) ================'
.print '    Low with_person is a stated coverage boundary: the register tracks N people,'
.print '    the insight stream can only reference those it can resolve.'
SELECT count(*) AS insights,
       SUM(attributed_person_id IS NOT NULL) AS with_person,
       SUM(attributed_lab_id    IS NOT NULL) AS with_lab,
       SUM(attributed_lab_id IS NULL AND attributed_person_id IS NULL) AS unattributed,
       (SELECT count(*) FROM people) AS people_in_register
FROM insights;

.print ''
.print '================ M4d · ENTITY RESOLUTION BY TIER / METHOD ================'
SELECT platform, confidence_tier, resolution_method, count(*) AS n
FROM identities GROUP BY 1,2,3 ORDER BY n DESC;

.print ''
.print '================ M4e · EVENT ENTITIES BY KIND / ROLE / BASIS ================'
SELECT entity_kind, role, basis, count(*) AS n
FROM event_entities GROUP BY 1,2,3 ORDER BY n DESC;

.print ''
.print '================ M5a · TOKENOMICS BY TASK ================'
SELECT task, model, count(*) AS calls,
       SUM(input_tokens) AS tok_in, SUM(output_tokens) AS tok_out,
       ROUND(SUM(cost_usd),4) AS usd,
       ROUND(SUM(cost_usd)/count(*),5) AS usd_per_call
FROM llm_calls GROUP BY 1,2 ORDER BY usd DESC;

.print ''
.print '================ M5b · COST PER INSIGHT + HAIKU-GATE SAVING ================'
SELECT (SELECT ROUND(SUM(cost_usd),4) FROM llm_calls)              AS total_usd,
       (SELECT count(*) FROM insights)                             AS insights,
       ROUND((SELECT SUM(cost_usd) FROM llm_calls)
             /NULLIF((SELECT count(*) FROM insights),0),5)         AS usd_per_insight,
       (SELECT count(*) FROM rejections WHERE reason='low_substance')
                                                                   AS killed_by_haiku_gate,
       ROUND((SELECT SUM(cost_usd) FROM llm_calls WHERE task='extract')
             /NULLIF((SELECT SUM(cost_usd) FROM llm_calls),0)*100,1)
                                                                   AS pct_cost_in_extract;

.print ''
.print '================ M6a · FETCH STATUS ================'
.print '    NOTE: a truncate+rebuild erases prior error/empty rows. If this shows only'
.print '    ok, the error-handling paths are no longer *demonstrated* in the committed DB —'
.print '    keep a prior DB or a log excerpt as the artifact.'
SELECT status, count(*) AS n FROM fetch_log GROUP BY 1;

.print ''
.print '================ M6b · NON-OK FETCHES BY SOURCE ================'
SELECT s.name, s.source_type, f.status, count(*) AS n, MAX(f.attempted_at) AS last_try
FROM fetch_log f JOIN sources s ON s.id=f.source_id
WHERE f.status <> 'ok' GROUP BY 1,2,3 ORDER BY n DESC;

.print ''
.print '================ M7 · LABS x EVENT TYPE (cross-tab for the report) ================'
SELECT COALESCE(l.name,'(none)') AS lab,
       SUM(i.event_type='release')        AS release,
       SUM(i.event_type='research')       AS research,
       SUM(i.event_type='benchmark')      AS benchmark,
       SUM(i.event_type='infrastructure') AS infra,
       SUM(i.event_type='commercial')     AS commercial,
       SUM(i.event_type='open_source')    AS open_src,
       SUM(i.event_type='personnel')      AS personnel,
       SUM(i.event_type='other')          AS other,
       count(*) AS total
FROM insights i LEFT JOIN labs l ON l.id=i.attributed_lab_id
GROUP BY 1 ORDER BY total DESC;

.print ''
.print '#############################################################'
.print '#  S · SCORING — populated after fli.score --bakeoff        #'
.print '#############################################################'

.print ''
.print '================ S1 · MODELS IN THE BAKE-OFF (identical coverage = C16) ================'
SELECT model, count(DISTINCT event_id) AS events,
       ROUND(MIN(score),3) AS min_s, ROUND(MAX(score),3) AS max_s
FROM event_scores GROUP BY model ORDER BY model;

.print ''
.print '================ S2 · TOP-10 EVENTS (winning model, from insights.score) ================'
SELECT ROUND(i.score,3) AS score, COALESCE(l.name,'(none)') AS lab, i.event_type,
       substr(i.claim,1,66) AS claim
FROM insights i LEFT JOIN labs l ON l.id=i.attributed_lab_id
WHERE i.score IS NOT NULL ORDER BY i.score DESC LIMIT 10;

.print ''
.print '================ S3 · PER-LAB SHARE OF TOP-30 (ranked-output fairness view) ================'
SELECT COALESCE(l.name,'(none)') AS lab, count(*) AS in_top30
FROM (SELECT attributed_lab_id FROM insights WHERE score IS NOT NULL
      ORDER BY score DESC LIMIT 30) t
LEFT JOIN labs l ON l.id=t.attributed_lab_id GROUP BY 1 ORDER BY in_top30 DESC;

.print ''
.print '================ S4 · PAIRWISE LABELS (ground truth) ================'
SELECT winner, count(*) AS n FROM pairwise_labels GROUP BY 1;

.print ''
.print '    NOTE: held-out pairwise accuracy, precision@10 / NDCG@20 per model,'
.print '    logistic coefficients, GBM importances, the ablation, and per-lab'
.print '    precision@10 are computed in fli/score.py (printed at --bakeoff) —'
.print '    they are model-fit outputs, not SQL aggregates.'

.print ''
.print '================ DONE ================'
