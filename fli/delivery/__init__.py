"""Delivery — the last mile, one shared core into two tailored outputs.

Everything below this package is audience-neutral: ingestion, the register,
extraction and scoring produce the same evidence and the same features whoever
is reading. This package is where the two readers diverge:

    positions.py  event -> holding edges. Which position does this touch, in
                  which direction, and through what mechanism.
    personas.py   the rendered judgement per audience: what it means and what
                  to do about it.
    digest.py     a periodic report, cited, exportable.
    alerts.py     the material-event path, for things that should not wait for
                  the digest.

Nothing in the pipeline imports this package. That is deliberate and enforced
by the architecture test: a change to how intelligence is PRESENTED must never
be able to change what was extracted or how it scored.
"""
