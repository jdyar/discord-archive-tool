#!/usr/bin/env python3
"""discord_archive: clean DiscordChatExporter exports and build a static, searchable archive site.

Usage:
  python discord_archive.py EXPORT_DIR OUT_DIR [--title T] [--anonymize] [--exclude-users id1,id2]
                            [--redact-contacts] [--drop-system] [--page-size 300]

Input: a folder of DiscordChatExporter exports, .json (preferred) and/or .html, with any media
folders next to them (export with --media so images are saved locally).
Cleaning: merges overlapping exports and removes duplicate messages by ID, sorts by time, resolves
<@id> mentions to names, converts basic Discord markdown, copies local media into the archive,
lists attachments that only exist as expiring cdn.discordapp.com links, and optionally pseudonymizes
users, drops opted-out users' messages, and redacts emails and phone numbers.
Output: OUT_DIR/index.html (channel list + full-text search, works offline from file://),
one paginated page set per channel, media/, report.txt, clean JSON per channel.

Only run this on exports the server owner made with a bot token (allowed by Discord), and with
the community's consent. See README.
"""
import argparse, collections, datetime, hashlib, html, json, re, shutil, pathlib, sys

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
SYSTEM_TYPES = {"ChannelPinnedMessage", "GuildMemberJoin", "RecipientAdd", "RecipientRemove", "ChannelNameChange",
                "ChannelIconChange", "ThreadCreated", "GuildBoost", "Call"}

# ---------------- loading ----------------
def load_json(path):
    d = json.load(open(path, encoding="utf-8"))
    ch = d["channel"]; out = []
    for m in d.get("messages", []):
        a = m.get("author") or {}
        out.append({
            "id": str(m["id"]), "type": m.get("type", "Default"), "ts": m.get("timestamp"),
            "edited": m.get("timestampEdited"), "pinned": bool(m.get("isPinned")),
            "content": m.get("content") or "", "author_id": str(a.get("id", "")),
            "author": a.get("nickname") or a.get("name") or "Unknown", "bot": bool(a.get("isBot")),
            "attachments": [{"url": x.get("url", ""), "name": x.get("fileName", "")} for x in m.get("attachments", [])],
            "reactions": [f'{(r.get("emoji") or {}).get("name", "?")} {r.get("count", 1)}' for r in m.get("reactions", [])],
            "reply_to": (m.get("reference") or {}).get("messageId"),
            "mentions": {str(x["id"]): x.get("nickname") or x.get("name") for x in m.get("mentions", [])},
        })
    return d.get("guild", {}).get("name", ""), str(ch["id"]), ch.get("name", "channel"), ch.get("category", ""), out, path.parent

