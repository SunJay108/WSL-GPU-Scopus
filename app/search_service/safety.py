"""
Safety models (Qwen3Guard, Apache-2.0):

- Qwen3Guard-Gen judges every question before anything is searched (input).
- Qwen3Guard-Stream checks the answer token by token while it is written
  (output).  The page only shows text that has already been checked (a
  sentence behind the model), and the answer is stopped as soon as the model
  marks it unsafe.

SAFETY_*_DEVICE=auto puts a model on the GPU only if it fits next to the
other models; on a 6 GB GPU the answer check gets the GPU and the question
check runs on the CPU, in parallel with the topic classification (guard.py).

Both report a risk level (Safe / Controversial / Unsafe) and a category.
Unsafe is always refused; Controversial only in the categories listed in
SAFETY_INPUT_CONTROVERSIAL / SAFETY_OUTPUT_CONTROVERSIAL.  When a model folder
is missing that check is skipped (with a warning); the other guard stages in
guard.py still apply.

Qwen3Guard-Stream is loaded without its own Python code (trust_remote_code):
the backbone is transformers' built-in Qwen3 model and the four small
classification heads are read from the checkpoint here.  Answer tokens can
then be checked in chunks, with exactly the same per-token results as
feeding them one at a time.
"""

import os
import re
import threading
import time
from dataclasses import dataclass

import torch
from torch import nn

from core.config import (
    SAFETY_INPUT, SAFETY_INPUT_CONTROVERSIAL, SAFETY_INPUT_DEVICE, SAFETY_INPUT_MODEL_PATH,
    SAFETY_OUTPUT, SAFETY_OUTPUT_CONTROVERSIAL, SAFETY_OUTPUT_DEVICE, SAFETY_OUTPUT_MIN_TOKENS,
    SAFETY_OUTPUT_MODEL_PATH,
)

# Qwen3Guard-Gen writes long category names; Qwen3Guard-Stream (and the
# settings) use the short ones.
SHORT_CATEGORY = {
    "Non-violent Illegal Acts": "Illegal Acts",
    "Sexual Content or Sexual Acts": "Sexual Content",
    "Suicide & Self-Harm": "Self-Harm",
    "Unethical Acts": "Unethical",
    "Politically Sensitive Topics": "Political",
    "Copyright Violation": "Copyright",
}
# Guard category (guard.MESSAGES) shown to the user for each safety category.
GUARD_CATEGORY = {"Political": "RESTRICTED", "PII": "PERSONAL", "Self-Harm": "SELF_HARM",
                  "Jailbreak": "JAILBREAK"}

_load_lock = threading.Lock()
_input = None          # (tokenizer, model, device) or False when unavailable
_output = None         # StreamGuard or False when unavailable


class UnsafeOutput(Exception):
    """Raised inside the answer stream when the output check stops it."""

    def __init__(self, verdict):
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass
class Verdict:
    risk: str                  # Safe | Controversial | Unsafe
    categories: list
    blocked: bool

    @property
    def guard_category(self):
        return next((GUARD_CATEGORY[c] for c in self.categories if c in GUARD_CATEGORY), "HARMFUL")

    @property
    def reason(self):
        return f"{self.risk} ({', '.join(self.categories) or 'no category'})"


def _is_blocked(risk, categories, controversial):
    return risk == "Unsafe" or (risk == "Controversial" and any(c in controversial for c in categories))


# GPU memory kept free besides the model itself (activations, the other models' growth).
GPU_RESERVE = 512 * 2**20


def _device(requested, model_path):
    """cuda / cpu as configured; auto = the GPU only if the model fits next to the others."""
    requested = (requested or "auto").strip().lower()
    if requested == "auto":
        if not torch.cuda.is_available():
            return "cpu"
        free, _ = torch.cuda.mem_get_info()
        need = os.path.getsize(os.path.join(model_path, "model.safetensors")) + GPU_RESERVE
        if free < need:
            print(f"Not enough free GPU memory for {os.path.basename(model_path)} "
                  f"({free / 2**30:.1f} GB free, {need / 2**30:.1f} GB needed); using the CPU.")
            return "cpu"
        return "cuda"
    if requested.startswith("cuda") and not torch.cuda.is_available():
        print("⚠️ CUDA requested for a safety model but unavailable; using the CPU.")
        return "cpu"
    return requested


def _dtype(device):
    # fp16 on the GPU (bf16 needs compute capability 8.0+); fp32 on the CPU.
    return torch.float16 if device.startswith("cuda") else torch.float32


# =====================================================
# Input: Qwen3Guard-Gen
# =====================================================

