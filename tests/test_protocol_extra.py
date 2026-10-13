import unittest

from undertow import protocol_extra as px


class ParserTests(unittest.TestCase):
    def test_hyperliquid_shape(self):
        payload = [{"universe": [{"name": "BTC"}, {"name": "DOGE"}]}, [{"dayNtlVlm": "1000000", "openInterest": "2", "markPx": "50000"}, {"dayNtlVlm": "5", "openInterest": "1", "markPx": "1"}]]
        out = px.parse_hyperliquid(payload)
        self.assertEqual(list(out), ["BTC"])
        self.assertEqual(out["BTC"]["volume_24h"], 1_000_000.0)
        self.assertEqual(out["BTC"]["oi_usd"], 100_000.0)

    def test_dydx_shape(self):
        payload = {"markets": {"BTC-USD": {"volume24H": "2000", "openInterest": "3", "oraclePrice": "100"}, "XYZ-USD": {"volume24H": "1", "openInterest": "1", "oraclePrice": "1"}}}
        out = px.parse_dydx(payload)
        self.assertEqual(list(out), ["BTC"])
        self.assertEqual(out["BTC"]["oi_usd"], 300.0)

    def test_fee_estimate_uses_taker_plus_maker(self):
        vol = {"data": {"volume_24h": 1000.0, "volume_7d": 7000.0, "volume_30d": 30000.0, "volume_all": 100000.0, "markets": [{"name": "BTC", "volume_24h": 1000.0}]}}
        snap = {"perps": {"1": {"name": "BTC", "taker_fee": 0.0003, "maker_fee": 0.0001}}}
        est = px.fee_estimate(vol, snap)
        self.assertAlmostEqual(est["fees_24h"], 0.4)
        self.assertAlmostEqual(est["fees_7d"], 2.8)

    def test_fee_estimate_none_without_volume(self):
        self.assertIsNone(px.fee_estimate({"data": None}, {"perps": {}}))
