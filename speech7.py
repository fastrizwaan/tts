import os, pathlib
import soundfile as sf
from gi.repository import GLib
from kokoro_onnx import Kokoro

def writable_path(filename):
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True)
    return os.path.join(d, filename)

# params
text = "Hi how are you? This is an example sentence."
lang = "en-us"
voice = "af_sarah"
speed = 1.0

kokoro = Kokoro("/app/share/kokoro-models/kokoro-v1.0.onnx",
                "/app/share/kokoro-models/voices-v1.0.bin")

print("=== Kokoro Debug ===")
print("Text:", text)
print("Language:", lang)
print("Voice:", voice)
print("Speed:", speed)
sentences = [s.strip() for s in text.split(".") if s.strip()]
print("Sentence count:", len(sentences))
for i, s in enumerate(sentences, 1):
    print(f"  {i}: {s}")

samples, sample_rate = kokoro.create(text, voice=voice, speed=speed, lang=lang)
print("Sample rate:", sample_rate)
print("Samples shape:", samples.shape)

out = writable_path("audio.wav")
sf.write(out, samples, sample_rate)
print(f"Created {out}")

