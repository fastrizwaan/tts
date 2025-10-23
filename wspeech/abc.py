import whisperspeech.pipeline as pipeline

pipe = pipeline.Pipeline()

pipe.generate_to_file("output.wav", "Hello, world!")
