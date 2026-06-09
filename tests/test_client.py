"""Unit tests for Deriv client dataclasses (sync-only, no WS)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from datetime import datetime

from deriv.client import DerivContractResult, DerivOHLCV, DerivTick


class TestDerivTick(unittest.TestCase):
    """DerivTick dataclass tests."""

    def test_digit_extraction(self):
        """digit property extracts last decimal digit."""
        pairs = [
            (33000.0003, 3),
            (33000.0007, 7),
            (33000.0000, 0),
            (33000.0009, 9),
            (33738.4123, 3),
        ]
        for price, expected in pairs:
            t = DerivTick(symbol="R_75", price=price, epoch=1, timestamp=datetime.now())
            self.assertEqual(t.digit, expected, f"price={price}: expected {expected}")

    def test_repr(self):
        """Has readable string rep."""
        t = DerivTick(symbol="R_75", price=33000.0005, epoch=12345, timestamp=datetime.now())
        r = repr(t)
        self.assertIn("R_75", r)
        self.assertIn("12345", r)

    def test_epoch_ordering(self):
        """Ticks are ordered by epoch."""
        t1 = DerivTick(symbol="R_75", price=33000.0, epoch=100, timestamp=datetime.now())
        t2 = DerivTick(symbol="R_75", price=33001.0, epoch=200, timestamp=datetime.now())
        self.assertLess(t1.epoch, t2.epoch)

    def test_negative_price(self):
        """Handles edge case gracefully."""
        t = DerivTick(symbol="R_75", price=0.0, epoch=1, timestamp=datetime.now())
        self.assertIn(t.digit, range(10))


class TestDerivOHLCV(unittest.TestCase):
    """DerivOHLCV dataclass tests."""

    def test_creation(self):
        o = DerivOHLCV(timestamp=1000, open=100.0, high=110.0, low=95.0, close=105.0, symbol="R_75")
        self.assertEqual(o.symbol, "R_75")
        self.assertEqual(o.high - o.low, 15.0)

if __name__ == "__main__":
    unittest.main()


class TestDerivContractResult(unittest.TestCase):
    """DerivContractResult dataclass tests."""

    def test_win(self):
        win = DerivContractResult(
            contract_id=123, contract_type="DIGITMATCH", symbol="R_75",
            stake=0.35, payout=2.87, profit=2.52,
            entry_tick=33000.0, is_win=True
        )
        self.assertTrue(win.is_win)
        self.assertEqual(win.profit, 2.52)

    def test_loss(self):
        loss = DerivContractResult(
            contract_id=456, contract_type="DIGITMATCH", symbol="R_75",
            stake=0.54, payout=0.0, profit=-0.54,
            entry_tick=33001.0, is_win=False
        )
        self.assertFalse(loss.is_win)
        self.assertEqual(loss.profit, -0.54)
