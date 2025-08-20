import soundfile as sf
from kokoro_onnx import Kokoro

model_path = "/home/rizvan/.local/share/app.kokoro.demo/models/kokoro-v0_19.onnx"
voices_path = "/home/rizvan/.local/share/app.kokoro.demo/models/voices-v1.0.bin"

kokoro = Kokoro(model_path, voices_path)

samples, sample_rate = kokoro.create(
    "hello world from kokoro tts",
    voice="af_heart",  # you can change to another available voice
    speed=1.0,
    lang="en-us"
)

sf.write("hello.wav", samples, sample_rate)
print("Saved to hello.wav")
