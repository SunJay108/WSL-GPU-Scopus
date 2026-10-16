"""
Grounding: every sentence shown to the user must be supported by the papers
the answer was written from.

The language model is told to answer only from the numbered sources and to
cite them, but a model can still add outside knowledge.  Each sentence is
therefore checked as soon as it is complete (while the answer streams), and
only supported sentences are shown:

1. Support: the cross-encoder of the search (the reranker) scores the sentence
   against every source passage; the best score must reach
   GROUNDING_MIN_SCORE.
2. Numbers: every number in the sentence (results, years, sample sizes) must
   appear in the sources.
3. Names: every name, acronym or term written with capitals ("ResNet",
   "LSTM", "Smith", "Europe") must appear in the sources.

Sentences that only say what the sources do not cover are kept (without a
citation).  The citation [n] of a kept sentence is the source that supports
it best, preferring the one the model cited.  A model that cites as "(n)" at
the end of a sentence or list item is understood the same way, when n is one
of the source numbers.
"""

import re
from dataclasses import dataclass

from core.config import GROUNDING_MIN_SCORE

# Periods that do not end a sentence ("Smith et al. (2024) showed ...").
_ABBREVIATIONS = (
    "et al.", "e.g.", "i.e.", "etc.", "vs.", "cf.", "approx.",
    "Fig.", "Figs.", "Eq.", "Ref.", "No.", "Vol.", "Dr.", "Prof.",
)
_DOT = "․"  # placeholder for protected periods

_CITATION = re.compile(r"\s*\[(\d+(?:\s*[,;–-]\s*\d+)*)\]")
# The model sometimes cites as "(2)" or "(Source 2)" at the end of a sentence or list item.
_PAREN_CITATION = re.compile(r"\s*\((?:sources?\s*)?(\d+(?:\s*[,;–-]\s*\d+)*)\)(?=[.!?]*[\"”’]*\s*$)", re.IGNORECASE)
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[^\W_][\w'’-]*")
_LIST_MARKER = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+)")
_MARKDOWN = re.compile(r"[*_`#>]+")
# "The sources do not discuss ...", "No information about ... is provided".
_NOT_COVERED = re.compile(
    r"\b(sources?|papers?|studies|abstracts?|literature|documents?|articles?|provided information)\b"
    r".*\b(not|no|neither|nor|lack|lacks|without)\b"
    r"|\b(no|not enough|insufficient)\b.*\b(information|data|evidence|details)\b",
    re.IGNORECASE)


def split_sentences(text):
    protected = text.strip()
    for abbr in _ABBREVIATIONS:
        protected = protected.replace(abbr, abbr.replace(".", _DOT))
    protected = re.sub(r"\b([A-Z])\.(?=\s+[A-Z])", r"\1" + _DOT, protected)  # initials
    parts = re.split(r"(?<=[.!?])\s+", protected)
    return [p.replace(_DOT, ".").strip() for p in parts if p.strip()]


def _numbers(text):
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _terms(sentence):
    """
    Names, acronyms and terms: words with capitals, except a first word that
    is only capitalized because it starts the sentence ("Smith et al.",
    "Smith's" and "ResNet" at the start do count).
    """
    words = _WORD.findall(sentence)
    base = lambda w: re.sub(r"['’]s?$", "", w.lower())           # Smith's -> smith
    terms = {base(w) for w in words[1:] if any(c.isupper() for c in w)}
    if words:
        first = words[0]
        rest = sentence.lstrip()[len(first):]
        if any(c.isupper() for c in first[1:]) or re.match(r"\s+et al\b", rest) or re.search(r"['’]s$", first):
            terms.add(base(first))
    return terms


@dataclass
class Source:
    n: int           # reference number shown to the user
    text: str        # passage given to the model
    header: str      # title (year, journal)


@dataclass
class Verdict:
    keep: bool
    citation: int = None
    score: float = 0.0
    reason: str = ""


