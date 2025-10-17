#!/usr/bin/env python3
# Auto-start after PREROLL; gapless; ordered; keys: 'z'=pause/resume, 's'=stop+exit.
import os, re, pathlib, threading, subprocess, sys, termios, tty, time
import numpy as np, soundfile as sf
from multiprocessing import Process, Queue as MPQ
from gi.repository import GLib
from kokoro_onnx import Kokoro

# --- config ---
MODEL="/app/share/kokoro-models/kokoro-v1.0.onnx"
VOICES="/app/share/kokoro-models/voices-v1.0.bin"
TEXT=("This is the 1st sentence. This is 2nd sentence. This is 3rd sentence! "
      "Is this 4th sentence? This is 5th sentence. And this is 6th sentence. "
      "and this is 7th sentence. And while it is 8th sentence. "
      "and this should be 9th sentence. And to stop the long string this is the 10th sentence.")
LANG="en-us"; VOICE="af_sarah"; SPEED=1.0
SR=24000; CHUNK_FRAMES=2400; PREROLL=3  # ~0.1s chunks
# ---------------

def outdir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or "/tmp"
    pathlib.Path(d).mkdir(parents=True, exist_ok=True); return d

def tokenize(t): return [p.strip() for p in re.split(r'(?<=[.!?])\s+', t.strip()) if p.strip()]
def f32_to_s16le(x): return (np.clip(x,-1,1)*32767.0).astype('<i2').tobytes()

def synth_one(kok, idx, sent, d):
    print(f"[SYNTH]{idx}: {sent}")
    wav, sr = kok.create(sent, voice=VOICE, speed=SPEED, lang=LANG)
    if sr != SR: print(f"[WARN] sr={sr}!=SR={SR}")
    path = os.path.join(d, f"kokoro_sent_{idx:02d}.wav"); sf.write(path, wav, sr)
    pcm = f32_to_s16le(wav); print(f"[FILE ]{idx}: {path} bytes={len(pcm)}"); return pcm, path

def producer_proc(sents, start_idx, d, q: MPQ):
    kok = Kokoro(MODEL, VOICES)
    try:
        for i in range(start_idx, len(sents)+1):
            try: q.put((i,)+synth_one(kok, i, sents[i-1], d))
            except Exception as e: print(f"[PROD ] err#{i}: {e}")
    finally:
        q.put((None,None,None))

def choose_play_cmd():
    for c in (["pacat","--rate",str(SR),"--channels","1","--format","s16le"],
              ["pw-cat","-p","--rate",str(SR),"--format","s16_le","--channels","1"]):
        try: subprocess.run([c[0],"--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return c
        except Exception: pass
    return None

class Controls:
    def __init__(self):
        self.paused = threading.Event()   # start playing immediately
        self.stop   = threading.Event()

def open_tty():
    if sys.stdin.isatty(): return sys.stdin
    try: return open("/dev/tty","rb", buffering=0)
    except Exception: return None

def keyboard_thread(ctrl: Controls, q: MPQ):
    f = open_tty()
    if not f: print("[KEYS ] No TTY. Use a terminal."); return
    fd = f.fileno()
    old = termios.tcgetattr(fd)
    # cbreak + no-echo
    new = termios.tcgetattr(fd)
    new[3] = new[3] & ~(termios.ECHO | termios.ICANON)  # lflag: -ECHO -ICANON
    termios.tcsetattr(fd, termios.TCSANOW, new)
    print("[KEYS ] 'z'=pause/resume  's'=stop+exit")
    try:
        while not ctrl.stop.is_set():
            ch = os.read(fd,1).decode(errors="ignore")
            if ch=='z':
                if ctrl.paused.is_set(): ctrl.paused.clear(); print("[KEYS ] resume")
                else: ctrl.paused.set(); print("[KEYS ] pause")
            elif ch=='s':
                ctrl.stop.set(); print("[KEYS ] stop")
                try: q.put((None,None,None))
                except Exception: pass
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if f is not sys.stdin: f.close()

def player_thread_ordered(qin: MPQ, ctrl: Controls, total: int):
    cmd = choose_play_cmd()
    if not cmd: print("[ERROR] pacat/pw-cat not found"); return
    print(f"[PLAY ] start: {' '.join(cmd)}")
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as p:
        frame_bytes=2; step=CHUNK_FRAMES*frame_bytes; sec_per_chunk=CHUNK_FRAMES/float(SR)
        expect=1; buf={}; eof=False

        def play_pcm(idx, pcm):
            off=0; n=len(pcm); print(f"[PLAY ] >>#{idx}")
            while off<n and not ctrl.stop.is_set():
                if ctrl.paused.is_set(): time.sleep(0.01); continue
                chunk = pcm[off:off+step]
                try: p.stdin.write(chunk); p.stdin.flush()
                except Exception as e: print(f"[PLAY ] write err#{idx}: {e}"); ctrl.stop.set(); break
                off += len(chunk); time.sleep(sec_per_chunk)
            print(f"[PLAY ] done #{idx}")

        # preroll (auto-start after PREROLL ready or early EOF)
        while not ctrl.stop.is_set() and len(buf) < PREROLL and not eof:
            idx, pcm, _ = qin.get()
            if idx is None: eof=True; break
            buf[idx]=pcm

        while not ctrl.stop.is_set():
            if expect in buf:
                play_pcm(expect, buf.pop(expect)); expect+=1
                if eof and expect>total: break
                continue
            idx, pcm, _ = qin.get()
            if idx is None:
                eof=True
                if expect>total: break
                continue
            buf[idx]=pcm

        try:
            if p.stdin: p.stdin.close(); p.wait(timeout=3)
        except Exception: pass
    print("[PLAY ] exit")

def main():
    d = outdir(); sents = tokenize(TEXT)
    print("=== Debug ==="); print(f"lang={LANG} voice={VOICE} speed={SPEED} count={len(sents)}")
    for i,s in enumerate(sents,1): print(f"  {i}: {s}")

    q = MPQ(maxsize=16); ctrl = Controls()
    threading.Thread(target=keyboard_thread, args=(ctrl,q), daemon=True).start()
    t_play = threading.Thread(target=player_thread_ordered, args=(q,ctrl,len(sents))); t_play.start()

    # PREROLL synthesis in main — abort immediately on 's'
    kok = Kokoro(MODEL, VOICES)
    first = min(PREROLL, len(sents))
    for i in range(1, first+1):
        if ctrl.stop.is_set(): break
        pcm, path = synth_one(kok, i, sents[i-1], d)
        if ctrl.stop.is_set(): break
        q.put((i, pcm, path))

    prod = None
    if not ctrl.stop.is_set() and first < len(sents):
        prod = Process(target=producer_proc, args=(sents, first+1, d, q)); prod.start()
    else:
        q.put((None,None,None))

    # Wait; honor stop immediately
    while t_play.is_alive():
        if ctrl.stop.is_set(): break
        time.sleep(0.05)

    # Cleanup on stop/exit
    try: q.put((None,None,None))
    except Exception: pass
    t_play.join(timeout=2)
    if prod and prod.is_alive(): prod.terminate(); prod.join(timeout=2)
    print("[MAIN] complete")

if __name__=="__main__": main()

