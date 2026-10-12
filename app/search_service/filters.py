"""
Keyword lists for the question guard (guard.py) and the matching rules.
"""

import re
import unicodedata

# =====================================================
# 1. BLOCKLISTS (politics, war, religion, ideology)
# =====================================================
#
# Two tiers (see guard.py for the full decision flow):
#
# HARD_BLOCK_KEYWORDS  always blocked, no model needed: names and phrases that
#                      practically never occur in a research question.
# TOPIC_KEYWORDS       restricted-topic words that also have common research
#                      meanings ("support vector machine", "heart attack",
#                      "nuclear magnetic resonance", "air pollution in China").
#                      A match makes the language model decide whether the
#                      question is really about a restricted topic.  Without the
#                      model (GUARD_MODE=keywords) these block as well.

HARD_BLOCK_KEYWORDS = [

    # ---- Political leaders ----
    "trump", "biden", "putin", "xi jinping", "zelensky", "netanyahu",

    # ---- Politics ----
    "president", "prime minister", "parliament", "senate", "democrat", "republican",
    "communist", "socialist", "ballot", "referendum", "geopolitics",

    # ---- Extremist groups / terror ----
    "isis", "al qaeda", "taliban", "hamas", "hezbollah",
    "genocide", "terrorist", "terrorism", "assassinate", "drone strike",

    # ---- Religious figures / scripture ----
    "allah", "jesus", "bible", "quran",

    # ---- Conspiracy / ideology slogans ----
    "plandemic", "deep state", "false flag", "woke", "identity politics",

    # ---- Persuasion ----
    "why we should", "is it good or bad", "who is right",
]

TOPIC_KEYWORD_GROUPS = {

    "politics": [
        # Politics / government / elections
        "election", "vote", "voting", "government", "congress", "capitalism", "liberal",
        "conservative", "dictator", "regime", "policy", "sanction", "campaign", "diplomacy",
        "politic",
        # Countries & organisations
        "russia", "ukraine", "china", "taiwan", "israel", "palestine", "iran", "iraq",
        "iranian", "iraqi",
        "north korea", "south korea", "nato", "eu", "european union", "united nations", "un",
    ],

    "military": [
        "war", "warfare", "battle", "military", "army", "navy", "air force", "missile", "nuclear",
        "weapon", "bomb", "invasion", "extremist", "soldier", "troop",
    ],

    "religion": [
        "god", "islam", "christian", "christianity", "muslim", "judaism", "jewish", "buddhism",
        "hinduism", "church", "mosque", "temple", "prayer", "prophet", "faith", "religion",
        "religious",
        # followers and beliefs
        "hindu", "buddhist", "catholic", "protestant", "sikh", "jew", "atheist", "atheism",
        "theology", "pope", "afterlife", "karma", "reincarnation",
    ],

    "ideology": [
        "gender", "lgbt", "lgbtq", "transgender", "feminism", "racism", "racial", "sexism",
        "abortion", "immigration", "migrant", "refugee",
    ],

    "conspiracy": [
        "conspiracy", "hoax", "propaganda", "mind control", "cover up",
    ],

    "persuasion": [
        "should we", "convince", "persuade", "support", "oppose",
    ],

    "violence": [
        "kill", "murder", "attack", "shooting", "bombing",
    ],
}

TOPIC_KEYWORDS = [kw for group in TOPIC_KEYWORD_GROUPS.values() for kw in group]
TOPIC_OF_KEYWORD = {kw: topic for topic, group in TOPIC_KEYWORD_GROUPS.items() for kw in group}


# =====================================================
# 2. Matching
# =====================================================

def _normalize(text: str) -> str:
    """Lower-case, fold full-width characters, and turn punctuation/underscores into spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[\W_]+", " ", text).strip()


def _keyword_pattern(keyword: str):
    """
    Compile one banned keyword into a word-aware pattern.

    - Words of a phrase may be joined by a space, hyphen or nothing, so
      "xi jinping" also matches "xijinping" and "Xi-Jinping".
    - Matching always starts at a word boundary, so "eu" no longer matches
      "neural" and "war" no longer matches "software".
    - A short last word (<= 4 letters) must be a whole word, optionally
      inflected ("war" -> "wars", "kill" -> "killed"), so "kill" does not hit
      "skills" and "isis" does not hit "crisis".
    - A longer last word also matches derived forms ("russia" -> "russian",
      "election" -> "elections", "islam" -> "islamic").
    - Keywords in non-Latin scripts match anywhere in the text.
    """
    words = _normalize(keyword).split()
    stems = [re.escape(w) for w in words]
    if not "".join(words).isascii():
        # Scripts such as Chinese have no English-style word breaks:
        # match these keywords anywhere.
        return re.compile(r"\s*".join(stems))
    last = words[-1]
    if last.endswith("y"):
        stems[-1] = re.escape(last[:-1]) + "(?:y|ie)"  # policy -> policies, army -> armies

    if len(last) <= 4:
        tail = r"(?:s|es|ed|ing|ings|er|ers)?\b"
    else:
        tail = r"\w*"
    return re.compile(r"\b" + r"\s*".join(stems) + tail)


_HARD_PATTERNS = [(kw, _keyword_pattern(kw)) for kw in HARD_BLOCK_KEYWORDS]
_TOPIC_PATTERNS = [(kw, _keyword_pattern(kw)) for kw in TOPIC_KEYWORDS]


def find_hard_keyword(text: str):
    """Return the first always-blocked keyword found in `text`, or None."""
    normalized = _normalize(text)
    return next((kw for kw, pattern in _HARD_PATTERNS if pattern.search(normalized)), None)


def find_topic_keywords(text: str):
    """All restricted-topic keywords found in `text`."""
    normalized = _normalize(text)
    return [kw for kw, pattern in _TOPIC_PATTERNS if pattern.search(normalized)]
