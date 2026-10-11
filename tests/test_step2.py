import unittest
from pathlib import Path

from undertow import accounts, blocks
from undertow.blocks import Event
from undertow.refresher import market_rows
from undertow.snapshot_parser import parse_snapshot

FIX = Path(__file__).resolve().parent / "fixtures"
SNAP = Path(__file__).resolve().parent.parent / "data" / "snapshot.txt"


class BlockTests(unittest.TestCase):
    def setUp(self):
        self.ev = blocks.parse_block((FIX / "block_111990116.txt").read_text(encoding="utf-8"))

    def test_event_count(self):
        self.assertEqual(len(self.ev), 105)

    def test_touched_accounts(self):
        t = blocks.touched_accounts(self.ev)
        self.assertIn(953, t)
        self.assertIn(5364, t)
        self.assertEqual(len(t), 12)

    def test_field_values(self):
        e = next(x for x in self.ev if x.name == "PositionOpenedV2")
        self.assertEqual((e.fields["accountId"], e.fields["leverageHdths"]), (5385, 1000))

    def test_no_liquidation_in_quiet_block(self):
        self.assertEqual(blocks.liquidations(self.ev), [])

    def test_liquidation_name_detection(self):
        self.assertTrue(Event("t", 1, "PositionLiquidated").is_liquidation)
        self.assertFalse(Event("t", 1, "CantLiquidatePosAboveMMR").is_liquidation)
        self.assertTrue(Event("t", 1, "PositionDeleveragedV2").is_deleverage)


class AccountTests(unittest.TestCase):
    def test_parse(self):
        a = accounts.parse_account((FIX / "account_5437.txt").read_text(encoding="utf-8"))
        self.assertEqual(a.account_id, 5437)
        self.assertEqual(a.address, "0xFfd3107aa7cc055AA5D9864Bc5CAe4c181f317fb")
        self.assertAlmostEqual(a.balance, 0.578593)
        self.assertEqual(a.fee_tier, 0)


class HistoryTests(unittest.TestCase):
    def test_market_rows(self):
        snap = parse_snapshot(SNAP.read_text(encoding="utf-8", errors="replace"))
        rows = {r["name"]: r for r in market_rows(snap)}
        self.assertNotIn("ARB", rows)  # paused and empty
        self.assertEqual(rows["BTC"]["n_long"], 154)



class OnchainTests(unittest.TestCase):
    def test_decode_account(self):
        words = [5437, 578593, 0, 0, int("ffd3107aa7cc055aa5d9864bc5cae4c181f317fb", 16), 1, 0, 0, 0]
        from undertow import onchain
        d = onchain.decode_account("0x" + "".join(f"{w:064x}" for w in words))
        self.assertEqual(d["account_id"], 5437)
        self.assertAlmostEqual(d["balance"], 0.578593)
        self.assertEqual(d["address"], "0xffd3107aa7cc055aa5d9864bc5cae4c181f317fb")


if __name__ == "__main__":
    unittest.main()
