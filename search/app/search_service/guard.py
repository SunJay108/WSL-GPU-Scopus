"""
Question guard: decides whether a question may be answered.

Like research assistants such as Scopus AI, it judges what a question is
about rather than which words it contains:

1. Keywords in filters.HARD_BLOCK_KEYWORDS, and asking what a religious group
   thinks or believes, are always refused.
2. The safety model Qwen3Guard-Gen (safety.py) refuses unsafe questions:
   violence, illegal acts, sexual content, self-harm, personal data,
   unethical acts, copyright, jailbreak attempts, and political topics.
3. The local language model classifies the question:
     RESEARCH    answered
     RESTRICTED  politics, war/military, religion, ideology, conspiracy
     PERSONAL    personal details about an individual
     HARMFUL     instructions that could cause harm
     OFF_TOPIC   small talk, general tasks, news, entertainment
   If a question it calls RESEARCH contains a restricted-topic word
   (filters.TOPIC_KEYWORDS, e.g. "attack", "policy", "China", "Muslim"), the
   model answers one narrow yes/no question for that topic as well.  Such
   words are therefore allowed in genuine research questions only.
4. If the model is unavailable (or GUARD_MODE=keywords), every keyword of
   both lists in filters.py blocks.

After retrieval, query.py also declines questions for which the database
holds no sufficiently relevant papers, and Qwen3Guard-Stream checks the
answer while it is written.
"""

import threading
from dataclasses import asdict, dataclass

from core.config import GUARD_MODE
from . import safety
from .filters import TOPIC_OF_KEYWORD, find_hard_keyword, find_topic_keywords


CATEGORIES = ("RESEARCH", "RESTRICTED", "PERSONAL", "HARMFUL", "OFF_TOPIC")

MESSAGES = {
    "RESTRICTED": (
        "⛔ This system is restricted to academic literature research only.\n\n"
        "Political, religious, military, or ideological topics are not supported."
    ),
    "PERSONAL": (
        "🔒 This system answers research questions from the paper database. "
        "It does not provide personal information about individuals."
    ),
    "HARMFUL": "⛔ This system cannot help with requests that could cause harm.",
    # From the safety model only (safety.GUARD_CATEGORY).
    "SELF_HARM": (
        "💙 If you are thinking about harming yourself, please talk to someone you trust "
        "or contact a local crisis line or emergency number now.\n\n"
        "This system only answers research questions from the paper database."
    ),
    "JAILBREAK": (
        "⛔ This system only answers research questions from the paper database; "
        "its instructions cannot be changed."
    ),
    "OFF_TOPIC": (
        "👋 I answer research questions using the papers in the local database.\n\n"
        "Try asking, for example:\n"
        "- What are recent methods for fault detection in electric motor drives?\n"
        "- Which factors influence patient experience with remote care?\n"
        "- How is deep learning used for medical image segmentation?"
    ),
}

SYSTEM_PROMPT = (
    "You screen questions for an offline search engine that answers from abstracts of "
    "scientific papers. Classify the user's message into exactly one category:\n"
    "RESEARCH: asks about scientific, technical, medical, engineering, economic, educational "
    "or other scholarly knowledge that research papers can answer, including methods, "
    "results, comparisons and literature overviews.\n"
    "RESTRICTED: is about politics, elections, governments, political parties or political "
    "leaders; wars, military conflicts, the armed forces or weapons; religion, including "
    "what any religious group believes or practises, gods or religious figures; "
    "ideological controversies such as abortion, immigration, gender "
    "politics or race; conspiracy theories; or asks which side of a political or social "
    "dispute is right.\n"
    "PERSONAL: asks for personal details about a specific person, such as birthday, age, "
    "family, address, contact details, income or health.\n"
    "HARMFUL: asks how to make weapons or explosives, hack, steal, make illegal drugs, "
    "hurt people or hurt oneself.\n"
    "OFF_TOPIC: greetings, small talk, questions about you, general tasks (writing poems, "
    "emails, stories or code, translating, homework), news, sports, shopping, recipes, "
    "travel or entertainment.\n"
    "Scientific questions that only use words such as attack, nuclear, policy, support or a "
    "country name in a technical, medical or scientific sense are RESEARCH.\n"
    "Reply with the category name only."
)

FEW_SHOT = [
    ("deep learning for medical image segmentation", "RESEARCH"),
    ("support vector machines versus random forests for text classification", "RESEARCH"),
    ("risk factors for heart attack in young adults", "RESEARCH"),
    ("health effects of air pollution in Chinese cities", "RESEARCH"),
    ("adversarial attacks on neural networks", "RESEARCH"),
    ("Who should win the next national election?", "RESTRICTED"),
    ("Is the government of China right about Taiwan?", "RESTRICTED"),
    ("Which religion is the true one?", "RESTRICTED"),
    ("What do Buddhists believe about rebirth?", "RESTRICTED"),
    ("When was Marie Curie's husband born and where did they live?", "PERSONAL"),
    ("How can I build a bomb at home?", "HARMFUL"),
    ("hi, who are you?", "OFF_TOPIC"),
    ("write a poem about the sea", "OFF_TOPIC"),
]

