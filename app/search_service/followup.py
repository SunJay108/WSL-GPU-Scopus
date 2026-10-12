"""
Follow-up questions: turns "How accurate are they?" into a standalone search
question using the earlier turns of the conversation.

Only real follow-ups are rewritten.  A message that names its own subject
("what is graphene", "tell me about Tokyo") is searched and judged exactly as
in a new chat, so an earlier topic can never take over a new question:

1. A message with words that point back ("they", "these methods",
   "what about ...") is a follow-up.
2. Otherwise the language model, which sees only the message, decides
   whether it leaves out what it is about ("What are the limitations?").
3. A rewrite is used only if it keeps every content word of the message and
   adds no more than a short description of what was referred to.
"""

import re

from core.config import HISTORY_TURNS
from .guard import yes_no_grammar

# Words that point back to an earlier turn.
_REFERRING_WORDS = {
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "this", "these", "those", "such", "same", "above", "aforementioned",
    "previous", "former", "latter", "he", "him", "his", "she", "her", "hers",
}
# Openings that continue the previous answer.
_FOLLOW_UP_OPENING = re.compile(
    r"^(?:and|or|but|also|so|then|what about|how about|what else|anything else|any other|"
    r"(?:tell me|explain|say) more)\b"
)

# Words that carry no subject; every other word of the message must survive a rewrite.
_STOPWORDS = set("""
a an the and or but if then so of in on at to for from by with about into over under
between among than as is are was were be been being am do does did done have has had
can could will would shall should may might must what which who whom whose when where
why how whether i me my we our you your there here not no yes all any some each every
more most other another please tell show give explain describe list find also just
only very much many
""".split()) | _REFERRING_WORDS

# A rewrite may add at most this many words (the subject that was referred to).
MAX_ADDED_WORDS = 40

CONTEXT_PROMPT = (
    "You check single messages sent to a scientific literature search engine during a "
    "conversation. Answer YES if the message leaves out what it is about, so it only makes "
    "sense together with the earlier conversation. Answer NO if the message says what it is "
    "about, even if it is short, vague or not a research question."
)
CONTEXT_EXAMPLES = [
    ("What are the limitations?", "YES"),
    ("Which datasets were used?", "YES"),
    ("Explain in more detail", "YES"),
    ("Why?", "YES"),
    ("How accurate are deep learning models for crack detection?", "NO"),
    ("what is graphene", "NO"),
    ("tell me about Tokyo", "NO"),
    ("hello", "NO"),
]

REWRITE_PROMPT = (
    "You rewrite the latest message of a conversation with a scientific literature search "
    "engine as one standalone search question. Replace words such as 'it', 'they' or 'these "
    "methods', or a missing subject, with what they refer to in the conversation. Keep every "
    "other word of the message and add nothing it does not ask about. If the message is "
    "already complete or is about a new subject, repeat it unchanged. Output only the question."
)
_EXAMPLE_CONVERSATION = (
    "User: deep learning methods for crack detection in concrete\n"
    "Assistant: Convolutional neural networks detect cracks in concrete images with high accuracy."
)
REWRITE_EXAMPLES = [
    ("How accurate are they?", "How accurate are deep learning methods for crack detection in concrete?"),
    ("What are the limitations?", "What are the limitations of deep learning methods for crack detection in concrete?"),
    ("Do these methods work on bridges?", "Do deep learning methods for crack detection in concrete work on bridges?"),
    ("what about asphalt?", "what about deep learning methods for crack detection in asphalt?"),
    ("what is graphene", "what is graphene"),
    ("hello", "hello"),
]


def _words(text):
    return re.findall(r"[^\W_]+", text.lower())


def refers_back(question) -> bool:
    """True if the message points back to the conversation ("these methods", "what about ...")."""
    text = question.strip().lower()
    return bool(_FOLLOW_UP_OPENING.match(text)) or any(w in _REFERRING_WORDS for w in _words(text))


def needs_context(question, chat) -> bool:
    """Ask the model (shown only the message) whether it leaves out its subject."""
    messages = [{"role": "system", "content": CONTEXT_PROMPT}]
    for example, label in CONTEXT_EXAMPLES:
        messages += [{"role": "user", "content": example}, {"role": "assistant", "content": label}]
    messages.append({"role": "user", "content": question})
    return chat(messages, max_tokens=2, grammar=yes_no_grammar()).strip().upper() == "YES"


def _stem(word):
    """Leading letters that survive inflection and derivation (China -> Chinese, study -> studies)."""
    if len(word) > 4:
        word = re.sub(r"(?:ies|es|s|ing|ed)$", "", word)
    return word[:4]


def keeps_message(question, rewritten) -> bool:
    """A rewrite must keep every content word of the message and add only a short subject."""
    if not rewritten or "\n" in rewritten:
        return False
    if len(rewritten.split()) > len(question.split()) + MAX_ADDED_WORDS:
        return False
    target = rewritten.lower()
    content = [w for w in _words(question) if w not in _STOPWORDS and (len(w) > 2 or w.isdigit())]
    return all(_stem(w) in target for w in content)


def _rewrite(question, turns, chat):
    context = "\n".join(
        f"User: {t['question']}\nAssistant: {(t.get('answer') or '')[:400]}" for t in turns
    )
    messages = [{"role": "system", "content": REWRITE_PROMPT}]
    for example, rewritten in REWRITE_EXAMPLES:
        messages += [
            {"role": "user", "content": f"Conversation:\n{_EXAMPLE_CONVERSATION}\n\nLatest message: {example}"},
            {"role": "assistant", "content": rewritten},
        ]
    messages.append({"role": "user", "content": f"Conversation:\n{context}\n\nLatest message: {question}"})
    return chat(messages, max_tokens=80).strip().strip('"').strip()


def standalone_question(question, history, chat) -> str:
    """
    The question to search for and to judge.  history: earlier turns as
    [{"question", "answer"}]; chat: query.chat().
    """
    turns = [t for t in (history or []) if t.get("question")][-HISTORY_TURNS:]
    if not turns:
        return question
    try:
        if not refers_back(question) and not needs_context(question, chat):
            return question
        rewritten = _rewrite(question, turns, chat)
    except Exception as exc:
        print(f"Follow-up rewrite failed: {exc}")
        return question
    if not keeps_message(question, rewritten):
        print(f"Follow-up rewrite rejected: {question!r} -> {rewritten!r}")
        return question
    return rewritten
