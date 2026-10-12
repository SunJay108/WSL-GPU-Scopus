# import re

# # -----------------------------
# # Blocked topic keywords
# # -----------------------------
# BANNED_KEYWORDS = [
#     # Politics
#     "election", "vote", "president", "government", "communist", "democrat", "republican",
#     "trump", "biden", "xi jinping", "putin", "ukraine", "israel", "palestine", "war", "nato",

#     # Ideology / sensitive
#     "religion", "god", "allah", "bible", "quran",
#     "racism", "gender", "sex", "lgbt", "abortion",

#     # General non-academic
#     "stock", "crypto", "bitcoin", "weather", "football", "nba", "movie", "music", "game",
#     "girlfriend", "boyfriend", "wife", "husband",

#     # Instructions / hacking
#     "hack", "crack", "exploit", "bypass"
# ]

# # -----------------------------
# # Academic intent keywords
# # -----------------------------
# # ACADEMIC_KEYWORDS = [
# #     "paper", "study", "research", "method", "dataset", "model", "approach",
# #     "algorithm", "analysis", "review", "survey", "experiment", "evaluation",
# #     "performance", "comparison", "results", "architecture", "framework",
# #     "systematic", "meta-analysis", "clinical", "trial"
# # ]

# ACADEMIC_INTENT_KEYWORDS = [

#     # Research actions
#     "paper", "papers", "study", "studies", "research", "article", "journal", "publication",
#     "survey", "review", "systematic review", "meta analysis", "meta-analysis",

#     # Analysis verbs
#     "analyze", "compare", "evaluate", "benchmark", "reproduce", "replicate",
#     "improve", "optimize", "ablation", "investigate", "explore", "examine",

#     # Methods & experiments
#     "method", "approach", "algorithm", "architecture", "framework", "pipeline",
#     "experiment", "experimental results", "dataset", "training", "evaluation",

#     # Metrics
#     "accuracy", "f1", "precision", "recall", "auc", "bleu", "rouge", "wer", "latency",

#     # Models
#     "transformer", "bert", "gpt", "llama", "resnet", "cnn", "rnn", "vit",

#     # Tasks
#     "classification", "retrieval", "ranking", "generation", "segmentation",
#     "detection", "translation", "summarization", "question answering",

#     # Citations & evidence
#     "cite", "citation", "reference", "references", "doi", "arxiv",

#     # Comparisons
#     "state of the art", "sota", "baseline", "previous work", "related work",

#     # Paper structure
#     "abstract", "introduction", "methodology", "results", "discussion", "conclusion",

#     # Science language
#     "hypothesis", "assumption", "limitation", "future work", "contribution"
# ]

# # -----------------------------
# # Check if query is forbidden
# # -----------------------------
# def is_blocked_query(query: str) -> bool:
#     q = query.lower()
#     for kw in BANNED_KEYWORDS:
#         if kw in q:
#             return True
#     return False

# # -----------------------------
# # Check if looks academic
# # -----------------------------
# def is_academic_query(query: str) -> bool:
#     q = query.lower()
#     for kw in ACADEMIC_INTENT_KEYWORDS:
#         if kw in q:
#             return True

#     # Also allow typical academic question patterns
#     academic_patterns = [
#         r"what is .* in",
#         r"compare .* and .*",
#         r"effect of .* on",
#         r"survey of",
#         r"review of",
#         r"how does .* work",
#         r"method for",
#     ]

#     for p in academic_patterns:
#         if re.search(p, q):
#             return True

#     return False


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

# Every keyword of both tiers (used by the keyword-only mode and output checks).
BANNED_KEYWORDS = HARD_BLOCK_KEYWORDS + TOPIC_KEYWORDS

# =====================================================
# 2. ACADEMIC QUERY PATTERNS (high precision intent)
# =====================================================

ACADEMIC_PATTERNS = [

    # ---- Literature search ----
    r"(recent|latest|state[- ]of[- ]the[- ]art) (papers|work|studies|research) on .*",
    r"(survey|review|systematic review|meta[- ]analysis) of .*",
    r"(what|which) (papers|studies|work) (discuss|address|study|investigate) .*",

    # ---- Comparison ----
    r"compare .* (methods|approaches|models|algorithms|techniques)",
    r"(comparison|benchmark|evaluation) of .*",
    r"how does .* compare to .*",

    # ---- Method explanation ----
    r"(explain|describe|analyze) (the )?(method|approach|algorithm|architecture) .*",
    r"how does .* (work|function|operate)",

    # ---- Performance / experiments ----
    r"(results|performance|accuracy|precision|recall|f1|auc|bleu|rouge) .*",
    r"(experimental|experiment) (results|setup|evaluation) .*",
    r"ablation (study|experiment) .*",

    # ---- Models ----
    r"(bert|gpt|llama|transformer|resnet|vit|cnn|rnn) .*",

    # ---- Datasets ----
    r"(dataset|benchmark|corpus) (used|for|in) .*",
    r"what dataset .* use",

    # ---- Reproducibility ----
    r"(reproduce|replicate|reimplementation) .*",
    r"how to implement .*",

    # ---- Citations ----
    r"(cite|citation|reference|references|doi|arxiv) .*",

    # ---- Paper structure ----
    r"(abstract|introduction|methodology|results|discussion|conclusion) of .*",

    # ---- Limitations / future work ----
    r"(limitations|future work|open problems) .*",
]

# =====================================================
# 3. Public API
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
_BANNED_PATTERNS = _HARD_PATTERNS + _TOPIC_PATTERNS


def find_banned_keyword(text: str, patterns=None):
    """Return the first banned keyword (either tier) found in `text`, or None."""
    normalized = _normalize(text)
    for keyword, pattern in patterns or _BANNED_PATTERNS:
        if pattern.search(normalized):
            return keyword
    return None


def find_hard_keyword(text: str):
    """Return the first always-blocked keyword found in `text`, or None."""
    return find_banned_keyword(text, _HARD_PATTERNS)


def find_topic_keyword(text: str):
    """Return the first restricted-topic keyword found in `text`, or None."""
    return find_banned_keyword(text, _TOPIC_PATTERNS)


def find_topic_keywords(text: str):
    """All restricted-topic keywords found in `text`."""
    normalized = _normalize(text)
    return [kw for kw, pattern in _TOPIC_PATTERNS if pattern.search(normalized)]


def is_blocked_query(text: str) -> bool:
    return find_banned_keyword(text) is not None


def is_academic_query(text: str) -> bool:
    t = text.lower()

    # Pattern-based detection (high precision)
    for p in ACADEMIC_PATTERNS:
        if re.search(p, t):
            return True

    # Keyword density fallback (high recall)
    ACADEMIC_KEYWORDS = [
        "paper", "study", "research", "method", "model", "algorithm", "dataset",
        "experiment", "results", "performance", "benchmark", "survey", "review",
        "evaluation", "architecture", "framework", "approach", "analysis", "compare",
        "citation", "reference", "doi", "arxiv"
    ]

    score = 0
    for kw in ACADEMIC_KEYWORDS:
        if kw in t:
            score += 1

    return score >= 2


def contains_banned_content(text: str, banned_keywords: list) -> bool:
    if banned_keywords is BANNED_KEYWORDS:
        patterns = _BANNED_PATTERNS
    else:
        patterns = [(kw, _keyword_pattern(kw)) for kw in banned_keywords]
    return find_banned_keyword(text, patterns) is not None
