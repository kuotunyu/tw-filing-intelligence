"""BM25 over Chinese is a bigram index or it is nothing, so most of this is about refusal.

The properties worth pinning down are the ones whose failure looks like a bad corpus rather
than a bug: a query that shares one character with a document is not a match, a figure is not
a bag of digit pairs, and an index that no longer matches the corpus is not searched. No GPU,
no model, no PDF.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from twfi.index.lexical import (
    TOKENISER_ID,
    Bm25Config,
    Bm25Index,
    Bm25Manifest,
    load_index,
    save_index,
    tokenise,
)

#: Three chunks in the register of a Taiwanese filing. Document 1 shares exactly one character
#: with the query 「營業收入」 -- 收 -- and no bigram, which is the difference between a bigram
#: index and a character index.
CORPUS = (
    "合併綜合損益表：營業收入淨額 530,738,356 仟元，較上年度增加。",
    "本公司於本年度收購子公司股權，並無其他重大事項應予說明。",
    "合併資產負債表：負債總額與權益總額合計等於資產總額。",
)


#: Fixed rather than a real clock reading: two saves of one corpus must differ in nothing.
BUILT_AT = "2026-08-01T00:00:00+00:00"


def manifest(rows: int = len(CORPUS), **overrides: object) -> Bm25Manifest:
    base: dict[str, object] = {
        "parser": "layout-aware",
        "rows": rows,
        "config": Bm25Config(),
        "built_at": BUILT_AT,
        "documents": ("2330-FY2023-AR",),
    }
    base.update(overrides)
    return Bm25Manifest(**base)  # type: ignore[arg-type]


def saved(directory: Path, index: Bm25Index) -> Path:
    save_index(directory, index, manifest(rows=len(index)))
    return directory


# --------------------------------------------------------------------- tokenising


def test_a_chinese_sentence_becomes_bigrams_not_one_token() -> None:
    """The whole design: ``split()`` would return one term here and BM25 would have nothing."""
    terms = tokenise("合併資產負債表")
    assert terms == ("合併", "併資", "資產", "產負", "負債", "債表")


def test_an_ascii_run_stays_whole_between_cjk_runs() -> None:
    assert tokenise("營業收入 530,738,356 仟元") == (
        "營業",
        "業收",
        "收入",
        "530,738,356",
        "仟元",
    )


def test_a_figure_is_one_term_and_not_a_bag_of_digit_pairs() -> None:
    """Split on its separators, a figure would match every other figure sharing a digit pair."""
    terms = tokenise("530,738,356")
    assert terms == ("530,738,356",)
    assert "738" not in terms


def test_a_decimal_point_stays_inside_a_number() -> None:
    assert tokenise("毛利率 66.25%") == ("毛利", "利率", "66.25")


def test_a_separator_outside_a_figure_splits_the_token() -> None:
    """``.`` joins only when a digit precedes it, so prose is not welded into one term."""
    assert tokenise("note.本期 abc,def") == ("note", "本期", "abc", "def")


def test_ascii_is_lowercased_so_case_is_not_a_second_vocabulary() -> None:
    assert tokenise("COWOS 與 N3") == ("cowos", "與", "n3")


def test_a_stock_code_survives_as_itself() -> None:
    assert "2330" in tokenise("台積電（2330）於臺灣證券交易所掛牌")


def test_fullwidth_alphanumerics_reach_the_same_term_as_ascii() -> None:
    """NFC leaves ＦＹ２０２３ fullwidth, and a query typed either way must find the filing."""
    assert tokenise("ＦＹ２０２３") == tokenise("FY2023") == ("fy2023",)


def test_a_compatibility_ideograph_reaches_the_same_term() -> None:
    """D-024: 1301 stores 年 as U+F98E, so an unnormalised query would miss 91 pages."""
    compatibility = chr(0xF98E) + chr(0xFA01)
    assert compatibility != "年度", "different codepoints, identical on the page"
    assert tokenise(compatibility) == tokenise("年度") == ("年度",)


def test_a_lone_cjk_character_is_indexed_rather_than_dropped() -> None:
    """It has no bigram; dropping it would make 「元」 unfindable rather than merely weak."""
    assert tokenise("A元B") == ("a", "元", "b")


def test_punctuation_and_whitespace_are_not_terms() -> None:
    assert tokenise("，。 ：（）") == ()
    assert tokenise("") == ()


# ------------------------------------------------------------------------- config


def test_the_default_config_is_the_standard_one() -> None:
    """Protocol 2.5 does not fix k1/b, so the Robertson defaults stand and are recorded."""
    assert (Bm25Config().k1, Bm25Config().b) == (1.2, 0.75)


@pytest.mark.parametrize(("field", "value"), [("k1", -0.1), ("b", -0.1), ("b", 1.5)])
def test_a_nonsense_config_is_rejected(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        Bm25Config(**{field: value})  # type: ignore[arg-type]


def test_zero_k1_is_legal_and_means_presence_only() -> None:
    index = Bm25Index.build(("營業收入營業收入營業收入", "營業收入"), Bm25Config(k1=0.0, b=0.0))
    scores = [score for _, score in index.search("營業收入", 2)]
    assert scores[0] == pytest.approx(scores[1]), "k1=0 must ignore term frequency entirely"


# ------------------------------------------------------------------------- search


def test_a_chinese_query_retrieves_the_document_that_says_it() -> None:
    """Documents 1 and 2 must not appear at all: 收 alone is not evidence of 營業收入."""
    hits = Bm25Index.build(CORPUS).search("營業收入", top_k=3)
    assert [doc for doc, _ in hits] == [0]


def test_sharing_one_character_is_not_a_match() -> None:
    index = Bm25Index.build(CORPUS)
    assert "收" in CORPUS[1]
    assert 1 not in {doc for doc, _ in index.search("營業收入", top_k=3)}


def test_a_figure_is_findable_verbatim() -> None:
    hits = Bm25Index.build(CORPUS).search("530,738,356", top_k=3)
    assert [doc for doc, _ in hits] == [0]


def test_a_permutation_of_a_figure_does_not_match_it() -> None:
    corpus = ("營業收入淨額 530,738,356 仟元", "營業收入淨額 356,738,530 仟元")
    hits = Bm25Index.build(corpus).search("530,738,356", top_k=2)
    assert [doc for doc, _ in hits] == [0]


def test_an_empty_corpus_has_no_documents_and_no_hits() -> None:
    index = Bm25Index.build(())
    assert len(index) == 0
    assert index.search("營業收入", top_k=5) == []


def test_an_empty_query_returns_nothing_rather_than_an_arbitrary_top_k() -> None:
    assert Bm25Index.build(CORPUS).search("", top_k=5) == []


def test_a_query_of_only_punctuation_returns_nothing() -> None:
    assert Bm25Index.build(CORPUS).search("，。：", top_k=5) == []


def test_top_k_larger_than_the_corpus_returns_what_exists() -> None:
    hits = Bm25Index.build(CORPUS).search("合併", top_k=99)
    assert sorted(doc for doc, _ in hits) == [0, 2]


def test_unmatched_documents_are_not_padded_into_the_result() -> None:
    """A zero-scoring chunk in the evidence set is context the generator may still cite."""
    assert len(Bm25Index.build(CORPUS).search("營業收入", top_k=3)) == 1


@pytest.mark.parametrize("top_k", [0, -1])
def test_a_non_positive_top_k_is_refused(top_k: int) -> None:
    """Returning [] would present a configuration mistake as a retrieval miss."""
    with pytest.raises(ValueError, match="top_k must be positive"):
        Bm25Index.build(CORPUS).search("營業收入", top_k=top_k)


def test_a_term_in_every_document_barely_contributes_to_the_ranking() -> None:
    corpus = (
        "本公司之營業收入淨額如附註所載",
        "本公司之董事會決議如附註所載",
        "本公司之股東常會決議如附註所載",
    )
    index = Bm25Index.build(corpus)
    universal = index.search("公司", top_k=3)
    rare = index.search("營業", top_k=3)
    assert len(universal) == 3, "公司 is in every document"
    assert universal[0][1] < 0.3 * rare[0][1]

    # And the term that is everywhere does not displace the one that discriminates.
    combined = index.search("本公司之營業收入", top_k=3)
    assert combined[0][0] == 0


def test_a_shorter_document_wins_when_both_say_it_once() -> None:
    corpus = ("營業收入淨額", "營業收入淨額" + "其他與本題無關的敘述" * 5)
    hits = Bm25Index.build(corpus).search("營業收入", top_k=2)
    assert [doc for doc, _ in hits] == [0, 1]


def test_equal_scores_are_ordered_by_document_index() -> None:
    corpus = ("營業收入淨額", "營業收入淨額", "資產負債表")
    hits = Bm25Index.build(corpus).search("營業收入", top_k=3)
    assert [doc for doc, _ in hits] == [0, 1]
    assert hits[0][1] == hits[1][1], "identical documents must not be separated by float noise"


def test_building_twice_gives_the_same_scores() -> None:
    first = Bm25Index.build(CORPUS).search("合併損益表", top_k=3)
    second = Bm25Index.build(CORPUS).search("合併損益表", top_k=3)
    assert first == second


def test_a_corpus_that_tokenised_to_nothing_is_refused() -> None:
    """An index over no terms answers every query with [], which reads as a missing answer."""
    with pytest.raises(ValueError, match="tokenised to zero terms"):
        Bm25Index.build(("", "，。", "   "))


def test_the_index_knows_its_own_size() -> None:
    index = Bm25Index.build(CORPUS)
    assert len(index) == 3
    assert index.vocabulary_size == len({term for doc in CORPUS for term in tokenise(doc)})


def test_length_norms_that_do_not_match_the_documents_are_refused() -> None:
    with pytest.raises(ValueError, match="length norms"):
        Bm25Index(config=Bm25Config(), postings={}, doc_lengths=(3, 4), length_norms=(1.0,))


# ------------------------------------------------------------------ save and load


def test_a_saved_index_round_trips_to_the_same_ranking(tmp_path: Path) -> None:
    index = Bm25Index.build(CORPUS, Bm25Config(k1=1.4, b=0.5))
    save_index(tmp_path / "bm25", index, manifest(config=Bm25Config(k1=1.4, b=0.5)))
    loaded, payload = load_index(tmp_path / "bm25")
    assert payload["parser"] == "layout-aware"
    assert payload["tokeniser"] == TOKENISER_ID
    assert loaded.config == Bm25Config(k1=1.4, b=0.5)
    assert loaded.search("營業收入", top_k=3) == index.search("營業收入", top_k=3)


def test_an_empty_index_round_trips(tmp_path: Path) -> None:
    save_index(tmp_path / "bm25", Bm25Index.build(()), manifest(rows=0))
    loaded, _ = load_index(tmp_path / "bm25", expect_documents=0)
    assert len(loaded) == 0


def test_saving_writes_both_files_together(tmp_path: Path) -> None:
    postings_path, manifest_path = save_index(
        tmp_path / "bm25", Bm25Index.build(CORPUS), manifest()
    )
    assert postings_path.is_file()
    assert manifest_path.is_file()


def test_two_builds_of_the_same_corpus_save_byte_identical_postings(tmp_path: Path) -> None:
    """The index has to be hashable for provenance, so key order must not drift."""
    left = save_index(tmp_path / "a", Bm25Index.build(CORPUS), manifest())
    right = save_index(tmp_path / "b", Bm25Index.build(CORPUS), manifest())
    assert [path.read_bytes() for path in left] == [path.read_bytes() for path in right]


def test_a_manifest_that_miscounts_the_documents_is_refused_at_save(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest says 9 rows"):
        save_index(tmp_path / "bm25", Bm25Index.build(CORPUS), manifest(rows=9))


def test_a_manifest_declaring_other_parameters_is_refused_at_save(tmp_path: Path) -> None:
    """The k1/b that produced a ranking must be the ones recorded beside it."""
    with pytest.raises(ValueError, match="scores with"):
        save_index(tmp_path / "bm25", Bm25Index.build(CORPUS), manifest(config=Bm25Config(k1=0.9)))


def test_a_manifest_claiming_another_tokeniser_is_refused_at_save(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="tokeniser"):
        save_index(tmp_path / "bm25", Bm25Index.build(CORPUS), manifest(tokeniser="jieba-v1"))


def test_half_an_index_is_not_an_index(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    (directory / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="rebuild the index"):
        load_index(directory)


def test_postings_that_outgrew_their_manifest_are_refused(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["rows"] = 9
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="different chunking"):
        load_index(directory)


def test_a_stale_index_is_refused_against_the_current_corpus(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    with pytest.raises(ValueError, match="rebuild rather than searching a stale index"):
        load_index(directory, expect_documents=9890)


def test_matching_expect_documents_loads(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    loaded, _ = load_index(directory, expect_documents=3)
    assert len(loaded) == 3


def test_an_index_built_by_another_tokeniser_is_refused_at_load(tmp_path: Path) -> None:
    """Two tokenisers are two retrievers; comparing across them would confound the factor."""
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["tokeniser"] = "cjk-bigram-ascii-v0"
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="rank differently"):
        load_index(directory)


def test_a_manifest_without_parameters_cannot_reproduce_a_ranking(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    del payload["config"]
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="records no k1/b"):
        load_index(directory)


def test_a_postings_file_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    (directory / "postings.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        load_index(directory)


def test_a_postings_file_without_document_lengths_is_refused(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    (directory / "postings.json").write_text('{"postings": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="no doc_lengths"):
        load_index(directory)


def test_a_postings_file_without_postings_is_refused(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    (directory / "postings.json").write_text('{"doc_lengths": [4, 4, 4]}', encoding="utf-8")
    with pytest.raises(ValueError, match="no postings"):
        load_index(directory)


def test_a_posting_pointing_outside_the_corpus_is_refused(tmp_path: Path) -> None:
    """build() cannot produce one; a hand-edited file can, and search would fail far from here."""
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    (directory / "postings.json").write_text(
        json.dumps({"doc_lengths": [4, 4, 4], "postings": {"營業": [[7, 1]]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="posting for document 7"):
        load_index(directory)


def test_a_loaded_index_with_no_terms_at_all_is_refused(tmp_path: Path) -> None:
    directory = saved(tmp_path / "bm25", Bm25Index.build(CORPUS))
    (directory / "postings.json").write_text(
        json.dumps({"doc_lengths": [0, 0, 0], "postings": {}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="tokenised to zero terms"):
        load_index(directory)


# -------------------------------------------------------------------- provenance


def test_the_manifest_records_what_produced_the_index() -> None:
    payload = manifest(notes="F1 chunks").to_json()
    assert payload["tokeniser"] == TOKENISER_ID
    assert payload["config"] == {"k1": 1.2, "b": 0.75}
    assert payload["documents"] == ["2330-FY2023-AR"]
    assert payload["notes"] == "F1 chunks"


def test_chunk_ids_are_summarised_not_dumped() -> None:
    payload = manifest(chunk_ids=tuple(f"c{n}" for n in range(50))).to_json()
    assert payload["chunk_ids_head"] == ["c0", "c1", "c2", "c3", "c4"]


def test_a_manifest_without_chunk_ids_omits_the_head() -> None:
    assert "chunk_ids_head" not in manifest().to_json()