class AnswerVerifier:
    """
    render(text) returns the supported complete sentences of `text`, with
    citations, keeping paragraphs and list items.  Call it as often as the
    answer grows; every sentence is checked once.
    """

    def __init__(self, sources, score_pairs, question=""):
        self.sources = sources
        self.score_pairs = score_pairs          # [(sentence, passage), ...] -> [float, ...]
        known = " ".join(f"{s.header} {s.text}" for s in sources)
        self.known_text = known.lower()
        self.known_numbers = _numbers(known)
        self.question_numbers = _numbers(question)
        self.source_numbers = {s.n for s in sources}
        self.verdicts = {}
        self.removed = []                       # (sentence, reason) of dropped sentences

    def normalize(self, sentence):
        """A final "(2)" naming sources is a citation like "[2]", not a number of the claim."""
        m = _PAREN_CITATION.search(sentence)
        if m and {int(n) for n in re.findall(r"\d+", m.group(1))} <= self.source_numbers:
            sentence = sentence[:m.start()] + f" [{m.group(1)}]" + sentence[m.end():]
        return sentence

    # ---- one sentence ----

    def verdict(self, sentence):
        sentence = self.normalize(sentence)
        if sentence not in self.verdicts:
            self.verdicts[sentence] = v = self._check(sentence)
            if not v.keep:
                self.removed.append((sentence, v.reason))
        return self.verdicts[sentence]

    def _check(self, sentence):
        cited = {int(n) for group in _CITATION.findall(sentence) for n in re.findall(r"\d+", group)}
        clean = _MARKDOWN.sub("", _CITATION.sub("", sentence)).strip()
        if len(_WORD.findall(clean)) < 3:
            return Verdict(False, reason="too short")

        missing = _numbers(clean) - self.known_numbers
        if _NOT_COVERED.search(clean):
            if missing - self.question_numbers:
                return Verdict(False, reason=f"numbers not in the sources: {sorted(missing)}")
            return Verdict(True, reason="says what the sources do not cover")
        if missing:
            return Verdict(False, reason=f"numbers not in the sources: {sorted(missing)}")
        unknown = [t for t in _terms(clean) if t not in self.known_text]
        if unknown:
            return Verdict(False, reason=f"names not in the sources: {unknown}")

        scores = self.score_pairs([(clean, s.text) for s in self.sources])
        best = {}
        for source, score in zip(self.sources, scores):
            best[source.n] = max(best.get(source.n, 0.0), float(score))
        supported = {n: sc for n, sc in best.items() if sc >= GROUNDING_MIN_SCORE}
        if not supported:
            top = max(best.values(), default=0.0)
            return Verdict(False, score=top, reason=f"not supported by the sources (best {top:.2f})")
        pool = {n: sc for n, sc in supported.items() if n in cited} or supported
        n = max(pool, key=pool.get)
        return Verdict(True, citation=n, score=pool[n], reason="supported")

    # ---- whole answer ----

    def render(self, text, final=False):
        """Supported sentences of `text`; the last one only when `final` (it may be unfinished)."""
        lines = []                                       # (marker, [sentences], is_heading, block)
        for b, block in enumerate(re.split(r"\n\s*\n", text.strip())):
            for line in block.split("\n"):
                if not line.strip():
                    continue
                m = _LIST_MARKER.match(line)
                marker, body = (m.group(1), line[m.end():]) if m else ("", line)
                heading = line.lstrip().startswith("#") or (body.rstrip().endswith(":") and len(body.split()) <= 10)
                lines.append((marker, [] if heading else _sentences(body), heading, b, body.strip()))
        if not final and lines:
            marker, sentences, heading, b, body = lines[-1]
            lines[-1] = (marker, sentences[:-1], heading, b, body) if not heading else (marker, [], True, b, "")

        out, pending_heading, seen = [], None, set()
        for marker, sentences, heading, b, body in lines:
            if heading:
                pending_heading = (b, _MARKDOWN.sub("", body).strip()) if body else None
                continue
            kept = []
            for sentence in sentences:
                sentence = self.normalize(sentence)
                key = _CITATION.sub("", sentence).strip().lower()
                if key in seen:          # a sentence the model repeated
                    continue
                v = self.verdict(sentence)
                if v.keep:
                    seen.add(key)
                    kept.append(_with_citation(sentence, v.citation))
            if not kept:
                continue
            if pending_heading:
                out.append((pending_heading[0], pending_heading[1]))
                pending_heading = None
            out.append((b, marker + " ".join(kept)))
        # Paragraphs separated by blank lines, lines of a list by newlines.
        text_out, last_block = "", None
        for b, line in out:
            if last_block is not None:
                text_out += "\n" if b == last_block else "\n\n"
            text_out += line
            last_block = b
        return text_out

    def supported_claims(self):
        return sum(1 for v in self.verdicts.values() if v.keep and v.citation)


def _sentences(text):
    """Sentences of one line; a citation written after the full stop ("... voltages. [1]")
    belongs to the sentence before it, and a line of citations alone is no sentence."""
    out = []
    for s in split_sentences(text):
        if _WORD.search(_CITATION.sub("", s)):
            out.append(s)
        elif out:
            out[-1] += " " + s
    return out


def _with_citation(sentence, n):
    clean = _CITATION.sub("", sentence).strip()
    if not n:
        return clean
    m = re.match(r"^(.*?)([.!?]+[\"”’)]*)$", clean)
    return f"{m.group(1)} [{n}]{m.group(2)}" if m else f"{clean} [{n}]"
