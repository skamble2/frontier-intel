"""Cache/batch pricing math, cache-token logging, and the Batch API path.

No network anywhere: the anthropic client is a stub injected into
LLM._clients, and batch polling sleep is silenced via the module alias.
"""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest import mock

from fli.ops import llm as L
from tests.helpers import memory_db


def _mem_conn() -> sqlite3.Connection:
    return memory_db()


class CostTests(unittest.TestCase):
    def test_plain_call_price_unchanged(self):
        pin, pout = L.PRICES["claude-sonnet-5"]
        self.assertAlmostEqual(L.cost_usd("claude-sonnet-5", 1000, 100),
                               (1000 * pin + 100 * pout) / 1e6)

    def test_cache_tokens_are_priced_at_their_multipliers(self):
        pin, _ = L.PRICES["claude-sonnet-5"]
        usd = L.cost_usd("claude-sonnet-5", 0, 0,
                         cache_write_tokens=1000, cache_read_tokens=1000)
        self.assertAlmostEqual(
            usd, (1000 * pin * L.CACHE_WRITE_MULT
                  + 1000 * pin * L.CACHE_READ_MULT) / 1e6)

    def test_batch_halves_everything(self):
        full = L.cost_usd("claude-sonnet-5", 2000, 300, cache_read_tokens=500)
        half = L.cost_usd("claude-sonnet-5", 2000, 300, cache_read_tokens=500,
                          batch=True)
        self.assertAlmostEqual(half, full * L.BATCH_DISCOUNT)

    def test_unpriced_model_still_raises(self):
        with self.assertRaises(KeyError):
            L.cost_usd("mystery-model", 1, 1)


class HelperTests(unittest.TestCase):
    def test_flatten_blocks_equals_their_text(self):
        blocks = [{"type": "text", "text": "part one ",
                   "cache_control": {"type": "ephemeral"}},
                  {"type": "text", "text": "part two"}]
        self.assertEqual(L._flatten(blocks), "part one part two")
        self.assertEqual(L._flatten("just a string"), "just a string")

    def test_cached_system_marks_the_block(self):
        [block] = L._cached_system("sys prompt")
        self.assertEqual(block["text"], "sys prompt")
        self.assertEqual(block["cache_control"], {"type": "ephemeral"})

    def test_cache_usage_defaults_to_zero(self):
        self.assertEqual(L._cache_usage(SimpleNamespace()), (0, 0))
        u = SimpleNamespace(cache_creation_input_tokens=7,
                            cache_read_input_tokens=None)
        self.assertEqual(L._cache_usage(u), (7, 0))


def _usage(inp=100, out=20, cw=0, cr=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_creation_input_tokens=cw,
                           cache_read_input_tokens=cr)


def _message(text, **usage_kw):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=_usage(**usage_kw))


class CallLoggingTests(unittest.TestCase):
    def test_cache_tokens_reach_llm_calls(self):
        conn = _mem_conn()
        llm = L.LLM(conn)
        client = mock.MagicMock()
        client.messages.create.return_value = _message("hi", cw=1200, cr=0)
        llm._clients["anthropic"] = client
        out = llm.call("verify", "sys", "user text", max_tokens=50)
        self.assertEqual(out, "hi")
        row = conn.execute("SELECT * FROM llm_calls").fetchone()
        self.assertEqual(row["cache_write_tokens"], 1200)
        self.assertIsNone(row["cache_read_tokens"])          # 0 stored as NULL
        pin, pout = L.PRICES[L.MODEL_FOR_TASK["verify"]]
        self.assertAlmostEqual(
            row["cost_usd"],
            (100 * pin + 20 * pout + 1200 * pin * L.CACHE_WRITE_MULT) / 1e6)

    def test_system_is_sent_as_cached_block(self):
        conn = _mem_conn()
        llm = L.LLM(conn)
        client = mock.MagicMock()
        client.messages.create.return_value = _message("ok")
        llm._clients["anthropic"] = client
        llm.call("verify", "the system prompt", "u")
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["system"],
                         [{"type": "text", "text": "the system prompt",
                           "cache_control": {"type": "ephemeral"}}])


def _batch_entry(cid, text=None, **usage_kw):
    if text is None:
        return SimpleNamespace(custom_id=cid,
                               result=SimpleNamespace(type="errored"))
    return SimpleNamespace(
        custom_id=cid,
        result=SimpleNamespace(type="succeeded",
                               message=_message(text, **usage_kw)))


class CallBatchTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(L, "_sleep", lambda s: None)
        p.start()
        self.addCleanup(p.stop)

    def _llm_with_batch(self, conn, results, statuses=("ended",)):
        llm = L.LLM(conn)
        client = mock.MagicMock()
        client.messages.batches.create.return_value = SimpleNamespace(
            id="b1", processing_status="in_progress")
        client.messages.batches.retrieve.side_effect = [
            SimpleNamespace(id="b1", processing_status=s) for s in statuses]
        client.messages.batches.results.return_value = iter(results)
        llm._clients["anthropic"] = client
        return llm, client

    def test_results_match_by_custom_id_and_log_at_batch_rate(self):
        conn = _mem_conn()
        # out of order on purpose: matching must be by custom_id, not position
        llm, _ = self._llm_with_batch(conn, [
            _batch_entry("2", "second"), _batch_entry("1", "first")])
        out = llm.call_batch("verify", "sys", [("1", "u1"), ("2", "u2")],
                             max_tokens=50)
        self.assertEqual(out, {"1": "first", "2": "second"})
        rows = conn.execute("SELECT cost_usd FROM llm_calls").fetchall()
        self.assertEqual(len(rows), 2)
        model = L.MODEL_FOR_TASK["verify"]
        self.assertAlmostEqual(rows[0]["cost_usd"],
                               L.cost_usd(model, 100, 20, batch=True))

    def test_errored_item_returns_none_for_sync_fallback(self):
        conn = _mem_conn()
        llm, _ = self._llm_with_batch(conn, [
            _batch_entry("1", "ok"), _batch_entry("2")])
        out = llm.call_batch("verify", "sys", [("1", "u1"), ("2", "u2")])
        self.assertEqual(out["1"], "ok")
        self.assertIsNone(out["2"])

    def test_polls_until_ended(self):
        conn = _mem_conn()
        llm, client = self._llm_with_batch(
            conn, [_batch_entry("1", "ok")],
            statuses=("in_progress", "ended"))
        out = llm.call_batch("verify", "sys", [("1", "u1")])
        self.assertEqual(out["1"], "ok")
        self.assertEqual(client.messages.batches.retrieve.call_count, 2)

    def test_openai_model_is_rejected(self):
        conn = _mem_conn()
        llm = L.LLM(conn)
        with self.assertRaises(ValueError):
            llm.call_batch("judge", "sys", [("1", "u")], model="gpt-5.2")


if __name__ == "__main__":
    unittest.main()
