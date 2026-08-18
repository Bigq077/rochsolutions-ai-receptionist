# Turns from the two jv_v2 calls of 2026-08-18, build 66dd7a1a12bd.
# t0 = "[ms_stt] FINAL -> queue"  (endpointer has already fired; comparable to
#      LATENCY.md's TTFA, which excludes the ~600ms endpoint silence)
# perceived = first audible output of ANY kind (filler clip or filler TTS)
# content   = first audible CONTENT (the real reply)
# times in seconds within each call
T = [  # (call, label, t0, t_perceived, t_content)
 ("call1","book intent",        4.463,  6.539,  7.204),
 ("call1","reason: ankle",     14.342, 17.216, 17.216),
 ("call1","yes please",        29.343, 31.067, 31.067),
 ("call1","anytime next week", 39.642, 39.997, 45.010),   # pre-recorded FillerGuard clip @350ms
 ("call1","picks slot",        54.042, 56.358, 56.358),
 ("call1","gives name",        66.673, 68.597, 75.028),
 ("call1","confirms phone",    84.542, 86.906, 86.906),
 ("call1","go for it (BOOK)",  96.570, 98.496,103.955),
 ("call2","move intent",       14.602, 16.613, 20.968),
 ("call2","confirms number",   32.898, 33.007, 37.870),
 ("call2","right one",         47.080, 49.397, 49.397),
 ("call2","soonest slot?",     59.381, 62.492, 67.058),
 ("call2","number 2 works",    81.380, 83.667, 83.667),
 ("call2","um yes (MOVE)",    106.380,111.776,117.258),
]
def p(xs, q):                      # numpy type-7, matching lat_parse.py
    xs = sorted(xs); h = (len(xs)-1) * q
    lo, hi = int(h), min(int(h)+1, len(xs)-1)
    return xs[lo] + (h-lo)*(xs[hi]-xs[lo])
def row(name, vals):
    v = [round(x*1000) for x in vals]
    print(f"{name:26} n={len(v):3} min={min(v):5} p50={round(p(v,.5)):5} "
          f"p90={round(p(v,.9)):5} p95={round(p(v,.95)):5} max={max(v):5}")

perc = [t[3]-t[2] for t in T]
cont = [t[4]-t[2] for t in T]
print("=== jv_v2, 2026-08-18, build 66dd7a1a12bd — 14 turns / 2 calls (ms) ===")
row("perceived TTFA", perc)
row("content TTFA", cont)
print()
print("+ ~600ms endpoint silence = voice-to-voice:")
row("voice-to-voice (perceived)", [x+0.6 for x in perc])
row("voice-to-voice (content)",   [x+0.6 for x in cont])
print()
print("=== worst content turns ===")
for c,l,a,b,d in sorted(T, key=lambda t:-(t[4]-t[2]))[:5]:
    print(f"  {round((d-a)*1000):6} ms  {c} {l}")