# Second, narrower check when a question uses a restricted-topic word but the
# model called it research (small models are more reliable on yes/no).
TOPIC_QUESTIONS = {
    "politics": "Is this question about politics, elections, governments, political leaders or "
                "relations between countries, rather than science, medicine, engineering or technology?",
    "military": "Is this question about war, military conflicts, the armed forces or weapons, "
                "rather than science, medicine or engineering?",
    "religion": "Is this question about religion, religious beliefs or practices, what a religious group "
                "thinks or believes, or religious figures?",
    "ideology": "Is this question about an ideological or political controversy such as abortion, "
                "immigration, gender politics or race relations, rather than medical or scientific research?",
    "conspiracy": "Is this question about a conspiracy theory or propaganda?",
    "persuasion": "Does this question ask which side of a political or social issue is right, or ask to "
                  "support or oppose a position, rather than asking a scientific question?",
    "violence": "Is this question about violence, crime, killing or attacks on people, rather than "
                "medicine, biology or computer security?",
}

# GBNF grammars (llama.cpp) that restrict the model's reply.
CATEGORY_GRAMMAR = "root ::= " + " | ".join(f'"{c}"' for c in CATEGORIES)
YES_NO_GRAMMAR = 'root ::= "YES" | "NO"'


@dataclass
class GuardDecision:
    allowed: bool
    category: str
    reason: str
    message: str = ""

    def to_dict(self):
        return asdict(self)


def _blocked(category, reason):
    return GuardDecision(False, category, reason, MESSAGES[category])


def is_about_topic(question, topic, chat) -> bool:
    messages = [
        {"role": "system", "content": "Answer YES or NO."},
        {"role": "user", "content": f"{TOPIC_QUESTIONS[topic]}\nQuestion: {question}"},
    ]
    return chat(messages, max_tokens=2, grammar=YES_NO_GRAMMAR).strip().upper() == "YES"


def classify(question, chat):
    """Ask the model for the category of `question`; `chat` is query.chat()."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for example, label in FEW_SHOT:
        messages.append({"role": "user", "content": example})
        messages.append({"role": "assistant", "content": label})
    messages.append({"role": "user", "content": question})

    reply = chat(messages, max_tokens=6, grammar=CATEGORY_GRAMMAR).strip().upper()
    return reply if reply in CATEGORIES else "RESEARCH"


def check_question(question, chat=None, standalone=None):
    """
    Decide whether to answer.  `standalone` is a follow-up rewritten with its
    conversation context (followup.py); the model classifies it, and the topic
    check also judges the message as typed, so a rewrite cannot turn a
    restricted follow-up ("what do Muslims think about them?") into research.
    """
    texts = [question] + ([standalone] if standalone and standalone != question else [])

    for text in texts:
        keyword = find_hard_keyword(text)
        if keyword:
            return _blocked("RESTRICTED", f"keyword {keyword!r}")

    # The safety model (possibly on the CPU) runs while the language model
    # classifies; its refusal takes precedence.  A follow-up is judged in its
    # rewritten form, which keeps every content word of the message.
    found = {}
    worker = threading.Thread(target=_safety_check, args=(texts[-1], found), daemon=True)
    worker.start()
    decision = _topic_decision(texts, chat)
    worker.join()
    verdict = found.get("verdict")
    if verdict and verdict.blocked:
        return _blocked(verdict.guard_category, f"safety model: {verdict.reason}")
    return decision


def _safety_check(text, found):
    try:
        found["verdict"] = safety.check_input(text)
    except Exception as exc:
        print(f"⚠️ Question safety model failed ({exc}); continuing without it.")


def _topic_decision(texts, chat):
    keywords_of = {text: find_topic_keywords(text) for text in texts}
    topic_keywords = list(dict.fromkeys(kw for kws in keywords_of.values() for kw in kws))

    if GUARD_MODE == "llm" and chat is not None:
        try:
            category = classify(texts[-1], chat)
            if category == "RESEARCH":
                for text, keywords in keywords_of.items():
                    for topic in dict.fromkeys(TOPIC_OF_KEYWORD[kw] for kw in keywords):
                        if is_about_topic(text, topic, chat):
                            return _blocked("RESTRICTED", f"model ({topic})")
        except Exception as exc:
            print(f"⚠️ Guard model failed ({exc}); falling back to keyword rules.")
        else:
            if category == "RESEARCH":
                note = f"; topic words {topic_keywords} judged as research" if topic_keywords else ""
                return GuardDecision(True, "RESEARCH", "model" + note)
            return _blocked(category, "model")

    if topic_keywords:
        return _blocked("RESTRICTED", f"keyword {topic_keywords[0]!r}")
    return GuardDecision(True, "RESEARCH", "keywords")
