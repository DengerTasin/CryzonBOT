"""MAS Anti-Scam Imagine System — perceptual image matching.

Reference scam images are reduced to several perceptual hashes (pHash, dHash,
wHash, average-hash). Incoming images are hashed the same way and compared via
Hamming distance. The best-scoring reference wins and its similarity drives the
enforcement action:

===========  =========================================================
Similarity   Action
===========  =========================================================
90–100 %     Ban the user + DM the rejoin invite
70–89 %      Kick the user + DM the rejoin invite
60–69 %      Delete the message silently
40–59 %      Report to the anti-scam channel (with screenshot + forward)
< 40 %       Ignore
===========  =========================================================
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import imagehash
from PIL import Image

from . import config

logger = logging.getLogger("cryzoncloud.antiscam")

ALGOS = ("phash", "dhash", "whash", "average_hash")


@dataclass
class MatchResult:
    percent: float
    entry: Optional[dict]
    action: Optional[str]  # ban | kick | delete | report | None


def compute_hashes(image_bytes: bytes) -> Dict[str, str]:
    """Return ``{algo: hexhash}`` for the given image bytes."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    # Normalise size for stability
    img = img.resize((256, 256))
    return {
        "phash": str(imagehash.phash(img)),
        "dhash": str(imagehash.dhash(img)),
        "whash": str(imagehash.whash(img)),
        "average_hash": str(imagehash.average_hash(img)),
    }


# Two *unrelated* 64-bit perceptual hashes differ by ~32 bits on average. That
# "noise floor" is why a naive ``1 - dist/64`` score sits near 50% for images
# that share nothing. We rescale so the noise floor maps to 0% and an exact
# match maps to 100%, which makes the operator-facing thresholds meaningful.
_NOISE_FLOOR_BITS = 32.0


def _similarity(h1: str, h2: str) -> float:
    """Percent similarity between two hex hashes (64-bit), noise-floor corrected."""
    try:
        a = int(h1, 16)
        b = int(h2, 16)
    except (ValueError, TypeError):
        return 0.0
    dist = bin(a ^ b).count("1")
    return max(0.0, (1.0 - dist / _NOISE_FLOOR_BITS) * 100.0)


def compare(uploaded: Dict[str, str], reference: Dict[str, str]) -> float:
    """Average per-algorithm similarity, weighted toward pHash."""
    scores = []
    weights = []
    for algo in ALGOS:
        if algo in uploaded and algo in reference:
            w = 2.0 if algo == "phash" else 1.0
            scores.append(_similarity(uploaded[algo], reference[algo]) * w)
            weights.append(w)
    if not weights:
        return 0.0
    return sum(scores) / sum(weights)


def find_best_match(uploaded: Dict[str, str], references: List[dict]) -> Tuple[float, Optional[dict]]:
    """Return ``(best_percent, best_entry)``.

    References are grouped by ``label`` so that all four hashes of a single
    reference image are compared together (weighted average, pHash weighted
    double). This is far more robust than comparing one algorithm in isolation
    and sharply reduces false positives.
    """
    groups: Dict[str, Dict[str, str]] = {}
    meta: Dict[str, dict] = {}
    for ref in references:
        label = ref.get("label") or f"ref-{ref.get('id')}"
        groups.setdefault(label, {})[ref.get("algo") or "phash"] = ref["hash"]
        meta.setdefault(label, ref)

    best_pct = 0.0
    best_entry: Optional[dict] = None
    for label, ref_hashes in groups.items():
        pct = compare(uploaded, ref_hashes)
        if pct > best_pct:
            best_pct = pct
            best_entry = meta[label]
    return best_pct, best_entry


def action_for(percent: float) -> Optional[str]:
    """Map a similarity percentage to an enforcement action."""
    if percent >= config.ANTISCAM_BAN_THRESHOLD:
        return "ban"
    if percent >= config.ANTISCAM_KICK_THRESHOLD:
        return "kick"
    if percent >= config.ANTISCAM_DELETE_THRESHOLD:
        return "delete"
    if percent >= config.ANTISCAM_REPORT_THRESHOLD:
        return "report"
    return None


def hash_reference(image_bytes: bytes) -> List[Tuple[str, str]]:
    """Return ``[(algo, hexhash), ...]`` for storing a reference image."""
    hashes = compute_hashes(image_bytes)
    return [(algo, h) for algo, h in hashes.items()]
