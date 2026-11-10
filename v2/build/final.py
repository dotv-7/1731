import subprocess, os, json
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
BG, W, H, FPS = "0x0b1a3d", 3840, 2160, 30
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(FPS)]
run = lambda c: subprocess.run(c, check=True)
dur = lambda f: float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]))

def tf(name, text):
    p = os.path.abspath(f"t_f_{name}.txt"); open(p, "w").write(text); return p

def card(name, lines, d):
    vf = [f"drawtext=fontfile={f}:textfile={tf(name+str(i), t)}:x=(w-text_w)/2:y={y}:fontsize={s}:fontcolor={c}"
          for i, (t, s, f, c, y) in enumerate(lines)]
    vf.append(f"fade=t=in:st=0:d=0.5,fade=t=out:st={d-0.5:.2f}:d=0.5")
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:r={FPS}:d={d}", "-vf", ",".join(vf), *ENC, f"f_{name}.mp4"])

card("whatif", [("What if...", 150, BOLD, "0x8fa3d6", 640),
                ("the Hermes you've spent hundreds of hours teaching", 110, BOLD, "white", 900),
                ("could talk to other people", 110, BOLD, "white", 1060),
                ("without giving away your secrets?", 110, BOLD, "0xff8a8a", 1220)], 8.0)
card("scene", [("Meet Salt", 150, BOLD, "white", 720),
               ("A crypto writer with public articles on stablecoins and bridges.", 80, REG, "0xc9d6ff", 960),
               ("She wants her Hermes to answer fans for her,", 80, REG, "0xc9d6ff", 1090),
               ("on Telegram, in DMs and in groups.", 80, REG, "0xc9d6ff", 1220)], 7.5)
card("outro", [("Salt's Secrets", 150, BOLD, "white", 560),
               ("A Hermes Agent plugin", 80, REG, "0xc9d6ff", 780),
               ("hermes plugins install salts-secrets", 96, MONO, "0x7ee2a8", 1020),
               ("Live demo:  salt.dotv.io", 96, BOLD, "white", 1260)], 8.0)
run(["ffmpeg", "-v", "error", "-y", "-i", "../assets/dash_4k.mp4",
     "-vf", "scale=3840:2160,fps=30,fade=t=in:st=0:d=0.4,fade=t=out:st=23.5:d=0.45", "-an", *ENC, "f_dash.mp4"])

segs = ["f_whatif", "f_scene", "f_dash", "k_round1", "k_round2", "k_round3", "f_outro"]
T, t = {}, 0.0
for s in segs:
    T[s] = t; t += dur(s + ".mp4")
total = t
vo = [("s1", "f_whatif", 0.8), ("s2", "f_scene", 0.6), ("d1", "f_dash", 0.3), ("d2", "f_dash", 12.8),
      ("t0", "k_round1", 0.2), ("t1", "k_round1", 6.25), ("t2", "k_round2", 0.5), ("t3", "k_round3", 0.4), ("e1", "f_outro", 0.8)]
open("list4k.txt", "w").write("".join(f"file '{s}.mp4'\n" for s in segs))
ins, fc = [], []
for i, (k, seg, off) in enumerate(vo):
    ins += ["-i", f"vo/{k}.mp3"]
    ms = int((T[seg] + off) * 1000)
    fc.append(f"[{i+1}:a]aresample=48000,adelay={ms}|{ms}[a{i}]")
fc.append("".join(f"[a{i}]" for i in range(len(vo))) + f"amix=inputs={len(vo)}:normalize=0,apad,atrim=0:{total:.3f}[aout]")
run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "list4k.txt", *ins, "-filter_complex", ";".join(fc),
     "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-movflags", "+faststart",
     "salts_secrets_demo_4k.mp4"])
print(json.dumps({k: round(v, 2) for k, v in T.items()}), "total", round(total, 2))
