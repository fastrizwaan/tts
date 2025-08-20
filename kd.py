import soundfile as sf
from kokoro_onnx import Kokoro
import onnxruntime
from onnxruntime import InferenceSession

# See list of providers https://github.com/microsoft/onnxruntime/issues/22101#issuecomment-2357667377
ONNX_PROVIDER = "CUDAExecutionProvider"  # "CPUExecutionProvider"
OUTPUT_FILE = "output.wav"
VOICE_MODEL = "af_sky"  # "af" "af_nicole"

TEXT = """
Hey, wow, this works even for long text strings without any problems!
"""

print(f"Available onnx runtime providers: {onnxruntime.get_all_providers()}")
session = InferenceSession("kokoro-v0_19.onnx", providers=[ONNX_PROVIDER])
kokoro = Kokoro.from_session(session, "voices.json")
print(f"Generating text with voice model: {VOICE_MODEL}")
samples, sample_rate = kokoro.create(TEXT, voice=VOICE_MODEL, speed=1.0, lang="en-us")
sf.write(OUTPUT_FILE, samples, sample_rate)
print(f"Wrote output file: {OUTPUT_FILE}")
