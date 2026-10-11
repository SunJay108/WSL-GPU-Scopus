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

# =====================================================
# 1. HARD BLOCKLIST (politics, war, religion, ideology)
# =====================================================

BANNED_KEYWORDS = [

    # ---- Politics / government / elections ----
    "president", "prime minister", "election", "vote", "voting", "ballot", "government",
    "parliament", "congress", "senate", "democrat", "republican", "communist", "socialist",
    "capitalism", "liberal", "conservative", "dictator", "regime", "policy", "sanction",
    "campaign", "referendum", "geopolitics", "diplomacy",

    # ---- Countries & leaders (modern) ----
    "trump", "biden", "putin", "xi jinping", "zelensky", "netanyahu",
    "russia", "ukraine", "china", "taiwan", "israel", "palestine", "iran", "iraq",
    "north korea", "south korea", "nato", "eu", "european union", "united nations", "un",

    # ---- War / conflict / weapons ----
    "war", "battle", "military", "army", "navy", "air force", "missile", "nuclear",
    "weapon", "bomb", "drone strike", "invasion", "genocide", "terrorist", "terrorism",
    "extremist", "isis", "al qaeda", "taliban", "hamas", "hezbollah",

    # ---- Religion ----
    "god", "allah", "jesus", "bible", "quran", "islam", "christian", "christianity",
    "muslim", "judaism", "jewish", "buddhism", "hinduism", "church", "mosque", "temple",
    "prayer", "prophet", "faith", "religion", "religious",

    # ---- Ideology / social conflict ----
    "gender", "lgbt", "lgbtq", "transgender", "feminism", "racism", "racial", "sexism",
    "abortion", "immigration", "migrant", "refugee", "woke", "identity politics",

    # ---- Conspiracy / propaganda ----
    "conspiracy", "hoax", "plandemic", "deep state", "false flag", "propaganda",
    "mind control", "cover up",

    # ---- Persuasion / activism ----
    "should we", "why we should", "convince", "persuade", "support", "oppose",
    "is it good or bad", "who is right",

    # ---- Violence / crime ----
    "kill", "murder", "assassinate", "attack", "shooting", "bombing"
]

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

def is_blocked_query(text: str) -> bool:
    t = text.lower()
    for word in BANNED_KEYWORDS:
        if word in t:
            return True
    return False


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
    text_lower = text.lower()

    for kw in banned_keywords:
        # whole word match or substring
        if re.search(rf"\b{re.escape(kw.lower())}\b", text_lower):
            return True

    return False
