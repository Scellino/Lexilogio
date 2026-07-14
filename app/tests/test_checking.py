"""Answer-checking rules — the heart of the quiz. These encode deliberate
product decisions (accent tolerance, typo forgiveness asymmetry, article
handling), so a failure here means user-visible behavior changed."""
from generic_vocab_bp import _check, _normalise
from el_vocab_app import _el_grammar_forms, _make_greek_check_fn
from de_vocab_app import _de_check_fn
from it_vocab_app import _it_check_fn
from es_vocab_app import _es_check_fn
from fr_vocab_app import _fr_check_fn
from nl_vocab_app import _nl_check_fn


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
    assert _greek_check("ο κεφάλι", "", "en→word", c) == ("close", "wrong_article")
    assert _greek_check("το κεφάλι", "", "en→word", c) == "correct"

def test_greek_gender_forms_accepted_no_article_required():
    # plain adjectives (no Gender/Article field) never require an article,
    # regardless of which gendered form was typed — this must not regress
    c = _card(word="άρρωστος", translation="ill",
              grammar=[{"label": "Feminine", "value": "άρρωστη"},
                       {"label": "Neuter", "value": "άρρωστο"}])
    assert _greek_check("άρρωστη", "", "en→word", c) == "correct"
    assert _greek_check("άρρωστο", "", "en→word", c) == "correct"

def test_greek_dual_gender_noun_feminine_form_with_correct_article():
    # a person-noun whose Feminine grammar value includes its own article
    # (η κολλητή) must be graded against THAT article, not the card's
    # masculine default (ο) — this was the bug: typing "η κολλητή" was
    # wrongly marked "wrong_article" because it was compared to "ο".
    c = _card(word="κολλητός", translation="best friend", gender="m",
              grammar=[{"label": "Gender", "value": "m"},
                       {"label": "Masculine", "value": "ο κολλητός"},
                       {"label": "Feminine", "value": "η κολλητή"}])
    assert _greek_check("η κολλητή", "", "en→word", c) == "correct"
    assert _greek_check("ο κολλητός", "", "en→word", c) == "correct"

def test_greek_dual_gender_noun_bare_feminine_still_needs_article():
    c = _card(word="κολλητός", translation="best friend", gender="m",
              grammar=[{"label": "Gender", "value": "m"},
                       {"label": "Masculine", "value": "ο κολλητός"},
                       {"label": "Feminine", "value": "η κολλητή"}])
    assert _greek_check("κολλητή", "", "en→word", c) == ("close", "missing_article")

def test_greek_dual_gender_noun_feminine_form_wrong_article():
    c = _card(word="κολλητός", translation="best friend", gender="m",
              grammar=[{"label": "Gender", "value": "m"},
                       {"label": "Masculine", "value": "ο κολλητός"},
                       {"label": "Feminine", "value": "η κολλητή"}])
    assert _greek_check("ο κολλητή", "", "en→word", c) == ("close", "wrong_article")

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


# ── Article handling on en→word answers, gendered-article languages ────────────
# Articles are always required whenever the card's gender is known: an
# omitted article and a wrong article are both treated as "close" (retry),
# not silently accepted. When gender is unknown (e.g. a card with no
# grammar data), the requirement can't be enforced, so it's waived.

def test_de_correct_article_is_correct():
    c = {"gender": "m", "word": "hund"}
    assert _de_check_fn("der hund", "hund", "en→word", c) == "correct"

def test_de_no_article_is_close():
    c = {"gender": "m", "word": "hund"}
    assert _de_check_fn("hund", "hund", "en→word", c) == ("close", "missing_article")

def test_de_wrong_article_is_close():
    c = {"gender": "m", "word": "hund"}
    assert _de_check_fn("die hund", "hund", "en→word", c) == ("close", "wrong_article")

def test_de_typo_without_article_is_close():
    c = {"gender": "m", "word": "hund"}
    assert _de_check_fn("hnd", "hund", "en→word", c) == "close"

