"""Hebrew Kokoro on MLX: its ONNX weights turned back into Kokoro's named weights, once per machine.

Hebrew Kokoro (thewh1teagle/kokoro-hebrew-nc) ships only as ONNX. Its exporter kept most weights' names
(`kmodel.decoder...`), but 117 of them (74M of 81M parameters) became `onnx::MatMul_NNNN` and the like. Each
of those feeds one operation named after its module (`/text_encoder/lstms.0/LSTM`), which gives its name back:

- MatMul's weight is the module's Linear weight, transposed.
- Conv / ConvTranspose's weight is weight_norm's folded product: stored back as weight_v = the weight and
  weight_g = its length per output channel, which multiply back to exactly the same weight.
- LSTM's W, R and B are both directions packed, gates in ONNX's order (i, o, f, c): split per direction,
  gates put back in PyTorch's (i, f, g, o), B split into bias_ih and bias_hh.

Every key and shape must match standard Kokoro's (kokoro_shapes.json, from hexgrad/Kokoro-82M) or it stops.
The result goes to ~/.voice-assistant/kokoro-hebrew/ (the model's licence is non-commercial: it isn't
published, each machine converts its own copy, in a few seconds). Checked: Hebrew Whisper hears the MLX
voice word for word as it hears the ONNX one, and they sound the same.
"""

import json
from pathlib import Path

import numpy as np

from home import HOME

HEBREW_REPO = "thewh1teagle/kokoro-hebrew-nc"
HEBREW_REVISION = "07e2334fc252d7965cb8dbd503bf7565bad71228"  # as hebrew_voice's
CONVERTED = HOME / "kokoro-hebrew"
SHAPES = Path(__file__).with_name("kokoro_shapes.json")
ONNX_GATES_TO_TORCH = [0, 2, 3, 1]  # i, o, f, c -> i, f, c(g), o
UNUSED = ("bert.pooler.weight", "bert.pooler.bias")  # BERT's pooler: computed, never used, left out of ONNX


def module_of(op_name: str) -> str:
    """'/text_encoder/lstms.0/LSTM' -> 'text_encoder.lstms.0'. A nested Sequential repeats its parent in the
    op's path ('/text_encoder/cnn.0/cnn.0.0/Conv' is text_encoder.cnn.0.0): the repeat is dropped."""
    parts = op_name.strip("/").split("/")[:-1]
    return ".".join(p for i, p in enumerate(parts) if not (i + 1 < len(parts) and parts[i + 1].startswith(p + ".")))


def resolve(mod: str, suffix: str, shape: tuple, ref: dict[str, tuple]) -> str:
    """The module's full name: some ops lost their top-level module ('/F0.0/conv1' is predictor.F0.0.conv1);
    when two modules end the same way ('/lstm'), the weight's shape picks."""
    if f"{mod}{suffix}" in ref:
        return mod
    found = {k[: -len(suffix)] for k in ref if k.endswith(f".{mod}{suffix}")}
    if len(found) > 1:
        found = {m for m in found if ref[f"{m}{suffix}"] == shape}
    if len(found) != 1:
        raise ValueError(f"can't place {mod}{suffix}: {sorted(found) or 'no match'}")
    return found.pop()


def gates(a: np.ndarray) -> np.ndarray:
    return np.concatenate([np.split(a, 4, axis=0)[i] for i in ONNX_GATES_TO_TORCH], axis=0)


def unfold(weight: np.ndarray, g_shape: tuple) -> tuple[np.ndarray, np.ndarray]:
    """weight_norm's folded weight -> (weight_v, weight_g): g is v's length over every axis but the one g keeps."""
    keep = next((i for i, n in enumerate(g_shape) if n > 1), None)  # None: one length for the whole weight
    axes = tuple(i for i in range(weight.ndim) if i != keep)
    return weight, np.sqrt((weight.astype(np.float64) ** 2).sum(axis=axes, keepdims=True)).astype(np.float32)


