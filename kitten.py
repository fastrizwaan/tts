from huggingface_hub import hf_hub_download
from kittentts import KittenTTS
import soundfile as sf

model = hf_hub_download("KittenML/kitten-tts-nano-0.2", "kitten_tts_nano_v0_2.onnx")
voices = hf_hub_download("KittenML/kitten-tts-nano-0.2", "voices.npz")

m = KittenTTS(model, voices_path=voices)
audio = m.generate("He lives in the Airport. Where does he live? Blakc lives matter. Who lives matter to you? It is on live tv. The live acting was amazing. But he lives for the live performance.", voice="expr-voice-2-f")
sf.write("output.wav", audio, 24000)

