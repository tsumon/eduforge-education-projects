"""core 层单元测试：分词、分片、BM25、导入解析。不依赖 FastAPI。"""
import unittest

from app.core.bm25 import BM25Index
from app.core.chunker import chunk_text
from app.core.importers import ImportError_, parse_upload
from app.core.tokenizer import content_tokens, segments, split_sentences, tokenize


class TokenizerTests(unittest.TestCase):
    def test_cjk_bigrams_and_ascii_words(self):
        toks = tokenize("Python 的默认递归深度是 1000")
        self.assertIn("python", toks)
        self.assertIn("递归", toks)
        self.assertIn("深度", toks)
        self.assertIn("1000", toks)
        self.assertNotIn("的", toks)  # 停用字

    def test_bigram_does_not_cross_stop_char(self):
        toks = tokenize("函数的定义")
        self.assertIn("函数", toks)
        self.assertIn("定义", toks)
        self.assertNotIn("数定", toks)

    def test_content_tokens_drop_single_chars_and_dedupe(self):
        toks = content_tokens("递归递归递归 深度")
        self.assertEqual(toks.count("递归"), 1)
        self.assertTrue(all(len(t) >= 2 for t in toks))

    def test_split_sentences(self):
        s = split_sentences("第一句。第二句！第三句？\n第四句")
        self.assertEqual(s, ["第一句。", "第二句！", "第三句？", "第四句"])


class ChunkerTests(unittest.TestCase):
    def test_heading_breaks_chunk_and_is_recorded(self):
        text = "# 标题A\n\n段落一内容。\n\n# 标题B\n\n段落二内容。"
        chunks = chunk_text(text)
        self.assertEqual([c.heading for c in chunks], ["标题A", "标题B"])
        self.assertEqual([c.ord for c in chunks], [0, 1])

    def test_long_paragraph_is_split_with_overlap(self):
        sentence = "这是一句用于测试分片长度的中文句子。"
        text = sentence * 60  # ~1200 字
        chunks = chunk_text(text, target=200, max_len=300, overlap_sentences=1)
        self.assertGreater(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c.text), 300 + len(sentence))
        # 相邻片重叠：后一片以前一片最后一句开头
        self.assertTrue(chunks[1].text.startswith(sentence))

    def test_leading_blockquote_disclaimer_is_not_indexed(self):
        text = "# 讲义\n\n> 本文为合成演示资料。\n\n## 正文\n\n正文第一段内容。\n\n> 正文中的引用要保留。"
        chunks = chunk_text(text)
        joined = "\n".join(c.text for c in chunks)
        self.assertNotIn("合成演示资料", joined)
        self.assertIn("正文中的引用要保留", joined)

    def test_blockquote_only_document_is_kept(self):
        chunks = chunk_text("> 只有引用块的文档也要能建索引。")
        self.assertEqual(len(chunks), 1)

    def test_short_paragraphs_are_packed(self):
        text = "\n\n".join(f"段落{i}。" for i in range(10))
        chunks = chunk_text(text, target=40)
        self.assertLess(len(chunks), 10)
        self.assertEqual(sum(c.text.count("段落") for c in chunks), 10)


class BM25Tests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            tokenize("递归函数必须有基线条件，默认递归深度 1000"),
            tokenize("二叉树的先序遍历顺序是根左右"),
            tokenize("闭区间上的连续函数有界"),
        ]
        self.idx = BM25Index.build(self.docs)

    def test_search_ranks_relevant_doc_first(self):
        hits = self.idx.search(tokenize("递归深度限制"))
        self.assertEqual(hits[0][0], 0)

    def test_search_returns_empty_for_unseen_terms(self):
        self.assertEqual(self.idx.search(tokenize("量子纠缠")), [])

    def test_coverage_in_unit_interval_and_ordering(self):
        q = content_tokens("递归深度")
        c0 = self.idx.coverage(q, 0)
        c1 = self.idx.coverage(q, 1)
        self.assertGreater(c0, c1)
        self.assertTrue(0.0 <= c1 <= c0 <= 1.0)

    def test_segment_coverage_detects_unknown_subject(self):
        # 问题"量子计算中 Shor 算法的时间复杂度"：只有"时间复杂度"词组能在 BST 片段里找到
        bst = tokenize("二叉搜索树查找、插入、删除的平均时间复杂度为 O(log n)")
        idx = BM25Index.build([bst])
        segs = segments("量子计算中 Shor 算法的时间复杂度是多少？")
        self.assertEqual(len(segs), 4)
        self.assertAlmostEqual(idx.segment_coverage(segs, 0), 0.25)
        good = segments("二叉搜索树查找的时间复杂度是多少？")
        self.assertEqual(idx.segment_coverage(good, 0), 1.0)

    def test_ascii_identifier_boosts_coverage(self):
        doc = tokenize("常见错误 UnboundLocalError：函数内先读后赋值且无 global 声明")
        idx = BM25Index.build([doc, tokenize("无关的另一篇资料内容")])
        cov = idx.coverage(content_tokens("为什么会出现 UnboundLocalError？"), 0)
        self.assertGreater(cov, 0.34)

    def test_empty_index(self):
        empty = BM25Index.build([])
        self.assertEqual(empty.search(["a"]), [])
        self.assertEqual(empty.coverage(["a"], 0), 0.0)
        self.assertEqual(empty.segment_coverage([["a"]], 0), 0.0)


class ImporterTests(unittest.TestCase):
    def test_markdown_title_from_h1(self):
        title, text = parse_upload("讲义.md", "# 第一讲 概述\n\n这是正文内容，需要至少二十个字才能通过校验。".encode("utf-8"))
        self.assertEqual(title, "第一讲 概述")
        self.assertIn("正文内容", text)

    def test_gbk_text_is_decoded(self):
        raw = "这是一段 GBK 编码的课程资料，用于测试编码兼容，长度足够。".encode("gb18030")
        title, text = parse_upload("资料.txt", raw)
        self.assertIn("GBK 编码", text)

    def test_csv_rows_become_sentences(self):
        raw = "知识点,说明\n递归,必须有基线条件\n作用域,LEGB 规则".encode("utf-8-sig")
        _, text = parse_upload("表.csv", raw)
        self.assertIn("知识点：递归", text)
        self.assertIn("说明：LEGB 规则", text)

    def test_rejects_unsupported_empty_and_short(self):
        with self.assertRaises(ImportError_):
            parse_upload("a.exe", b"xx")
        with self.assertRaises(ImportError_):
            parse_upload("a.txt", b"")
        with self.assertRaises(ImportError_):
            parse_upload("a.txt", "太短".encode("utf-8"))

    def test_rejects_oversize(self):
        with self.assertRaises(ImportError_):
            parse_upload("big.txt", b"x" * (8 * 1024 * 1024 + 1))

    def test_malformed_docx_reports_import_error(self):
        with self.assertRaises(ImportError_) as caught:
            parse_upload("broken.docx", b"not a valid document file" * 2)
        self.assertIn(".docx", str(caught.exception))

    def test_malformed_pdf_reports_import_error(self):
        with self.assertRaises(ImportError_) as caught:
            parse_upload("broken.pdf", b"%PDF-1.7\nnot a valid document file" * 2)
        self.assertIn(".pdf", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
