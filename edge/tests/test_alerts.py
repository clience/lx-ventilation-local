import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'bridge'))
import unittest
from alerts import evaluate

class AlertTests(unittest.TestCase):
    def test_hysteresis_and_delivery_failure(self):
        rules=[{'id':'co2_high','vent':'co2','above':1000,'rearm':800}]
        self.assertEqual(evaluate(rules,{'co2':1100},{},lambda _:False),{})
        sent=evaluate(rules,{'co2':1100},{},lambda _:True)
        self.assertTrue(sent['co2_high'])
        sent=evaluate(rules,{'co2':900},sent,lambda _:self.fail('duplicate delivery'))
        self.assertTrue(sent['co2_high'])
        self.assertFalse(evaluate(rules,{'co2':800},sent,lambda _:True)['co2_high'])
    def test_no_temperature_humidity_or_cloud_rules(self):
        for vent in ['temperature','humidity','temp','hum']:
            with self.assertRaises(ValueError): evaluate([{'id':'bad','vent':vent}],{},{},lambda _:True)
    def test_consumable_threshold(self):
        rules=[{'id':'filter','vent':'filterPct','below':2,'rearm':12}]
        sent=evaluate(rules,{'filterPct':2},{},lambda _:True)
        self.assertTrue(sent['filter'])
        self.assertFalse(evaluate(rules,{'filterPct':12},sent,lambda _:True)['filter'])
