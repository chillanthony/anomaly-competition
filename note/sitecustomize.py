"""Force the BiAn backend onto Apple MPS when no CUDA device is present.

Loaded automatically by CPython because the project root is on sys.path
(editable install).  Only affects the transformers loading path used by
baseline/bian/models/backend.py, which otherwise falls back to CPU.
"""

import os
import sys


def _patch() -> None:
    if sys.platform != "darwin":
        return
    if os.environ.get("AIOPS_FORCE_MPS") != "1":
        return
    try:
        import torch
        from transformers import AutoModelForCausalLM
    except Exception:
        return
    if torch.cuda.is_available() or not torch.backends.mps.is_available():
        return

    original = AutoModelForCausalLM.from_pretrained.__func__

    def from_pretrained(cls, *args, **kwargs):
        if isinstance(kwargs.get("device_map"), dict):
            kwargs.update({"torch_dtype": torch.bfloat16, "device_map": {"": "mps"}})
        return original(cls, *args, **kwargs)

    import functools

    AutoModelForCausalLM.from_pretrained = classmethod(functools.wraps(original)(from_pretrained))


_patch()