class QuestionGuard:
    """
    Qwen3Guard-Gen.  Its reply starts with "Safety: Safe|Unsafe|Controversial",
    so one forward pass decides; the category line is only generated for
    questions that are not safe.  The long policy text at the start of the
    prompt is the same for every question and stays in the cache.
    """

    LABELS = ("Safe", "Unsafe", "Controversial")

    def __init__(self, path, device):
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForCausalLM.from_pretrained(path, dtype=_dtype(device)).to(device).eval()
        self.end_id = self.tokenizer.convert_tokens_to_ids("<|im_end|>")
        # First token of " Safe", " Unsafe", " Controversial" after "Safety:".
        self.label_ids = [self.tokenizer(" " + label, add_special_tokens=False).input_ids[0] for label in self.LABELS]
        if len(set(self.label_ids)) != len(self.LABELS):
            raise RuntimeError("unexpected tokenizer for the question safety model")
        self.lock = threading.Lock()
        self.cache, self.cached_ids = None, []

    def _prompt_ids(self, question):
        prompt = self.tokenizer.apply_chat_template([{"role": "user", "content": question}], tokenize=False)
        return self.tokenizer(prompt + "Safety:", add_special_tokens=False).input_ids

    def _forward(self, ids):
        x = torch.tensor([ids], dtype=torch.long, device=self.device)
        return self.model(input_ids=x, past_key_values=self.cache, use_cache=True, logits_to_keep=1).logits[0, -1]

    @torch.no_grad()
    def check(self, question):
        with self.lock:
            ids = self._prompt_ids(question)
            if self.cache is None:
                from transformers import DynamicCache
                self.cache = DynamicCache()
            # Reuse the cached part shared with this prompt (the policy text).
            shared = 0
            while shared < min(len(self.cached_ids), len(ids) - 1) and self.cached_ids[shared] == ids[shared]:
                shared += 1
            if shared < len(self.cached_ids):
                self.cache.crop(shared - self.cache.get_seq_length())
                self.cached_ids = ids[:shared]
            if not self.cached_ids:
                # First question: cache everything up to the question itself.
                shared = self._policy_length(ids)
                self._forward(ids[:shared])
                self.cached_ids = ids[:shared]
            try:
                logits = self._forward(ids[shared:])
                scores = logits[self.label_ids]
                risk = self.LABELS[int(scores.argmax())]
                categories = self._categories(self.label_ids[int(scores.argmax())]) if risk != "Safe" else []
            finally:
                self.cache.crop(len(self.cached_ids) - self.cache.get_seq_length())
        return Verdict(risk, categories, _is_blocked(risk, categories, SAFETY_INPUT_CONTROVERSIAL))

    def _policy_length(self, ids):
        """Tokens before the question (identical for every question)."""
        other = self._prompt_ids("x")
        n = 0
        while n < min(len(ids), len(other)) and ids[n] == other[n]:
            n += 1
        return max(1, n - 2)

    def _categories(self, first_id):
        """Greedy reply after the risk label: 'Unsafe\\nCategories: Violent ...'."""
        out, next_id = [], first_id
        for _ in range(40):
            out.append(next_id)
            next_id = int(self._forward([next_id]).argmax())
            if next_id == self.end_id:
                break
        reply = self.tokenizer.decode(out, skip_special_tokens=True)
        line = _CATEGORIES_RE.search(reply)
        names = [_CATEGORY_NAMES.get(c.strip().lower(), c.strip()) for c in (line.group(1) if line else "").split(",")]
        return [c for c in names if c and c != "None"]


_CATEGORIES_RE = re.compile(r"Categories:\s*([^\n]*)")
# Any spelling the model writes ("Suicide & Self-harm") -> the short name.
_CATEGORY_NAMES = {**{long.lower(): short for long, short in SHORT_CATEGORY.items()},
                   **{short.lower(): short for short in list(SHORT_CATEGORY.values()) + ["Violent", "PII", "Jailbreak"]}}


def _load_input():
    global _input
    if _input is not None:
        return _input
    if not SAFETY_INPUT:
        _input = False
    elif not os.path.isdir(SAFETY_INPUT_MODEL_PATH):
        print(f"⚠️ Question safety model not found ({SAFETY_INPUT_MODEL_PATH}); input check skipped.")
        _input = False
    else:
        device = _device(SAFETY_INPUT_DEVICE, SAFETY_INPUT_MODEL_PATH)
        print(f"Loading question safety model on {device}...")
        _input = QuestionGuard(SAFETY_INPUT_MODEL_PATH, device)
    return _input


def check_input(question):
    """Verdict of Qwen3Guard-Gen for one question, or None when the check is off."""
    with _load_lock:
        guard = _load_input()
    return guard.check(question) if guard else None


# =====================================================
# Output: Qwen3Guard-Stream
# =====================================================

