import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('compare_recall', Path(__file__).resolve().parents[1] / 'scripts/research/compare_recall.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CompareRecallTest(unittest.TestCase):
    def report(self, rank=1, status='present'):
        return {'max_chars': 1500, 'cases': [{'source': 'manual', 'query': 'vpn', 'expected': 'net/vpn.md', 'valid': True, 'rank': rank, 'brief_status': status}]}

    def test_detects_regression_in_final_brief(self):
        result = module.compare(self.report(), self.report(2, 'filtered'))
        self.assertEqual(result['changed_cases'], 1)
        self.assertEqual(result['changes'][0]['after']['brief_evidence_recall'], 0)

    def test_detects_evidence_swap_even_if_average_is_unchanged(self):
        a, b = self.report(), self.report()
        a['cases'][0]['evidence'] = [{'expected': 'a.md', 'rank': 1, 'brief_status': 'present'},
                                     {'expected': 'b.md', 'rank': 2, 'brief_status': 'filtered'}]
        b['cases'][0]['evidence'] = [{'expected': 'a.md', 'rank': 1, 'brief_status': 'filtered'},
                                     {'expected': 'b.md', 'rank': 2, 'brief_status': 'present'}]
        self.assertEqual(module.compare(a, b)['changed_cases'], 1)

    def test_rejects_incomparable_reports(self):
        a, b = self.report(), self.report()
        b['max_chars'] = 512
        with self.assertRaises(ValueError): module.compare(a, b)
        b = self.report()
        b['cases'][0]['valid'] = False
        with self.assertRaises(ValueError): module.compare(a, b)
        b = self.report()
        b['cases'][0]['query'] = 'different'
        with self.assertRaises(ValueError): module.compare(a, b)
        b = self.report()
        b['cases'].append(b['cases'][0])
        with self.assertRaises(ValueError): module.compare(a, b)


if __name__ == '__main__': unittest.main()
