#!/usr/bin/env python3
"""Synthetic DiscordChatExporter-style JSON exports (made-up server, users, messages) for testing.
Mimics DCE's JSON shape: guild, channel, messages[{id,type,timestamp,timestampEdited,isPinned,content,
author{id,name,discriminator,nickname,isBot,avatarUrl},attachments[{id,url,fileName,fileSizeBytes}],
embeds,stickers,reactions[{emoji{name},count}],mentions[{id,name,nickname}],reference{messageId}}]."""
import json, random, datetime, pathlib, struct, zlib
random.seed(42)
OUT = pathlib.Path(__file__).parent/"synthetic_export"
(OUT/"media").mkdir(parents=True, exist_ok=True)

def png(path, rgb):  # tiny solid-color PNG so media copying can be tested
    w = h = 32; raw = b"".join(b"\x00" + bytes(rgb)*w for _ in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t+d) & 0xffffffff)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

users = [{"id": str(100000+i), "name": n, "discriminator": "0000", "nickname": nk, "isBot": b, "avatarUrl": ""}
         for i, (n, nk, b) in enumerate([("mossfox", "Moss 🦊", False), ("tidewolf", "Tide", False), ("pixelotter", "Otter", False),
                                          ("emberhyena", "Ember", False), ("Deleted User", "Deleted User", False), ("ModBot", "ModBot", True),
                                          ("quillcat", "Quill", False), ("runeraccoon", "Rune", False)])]
guild = {"id": "900000000000000001", "name": "Fluffy Workshop (synthetic test server)", "iconUrl": ""}
lines = ["anyone going to the con this year?", "finished the **ref sheet** finally", "new commission slots open soon",
         "check this out https://example.com/wip", "lol same", "`#ff8800` is the orange I used", "||spoiler: the ending||",
         "my email is mossfox@example.com if you want the file", "call me at 555-123-4567", "*so* tired today", "gm everyone",
         "how do you pattern a fursuit head?", "use duct tape method", "thanks!!", "meeting moved to saturday"]
mid = 1300000000000000000
def msgs(channel_name, n, start):
    global mid
    out = []; t = start
    for i in range(n):
        mid += random.randint(1, 9999); t += datetime.timedelta(minutes=random.randint(1, 600))
        a = random.choice(users); content = random.choice(lines)
        m = {"id": str(mid), "type": "Default", "timestamp": t.isoformat(timespec="milliseconds") + "+00:00",
             "timestampEdited": (t + datetime.timedelta(minutes=3)).isoformat() + "+00:00" if random.random() < .05 else None,
             "isPinned": random.random() < .02, "content": content, "author": a, "attachments": [], "embeds": [],
             "stickers": [], "reactions": [], "mentions": [], "reference": None}
        if random.random() < .08:
            mm = random.choice(users); m["content"] = f"<@{mm['id']}> " + content; m["mentions"] = [{"id": mm["id"], "name": mm["name"], "nickname": mm["nickname"]}]
        if random.random() < .07 and out: m["type"] = "Reply"; m["reference"] = {"messageId": random.choice(out)["id"]}
        if random.random() < .06:
            fn = f"{mid}.png"
            if random.random() < .6:
                png(OUT/"media"/fn, (random.randrange(256), random.randrange(256), random.randrange(256)))
                url = f"media/{fn}"
            else:
                url = f"https://cdn.discordapp.com/attachments/1/{mid}/{fn}?ex=deadbeef"  # expiring remote link
            m["attachments"] = [{"id": str(mid), "url": url, "fileName": fn, "fileSizeBytes": 120}]
        if random.random() < .1: m["reactions"] = [{"emoji": {"name": random.choice(["❤️", "😂", "🦊", "👍"])}, "count": random.randint(1, 6)}]
        if random.random() < .02: m["type"] = "ChannelPinnedMessage"; m["content"] = "Pinned a message."
        out.append(m)
    return out

start = datetime.datetime(2025, 6, 1, 12, 0)
chans = [("general", 420), ("commissions", 160), ("con-planning", 90)]
all_parts = []
for i, (name, n) in enumerate(chans):
    ch = {"id": str(910000000000000000+i), "type": "GuildTextChat", "categoryId": "1", "category": "Main", "name": name, "topic": f"{name} talk"}
    ms = msgs(name, n, start)
    doc = {"guild": guild, "channel": ch, "dateRange": {"after": None, "before": None}, "exportedAt": "2026-02-10T00:00:00+00:00", "messages": ms, "messageCount": len(ms)}
    json.dump(doc, open(OUT/f"Fluffy Workshop - Main - {name} [{ch['id']}].json", "w"), ensure_ascii=False, indent=1)
    all_parts.append((ch, ms))
# overlapping second export of 'general' (common when admins re-export): last 50 msgs duplicated + 20 new
ch, ms = all_parts[0]
extra = msgs("general", 20, datetime.datetime.fromisoformat(ms[-1]["timestamp"][:23]))
doc = {"guild": guild, "channel": ch, "dateRange": {}, "exportedAt": "2026-02-12T00:00:00+00:00", "messages": ms[-50:] + extra, "messageCount": 70}
json.dump(doc, open(OUT/f"Fluffy Workshop - Main - general [{ch['id']}] (part 2).json", "w"), ensure_ascii=False, indent=1)
print("wrote", len(list(OUT.glob('*.json'))), "json files;", len(list((OUT/'media').iterdir())), "media files")
