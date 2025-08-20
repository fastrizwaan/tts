#!/usr/bin/env python3
import numpy as np, soundfile as sf
from onnxruntime import InferenceSession

MODEL  = "/home/rizvan/.local/share/app.kokoro.demo/models/timestamped/onnx/model.onnx"
VOICES = "/home/rizvan/.local/share/app.kokoro.demo/models/timestamped/voices-v1.0.bin"
VOICE_NAME = "af_bella"  # change if needed

def ensure_vec(x):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 3 and x.shape[-1] == 256: x = x[0,0]        # e.g., (1,1,256)
    elif x.ndim == 2 and x.shape[-1] == 256 and x.shape[0] == 1: x = x[0]
    elif x.ndim == 1 and x.size == 256: pass
    elif x.ndim == 2 and x.shape[-1] == 256 and x.shape[0] > 1:
        raise ValueError("Got multiple rows; pick one by index.")
    else:
        raise ValueError(f"Unexpected voice shape {x.shape}")
    return x.reshape(1,256)  # (1,256)

def load_voice_vec(npz, name):
    # 1) direct key
    if name in npz.files:
        return ensure_vec(npz[name])
    # 2) object-dict under a single key (e.g., 'voices' or 'data')
    for k in ("voices","data","voice_map"):
        if k in npz.files and npz[k].dtype == object:
            obj = npz[k].item() if npz[k].size == 1 else None
            if isinstance(obj, dict) and name in obj:
                return ensure_vec(obj[name])
    # 3) parallel arrays: names + embeddings
    for names_k, embs_k in (("names","embeddings"),("names","vectors"),("voice_names","voice_vectors")):
        if names_k in npz.files and embs_k in npz.files:
            names = [n.decode() if isinstance(n,(bytes,bytearray)) else str(n) for n in npz[names_k]]
            embs  = np.asarray(npz[embs_k])
            idx = names.index(name) if name in names else -1
            if idx >= 0:
                v = embs[idx]
                if v.ndim == 2 and v.shape[0] == 1: v = v[0]
                return ensure_vec(v)
    raise KeyError(f"Voice '{name}' not found in bundle; keys: {npz.files}")

# ---- run ----
sess = InferenceSession(MODEL, providers=["CPUExecutionProvider"])
npz  = np.load(VOICES, allow_pickle=True)

style = load_voice_vec(npz, VOICE_NAME)      # (1,256) float32
speed = np.array([1.0], np.float32)          # (1,)   float32
input_ids = np.array([[0, 10, 20, 30, 40, 50, 0]], np.int64)  # TODO: real phoneme IDs

out = sess.run(None, {"input_ids": input_ids, "style": style, "speed": speed})
audio = out[0].squeeze().astype(np.float32)
sf.write("out.wav", audio, 24000)
print("Saved out.wav")

