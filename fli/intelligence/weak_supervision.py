"""Dawid-Skene EM: labeler reliability without a gold standard.

There is no ground truth for "which of these two events matters more to a
portfolio manager", so a judge cannot be scored directly. Dawid & Skene (1979)
estimates each labeler's accuracy and the latent true label from the
DISAGREEMENT STRUCTURE alone: EM alternates between inferring the true label
from the labelers and scoring each labeler against that inference.

WHAT MAKES THE ESTIMATE MEANINGFUL, and what breaks it:

DS assumes labelers are conditionally independent given the true label. Two
prompt variants of one model are not independent — they share weights, training
data and failure modes, so they agree for reasons unrelated to being right.
Measured here: three variants of one model agreed 92-100%, and DS duly rated
all three ~0.99. That number was an artifact of asking one model three times.

The figure that consumes this therefore requires at least two independent MODEL
FAMILIES judging the same rubric, and refuses to render otherwise. With Claude
and GPT on the identical 300 pairs it reports 0.885 and 0.855 — an estimate
with something real behind it.

No new dependency: about 40 lines of numpy.
"""
from __future__ import annotations

import numpy as np


def dawid_skene(votes: np.ndarray, iters: int = 100, tol: float = 1e-6):
    """Estimate labeler accuracies and latent labels with no gold data.

    votes: (n_items, n_labelers) with +1 / -1 / 0(abstain).
    Returns (posterior P(a wins) per item, accuracy per labeler).

    Binary specialisation of Dawid & Skene 1979: each labeler has a single
    accuracy p_j = P(vote == truth), the symmetric-error case. Enough here, and
    it keeps the estimate identifiable at a few hundred items.
    """
    n_items, n_lab = votes.shape
    # init: majority vote, smoothed away from 0/1 so log() stays finite
    post = np.clip((votes.sum(axis=1) > 0).astype(float), 0.05, 0.95)
    acc = np.full(n_lab, 0.7)
    prev = None
    for _ in range(iters):
        # M-step: accuracy = agreement with the current posterior
        for j in range(n_lab):
            mask = votes[:, j] != 0
            if not mask.any():
                acc[j] = 0.5                       # never voted: uninformative
                continue
            agree = np.where(votes[mask, j] == 1, post[mask], 1 - post[mask])
            acc[j] = np.clip(agree.mean(), 0.01, 0.99)
        # E-step: posterior from the labelers that actually voted
        log_a = np.zeros(n_items)
        log_b = np.zeros(n_items)
        for j in range(n_lab):
            m = votes[:, j] != 0
            says_a = votes[:, j] == 1
            log_a[m] += np.log(np.where(says_a[m], acc[j], 1 - acc[j]))
            log_b[m] += np.log(np.where(says_a[m], 1 - acc[j], acc[j]))
        mx = np.maximum(log_a, log_b)
        pa, pb = np.exp(log_a - mx), np.exp(log_b - mx)
        post = pa / (pa + pb)
        if prev is not None and np.abs(post - prev).max() < tol:
            break
        prev = post.copy()
    return post, acc