def test_de_unknown_gender_article_not_required():
    # no grammar data on the card → can't resolve an expected article,
    # so a bare word is graded correct rather than penalized
    assert _de_check_fn("hund", "hund", "en→word", {}) == "correct"

def test_it_correct_article_is_correct():
    c = {"gender": "m", "word": "gatto"}
    assert _it_check_fn("il gatto", "gatto", "en→word", c) == "correct"

def test_it_no_article_is_close():
    c = {"gender": "m", "word": "gatto"}
    assert _it_check_fn("gatto", "gatto", "en→word", c) == ("close", "missing_article")

def test_it_wrong_article_is_close():
    c = {"gender": "m", "word": "gatto"}
    assert _it_check_fn("la gatto", "gatto", "en→word", c) == ("close", "wrong_article")

def test_es_correct_article_is_correct():
    c = {"gender": "m", "word": "perro"}
    assert _es_check_fn("el perro", "perro", "en→word", c) == "correct"

def test_es_no_article_is_close():
    c = {"gender": "m", "word": "perro"}
    assert _es_check_fn("perro", "perro", "en→word", c) == ("close", "missing_article")

def test_es_wrong_article_is_close():
    c = {"gender": "m", "word": "perro"}
    assert _es_check_fn("la perro", "perro", "en→word", c) == ("close", "wrong_article")

def test_fr_correct_article_is_correct():
    c = {"gender": "m", "word": "chien"}
    assert _fr_check_fn("le chien", "chien", "en→word", c) == "correct"

def test_fr_no_article_is_close():
    c = {"gender": "m", "word": "chien"}
    assert _fr_check_fn("chien", "chien", "en→word", c) == ("close", "missing_article")

def test_fr_wrong_article_is_close():
    c = {"gender": "f", "word": "chien"}
    assert _fr_check_fn("le chien", "chien", "en→word", c) == ("close", "wrong_article")

def test_fr_vowel_start_elision():
    c = {"gender": "m", "word": "arbre"}
    assert _fr_check_fn("l'arbre", "arbre", "en→word", c) == "correct"

def test_nl_correct_article_is_correct():
    c = {"gender": "de", "word": "hond"}
    assert _nl_check_fn("de hond", "hond", "en→word", c) == "correct"

def test_nl_no_article_is_close():
    c = {"gender": "de", "word": "hond"}
    assert _nl_check_fn("hond", "hond", "en→word", c) == ("close", "missing_article")

def test_nl_wrong_article_is_close():
    c = {"gender": "het", "word": "hond"}
    assert _nl_check_fn("de hond", "hond", "en→word", c) == ("close", "wrong_article")

def test_greek_no_article_is_close():
    c = _card(word="σκύλος", translation="dog",
              grammar=[{"label": "Article", "value": "ο σκύλος"}])
    assert _greek_check("σκύλος", "", "en→word", c) == ("close", "missing_article")


# ── word→en multi-sense translations, gendered-article languages ──────────────
# card.translation is often comma-separated ("to see, to view"). Typing any
# one sense in full — even without "to" — must count, not just the first
# listed sense or a close match to the whole combined string.

def test_de_word_to_en_multi_sense_second_option():
    assert _de_check_fn("view", "to see, to view", "word→en", {}) == "correct"

def test_de_word_to_en_multi_sense_first_option():
    assert _de_check_fn("see", "to see, to view", "word→en", {}) == "correct"

def test_it_word_to_en_multi_sense():
    assert _it_check_fn("view", "to see, to view", "word→en", {}) == "correct"

def test_es_word_to_en_multi_sense():
    assert _es_check_fn("view", "to see, to view", "word→en", {}) == "correct"

def test_fr_word_to_en_multi_sense():
    assert _fr_check_fn("view", "to see, to view", "word→en", {}) == "correct"

def test_nl_word_to_en_multi_sense():
    assert _nl_check_fn("view", "to see, to view", "word→en", {}) == "correct"
