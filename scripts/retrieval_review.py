"""Development-only retrieval comparison; does not call a language model."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingest import load_tickets
from src.retrieve import build_retriever
from src.classify import Classifier
from src.route import NO_DOC_INTENTS

def main():
    tickets = list(load_tickets('Capstone_Pack/05_Datasets/development_tickets.json'))
    classifier = Classifier()
    hybrid = build_retriever('Capstone_Pack/05_Datasets/documentation.json', backend='hybrid')
    assert hybrid.semantic_available, 'Cannot tune hybrid when embeddings are unavailable'
    # Memoize identical requests; no candidate sees gold labels inside retrieval.
    for retriever in (hybrid.semantic, hybrid.lexical):
        original = retriever.search
        memo = {}
        def cached(query, *, top_k=5, original=original, memo=memo):
            key = (query, top_k)
            if key not in memo: memo[key] = original(query, top_k=top_k)
            return memo[key]
        retriever.search = cached
    classifications = [classifier.classify(t.text) for t in tickets]
    results = []
    for backend in ('lexical', 'hybrid'):
        for cutoff in ([.3, .42, .5, .6, .7] if backend == 'lexical' else [.5, .55, .6, .65, .7, .75]):
            r = build_retriever('Capstone_Pack/05_Datasets/documentation.json', backend='lexical', floor=cutoff) if backend == 'lexical' else hybrid
            if backend == 'hybrid': r.gate = cutoff
            hit = expected_n = abstain = no_doc = 0
            for t,c in zip(tickets, classifications):
                got = r.search(t.text) if c.intent not in NO_DOC_INTENTS else []
                expected = set(t.labels.get('expected_doc_ids') or [])
                if expected:
                    expected_n += 1
                    hit += bool(expected & {p.doc_id for p in got})
                else:
                    no_doc += 1; abstain += not got
            results.append({'backend':backend, 'cutoff':cutoff, 'hit_rate':hit/expected_n,
                            'correct_abstention':abstain/no_doc, 'balanced_score':(hit/expected_n+abstain/no_doc)/2})
    report = {'dataset':'500 development tickets; exploratory retrieval tuning', 'candidates':results,
              'selection':'Retain hybrid gate 0.60 to prioritize coverage; report the abstention tradeoff and enforce policy before automatic release.'}
    Path('evaluation/final/retrieval.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
if __name__ == '__main__': main()
