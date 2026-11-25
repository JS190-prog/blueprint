"""Narration for a film, spoken by a local Qwen3-TTS voice clone and placed on the film's clock.

tools/music.py --narration lines.json calls this. lines.json is a list (or {"lines": [...]}) of
    {"t": 3.2, "say": "..."}                 at 3.2 s
    {"at": "dive", "dt": -0.5, "say": "..."} 0.5 s before the film's "dive" moment (timeline.json "T")
One sentence per line: a long line synthesised in one go goes flat.

The voice is a clone of a reference recording, made by a runner that follows the tts_local.py contract
(`python -X utf8 -I <runner> synth qwen <job.json>`, job {"ref_wav", "ref_text", "takes", "items": [{"text", "out"}]}).
Where they live, first match wins:
    QWEN_TTS_PYTHON, QWEN_TTS_RUNNER, QWEN_TTS_VOICE, QWEN_TTS_VOICE_TEXT (environment), then
    ~/.blueprint/tts.json {"python", "runner", "ref_wav", "ref_text", "takes"}.
Each line is cached by its text and the voice, trimmed of edge silence and brought to SPEECH_LUFS.

    python tools/voice.py selftest
"""
import hashlib
import json
import os
import re
import subprocess
import sys

FF = os.environ.get("FFMPEG", "ffmpeg")
SPEECH_LUFS = -17.0
TRIM = "silenceremove=start_periods=1:start_threshold=-45dB"
EDGES = f"{TRIM},areverse,{TRIM},areverse"
CONFIG = os.path.join(os.path.expanduser("~"), ".blueprint", "tts.json")
ENV = {"python": "QWEN_TTS_PYTHON", "runner": "QWEN_TTS_RUNNER", "ref_wav": "QWEN_TTS_VOICE", "ref_text": "QWEN_TTS_VOICE_TEXT"}


def lufs(path):
    err = subprocess.run([FF, "-hide_banner", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
    return float(m[-1]) if m else None


def seconds(path):
    err = subprocess.run([FF, "-hide_banner", "-i", path, "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    h, m, s = re.findall(r"time=(\d+):(\d+):([\d.]+)", err)[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


def config():
    cfg = json.load(open(CONFIG, encoding="utf-8")) if os.path.exists(CONFIG) else {}
    cfg.update({k: os.environ[e] for k, e in ENV.items() if os.environ.get(e)})
    missing = [f"{k} ({ENV[k]})" for k in ENV if not cfg.get(k)]
    if missing:
        sys.exit(f"narration: no local Qwen voice set up, missing {', '.join(missing)}; see {CONFIG}")
    for k in ("python", "runner", "ref_wav"):
        if not os.path.exists(cfg[k]):
            sys.exit(f"narration: {k} not found: {cfg[k]}")
    return cfg


def place(lines, tl):
    """lines -> [(seconds, text)] sorted, every time inside the film."""
    lines = lines["lines"] if isinstance(lines, dict) else lines
    out = []
    for ln in lines:
        if "at" in ln:
            moments = tl.get("T", {})
            if ln["at"] not in moments:
                sys.exit(f"narration: no moment {ln['at']!r} in the timeline (has {', '.join(moments)})")
            t = moments[ln["at"]] + ln.get("dt", 0.0)
        else:
            t = ln["t"]
        if not 0 <= t < tl["duration"]:
            sys.exit(f"narration: {ln['say'][:20]!r} at {t:.2f} s is outside the film (0-{tl['duration']:.2f} s)")
        out.append((round(float(t), 3), ln["say"].strip()))
    return sorted(out)


def synth(texts, cfg, cache):
    """{text: trimmed wav}, synthesising only the lines not yet in the cache."""
    os.makedirs(cache, exist_ok=True)
    takes = int(cfg.get("takes", 2))
    voice = hashlib.sha1(open(cfg["ref_wav"], "rb").read()).hexdigest()
    key = {t: hashlib.sha1(f"{voice}|{cfg['ref_text']}|{takes}|{t}".encode()).hexdigest()[:16] for t in texts}
    raw = {t: os.path.join(cache, f"{key[t]}.wav") for t in texts}
    trimmed = {t: os.path.join(cache, f"{key[t]}.trim.wav") for t in texts}
    todo = [t for t in dict.fromkeys(texts) if not os.path.exists(trimmed[t])]
    if todo:
        job = os.path.join(cache, "job.json")
        json.dump({"ref_wav": cfg["ref_wav"], "ref_text": cfg["ref_text"], "takes": takes, "match_voice": True,
                   "batch": 4, "adaptive": True, "resume": True,
                   "items": [{"text": t, "out": raw[t]} for t in todo]},
                  open(job, "w", encoding="utf-8"), ensure_ascii=False)
        log = os.path.join(cache, "tts.log")
        print(f"narration: {len(todo)} line(s) with the local Qwen voice (log {log})", flush=True)
        with open(log, "a", encoding="utf-8") as f:
            r = subprocess.run([cfg["python"], "-X", "utf8", "-I", cfg["runner"], "synth", "qwen", job],
                               stdout=f, stderr=subprocess.STDOUT)
        if r.returncode:
            sys.exit(f"narration: the Qwen runner failed (exit {r.returncode}), see {log}")
        for t in todo:
            subprocess.run([FF, "-v", "error", "-y", "-i", raw[t], "-af", EDGES, "-ar", "48000", trimmed[t]], check=True)
    return trimmed


def track(placed, wavs, dur, out):
    """One voice track as long as the film, every line at its time and level. Returns the overlap warnings."""
    args, chains, warn = [FF, "-v", "error", "-y"], [], []
    ends = []
    for i, (t, text) in enumerate(placed):
        w = wavs[text]
        level = lufs(w)
        gain = SPEECH_LUFS - level if level is not None else 0.0
        args += ["-i", w]
        chains.append(f"[{i}:a]aformat=channel_layouts=mono,volume={gain:.2f}dB,adelay={int(t * 1000)}:all=1[v{i}]")
        ends.append((t, t + seconds(w), text))
    for (t0, e0, a), (t1, _, b) in zip(ends, ends[1:]):
        if e0 > t1:
            warn.append(f"{a[:16]!r} runs {e0 - t1:.2f} s into {b[:16]!r}")
    if ends and ends[-1][1] > dur:
        warn.append(f"{ends[-1][2][:16]!r} runs {ends[-1][1] - dur:.2f} s past the end")
    mix = "".join(f"[v{i}]" for i in range(len(placed)))
    chains.append(f"{mix}amix=inputs={len(placed)}:normalize=0,apad,atrim=0:{dur},"
                  f"alimiter=limit=0.75:level=false,aformat=channel_layouts=stereo[vo]")
    subprocess.run(args + ["-filter_complex", ";".join(chains), "-map", "[vo]", "-ar", "48000", out], check=True)
    return warn


def selftest():
    tl = {"duration": 20.0, "T": {"dive": 12.0}}
    assert place([{"t": 5, "say": " b "}, {"at": "dive", "dt": -0.5, "say": "a"}], tl) == [(5.0, "b"), (11.5, "a")]
    assert place({"lines": [{"t": 0, "say": "x"}]}, tl) == [(0.0, "x")]
    for bad in ([{"t": 20, "say": "x"}], [{"at": "nope", "say": "x"}]):
        try:
            place(bad, tl)
            raise AssertionError(bad)
        except SystemExit:
            pass
    print("voice selftest ok")


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["selftest"]:
        selftest()
