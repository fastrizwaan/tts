# pip install kittentts huggingface_hub soundfile onnxruntime
from huggingface_hub import hf_hub_download
from kittentts import KittenTTS
import numpy as np, re, unicodedata, soundfile as sf, shutil, subprocess

TEXT = """Get to scrubbin’. And I mean this literally and figuratively. If you want to stop drinking alcohol, remove every drop of it from your house (and your vacation house, if you have one). Get rid of the glasses, any fancy utensils or doo-dads you use when you drink, and those decorative olives, too. If you want to stop drinking coffee, heave the coffee maker, and give that bag of gourmet grounds to a sleepy neighbor. If you’re trying to curb your spending, take an evening and cancel every catalogue or retail offer that flies in through your mailbox or your inbox, so you won’t even need to muster the discipline to walk it from the front door to the recycle bin. If you want to eat more healthfully, clean your cupboards of all the crap, stop buying the junk food—and stop buying into the argument that it’s “not fair” to deny the other people in your family junk food just because you don’t want it in your life. Trust me; everyone in your family is better off without it. Don’t bring it into the house, period. Get rid of whatever enables your bad habits."""
TEXT = unicodedata.normalize("NFKC", TEXT)
SR = 24000
PAUSE = np.zeros(int(0.25 * SR), dtype=np.float32)

model = hf_hub_download("KittenML/kitten-tts-nano-0.2","kitten_tts_nano_v0_2.onnx")
voices = hf_hub_download("KittenML/kitten-tts-nano-0.2","voices.npz")
m = KittenTTS(model, voices_path=voices)

ALL_VOICES = [
    "expr-voice-2-m","expr-voice-2-f","expr-voice-3-m","expr-voice-3-f",
    "expr-voice-4-m","expr-voice-4-f","expr-voice-5-m","expr-voice-5-f",
]

sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', TEXT) if s.strip()]

def synth_line(voice, line, max_chars=160):
    chunks = [c.strip() for c in re.findall(r'.{1,%d}(?:\s|$)'%max_chars, line) if c.strip()]
    aud = [m.generate(c, voice=voice) for c in chunks]
    return np.concatenate(aud) if aud else np.zeros(0, dtype=np.float32)

players = [("ffplay", ["-nodisp","-autoexit","-hide_banner","-loglevel","error"]),
           ("paplay", []), ("aplay", []), ("cvlc", ["--play-and-exit","-Idummy"]),
           ("gst-play-1.0", [])]
player = next(((p,opts) for p,opts in players if shutil.which(p)), None)

for v in ALL_VOICES:
    clips = []
    for s in sentences:
        clips.append(synth_line(v, s))
        clips.append(PAUSE)
    wav = np.concatenate(clips) if clips else np.zeros(0, dtype=np.float32)
    out = f"{v}.wav"
    sf.write(out, wav, SR)
    print("Saved", out)
    if player:
        cmd = [player[0], *player[1], out]
        try: subprocess.run(cmd, check=False)
        except Exception: pass