def convert(onnx_path: str, ref: dict[str, tuple]) -> dict[str, np.ndarray]:
    import onnx
    from onnx import numpy_helper

    model = onnx.load(onnx_path)
    inits = {i.name: numpy_helper.to_array(i).astype(np.float32) for i in model.graph.initializer}
    out = {name.removeprefix("kmodel."): v for name, v in inits.items() if name.startswith("kmodel.")}
    for node in model.graph.node:
        for slot, name in enumerate(node.input):
            if name not in inits or name.startswith("kmodel.") or node.op_type == "Identity":
                continue
            w = inits[name]
            if node.op_type == "MatMul":
                probe, shape = ".weight", w.T.shape
            elif node.op_type == "LSTM":  # placed by its input weights (slot 1), which tell the two LSTMs apart
                probe, shape = ".weight_ih_l0", inits[node.input[1]][0].shape
            elif slot == 2:
                probe, shape = ".bias", w.shape
            else:  # a conv's weight: weight_norm'd (its weight_v has the weight's shape) or plain
                m = module_of(node.name)
                probe, shape = (".weight_v" if any(k.endswith(f"{m}.weight_v") for k in ref) else ".weight"), w.shape
            mod = resolve(module_of(node.name), probe, tuple(shape), ref)
            if node.op_type == "MatMul" and slot == 1:
                out[f"{mod}.weight"] = w.T
            elif node.op_type in ("Conv", "ConvTranspose") and slot in (1, 2):
                if slot == 2:
                    out[f"{mod}.bias"] = w
                elif f"{mod}.weight_g" in ref:
                    out[f"{mod}.weight_v"], out[f"{mod}.weight_g"] = unfold(w, ref[f"{mod}.weight_g"])
                else:
                    out[f"{mod}.weight"] = w
            elif node.op_type == "LSTM" and slot in (1, 2, 3):
                for d, suffix in enumerate(("", "_reverse")):
                    if slot == 1:
                        out[f"{mod}.weight_ih_l0{suffix}"] = gates(w[d])
                    elif slot == 2:
                        out[f"{mod}.weight_hh_l0{suffix}"] = gates(w[d])
                    else:
                        wb, rb = np.split(w[d], 2)
                        out[f"{mod}.bias_ih_l0{suffix}"], out[f"{mod}.bias_hh_l0{suffix}"] = gates(wb), gates(rb)
            else:
                raise ValueError(f"don't know {node.op_type} slot {slot} ({node.name}, {name} {w.shape})")
    for k in UNUSED:
        out.setdefault(k, np.zeros(ref[k], np.float32))
    for k in [k for k in out if k not in ref]:  # ONNX's InstanceNorm affine (all ones / zeros) and STFT tables
        v = out[k]
        if not (k.endswith(".norm.weight") and np.allclose(v, 1) or k.endswith(".norm.bias") and np.allclose(v, 0) or ".stft." in k):
            raise ValueError(f"not a Kokoro weight and not a known constant: {k} {v.shape}")
        del out[k]
    missing = [k for k in ref if k not in out]
    wrong = [k for k in out if tuple(out[k].shape) != ref[k]]
    if missing or wrong:
        raise ValueError(f"{len(missing)} missing ({missing[:3]}), {len(wrong)} wrong shape ({wrong[:3]})")
    return out


def converted() -> tuple[Path, dict]:
    """The converted weights (converting them the first time) and Hebrew Kokoro's config (its IPA vocab)."""
    from huggingface_hub import hf_hub_download
    from safetensors.numpy import save_file

    weights, config = CONVERTED / "kokoro-hebrew.safetensors", CONVERTED / "config.json"
    if not weights.exists():
        ref = {k: tuple(v) for k, v in json.loads(SHAPES.read_text()).items()}
        out = convert(hf_hub_download(HEBREW_REPO, "kokoro.onnx", revision=HEBREW_REVISION), ref)
        CONVERTED.mkdir(parents=True, exist_ok=True)
        save_file({k: np.ascontiguousarray(v) for k, v in out.items()}, str(weights.with_suffix(".tmp")))
        weights.with_suffix(".tmp").rename(weights)
        config.write_text(Path(hf_hub_download(HEBREW_REPO, "config.json", revision=HEBREW_REVISION)).read_text())
    return weights, json.loads(config.read_text())


def load_model():
    """MLX Kokoro with Hebrew Kokoro's weights and vocab."""
    import mlx.core as mx
    from mlx_audio.tts.utils import load_model as load

    weights, config = converted()
    model = load("mlx-community/Kokoro-82M-bf16")
    model.vocab = config["vocab"]
    model.load_weights(list(model.sanitize(mx.load(str(weights))).items()), strict=True)
    mx.eval(model.parameters())
    return model
