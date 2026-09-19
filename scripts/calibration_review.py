"""Reproducible development-only calibration comparison with grouped folds."""
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.train_classifier import fit, predict
from src.classify import Classifier
from src.ingest import load_tickets
from src.retrieve import tokenise


def temperature(probs, value):
    scores = [(k, math.log(max(p, 1e-300)) / value) for k, p in probs]
    highest = max(v for _, v in scores)
    weights = [(k, math.exp(v - highest)) for k, v in scores]
    total = sum(v for _, v in weights)
    return [(k, v / total) for k, v in weights]


def measure(rows, temp, classifier=None):
    predictions = []
    nll = 0
    for probs, gold in rows:
        ranked = temperature(probs, temp)
        pred, confidence = ranked[0]
        if classifier:
            confidence = classifier.calibrate(confidence)
        predictions.append((confidence, pred == gold))
        nll -= math.log(max(dict(ranked).get(gold, 0), 1e-15))
    bins = []
    for lo in range(10):
        members = [(c, ok) for c, ok in predictions if lo / 10 <= c < (lo + 1) / 10 or lo == 9 and c == 1]
        if members:
            bins.append({'lo': lo / 10, 'n': len(members),
                         'confidence': sum(c for c, _ in members) / len(members),
                         'accuracy': sum(ok for _, ok in members) / len(members)})
    return {'n': len(rows), 'accuracy': sum(ok for _, ok in predictions) / len(rows),
            'nll': nll / len(rows), 'brier_top_label': sum((c - ok)**2 for c, ok in predictions) / len(rows),
            'ece': sum(b['n'] * abs(b['confidence'] - b['accuracy']) for b in bins) / len(rows), 'bins': bins}


def main():
    tickets = list(load_tickets('Capstone_Pack/05_Datasets/development_tickets.json'))
    # Identical normalized bodies stay in the same fold even if subjects differ.
    keys = [' '.join(tokenise(t.body)) for t in tickets]
    folds = [int(hashlib.sha256(k.encode()).hexdigest()[:8], 16) % 5 for k in keys]
    rows = [(tokenise(t.text), t.labels['intent']) for t in tickets]
    oof = []
    nested = []
    nested_temperatures = []
    grid = [0.5, 1, 2, 3, 5, 8, 12, 20]
    for fold in range(5):
        model = fit([row for row, f in zip(rows, folds) if f != fold])
        held = [(predict(model, row[0]), row[1]) for row, f in zip(rows, folds) if f == fold]
        oof.extend(held)
        inner = []
        for inner_fold in range(5):
            if inner_fold == fold:
                continue
            inner_model = fit([row for row, f in zip(rows, folds) if f not in (fold, inner_fold)])
            inner.extend((predict(inner_model, row[0]), row[1]) for row, f in zip(rows, folds) if f == inner_fold)
        selected = min(grid, key=lambda t: measure(inner, t)['nll'])
        nested_temperatures.append(selected)
        nested.extend((temperature(p, selected), gold) for p, gold in held)
    classifier = Classifier()
    candidates = {str(t): measure(oof, t) for t in [0.5, 1, 2, 3, 5, 8, 12, 20]}
    best = min(candidates, key=lambda t: candidates[t]['nll'])
    groups = defaultdict(list)
    for key, ticket in zip(keys, tickets):
        groups[key].append(ticket.labels['expected_route'])
    conflicts = [v for v in groups.values() if len(set(v)) > 1]
    report = {'data': 'development only', 'tickets': len(tickets), 'unique_bodies': len(groups),
              'fold_method': 'SHA256(normalized body) modulo 5; groups kept intact',
              'fold_sizes': dict(Counter(folds)), 'raw': measure(oof, 1),
              'existing_bins_diagnostic': measure(oof, 1, classifier),
              'temperature_candidates': candidates, 'best_temperature_exploratory': float(best),
              'nested_temperature_evaluation': measure(nested, 1),
              'nested_temperatures': nested_temperatures,
              'selection_note': 'Candidate scores are exploratory on the same OOF predictions used for temperature selection; not an independent final estimate.',
              'conflicting_body_groups': len(conflicts), 'tickets_in_conflicting_body_groups': sum(map(len, conflicts)),
              'current_confidence_distribution': dict(Counter(classifier.classify(t.text).confidence for t in tickets))}
    out = Path('evaluation/final/calibration.json'); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('temperature_candidates', 'existing_bins_diagnostic', 'raw')}, indent=2))
    print('raw:', report['raw']); print('existing:', report['existing_bins_diagnostic'])
    if '--apply' in sys.argv:
        if report['nested_temperature_evaluation']['nll'] < report['raw']['nll']:
            Path('models/temperature.json').write_text(json.dumps({'temperature': float(best),
                'fit_source': 'grouped development out-of-fold NLL', 'report': str(out)}, indent=2), encoding='utf-8')
            print('Applied temperature', best)
        else:
            print('No calibration replacement: nested NLL did not improve.')


if __name__ == '__main__':
    main()
