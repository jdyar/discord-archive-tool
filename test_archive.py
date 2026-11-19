#!/usr/bin/env python3
"""Automated checks on the synthetic data. Run: python test_archive.py"""
import json, pathlib, re, subprocess, sys, shutil
R = pathlib.Path(__file__).parent; PY = sys.executable
def run(*args): return subprocess.run([PY, str(R/"discord_archive.py"), *args], capture_output=True, text=True, check=True).stdout
fails = []
def check(cond, msg): print(("PASS " if cond else "FAIL ") + msg); cond or fails.append(msg)

# 1) plain build
out = R/"_t_plain"; run(str(R/"synthetic_export"), str(out))
rep = (out/"report.txt").read_text()
check("raw_messages: 740" in rep and "duplicates_removed: 50" in rep, "overlapping exports merged (740 raw, 50 duplicates)")
gen = json.load(open(out/"data/general.json"))
check(len(gen) == 440, f"general has 420 + 20 new = 440 messages (got {len(gen)})")
ts = [m["ts"] for m in gen]; check(ts == sorted(ts), "messages sorted by time")
check(len({m["id"] for m in gen}) == len(gen), "no duplicate IDs")
check(any("<span class=\"mention\">@" in m["html"] for m in gen), "mentions resolved to names")
check(len(list((out/"media").iterdir())) > 0 and all((out/m["attachments"][0]["src"]).exists() for m in gen if m["attachments"] and m["attachments"][0]["src"]), "local media copied and linked")
check("cdn.discordapp.com" not in "".join(p.read_text() for p in out.glob("*.html")), "no expiring CDN links embedded")

# 2) privacy build
out2 = R/"_t_private"; run(str(R/"synthetic_export"), str(out2), "--anonymize", "--redact-contacts", "--exclude-users", "100003")
blob = "".join(p.read_text() for p in out2.rglob("*") if p.suffix in (".html", ".js", ".json"))
check(not re.search(r"[\w.+-]+@example\.com", blob), "emails redacted everywhere (pages, data, search index)")
check("555-123-4567" not in blob, "phone numbers redacted")
check(not any(n in blob for n in ["mossfox", "tidewolf", "pixelotter", "Moss 🦊"]), "real usernames/nicknames gone when --anonymize")
priv = json.load(open(out2/"data/general.json"))
check(len(priv) < 440, f"opted-out user's messages removed ({440 - len(priv)} removed from #general)")

# 3) search index works (evaluate the page's own search code in node)
js = (out/"search_index.js").read_text()
node = subprocess.run(["node", "-e", js + "; const w=['ref','sheet']; const h=SEARCH.filter(r=>w.every(x=>(r[4]+' '+r[5]).toLowerCase().includes(x))); console.log(h.length)"], capture_output=True, text=True)
n = int(node.stdout.strip() or 0); check(n > 0, f"search for 'ref sheet' finds {n} messages")

# 4) HTML export path (synthetic HTML using DiscordChatExporter's class names)
hdir = R/"_t_html_src"; hdir.mkdir(exist_ok=True)
(hdir/"Test Server - general [1].html").write_text('''<html><head><title>Test Server - general</title></head><body>
<div class="chatlog__message-container" data-message-id="11"><span class="chatlog__author" data-user-id="5">Ari</span>
<span class="chatlog__timestamp" title="Monday, February 9, 2026 3:04 PM"><a>2/9/26</a></span>
<div class="chatlog__content chatlog__markdown"><span class="chatlog__markdown-preserve">hello <b>world</b></span></div></div>
<div class="chatlog__message-container" data-message-id="12"><div class="chatlog__short-timestamp" title="Monday, February 9, 2026 3:05 PM">3:05</div>
<div class="chatlog__content chatlog__markdown"><span class="chatlog__markdown-preserve">second message same author</span></div></div>
</body></html>''')
out3 = R/"_t_html"; run(str(hdir), str(out3))
hm = json.load(open(next((out3/"data").glob("*.json"))))
check(len(hm) == 2 and hm[1]["author"] == "Ari" and hm[0]["ts"].startswith("2026-02-09T15:04"), "HTML export parsed (grouped author carried over, timestamp parsed)")
for d in [out, out2, out3, hdir]: shutil.rmtree(d)
print(f"\n{'ALL PASS' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
