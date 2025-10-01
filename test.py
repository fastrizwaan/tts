from bs4 import BeautifulSoup

html = """<h1 class="chapter-title"><span data-tts-id="dcf4ef6622d9">1.</span></h1>
<p class="first-in-chapter" style="text-align: justify"><span class="first-in-chapter"><span data-tts-id="ca73ab65568c"><span data-tts-id="ca73ab65568c"><span data-tts-id="ca73ab65568c">I</span></span></span></span><span data-tts-id="328d8e14f9e7"><span data-tts-id="328d8e14f9e7">t’s the cry of the albatross that rouses me in the morning.</span></span></p>
<p style="text-align: justify"><span data-tts-id="3ef2aa4e0679"><span data-tts-id="3ef2aa4e0679">The bird has been coming to my bedroom window for a few months now, always just after sunrise.</span></span> <span data-tts-id="8c41809fa36c"><span data-tts-id="8c41809fa36c">When I open my curtain, it is there, on the windowsill, cocking its head and looking at me curiously.</span></span> <span data-tts-id="36283b03de4e"><span data-tts-id="36283b03de4e">Meaningfully, even.</span></span></p>
<p style="text-align: justify"><span data-tts-id="1553ed13eae0"><span data-tts-id="1553ed13eae0">The elderly people on the island of Skylge might have told me that an albatross is a pure, human soul taking flight on earthly wings after death, but I’m not so sure I believe that.</span></span> <span data-tts-id="72001bbb1619"><span data-tts-id="72001bbb1619">Mostly, they just pick fights with the gulls on the beach at low tide, trying to grab the best food once the rocks littered with mussels rise above the brine.</span></span> <span data-tts-id="7a315964114f"><span data-tts-id="7a315964114f">Doesn’t look very pure to me.</span></span></p>"""

soup = BeautifulSoup(html, "html.parser")
parts = []
h = soup.find("h1", class_="chapter-title")
if h: parts.append(h.get_text(strip=True))
for p in soup.find_all("p"):
    txt = p.get_text(separator=" ", strip=True)
    txt = txt.replace("I t’s", "It’s")  # fix split-first-letter cases
    parts.append(txt)
print("\n\n".join(parts))