class StreamGuard(nn.Module):
    """Qwen3 backbone + risk/category heads for the user turn ("query") and the answer ("response")."""

    HEADS = (("pre", "risk_level_category_pre"), ("norm", "risk_level_category_layernorm"),
             ("risk", "risk_level_head"), ("category", "category_head"))

    def __init__(self, path, device):
        super().__init__()
        from safetensors import safe_open
        from transformers import AutoTokenizer, Qwen3Config, Qwen3Model
        from transformers.models.qwen3.modeling_qwen3 import Qwen3RMSNorm

        dtype = _dtype(device)
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        config = Qwen3Config.from_pretrained(path)
        self.backbone = Qwen3Model.from_pretrained(path, config=config, dtype=dtype)
        self.max_tokens = config.max_position_embeddings
        inner = config.guard_inner_size
        sizes = {"response": (config.num_risk_level, config.num_category),
                 "query": (config.num_query_risk_level, config.num_query_category)}
        self.heads = nn.ModuleDict({
            part: nn.ModuleDict({
                "pre": nn.Linear(config.hidden_size, inner, bias=False),
                "norm": Qwen3RMSNorm(inner, eps=config.rms_norm_eps),
                "risk": nn.Linear(inner, n_risk, bias=False),
                "category": nn.Linear(inner, n_category, bias=False),
            })
            for part, (n_risk, n_category) in sizes.items()
        })
        state = {}
        with safe_open(os.path.join(path, "model.safetensors"), framework="pt") as f:
            for part, prefix in (("response", ""), ("query", "query_")):
                for mine, theirs in self.HEADS:
                    state[f"{part}.{mine}.weight"] = f.get_tensor(f"{prefix}{theirs}.weight")
        self.heads.load_state_dict(state)
        self.labels = {
            "response": ({int(k): v for k, v in config.response_risk_level_map.items()},
                         {int(k): v for k, v in config.response_category_map.items()}),
            "query": ({int(k): v for k, v in config.query_risk_level_map.items()},
                      {int(k): v for k, v in config.query_category_map.items()}),
        }
        self.device_name = device
        self.to(device=device, dtype=dtype).eval()

    def new_cache(self):
        from transformers import DynamicCache
        try:
            return DynamicCache(config=self.backbone.config)
        except TypeError:
            return DynamicCache()

    @torch.no_grad()
    def step(self, ids, cache, part):
        """Feed token ids after the cached ones; (risk, category) for each fed token."""
        x = torch.tensor([ids], dtype=torch.long, device=self.device_name)
        hidden = self.backbone(input_ids=x, past_key_values=cache, use_cache=True).last_hidden_state[0]
        head = self.heads[part]
        z = head["norm"](head["pre"](hidden))
        risks = head["risk"](z).argmax(-1).tolist()
        categories = head["category"](z).argmax(-1).tolist()
        risk_names, category_names = self.labels[part]
        return [(risk_names[r], category_names[c]) for r, c in zip(risks, categories)]


def _load_output():
    global _output
    if _output is not None:
        return _output
    if not SAFETY_OUTPUT:
        _output = False
    elif not os.path.isdir(SAFETY_OUTPUT_MODEL_PATH):
        print(f"⚠️ Answer safety model not found ({SAFETY_OUTPUT_MODEL_PATH}); output check skipped.")
        _output = False
    else:
        device = _device(SAFETY_OUTPUT_DEVICE, SAFETY_OUTPUT_MODEL_PATH)
        print(f"Loading answer safety model on {device}...")
        _output = StreamGuard(SAFETY_OUTPUT_MODEL_PATH, device)
    return _output


