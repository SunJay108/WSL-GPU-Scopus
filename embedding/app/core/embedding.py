"""
The embedding model (multilingual-e5-small): the embedding app embeds paper
passages, the search app the questions.  Both apps have a copy of this file;
the copies must stay identical.

e5 models are trained with "passage: " in front of documents and "query: "
in front of questions; indexes built before these prefixes were used
(legacy=True) are queried without them.
"""

import gc

from .config import EMBED_MODEL_PATH

QUERY_PREFIX = "query: "
PASSAGE_PREFIX = "passage: "


def resolve_device(requested):
    import torch

    requested = (requested or "cpu").strip().lower()
    if requested.startswith("cuda") and not torch.cuda.is_available():
        print("⚠️ CUDA requested but unavailable; embedding on the CPU.")
        return "cpu"
    return requested


def load(device="cuda", fp16=True):
    """SentenceTransformer on `device`; half precision on CUDA (several times faster, same ranking)."""
    import torch
    from sentence_transformers import SentenceTransformer

    device = resolve_device(device)
    model = SentenceTransformer(EMBED_MODEL_PATH, device=device)
    if device.startswith("cuda") and fp16:
        model.half()
        torch.cuda.empty_cache()  # give the freed fp32 copy back
    return model


def release(model):
    del model
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def device_label(model):
    import torch

    label = str(model.device)
    if next(model.parameters()).dtype == torch.float16:
        label += " (fp16)"
    return label


def embed_passages(model, texts, batch_size=128):
    """Normalized vectors of document passages (list of float lists)."""
    vectors = model.encode([PASSAGE_PREFIX + t for t in texts], batch_size=batch_size,
                           normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def embed_queries(model, texts, batch_size=64):
    vectors = model.encode([QUERY_PREFIX + t for t in texts], batch_size=batch_size,
                           normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
    return vectors.tolist()


def embed_query(model, text, legacy=False):
    vector = model.encode([text if legacy else QUERY_PREFIX + text], normalize_embeddings=True,
                          convert_to_numpy=True, show_progress_bar=False)[0]
    return vector.tolist()
