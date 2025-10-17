import soundfile as sf
from kokoro_onnx import Kokoro

model_path = "/home/rizvan/.local/share/app.kokoro.demo/models/kokoro-v0_19.onnx"
voices_path = "/home/rizvan/.local/share/app.kokoro.demo/models/voices-v1.0.bin"

kokoro = Kokoro(model_path, voices_path)

samples, sample_rate = kokoro.create(
    "जापान के पूर्व प्रधानमंत्री शिंजो आबे का निधन, भाषण के दौरान मारी गयी थी गोली। काबुल के एक स्कूल के नजदीक बम विस्फोट होने से लगभग 85 लोगों की मृत्यु हो गई और 150 से अधिक लोग घायल हो गए। इज़राइल के माउंट मेरन में भगदड़ शुरू",
    voice="hm_omega",  # you can change to another available voice
    speed=1.0,
    lang="hi"
)

sf.write("hello.wav", samples, sample_rate)
print("Saved to hello.wav")