class OutputMonitor:
    """
    Checks one answer while it is written.  update(text) with the answer so far
    (it may also change earlier text, e.g. when a continuation is merged);
    verified(text) is the part already checked and safe; finish(text) checks
    the rest and returns the Verdict (blocked or not).  The check runs in its
    own thread, so the answer is not slowed down by waiting for it.
    """

    HOLD_BACK = 2     # the last tokens may still change when more text arrives
    # The model sometimes flags a sentence only a few tokens after it ends, so
    # a token is shown once this many later tokens were checked as well.
    SHOW_AFTER = 24
    # Check in chunks at most this often (seconds): every check costs Python
    # time that the answer generation in the other thread needs.
    INTERVAL = 0.25

    def __init__(self, guard, question):
        self.guard = guard
        tok = guard.tokenizer
        marker = "⁣ANSWER⁣"
        text = tok.apply_chat_template(
            [{"role": "user", "content": question}, {"role": "assistant", "content": marker}],
            tokenize=False, add_generation_prompt=False, enable_thinking=False)
        prefix_ids = tok(text[:text.index(marker)], add_special_tokens=False).input_ids
        # As in the reference code: the user turn up to its <|im_end|> is read
        # with the query heads, everything after it with the response heads.
        im_end = tok.convert_tokens_to_ids("<|im_end|>")
        self.n_user = prefix_ids.index(im_end) + 1
        self.header = prefix_ids[self.n_user:]
        self.cache = guard.new_cache()
        guard.step(prefix_ids[:self.n_user], self.cache, "query")

        self.fed, self.risks, self.categories = [], [], []
        self.header_fed = False
        self.verified_chars = 0
        self.flagged = None
        self.too_long = False
        self._text, self._final, self._done = "", False, False
        self._error = None
        self._cond = threading.Condition()
        self._thread = threading.Thread(target=self._run, name="answer-safety", daemon=True)
        self._thread.start()

    # ---- called by the answer thread ----

    def update(self, text):
        with self._cond:
            self._text = text
            self._cond.notify()
        if self.flagged:
            raise UnsafeOutput(self.flagged)

    def verified(self, text):
        return text[:self.verified_chars]

    def finish(self, text):
        with self._cond:
            self._text, self._final = text, True
            self._cond.notify()
            while not self._done:
                self._cond.wait()
        if self._error:
            print(f"⚠️ Answer safety check failed ({self._error}); answer not checked to the end.")
        return self.flagged or Verdict("Safe", [], False)

    def close(self):
        with self._cond:
            self._final, self._done = True, True
            self._cond.notify_all()

    # ---- worker thread ----

    def _run(self):
        processed, last = None, 0.0
        try:
            while True:
                with self._cond:
                    while not self._done and not self._final:
                        wait = self.INTERVAL - (time.time() - last)
                        if self._text != processed and wait <= 0:
                            break
                        self._cond.wait(timeout=wait if wait > 0 else None)
                    if self._done:
                        return
                    text, final = self._text, self._final
                self._process(text, final)
                processed, last = text, time.time()
                if final or self.flagged:
                    break
        except Exception as exc:  # never break the answer because of the check
            self._error = exc
            self.verified_chars = 10 ** 9
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def _process(self, text, final):
        enc = self.guard.tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        ids, offsets = enc["input_ids"], enc["offset_mapping"]
        stable = len(ids) if final else max(0, len(ids) - self.HOLD_BACK)

        # Rewind if earlier text changed.
        same = 0
        while same < min(len(self.fed), stable) and self.fed[same] == ids[same]:
            same += 1
        if same < len(self.fed):
            self.cache.crop(self.n_user + len(self.header) + same - self.cache.get_seq_length())
            del self.fed[same:], self.risks[same:], self.categories[same:]

        new = ids[len(self.fed):stable]
        room = self.guard.max_tokens - self.n_user - len(self.header) - len(self.fed)
        if len(new) > room:
            # Longer than the model's context: the rest is not checked.
            if not self.too_long:
                print("⚠️ Answer longer than the safety model's context; the end is not checked.")
            self.too_long = True
            new = new[:max(0, room)]
        if new or not self.header_fed:
            chunk = ([] if self.header_fed else self.header) + new
            labels = self.guard.step(chunk, self.cache, "response") if chunk else []
            if not self.header_fed:
                labels = labels[len(self.header):]
                self.header_fed = True
            self.fed += new
            self.risks += [r for r, _ in labels]
            self.categories += [c for _, c in labels]
            self._check()
        if not self.flagged:
            shown = len(self.fed) - (0 if final else self.SHOW_AFTER)
            self.verified_chars = len(text) if self.too_long else (offsets[shown - 1][1] if shown > 0 else 0)

    def _check(self):
        """Blocked when SAFETY_OUTPUT_MIN_TOKENS tokens in a row are judged unsafe."""
        run = 0
        for risk, category in zip(self.risks, self.categories):
            run = run + 1 if _is_blocked(risk, [category], SAFETY_OUTPUT_CONTROVERSIAL) else 0
            if run >= SAFETY_OUTPUT_MIN_TOKENS:
                self.flagged = Verdict(risk, [category], True)
                return


def output_monitor(question):
    """An OutputMonitor for one answer, or None when the output check is off."""
    with _load_lock:
        guard = _load_output()
    return OutputMonitor(guard, question) if guard else None


def check_output(question, text):
    """Check a finished text (summary, suggestions) in one pass; None when the check is off."""
    monitor = output_monitor(question)
    if monitor is None:
        return None
    try:
        return monitor.finish(text)
    finally:
        monitor.close()


def load():
    """
    Load both models (first question, after the other models).  The answer
    check runs for every token, so it gets the GPU first when memory is short.
    """
    with _load_lock:
        _load_output()
        _load_input()


def loaded() -> bool:
    return _input is not None and _output is not None
