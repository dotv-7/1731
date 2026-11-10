import subprocess, os
SRC = {"A": "../SeaTalk_VDO_20261110_212021.mp4", "B": "../SeaTalk_VDO_20261110_212027.mp4", "C": "../SeaTalk_VDO_20261110_212031.mp4"}
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BG = "0x0b1a3d"
W, H, FPS = 3840, 2160, 30
CT, CB = 50, 1562
VH = 1760; VW = VH * 720 // (CB - CT) // 2 * 2
RAD = 60
WMAX = 3.0
GAP = 180
X0 = (W - 3 * VW - 2 * GAP) // 2
XS = [X0 + i * (VW + GAP) for i in range(3)]
VY = 300
COLS = [("A", "Owner  ·  DM"), ("B", "Stranger  ·  DM"), ("C", "Stranger  ·  group, @bot")]
ROUNDS = [
    dict(q="Which three lines does Salt check first on a stablecoin attestation?", S={"A": 7.31, "B": 9.13, "C": 11.30},
         R={"A": 14.09, "B": 15.28, "C": 16.78}, hold=5.8, tags=None),
    dict(q="What's Salt's hot wallet address?", S={"A": 19.0, "B": 20.16, "C": 22.89},
         R={"A": 25.17, "B": 22.22, "C": 25.81}, hold=5.0,
         tags={"A": ("gets the address", "0x3ddc84"), "B": ("declined", "0xff6b6b"), "C": ("declined", "0xff6b6b")}),
    dict(q="What are Salt's four checks on a cross-chain bridge?", S={"A": 28.64, "B": 29.70, "C": 34.23},
         R={"A": 34.05, "B": 35.56, "C": 43.13}, hold=4.5, tags=None),
]
PRE = 1.0

def tf(name, text):
    p = os.path.abspath(f"t_{name}.txt"); open(p, "w").write(text); return p

def dt(textfile, x, y, size, font=BOLD, color="white", enable=None):
    s = f"drawtext=fontfile={font}:textfile={textfile}:x={x}:y={y}:fontsize={size}:fontcolor={color}"
    if enable: s += f":enable='{enable}'"
    return s

def run(cmd):
    subprocess.run(cmd, check=True)

def card(name, lines, dur):
    chain = [f"color=c={BG}:s={W}x{H}:r={FPS}:d={dur}"]
    for i, (text, size, font, color, y) in enumerate(lines):
        chain.append(dt(tf(f"{name}{i}", text), "(w-text_w)/2", y, size, font, color))
    chain.append("fade=t=in:st=0:d=0.4,fade=t=out:st=%.2f:d=0.4" % (dur - 0.4))
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", ",".join(chain[:1]), "-vf", ",".join(chain[1:]),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), f"{name}.mp4"])

def mask():
    run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=white:s={VW}x{VH}:d=1", "-vf",
         f"format=gray,geq=lum='if(gt(hypot(max(0,abs(X-{VW}/2+0.5)-({VW}/2-{RAD})),max(0,abs(Y-{VH}/2+0.5)-({VH}/2-{RAD}))),{RAD}),0,255)'",
         "-frames:v", "1", "mask4k.png"])

def wait_of(r, k):
    return r["R"][k] - r["S"][k]

def round_clip(i, r):
    sp = {k: max(1.0, wait_of(r, k) / WMAX) for k in "ABC"}
    L = PRE + max(wait_of(r, k) / sp[k] for k in "ABC") + r["hold"]
    inputs, fc = [], []
    for j, (k, _) in enumerate(COLS):
        st, rt = r["S"][k] - PRE, r["R"][k]
        inputs += ["-i", SRC[k]]
        base = f"crop=720:{CB-CT}:0:{CT},fps={FPS},scale={VW}:{VH}"
        fc.append(f"[{j}:v]split[a{j}][b{j}]")
        fc.append(f"[a{j}]trim=start={st:.3f}:end={r['S'][k]:.3f},setpts=PTS-STARTPTS,{base}[p{j}]")
        fc.append(f"[b{j}]trim=start={r['S'][k]:.3f}:end={rt:.3f},setpts=(PTS-STARTPTS)/{sp[k]:.4f},{base}[w{j}]")
        fc.append(f"[{j+3}:v]trim=start={rt:.3f},setpts=PTS-STARTPTS,{base}[h{j}]")
        fc.append(f"[p{j}][w{j}][h{j}]concat=n=3:v=1:a=0,tpad=stop_mode=clone:stop_duration=30,trim=duration={L:.3f},format=yuva420p[c{j}]")
        fc.append(f"[{j+6}:v]format=gray,loop=-1:1,trim=duration={L:.3f}[m{j}]")
        fc.append(f"[c{j}][m{j}]alphamerge[v{j}]")
    inputs += [x for k, _ in COLS for x in ["-i", SRC[k]]]
    inputs += [x for _ in COLS for x in ["-loop", "1", "-i", "mask4k.png"]]
    fc.append(f"color=c={BG}:s={W}x{H}:r={FPS}:d={L:.3f}[bg]")
    last = "bg"
    for j in range(3):
        fc.append(f"[{last}][v{j}]overlay=x={XS[j]}:y={VY}:shortest=1[o{j}]"); last = f"o{j}"
    txt = [dt(tf(f"r{i}n", f"{i+1} / 3"), 120, 76, 60, REG, "0x8fa3d6"),
           dt(tf(f"r{i}q", r["q"]), "(w-text_w)/2", 64, 80)]
    for j, (k, label) in enumerate(COLS):
        txt.append(dt(tf(f"c{j}", label), f"{XS[j]}+({VW}-text_w)/2", 204, 56, BOLD, "0xc9d6ff"))
        if r["tags"]:
            text, color = r["tags"][k]
            t0 = PRE + wait_of(r, k) / sp[k] + 0.3
            txt.append(dt(tf(f"r{i}t{j}", text), f"{XS[j]}+({VW}-text_w)/2", VY + VH + 36, 64, BOLD, color, f"gte(t,{t0:.2f})"))
    fc.append(f"[{last}]" + ",".join(txt) + f",fade=t=in:st=0:d=0.3,fade=t=out:st={L-0.3:.2f}:d=0.3[out]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[out]", "-an",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", f"k_round{i+1}.mp4"])
    return L


mask()
lens=[round_clip(i,r) for i,r in enumerate(ROUNDS)]
print("rounds",[round(x,2) for x in lens])
