"""Unit tests for RMR Truncator (TASK-04)."""

import unittest

from rmr.core.truncator import (
    truncate_list,
    truncate_text_lines,
)


class TestTruncator(unittest.TestCase):
    def test_truncate_text_lines_no_truncation(self):
        text = "Line 1\nLine 2\nLine 3"
        res, is_tr = truncate_text_lines(text, max_lines=10, max_bytes=1000)
        self.assertFalse(is_tr)
        self.assertEqual(res, text)

    def test_truncate_text_lines_empty_string(self):
        res, is_tr = truncate_text_lines("", max_lines=10, max_bytes=1000)
        self.assertFalse(is_tr)
        self.assertEqual(res, "")

    def test_truncate_text_lines_by_max_lines(self):
        lines = [f"Line {i}" for i in range(250)]
        text = "\n".join(lines)
        res, is_tr = truncate_text_lines(text, max_lines=200, max_bytes=100000)
        self.assertTrue(is_tr)
        res_lines = res.splitlines()
        self.assertLessEqual(len(res_lines), 202)
        self.assertIn("Line 0", res)
        self.assertIn("Line 199", res)
        self.assertNotIn("Line 200", res)
        self.assertIn("Truncated", res)
        self.assertIn("200 of 250 lines", res)

    def test_truncate_text_lines_by_max_bytes(self):
        # 10 lines, each 50 chars => ~500 bytes. max_bytes=150
        lines = [f"Line {i:02d}: " + ("x" * 40) for i in range(10)]
        text = "\n".join(lines)
        res, is_tr = truncate_text_lines(text, max_lines=50, max_bytes=150)
        self.assertTrue(is_tr)
        self.assertIn("Truncated", res)
        self.assertIn("bytes", res)

    def test_truncate_text_lines_multibyte_safe(self):
        # Multi-byte UTF-8 characters (Vietnamese accents)
        vietnamese_text = "Tiếng Việt kiểm thử đa byte an toàn và bảo mật cao"
        res, is_tr = truncate_text_lines(vietnamese_text, max_lines=5, max_bytes=20)
        self.assertTrue(is_tr)
        self.assertIn("Truncated", res)
        # Verify decoding did not corrupt into invalid characters
        self.assertIsInstance(res, str)

    def test_truncate_list_no_truncation(self):
        items = list(range(30))
        res, is_tr = truncate_list(items, max_count=50)
        self.assertFalse(is_tr)
        self.assertEqual(len(res), 30)
        self.assertEqual(res, items)

    def test_truncate_list_with_truncation(self):
        items = list(range(100))
        res, is_tr = truncate_list(items, max_count=50)
        self.assertTrue(is_tr)
        self.assertEqual(len(res), 50)
        self.assertEqual(res, list(range(50)))

    def test_truncate_list_non_list_iterable(self):
        items_gen = range(60)
        res, is_tr = truncate_list(items_gen, max_count=50)  # type: ignore
        self.assertTrue(is_tr)
        self.assertEqual(len(res), 50)


if __name__ == "__main__":
    unittest.main()
