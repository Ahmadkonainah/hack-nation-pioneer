"""The saved-answer cache and the model wrapper, with a fake client: no key and no network are used."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import llm

SCHEMA = {"type": "object", "properties": {"rules": {"type": "array", "items": {"type": "string"}}}}


def fake_response(payload: str, stop="end_turn"):
    """Shaped like the SDK's response: text blocks, a stop reason and token usage."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=payload)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


class CacheKey(unittest.TestCase):
    def test_changes_when_anything_that_shapes_the_answer_changes(self):
        base = llm.cache_key("sys", SCHEMA, "user", "m1")
        self.assertEqual(base, llm.cache_key("sys", SCHEMA, "user", "m1"))
        self.assertEqual(len(base), 12)
        for other in (llm.cache_key("sys2", SCHEMA, "user", "m1"), llm.cache_key("sys", {"x": 1}, "user", "m1"),
                      llm.cache_key("sys", SCHEMA, "user2", "m1"), llm.cache_key("sys", SCHEMA, "user", "m2")):
            self.assertNotEqual(base, other)

    def test_schema_key_order_does_not_matter(self):
        self.assertEqual(llm.cache_key("s", {"a": 1, "b": 2}, "u", "m"), llm.cache_key("s", {"b": 2, "a": 1}, "u", "m"))


class CachedStructuredCall(unittest.TestCase):
    def call(self, d, **kw):
        return llm.cached_structured_call(cache_dir=Path(d), system="s", schema=SCHEMA, user="u", model="claude-sonnet-5-5",
                                          label="test", list_key="rules", **kw)

    def test_second_call_uses_the_saved_answer(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(llm, "get_client", return_value=object()) as gc, \
             mock.patch.object(llm, "call_structured", return_value=fake_response('{"rules": ["a"]}')) as cs:
            first = self.call(d)
            second = self.call(d)
            self.assertEqual(first["result"], {"rules": ["a"]})
            self.assertEqual(second["result"], first["result"])
            self.assertEqual(cs.call_count, 1)                       # one paid call, then free
            self.assertEqual(gc.call_count, 1)                       # the key is not even needed the second time
            self.assertEqual(len(list(Path(d).glob("*.json"))), 1)

    def test_refresh_asks_again(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.object(llm, "get_client", return_value=object()), \
             mock.patch.object(llm, "call_structured", return_value=fake_response('{"rules": []}')) as cs:
            self.call(d)
            self.call(d, refresh=True)
            self.assertEqual(cs.call_count, 2)

    def test_unusable_answers_are_not_saved(self):
        for bad in (fake_response("not json"), fake_response('{"other": 1}'), fake_response('{"rules": []}', stop="max_tokens"),
                    fake_response('{"rules": []}', stop="refusal")):
            with tempfile.TemporaryDirectory() as d, \
                 mock.patch.object(llm, "get_client", return_value=object()), \
                 mock.patch.object(llm, "call_structured", return_value=bad):
                with self.assertRaises(SystemExit):
                    self.call(d)
                self.assertEqual(list(Path(d).glob("*.json")), [])


class Strictify(unittest.TestCase):
    def test_every_object_forbids_extra_keys_and_requires_all_fields(self):
        schema = llm.strictify({"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "array", "items": {
            "type": "object", "properties": {"c": {"type": "integer"}}}}}})
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(sorted(schema["required"]), ["a", "b"])
        inner = schema["properties"]["b"]["items"]
        self.assertIs(inner["additionalProperties"], False)
        self.assertEqual(inner["required"], ["c"])


class Cost(unittest.TestCase):
    def test_usd_is_monotonic_and_positive(self):
        self.assertGreater(llm.usd(1_000_000, 0, "claude-sonnet-5-5"), 0)
        self.assertGreater(llm.usd(0, 1_000_000, "claude-sonnet-5-5"), llm.usd(0, 1000, "claude-sonnet-5-5"))


class KeyHandling(unittest.TestCase):
    def test_missing_key_message_points_to_the_example_file_and_never_prints_a_key(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch.object(llm, "load_env", lambda: None):
            with self.assertRaises(SystemExit) as cm:
                llm.get_client()
        self.assertIn(".env", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
