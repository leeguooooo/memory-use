#!/usr/bin/env python3
"""Compare two memory-use eval --json outputs for the exact same cases and budget."""
import argparse
import json
from pathlib import Path


def compare(before, after):
    if before.get('max_chars') != after.get('max_chars'):
        raise ValueError('brief budgets differ')
    def index(report):
        result = {}
        for row in report['cases']:
            key = (row['source'], row['query'], row['expected'])
            if key in result:
                raise ValueError('duplicate case identity; use unique questions')
            result[key] = row
        return result
    old, new = index(before), index(after)
    if old.keys() != new.keys():
        raise ValueError('case identities differ')
    changes = []
    for key, left in old.items():
        right = new[key]
        if left['valid'] != right['valid']:
            raise ValueError('target validity changed; comparison is not comparable')
        if not left['valid']:
            continue
        def metrics(row):
            return {'rank': row['rank'], 'brief_status': row['brief_status'],
                    'recall_at_3': row.get('recall_at_3', float(0 < row['rank'] <= 3)),
                    'brief_evidence_recall': row.get('brief_evidence_recall', float(row['brief_status'] == 'present')),
                    'evidence': [{k: item[k] for k in ('expected', 'rank', 'brief_status')}
                                 for item in row.get('evidence', [])]}
        a, b = metrics(left), metrics(right)
        if a != b:
            changes.append({'source': key[0], 'query': key[1], 'expected': key[2], 'before': a, 'after': b})
    return {'max_chars': before['max_chars'], 'cases': len(old), 'changed_cases': len(changes), 'changes': changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    args = parser.parse_args()
    try:
        before = json.loads(args.before.read_text())
        after = json.loads(args.after.read_text())
        print(json.dumps(compare(before, after), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f'comparison failed: {exc}\n')


if __name__ == '__main__':
    main()