def load_html(path):
    """Best-effort parser for DiscordChatExporter HTML (class names checked against DCE's
    MessageGroupTemplate.cshtml, Nov 2026). JSON exports are more complete; prefer them."""
    from bs4 import BeautifulSoup
    from dateutil import parser as dp
    soup = BeautifulSoup(open(path, encoding="utf-8"), "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else path.stem
    guild, _, chname = title.partition(" - ")
    chname = chname or title
    chid = "html-" + hashlib.sha1(str(path.name).encode()).hexdigest()[:10]
    out, last_author, last_aid = [], "Unknown", ""
    for c in soup.select("div.chatlog__message-container"):
        au = c.select_one(".chatlog__author")
        if au: last_author, last_aid = au.get_text(strip=True), au.get("data-user-id", "")
        tsel = c.select_one(".chatlog__timestamp") or c.select_one(".chatlog__short-timestamp")
        ts = None
        if tsel and tsel.get("title"):
            try: ts = dp.parse(tsel["title"], fuzzy=True).isoformat()
            except Exception: ts = None
        content = c.select_one(".chatlog__content")
        atts = [{"url": a.get("href", ""), "name": pathlib.Path(a.get("href", "")).name}
                for a in c.select(".chatlog__attachment a[href]")]
        out.append({"id": c.get("data-message-id", ""), "type": "Default", "ts": ts, "edited": None,
                    "pinned": "chatlog__message-container--pinned" in (c.get("class") or []),
                    "content": content.get_text(" ", strip=True).replace("(edited)", "").strip() if content else "",
                    "author_id": last_aid, "author": last_author, "bot": False, "attachments": atts,
                    "reactions": [], "reply_to": None, "mentions": {}, "_prerendered": True})
    return guild, chid, chname, "", out, path.parent

# ---------------- cleaning ----------------
def md(text, names):
    t = html.escape(text)
    t = re.sub(r"&lt;@!?(\d+)&gt;", lambda m: f'<span class="mention">@{html.escape(names.get(m.group(1), "unknown-user"))}</span>', t)
    t = re.sub(r"```(.+?)```", r"<pre>\1</pre>", t, flags=re.S)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    t = re.sub(r"__(.+?)__", r"<u>\1</u>", t)
    t = re.sub(r"~~(.+?)~~", r"<s>\1</s>", t)
    t = re.sub(r"\|\|(.+?)\|\|", r'<span class="spoiler" onclick="this.classList.add(\'shown\')">\1</span>', t)
    t = re.sub(r"(https?://[^\s<]+)", r'<a href="\1" rel="nofollow noopener" target="_blank">\1</a>', t)
    return t.replace("\n", "<br>")

def pseudonym(uid):
    adj = ["Amber", "Brisk", "Cedar", "Dusky", "Ember", "Frost", "Gale", "Hazel", "Indigo", "Juniper", "Kestrel", "Lumen"]
    ani = ["Fox", "Otter", "Wolf", "Hare", "Lynx", "Raven", "Badger", "Heron", "Moth", "Newt", "Stoat", "Wren"]
    h = int(hashlib.sha256(uid.encode()).hexdigest(), 16)
    return f"{adj[h % 12]} {ani[(h // 12) % 12]} {h % 97:02d}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out")
    ap.add_argument("--title"); ap.add_argument("--anonymize", action="store_true")
    ap.add_argument("--exclude-users", default="", help="comma-separated user IDs whose messages are removed (opt-outs)")
    ap.add_argument("--redact-contacts", action="store_true"); ap.add_argument("--drop-system", action="store_true")
    ap.add_argument("--page-size", type=int, default=300)
    a = ap.parse_args()
    src, out = pathlib.Path(a.src), pathlib.Path(a.out)
    excluded = {x.strip() for x in a.exclude_users.split(",") if x.strip()}
    channels = collections.OrderedDict(); guild = ""
    stats = collections.Counter(); missing_media = []
    files = sorted(src.rglob("*.json")) + sorted(src.rglob("*.html"))
    if not files: sys.exit("no .json or .html exports found")
    for f in files:
        g, cid, cname, cat, msgs, base = (load_json if f.suffix == ".json" else load_html)(f)
        guild = guild or g; stats["files"] += 1; stats["raw_messages"] += len(msgs)
        ch = channels.setdefault(cid, {"name": cname, "category": cat, "msgs": {}, "base": base})
        for m in msgs:
            if m["id"] in ch["msgs"]: stats["duplicates_removed"] += 1; continue
            m["_base"] = base; ch["msgs"][m["id"]] = m
    names = {}
    for ch in channels.values():
        for m in ch["msgs"].values():
            names.setdefault(m["author_id"], m["author"]); names.update(m["mentions"])
    if a.anonymize: names = {uid: pseudonym(uid) for uid in names}
    if out.exists(): shutil.rmtree(out)
    (out/"media").mkdir(parents=True); (out/"data").mkdir()
    search = []; chan_index = []
    for cid, ch in channels.items():
        msgs = sorted(ch["msgs"].values(), key=lambda m: (m["ts"] or "", m["id"]))
        clean = []
        for m in msgs:
            if m["author_id"] in excluded: stats["opt_out_removed"] += 1; continue
            if a.drop_system and m["type"] in SYSTEM_TYPES: stats["system_dropped"] += 1; continue
            text = m["content"]
            if a.redact_contacts:
                text, n1 = EMAIL.subn("[email removed]", text); text, n2 = PHONE.subn("[phone removed]", text)
                stats["contacts_redacted"] += n1 + n2
            atts = []
            for at in m["attachments"]:
                u = at["url"]
                if u.startswith("http"):
                    if "cdn.discordapp.com" in u or "media.discordapp.net" in u:
                        missing_media.append(f'{ch["name"]}  {m["ts"]}  {at["name"]}'); stats["media_remote_only"] += 1
                    atts.append({"name": at["name"], "src": None})
                else:
                    p = (m["_base"]/u)
                    if p.exists():
                        dest = f'media/{cid}_{p.name}'; shutil.copy(p, out/dest); stats["media_copied"] += 1
                        atts.append({"name": at["name"], "src": dest})
                    else:
                        missing_media.append(f'{ch["name"]}  {m["ts"]}  {at["name"]} (local file missing)'); stats["media_missing"] += 1
                        atts.append({"name": at["name"], "src": None})
            author = names.get(m["author_id"], m["author"]) if a.anonymize else m["author"]
            body = html.escape(text) if m.get("_prerendered") else md(text, names)
            if a.anonymize and m.get("_prerendered"): body = body  # HTML mode can't resolve mentions; note in README
            clean.append({"id": m["id"], "ts": m["ts"], "author": author, "bot": m["bot"], "html": body, "text": text,
                          "attachments": atts, "reactions": m["reactions"], "reply_to": m["reply_to"],
                          "edited": bool(m["edited"]), "pinned": m["pinned"], "type": m["type"]})
        stats["clean_messages"] += len(clean)
        slug = re.sub(r"[^a-z0-9]+", "-", ch["name"].lower()).strip("-") or cid
        pages = max(1, -(-len(clean) // a.page_size))
        by_id = {m["id"]: m for m in clean}
        for pi in range(pages):
            chunk = clean[pi*a.page_size:(pi+1)*a.page_size]
            for m in chunk: search.append([slug, pi+1, m["id"], (m["ts"] or "")[:10], m["author"], m["text"][:500]])
            write_channel_page(out, a.title or guild, ch["name"], slug, pi+1, pages, chunk, by_id, a.page_size, clean)
        json.dump(clean, open(out/"data"/f"{slug}.json", "w"), ensure_ascii=False, indent=1)
        first = next((m["ts"] for m in clean if m["ts"]), ""); last = next((m["ts"] for m in reversed(clean) if m["ts"]), "")
        chan_index.append({"slug": slug, "name": ch["name"], "category": ch["category"], "count": len(clean), "from": (first or "")[:10], "to": (last or "")[:10]})
    (out/"search_index.js").write_text("const SEARCH=" + json.dumps(search, ensure_ascii=False) + ";")
    write_index(out, a.title or guild, chan_index, a.anonymize)
    report = [f"Archive: {a.title or guild}", f"Built: {datetime.datetime.now().isoformat(timespec='seconds')}", ""]
    report += [f"{k}: {v}" for k, v in sorted(stats.items())]
    report += ["", f"Attachments without a local copy ({len(missing_media)}). Re-export with DiscordChatExporter --media to save them:"] + missing_media
    (out/"report.txt").write_text("\n".join(report))
    print("\n".join(report[:3 + len(stats)]))

CSS = """body{margin:0;font:15px/1.45 system-ui,sans-serif;background:#f5f5f7;color:#1d1d1f}@media(prefers-color-scheme:dark){body{background:#17171a;color:#e9e9ee}.m{border-color:#333!important}}
.wrap{max-width:860px;margin:auto;padding:16px}h1{font-size:22px;margin:4px 0}a{color:#3b6fd8}.sub{color:#888;font-size:13px}
.m{padding:6px 0;border-bottom:1px solid #ddd}.a{font-weight:600}.t{color:#888;font-size:12px;margin-left:6px}.bot{font-size:10px;background:#5865f2;color:#fff;border-radius:3px;padding:0 4px;margin-left:4px}
.day{margin:18px 0 6px;font-weight:600;color:#888;font-size:13px;text-transform:uppercase}.r{font-size:13px;color:#888}.reply{font-size:12px;color:#888;border-left:2px solid #aaa;padding-left:6px;margin:2px 0}
img.att{max-width:320px;max-height:240px;border-radius:6px;display:block;margin-top:4px}.miss{font-size:12px;color:#b45}.spoiler{background:#555;color:transparent;border-radius:3px;cursor:pointer}.spoiler.shown{color:inherit;background:#8883}
.mention{background:#5865f233;border-radius:3px;padding:0 2px}code,pre{background:#8882;border-radius:4px;padding:1px 4px}.nav{display:flex;gap:10px;margin:12px 0;flex-wrap:wrap}
input{font:inherit;width:100%;padding:9px;border-radius:8px;border:1px solid #9995;box-sizing:border-box}.hit{padding:6px 0;border-bottom:1px solid #8883}mark{background:#ffe066}
:target{background:#ffe06655}"""

def write_channel_page(out, title, name, slug, page, pages, msgs, by_id, size, allmsgs):
    loc = {m["id"]: (i // size) + 1 for i, m in enumerate(allmsgs)}
    rows, day = [], None
    for m in msgs:
        d = (m["ts"] or "")[:10]
        if d != day: rows.append(f'<div class="day">{d or "date unknown"}</div>'); day = d
        rep = ""
        if m["reply_to"] and m["reply_to"] in by_id:
            r = by_id[m["reply_to"]]; rp = loc[r["id"]]
            rep = f'<div class="reply">↪ <a href="{slug}-{rp}.html#m{r["id"]}">{html.escape(r["author"])}: {html.escape(r["text"][:80])}</a></div>'
        att = "".join(f'<a href="{x["src"]}"><img class="att" loading="lazy" src="{x["src"]}" alt="{html.escape(x["name"])}"></a>'
                      if x["src"] and x["src"].lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                      else (f'<div><a href="{x["src"]}">{html.escape(x["name"])}</a></div>' if x["src"] else f'<div class="miss">attachment not saved: {html.escape(x["name"])}</div>')
                      for x in m["attachments"])
        rows.append(f'<div class="m" id="m{m["id"]}">{rep}<span class="a">{html.escape(m["author"])}</span>{"<span class=bot>BOT</span>" if m["bot"] else ""}'
                    f'<a class="t" href="#m{m["id"]}">{(m["ts"] or "")[11:16]}{" · edited" if m["edited"] else ""}{" · 📌" if m["pinned"] else ""}</a>'
                    f'<div>{m["html"]}</div>{att}{"<div class=r>" + " · ".join(html.escape(x) for x in m["reactions"]) + "</div>" if m["reactions"] else ""}</div>')
    nav = '<div class="nav"><a href="index.html">← all channels</a>' + "".join(
        f'<a href="{slug}-{p}.html">{"<b>" if p == page else ""}{p}{"</b>" if p == page else ""}</a>' for p in range(1, pages+1)) + "</div>"
    (out/f"{slug}-{page}.html").write_text(f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>#{html.escape(name)} – {html.escape(title)}</title><style>{CSS}</style><div class="wrap"><h1>#{html.escape(name)}</h1><div class="sub">{html.escape(title)} · page {page} of {pages}</div>{nav}{"".join(rows)}{nav}</div>')

def write_index(out, title, chans, anon):
    lis = "".join(f'<li><a href="{c["slug"]}-1.html">#{html.escape(c["name"])}</a> <span class="sub">{c["count"]} messages · {c["from"]} → {c["to"]}</span></li>' for c in chans)
    js = """function go(){const q=document.getElementById('q').value.toLowerCase().trim();const box=document.getElementById('res');if(q.length<2){box.innerHTML='';return}
const w=q.split(/\\s+/);const hits=SEARCH.filter(r=>w.every(x=>(r[4]+' '+r[5]).toLowerCase().includes(x))).slice(0,200);
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
box.innerHTML='<div class=sub>'+hits.length+(hits.length==200?'+':'')+' results</div>'+hits.map(r=>`<div class=hit><a href="${r[0]}-${r[1]}.html#m${r[2]}">#${r[0]}</a> <span class=sub>${r[3]} · ${esc(r[4])}</span><div>${esc(r[5]).replace(new RegExp('('+w.map(x=>x.replace(/[.*+?^${}()|[\\]\\\\]/g,'\\\\$&')).join('|')+')','gi'),'<mark>$1</mark>')}</div></div>`).join('')}"""
    (out/"index.html").write_text(f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)} archive</title><style>{CSS}</style>'
        f'<div class="wrap"><h1>{html.escape(title)}</h1><div class="sub">Read-only archive{" · names pseudonymized" if anon else ""} · works offline</div>'
        f'<p><input id="q" placeholder="Search all messages (author or text)..." oninput="go()"></p><div id="res"></div><h2>Channels</h2><ul>{lis}</ul></div>'
        f'<script src="search_index.js"></script><script>{js}</script>')

if __name__ == "__main__":
    main()
