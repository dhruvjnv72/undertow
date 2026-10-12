import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from undertow import history_store as hs

FIX = json.loads((Path(__file__).resolve().parent / "fixtures" / "position_logs.json").read_text(encoding="utf-8"))
DEC = {int(k): tuple(v) for k, v in FIX["dec"].items()}


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.rows = [hs.decode_log(l, DEC) for l in FIX["logs"]]

    def test_every_real_log_decodes(self):
        self.assertEqual(len(FIX["logs"]), 64)
        self.assertTrue(all(r is not None for r in self.rows))

    def test_topics_match_the_contract_abi(self):
        self.assertEqual(len(hs.TOPIC), 7)
        self.assertEqual(len(set(hs.TOPIC.values())), 7)
        self.assertTrue(all(l["topics"][0] in hs.BY_TOPIC for l in FIX["logs"]))

    def test_block_log_and_timestamp_come_from_the_log(self):
        r, l = self.rows[0], FIX["logs"][0]
        self.assertEqual(r["block"], int(l["blockNumber"], 16))
        self.assertEqual(r["ts"], int(l["blockTimestamp"], 16))
        self.assertEqual(r["id"], f"{r['block']}-{int(l['logIndex'], 16)}")

    def test_opened_starts_from_zero_closed_ends_at_zero(self):
        for r in self.rows:
            if r["kind"] == "opened":
                self.assertEqual(r["size_before"], 0.0)
                self.assertGreater(r["size_after"], 0)
            if r["kind"] == "closed":
                self.assertEqual(r["size_after"], 0.0)
                self.assertIsNotNone(r["realized"])

    def test_values_are_scaled_to_real_units(self):
        btc = [r for r in self.rows if r["perp_id"] == 1 and r["price"]]
        self.assertTrue(btc)
        self.assertTrue(all(10_000 < r["price"] < 500_000 for r in btc))  # dollars, not scaled integers
        self.assertTrue(all(r["side"] in ("Long", "Short") for r in self.rows))

    def test_consecutive_events_chain_in_size(self):
        by = {}
        for r in sorted(self.rows, key=lambda x: (x["block"], x["log_index"])):
            by.setdefault((r["account_id"], r["perp_id"]), []).append(r)
        pairs = ok = 0
        for seq in by.values():
            for a, b in zip(seq, seq[1:]):
                if a["size_after"] is not None and b["size_before"] is not None:
                    pairs += 1
                    ok += abs(a["size_after"] - b["size_before"]) < 1e-9
        self.assertGreater(pairs, 3)
        self.assertEqual(ok, pairs)

    def test_unknown_event_or_market_is_skipped(self):
        l = dict(FIX["logs"][0])
        self.assertIsNone(hs.decode_log({**l, "topics": ["0x" + "00" * 32]}, DEC))
        self.assertIsNone(hs.decode_log(l, {}))  # no decimals known for the market


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patch = mock.patch.object(hs, "DB_FILE", Path(self.tmp.name) / "p.sqlite")
        self.patch.start()
        self.rows = [hs.decode_log(l, DEC) for l in FIX["logs"]]

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_store_is_idempotent(self):
        con = hs.connect()
        hs.store(con, self.rows)
        hs.store(con, self.rows)  # same ids again
        con.commit()
        n = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        con.close()
        self.assertEqual(n, 64)

    def test_wallet_events_are_only_that_account_and_ordered(self):
        con = hs.connect()
        hs.store(con, self.rows)
        hs._set_meta(con, "first_block", self.rows[0]["block"])
        hs._set_meta(con, "last_block", self.rows[-1]["block"])
        hs._set_meta(con, "last_ts", self.rows[-1]["ts"])
        hs._set_meta(con, "first_ts", self.rows[0]["ts"])
        con.commit()
        con.close()
        acct = next(r["account_id"] for r in self.rows)
        out = hs.wallet_events(acct)
        self.assertTrue(out["rows"])
        blocks = [r[1] for r in out["rows"]]
        self.assertEqual(blocks, sorted(blocks))
        self.assertEqual(len(out["rows"]), sum(1 for r in self.rows if r["account_id"] == acct))
        self.assertEqual(out["window_start_ts"], self.rows[0]["ts"])

    def test_empty_store_returns_none(self):
        self.assertIsNone(hs.wallet_events(1))

    def test_get_logs_splits_big_ranges(self):
        calls = []

        def fake(method, params, **kw):
            calls.append((int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)))
            return []
        with mock.patch.object(hs, "_rpc", fake), mock.patch.dict(hs._FIND_GETLOGS_SPAN, {"span": 1000}):
            hs.get_logs(0, 2499)
        self.assertEqual(calls, [(0, 999), (1000, 1999), (2000, 2499)])

    def test_get_logs_shrinks_range_when_rpc_refuses(self):
        seen = []

        def fake(method, params, **kw):
            lo, hi = int(params[0]["fromBlock"], 16), int(params[0]["toBlock"], 16)
            seen.append(hi - lo + 1)
            if hi - lo + 1 > 100:
                raise RuntimeError("block range too large")
            return []
        with mock.patch.object(hs, "_rpc", fake), mock.patch.dict(hs._FIND_GETLOGS_SPAN, {"span": 1000}):
            hs.get_logs(0, 999)
            self.assertEqual(hs._FIND_GETLOGS_SPAN["span"], 100)
        self.assertEqual(seen[0], 1000)
        self.assertTrue(all(s <= 100 for s in seen[1:]))


if __name__ == "__main__":
    unittest.main()
