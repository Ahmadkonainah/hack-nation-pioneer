"""Quote snapping: every rule's quote must be an exact slice of its source document."""
import unittest

import extract as X
from common import fold_typography

TEXT = ("Section 1947.12. A landlord shall not, over the course of any 12-month period, increase the gross rental rate "
        "for a dwelling or a residential unit more than 5 percent plus the percentage change in the cost of living.\n\n"
        "Section 1947.13. Nothing in this “part” limits a city’s authority — see local ordinances.")


class SnapQuote(unittest.TestCase):
    def snap(self, q, **kw):
        return X.snap_quote(q, X.tokenize(TEXT), TEXT, **kw)

    def test_exact_quote(self):
        got, ratio = self.snap("shall not, over the course of any 12-month period,")
        self.assertEqual(ratio, 1.0)
        self.assertIn(got, TEXT)                      # the slice is copied from the source, never typed by the model

    def test_line_breaks_case_and_curly_quotes_do_not_matter(self):
        got, ratio = self.snap('NOTHING in this "part" limits a city\'s authority - see local ordinances')
        self.assertGreaterEqual(ratio, 0.99)
        self.assertIn(got, TEXT)

    def test_punctuation_at_word_edges_is_ignored(self):
        got, ratio = self.snap("12-month period increase the gross rental rate")
        self.assertIsNotNone(got)

    def test_near_match_needs_a_high_ratio(self):
        near = "increase the gross rental rate for a dwelling or a residential unit more than 5 percent plus the percentage change in cost of living"
        got, ratio = self.snap(near)
        self.assertIsNotNone(got)
        self.assertLess(ratio, 1.0)
        self.assertIsNone(self.snap("increase the gross rental rate for a dwelling by any amount the landlord likes"))

    def test_invented_quote_is_rejected(self):
        self.assertIsNone(self.snap("tenants may never be evicted for any reason whatsoever"))

    def test_two_words_prove_nothing(self):
        self.assertIsNone(self.snap("gross rental"))


class Typography(unittest.TestCase):
    def test_fold(self):
        self.assertEqual(fold_typography("“hi” – it’s ok­"), '"hi" - it\'s ok')


class ChunkText(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(X.chunk_text("short"), ["short"])

    def test_chunks_cover_everything_and_overlap(self):
        text = "".join(f"Paragraph {i} " + "word " * 40 + "\n\n" for i in range(400))
        chunks = X.chunk_text(text, max_chars=5_000, overlap=300)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(len(c) <= 5_000 for c in chunks))
        self.assertEqual(chunks[0], text[:len(chunks[0])])
        self.assertTrue(chunks[-1].endswith(text[-50:]))
        # Every chunk is a real slice of the text, and each one starts before the previous one ended (the overlap).
        pos = 0
        for a, b in zip(chunks, chunks[1:]):
            start_a = text.find(a, pos)
            start_b = text.find(b, start_a)
            self.assertGreaterEqual(start_a, 0)
            self.assertLess(start_b, start_a + len(a))
            pos = start_a


if __name__ == "__main__":
    unittest.main()
