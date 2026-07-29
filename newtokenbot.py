#!/usr/bin/env python3
"""
SWOGE FUN — Telegram "New Token" Bot
------------------------------------
Watches the SwogeFun V2 launchpad and posts a Telegram message every time a NEW
token is launched on the site — with its name, symbol, creator, contract, the
DexScreener link, and the token's logo as a photo. Read-only: it never trades or
touches funds.

Runs anywhere (Railway, a VPS, your PC). Stdlib only, no dependencies.

Setup (env vars):
  TELEGRAM_BOT_TOKEN   from @BotFather
  TELEGRAM_CHAT_ID     the group/channel id (add the bot as admin)
  SITE_URL             optional, default https://swoleeswoge.dog/launchpad.html
  POLL_SECONDS         optional, default 20
  ANNOUNCE_BACKLOG     optional "1" to also post the most recent existing tokens
                       once on startup (default: only NEW launches from now on)
"""
import os, time, json, html, base64, urllib.request, urllib.error

# ---------- chain / contract (Robinhood Chain, SwogeFun V2) ----------
RPC      = "https://rpc.mainnet.chain.robinhood.com"
SCAN     = "https://robinhoodchain.blockscout.com"
DEX      = "https://dexscreener.com/robinhood"
SWOGEFUN = "0x4De26D120A4fF2d7c1875E6C7D611262b9cA426d"
# keccak256 event topics (verified on-chain):
CREATED  = "0xe24def8f40c993591ec8d6fe0afef5090346e2c3b0712c4a1ed94094f6b99e7a"  # Created(address,address,string,string,uint8,address,uint16,uint16)
LAUNCHED = "0x0d6efe4ae681464f9cc9a7e40579bc542b40245942b72c25a8f4388c3be3e078"  # LaunchedInstant(address,address,address,uint256)
META     = "0x4b61405e238df570af60c371625a1b234a1e8ab96f3670bc6f67c87b179d2799"  # Meta(address,string,string,string,string)
SEL_NAME   = "0x06fdde03"
SEL_SYMBOL = "0x95d89b41"

# ---------- user config ----------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SITE_URL  = os.environ.get("SITE_URL", "https://swoleeswoge.dog/launchpad.html").strip()
POLL      = int(os.environ.get("POLL_SECONDS", "20"))
BACKLOG   = os.environ.get("ANNOUNCE_BACKLOG", "0").strip() == "1"

