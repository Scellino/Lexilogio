"""Answer-checking rules — the heart of the quiz. These encode deliberate
product decisions (accent tolerance, typo forgiveness asymmetry, article
handling), so a failure here means user-visible behavior changed."""
from generic_vocab_bp import _check, _normalise
from el_vocab_app import _el_grammar_forms, _make_greek_check_fn
from de_vocab_app import _de_check_fn
from it_vocab_app import _it_check_fn
from es_vocab_app import _es_check_fn


# ── Generic checker ───────────────────────────────────────────────────────────

def test_exact_match():
    assert _check("maison", "maison") == "correct"

def test_case_and_whitespace():
    assert _check("  Maison ", "maison") == "correct"

def test_accent_insensitive():
    assert _check("etre", "être") == "correct"

def test_leading_to_stripped():
    assert _check("run", "to run") == "correct"
    assert _check("to run", "run") == "correct"

def test_comma_alternatives():
    assert _check("language", "tongue, language") == "correct"

def test_typo_forgiven_towards_english():
    # word→en: minor typo in your native language auto-corrects
    assert _check("hose", "house", direction="word→en") == "correct"

def test_typo_strict_towards_target_language():
    # en→word: recall of the foreign word matters → retry, not free pass
    assert _check("mais0n", "maison", direction="en→word") == "close"

def test_wrong_answer():
    assert _check("bread", "house") == "wrong"

def test_short_words_no_fuzzy():
    # 2-letter words must match exactly — edit distance would make 'de'≈'le'
    assert _check("de", "le") == "wrong"

def test_leading_the_stripped_both_ways():
    assert _check("house", "the house", direction="word→en") == "correct"
    assert _check("the house", "house", direction="word→en") == "correct"


# ── "the" article on English-side answers, all trainers ────────────────────────
# grammar.Gender labels differ per language checker so a bare {} card is safe:
# none of these checkers touch card fields on the word→en branch.

def test_the_stripped_german():
    assert _de_check_fn("house", "the house", "word→en", {}) == "correct"
    assert _de_check_fn("the house", "house", "word→en", {}) == "correct"

def test_the_stripped_italian():
    assert _it_check_fn("house", "the house", "word→en", {}) == "correct"
    assert _it_check_fn("the house", "house", "word→en", {}) == "correct"

def test_the_stripped_spanish():
    assert _es_check_fn("house", "the house", "word→en", {}) == "correct"
    assert _es_check_fn("the house", "house", "word→en", {}) == "correct"

def test_the_stripped_greek():
    check = _make_greek_check_fn({})
    card = {"id": "x", "word": "σπίτι", "translation": "the house"}
    assert check("house", "", "word→en", card) == "correct"
    card2 = {"id": "y", "word": "σπίτι", "translation": "house"}
    assert check("the house", "", "word→en", card2) == "correct"


# ── Greek checker ─────────────────────────────────────────────────────────────

_greek_check = _make_greek_check_fn({})

def _card(**kw):
    base = {"id": "x", "word": "", "translation": "", "grammar": []}
    base.update(kw)
    return base

def test_greek_exact():
    c = _card(word="καρδιά", translation="heart")
    assert _greek_check("καρδιά", "", "en→word", c) == "correct"

def test_greek_accent_insensitive():
    c = _card(word="καρδιά", translation="heart")
    assert _greek_check("καρδια", "", "en→word", c) == "correct"

def test_greek_alt_spelling_from_grammar():
    # the εφτά/επτά case: 'Also written' grammar entries are accepted answers
    c = _card(word="εφτά", translation="seven",
              grammar=[{"label": "Also written", "value": "επτά (more formal)"}])
    assert _greek_check("επτά", "", "en→word", c) == "correct"

def test_greek_gender_forms_accepted():
    c = _card(word="άρρωστος", translation="ill",
              grammar=[{"label": "Feminine", "value": "άρρωστη"},
                       {"label": "Neuter", "value": "άρρωστο"}])
    assert _greek_check("άρρωστη", "", "en→word", c) == "correct"

def test_greek_wrong_article_is_close():
    c = _card(word="κεφάλι", translation="head",
              grammar=[{"label": "Article", "value": "το κεφάλι"}])
    assert _greek_check("ο κεφάλι", "", "en→word", c) == "close"
    assert _greek_check("το κεφάλι", "", "en→word", c) == "correct"

def test_greek_to_english_multi_option():
    c = _card(word="γλώσσα", translation="tongue, language")
    assert _greek_check("language", "", "word→en", c) == "correct"

def test_greek_wrong():
    c = _card(word="καρδιά", translation="heart")
    assert _greek_check("ψωμί", "", "en→word", c) == "wrong"

def test_grammar_forms_extraction():
    card = {"grammar": [
        {"label": "Feminine", "value": "άρρωστη"},
        {"label": "Also written", "value": "επτά (more formal)"},
        {"label": "Genitive", "value": "του κεφαλιού"},   # not an accepted answer
    ]}
    forms = _el_grammar_forms(card)
    assert "αρρωστη" in forms
    assert "επτα" in forms
    assert all("κεφαλ" not in f for f in forms)
