"""Dawid-Skene EM: labeler reliability without a gold standard."""
from __future__ import annotations

import numpy as np


def dawid_skene(votes: np.ndarray, iters: int = 100, tol: float = 1e-6):
    """Estimate labeler accuracies and latent labels with no gold data."""
    n_items, n_lab = votes.shape
    post = np.clip((votes.sum(axis=1) > 0).astype(float), 0.05, 0.95)
    acc = np.full(n_lab, 0.7)
    prev = None
    for _ in range(iters):
        for j in range(n_lab):
            mask = votes[:, j] != 0
            if not mask.any():
                acc[j] = 0.5
                continue
            agree = np.where(votes[mask, j] == 1, post[mask], 1 - post[mask])
            acc[j] = np.clip(agree.mean(), 0.01, 0.99)
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