# ---------- JSON-RPC ----------
def rpc(method, params):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    req  = urllib.request.Request(RPC, data=body, headers={"content-type":"application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = json.loads(r.read())
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out.get("result")

def hx(n):         return hex(n)
def to_int(h):     return int(h, 16)
def word(d, i):    return d[2+i*64:2+(i+1)*64]
def addr_topic(t): return "0x" + t[-40:]
def short(a):      return a[:6] + "…" + a[-4:]

def get_logs(topic, frm, to):
    return rpc("eth_getLogs", [{"fromBlock": hx(frm), "toBlock": hx(to), "address": SWOGEFUN, "topics": [topic]}])

# ---------- ABI string decoding ----------
def decode_dyn_str(data, word_index):
    """Read the i-th dynamic string from event data (data is hex incl. 0x)."""
    try:
        off = int(word(data, word_index), 16)
        p = 2 + off * 2
        ln = int(data[p:p+64], 16)
        return bytes.fromhex(data[p+64:p+64+ln*2]).decode("utf-8", "ignore")
    except Exception:
        return ""

def eth_call_str(token, selector):
    try:
        r = rpc("eth_call", [{"to": token, "data": selector}, "latest"])
        if not r or r == "0x": return ""
        b = bytes.fromhex(r[2:])
        if len(b) < 64: return b.rstrip(b"\x00").decode("utf-8", "ignore")
        off = int.from_bytes(b[0:32], "big"); ln = int.from_bytes(b[off:off+32], "big")
        return b[off+32:off+32+ln].decode("utf-8", "ignore")
    except Exception:
        return ""

# ---------- gather launches in a block range ----------
def launches_in_range(frm, to):
    created = get_logs(CREATED, frm, to)
    if not created:
        return []
    launched = get_logs(LAUNCHED, frm, to)
    meta     = get_logs(META, frm, to)
    lmap = {addr_topic(l["topics"][1]).lower(): l for l in launched}
    mmap = {addr_topic(l["topics"][1]).lower(): l for l in meta}
    out = []
    for c in sorted(created, key=lambda l: (to_int(l["blockNumber"]), to_int(l.get("logIndex","0x0")))):
        token   = addr_topic(c["topics"][1])
        creator = addr_topic(c["topics"][2])
        tk = token.lower()
        pool = ""
        if tk in lmap:
            pool = "0x" + word(lmap[tk]["data"], 0)[-40:]
        tg = tw = web = logo = ""
        if tk in mmap:
            d = mmap[tk]["data"]
            tg = decode_dyn_str(d, 0); tw = decode_dyn_str(d, 1)
            web = decode_dyn_str(d, 2); logo = decode_dyn_str(d, 3)
        out.append({
            "token": token, "creator": creator, "pool": pool,
            "name": eth_call_str(token, SEL_NAME), "symbol": eth_call_str(token, SEL_SYMBOL),
            "tg": tg, "tw": tw, "web": web, "logo": logo, "tx": c["transactionHash"],
        })
    return out

# ---------- Telegram ----------
TG = "https://api.telegram.org/bot{}".format(BOT_TOKEN)
def tg_call(method, data, headers):
    req = urllib.request.Request(TG + "/" + method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r: r.read()
        return True
    except urllib.error.HTTPError as e:
        print("Telegram error:", e.read().decode("utf-8", "ignore")); return False
    except Exception as e:
        print("Telegram error:", e); return False

def tg_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[dry-run]\n" + text + "\n"); return
    body = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    tg_call("sendMessage", body, {"content-type": "application/json"})

def tg_photo(img_bytes, caption):
    boundary = "----swoge" + str(len(img_bytes))
    def field(name, val):
        return ("--{}\r\nContent-Disposition: form-data; name=\"{}\"\r\n\r\n{}\r\n"
                .format(boundary, name, val)).encode()
    body  = field("chat_id", str(CHAT_ID)) + field("caption", caption) + field("parse_mode", "HTML")
    body += ("--{}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"logo\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n".format(boundary)).encode()
    body += img_bytes + b"\r\n" + ("--{}--\r\n".format(boundary)).encode()
    return tg_call("sendPhoto", body, {"content-type": "multipart/form-data; boundary=" + boundary})

# ---------- announce one launch ----------
def announce(l):
    sym  = html.escape(l["symbol"] or "?")[:24]
    name = html.escape(l["name"] or "Unknown")[:64]
    lines = [
        "🚀 <b>NEW TOKEN LAUNCHED</b> on SWOGE FUN",
        "",
        "<b>{}</b>  (${})".format(name, sym),
        "🔒 LP Locked · ⚡ live on Uniswap",
        "",
        "👤 Creator: <code>{}</code>".format(short(l["creator"])),
        "📄 Contract: <code>{}</code>".format(l["token"]),
    ]
    links = []
    if l["pool"]: links.append("📊 <a href=\"{}/{}\">DexScreener</a>".format(DEX, l["pool"]))
    links.append("🛒 <a href=\"{}\">Buy on SWOGE</a>".format(SITE_URL))
    links.append("🔎 <a href=\"{}/address/{}\">Explorer</a>".format(SCAN, l["token"]))
    lines.append(" · ".join(links))
    socials = []
    if l["tg"]:  socials.append("<a href=\"{}\">Telegram</a>".format(html.escape(l["tg"])))
    if l["tw"]:  socials.append("<a href=\"{}\">Twitter</a>".format(html.escape(l["tw"])))
    if l["web"]: socials.append("<a href=\"{}\">Website</a>".format(html.escape(l["web"])))
    if socials: lines.append("🔗 " + " · ".join(socials))
    caption = "\n".join(lines)

    # try to attach the logo as a photo
    logo = l["logo"] or ""
    if not BOT_TOKEN or not CHAT_ID:
        print("[dry-run]\n" + caption + ("\n(+logo)" if logo else "") + "\n"); return
    try:
        if logo.startswith("data:image") and ";base64," in logo:
            img = base64.b64decode(logo.split(";base64,", 1)[1])
            if len(img) < 9_000_000 and tg_photo(img, caption): return
        elif logo.startswith("http"):
            body = json.dumps({"chat_id": CHAT_ID, "photo": logo, "caption": caption,
                               "parse_mode": "HTML"}).encode()
            if tg_call("sendPhoto", body, {"content-type": "application/json"}): return
    except Exception as e:
        print("photo failed, sending text:", e)
    tg_message(caption)   # fallback: text only

# ---------- main loop ----------
def main():
    print("SWOGE FUN new-token bot starting…")
    tip = to_int(rpc("eth_blockNumber", []))
    if BACKLOG:
        # announce the last few existing tokens once (scan a recent window)
        start = max(0, tip - 500000)
        recent = launches_in_range(start, tip)[-5:]
        print("posting {} backlog launches".format(len(recent)))
        for l in recent: announce(l); time.sleep(1)
    last = tip
    print("Ready. Watching from block", last)
    while True:
        try:
            t = to_int(rpc("eth_blockNumber", []))
            if t > last:
                for l in launches_in_range(last + 1, t):
                    announce(l); time.sleep(1)
                last = t
        except Exception as ex:
            print("loop error:", ex)
        time.sleep(POLL)

if __name__ == "__main__":
    main()
