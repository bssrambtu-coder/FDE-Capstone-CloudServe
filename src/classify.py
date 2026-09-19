"""Classify: intent, urgency and a calibrated confidence (A3).

Intent is a multinomial Naive Bayes model over ticket text, fitted offline by
scripts/train_classifier.py and loaded from models/. Urgency is an
intent-conditioned prior, because urgency is not recoverable from the wording
in this dataset (text model 46.4% against a 45.2% majority baseline).

Never raises. An unclassifiable ticket returns the defined fallback so the run
continues, which A3 requires explicitly.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib

from .models import Classification
from .retrieve import tokenise

log = logging.getLogger(__name__)

MODELS = pathlib.Path("models")
FALLBACK = Classification(intent="unclear_request", urgency="medium", confidence=0.0)


class Classifier:
    def __init__(self, models_dir: pathlib.Path = MODELS):
        self.dir = models_dir
        self.model = self._load("intent_nb.json")
        self.urgency = self._load("urgency_prior.json")
        self.calibration = self._load("calibration.json")
        self.temperature = self._load("temperature.json").get("temperature")
        self._vocab = set(self.model.get("vocab", [])) if self.model else set()
        if not self.model:
            log.warning("no intent model in %s; run scripts/train_classifier.py. "
                        "Classification will fall back for every ticket.", models_dir)

    def _load(self, name: str) -> dict:
        path = self.dir / name
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("could not load %s: %s", path, exc)
            return {}

    def _posteriors(self, tokens: list[str]) -> list[tuple[str, float]]:
        alpha = self.model["alpha"]
        v = self.model["vocab_size"]
        default = math.log(alpha)
        scores = {}
        for label, c in self.model["classes"].items():
            lp = c["prior"]
            weights, denom = c["weights"], c["denom"]
            for t in tokens:
                lp += weights.get(t, default) - denom
            scores[label] = lp
        if self.temperature:
            scores = {k: v / float(self.temperature) for k, v in scores.items()}
        top = max(scores.values())
        exp = {k: math.exp(s - top) for k, s in scores.items()}
        z = sum(exp.values()) or 1.0
        return sorted(((k, s / z) for k, s in exp.items()), key=lambda kv: (-kv[1], kv[0]))

    def calibrate(self, raw: float) -> float:
        """Map a raw posterior onto the accuracy observed for that band.

        The bands come from out-of-fold measurement on the development set, so
        a reported confidence means "this is how often predictions in this
        band were actually right" rather than "this is how sure the maths
        feels". Falls through to the raw value if no band matches.
        """
        if self.temperature:
            return min(raw, 0.9999)
        for b in self.calibration.get("bins", []):
            if b["lo"] <= raw < b["hi"]:
                return float(b["accuracy"])
        return raw

    def classify(self, text: str) -> Classification:
        if not self.model:
            return FALLBACK
        try:
            tokens = [t for t in tokenise(text) if t in self._vocab]
            if not tokens:
                return FALLBACK
            ranked = self._posteriors(tokens)
            intent, raw = ranked[0]
            entry = (self.urgency.get("by_intent") or {}).get(intent, {})
            urgency = entry.get("urgency") or self.urgency.get("default", "medium")
            return Classification(
                intent=intent,
                urgency=urgency,
                confidence=round(self.calibrate(raw), 4),
                alternatives=[(k, round(v, 4)) for k, v in ranked[1:4]],
            )
        except Exception as exc:  # a classifier fault must not stop the run
            log.warning("classification failed, using fallback: %s", exc)
            return FALLBACK
