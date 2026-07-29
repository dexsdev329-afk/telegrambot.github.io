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

# ---------- $SWOGE burn watching ----------
SWOGE_TOKEN   = "0x8a166Fb41Cd659a0a43396272FF73973Ce29F817"   # Swole Doge ($SWOGE)
DEAD          = "0x000000000000000000000000000000000000dEaD"
TRANSFER      = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # Transfer(address,address,uint256)
SEL_BALANCEOF = "0x70a08231"
SEL_SUPPLY    = "0x18160ddd"
import urllib.parse

# ---------- buy watching (Uniswap Swap events on launchpad pools) ----------
WETH   = "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73"
WETH_L = WETH.lower()
SWAP   = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"  # Swap(...)
POOLS  = {}   # pool_addr(lower) -> {"token":.., "symbol":..}

# ---------- user config ----------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
SITE_URL  = os.environ.get("SITE_URL", "https://swoleeswoge.dog/launchpad.html").strip()
POLL      = int(os.environ.get("POLL_SECONDS", "20"))
BACKLOG   = os.environ.get("ANNOUNCE_BACKLOG", "0").strip() == "1"
WATCH_BUYS        = os.environ.get("WATCH_BUYS", "1").strip() == "1"     # 🟢 buy alerts
MIN_BUY_USD       = float(os.environ.get("MIN_BUY_USD", "1"))            # skip dust buys
USD_PER_BUY_EMOJI = float(os.environ.get("USD_PER_BUY_EMOJI", "10"))
BUY_EMOJI         = os.environ.get("BUY_EMOJI", "🟢")

# ---------- JSON-RPC ----------
# Some RPC providers (Cloudflare-fronted) reject the default "Python-urllib"
# User-Agent with 403 — send a normal one. Retry on 429/5xx with backoff so a
# rate limit or a hiccup never crashes the bot.
UA = "Mozilla/5.0 (compatible; SwogeBot/1.0; +https://swoleeswoge.dog)"
def rpc(method, params, tries=6):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(RPC, data=body,
                headers={"content-type":"application/json", "accept":"application/json", "user-agent":UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                out = json.loads(r.read())
            if out.get("error"):
                raise RuntimeError(out["error"])
            return out.get("result")
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(30, 2 ** attempt))   # 1,2,4,8,16,30 — backs off on rate limit
                continue
            raise
        except Exception as e:                       # network hiccup / timeout
            last = e
            time.sleep(min(30, 2 ** attempt))
    raise last

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
    headers = dict(headers); headers.setdefault("user-agent", UA)
    req = urllib.request.Request(TG + "/" + method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r: r.read()
        return True
    except urllib.error.HTTPError as e:
        print("Telegram error:", e.read().decode("utf-8", "ignore")); return False
    except Exception as e:
        print("Telegram error:", e); return False

def tg_message(text, reply_markup=None):
    if not BOT_TOKEN or not CHAT_ID:
        print("[dry-run]\n" + text + "\n"); return
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if reply_markup: payload["reply_markup"] = reply_markup
    tg_call("sendMessage", json.dumps(payload).encode(), {"content-type": "application/json"})

def tg_photo(img_bytes, caption, reply_markup=None):
    boundary = "----swoge" + str(len(img_bytes))
    def field(name, val):
        return ("--{}\r\nContent-Disposition: form-data; name=\"{}\"\r\n\r\n{}\r\n"
                .format(boundary, name, val)).encode()
    body  = field("chat_id", str(CHAT_ID)) + field("caption", caption) + field("parse_mode", "HTML")
    if reply_markup: body += field("reply_markup", json.dumps(reply_markup))
    body += ("--{}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"logo\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n".format(boundary)).encode()
    body += img_bytes + b"\r\n" + ("--{}--\r\n".format(boundary)).encode()
    return tg_call("sendPhoto", body, {"content-type": "multipart/form-data; boundary=" + boundary})

def tweet_button(l):
    text = "🚀 ${} just launched on SWOGE FUN — instantly on Uniswap & DexScreener! 🔒 LP locked, non-ruggable.".format(l["symbol"] or "TOKEN")
    url  = "https://twitter.com/intent/tweet?text=" + urllib.parse.quote(text) + "&url=" + urllib.parse.quote(SITE_URL)
    return {"inline_keyboard": [[{"text": "🐦 Tweet this", "url": url}]]}

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

    kb = tweet_button(l)   # 🐦 Tweet-this button under the alert
    # try to attach the logo as a photo
    logo = l["logo"] or ""
    if not BOT_TOKEN or not CHAT_ID:
        print("[dry-run]\n" + caption + ("\n(+logo)" if logo else "") + "\n[🐦 Tweet button]\n"); return
    try:
        if logo.startswith("data:image") and ";base64," in logo:
            img = base64.b64decode(logo.split(";base64,", 1)[1])
            if len(img) < 9_000_000 and tg_photo(img, caption, kb): return
        elif logo.startswith("http"):
            body = json.dumps({"chat_id": CHAT_ID, "photo": logo, "caption": caption,
                               "parse_mode": "HTML", "reply_markup": kb}).encode()
            if tg_call("sendPhoto", body, {"content-type": "application/json"}): return
    except Exception as e:
        print("photo failed, sending text:", e)
    tg_message(caption, kb)   # fallback: text only

# ---------- $SWOGE burns ----------
def swoge_burns_in_range(frm, to):
    dead_topic = "0x" + DEAD[2:].rjust(64, "0")
    flt = {"fromBlock": hx(frm), "toBlock": hx(to), "address": SWOGE_TOKEN,
           "topics": [TRANSFER, None, dead_topic]}   # Transfer(from, *, to=DEAD)
    logs = rpc("eth_getLogs", [flt])
    out = []
    for l in sorted(logs, key=lambda x: (to_int(x["blockNumber"]), to_int(x.get("logIndex","0x0")))):
        out.append({"from": addr_topic(l["topics"][1]),
                    "amount": to_int(l["data"]) / 1e18,   # SWOGE is 18 decimals
                    "tx": l["transactionHash"]})
    return out

def swoge_total_burned():
    try:
        r = rpc("eth_call", [{"to": SWOGE_TOKEN, "data": SEL_BALANCEOF + "0"*24 + DEAD[2:]}, "latest"])
        return to_int(r) / 1e18
    except Exception:
        return None

def announce_burn(b):
    total = swoge_total_burned()
    lines = [
        "🔥🔥🔥 <b>$SWOGE BURN</b> 🔥🔥🔥",
        "",
        "🔥 Just burned: <b>{:,.0f} SWOGE</b>".format(b["amount"]),
    ]
    if total is not None:
        lines.append("📊 Total burned: <b>{:,.0f} SWOGE</b>  ({:.3f}% of supply)".format(total, total/1e9*100))
    lines.append("👤 <a href=\"{}/address/{}\">{}</a> · <a href=\"{}/tx/{}\">Tx ↗</a>".format(
        SCAN, b["from"], short(b["from"]), SCAN, b["tx"]))
    tg_message("\n".join(lines))

# ---------- 🟢 buys ----------
_eth = {"v": None, "t": 0.0}
def eth_usd():
    if _eth["v"] and (time.time() - _eth["t"] < 120):
        return _eth["v"]
    for url, path in [("https://coins.llama.fi/prices/current/coingecko:ethereum", ("coins","coingecko:ethereum","price")),
                      ("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", ("ethereum","usd"))]:
        try:
            req = urllib.request.Request(url, headers={"user-agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                j = json.loads(r.read())
            for k in path: j = j[k]
            _eth["v"] = float(j); _eth["t"] = time.time(); return _eth["v"]
        except Exception:
            continue
    return _eth["v"]

def fmt_amt(n):
    if n >= 1e9: return "{:.2f}B".format(n/1e9)
    if n >= 1e6: return "{:.2f}M".format(n/1e6)
    if n >= 1e3: return "{:.1f}K".format(n/1e3)
    return "{:,.0f}".format(n)

def sint(hexword):
    v = int(hexword, 16)
    return v - (1 << 256) if v >= (1 << 255) else v

def build_pools(tip):
    created  = get_logs(CREATED, 0, tip)
    launched = get_logs(LAUNCHED, 0, tip)
    sym = {}
    for c in created:
        sym[addr_topic(c["topics"][1]).lower()] = decode_dyn_str(c["data"], 1)  # symbol = 2nd string
    for l in launched:
        tk   = addr_topic(l["topics"][1])
        pool = ("0x" + word(l["data"], 0)[-40:]).lower()
        POOLS[pool] = {"token": tk, "symbol": sym.get(tk.lower(), "?")}

def buys_in_range(frm, to):
    if not POOLS: return []
    flt = {"fromBlock": hx(frm), "toBlock": hx(to), "address": list(POOLS.keys()), "topics": [SWAP]}
    logs = rpc("eth_getLogs", [flt])
    out = []
    for l in sorted(logs, key=lambda x: (to_int(x["blockNumber"]), to_int(x.get("logIndex","0x0")))):
        info = POOLS.get(l["address"].lower())
        if not info: continue
        d = l["data"]; a0 = sint(d[2:66]); a1 = sint(d[66:130])
        t0w = WETH_L < info["token"].lower()
        wdelta = a0 if t0w else a1       # WETH delta for the pool
        tdelta = a1 if t0w else a0
        if wdelta <= 0: continue          # only buys (WETH flowing INTO the pool)
        out.append({"symbol": info["symbol"], "pool": l["address"],
                    "eth": wdelta/1e18, "tokens": abs(tdelta)/1e18,
                    "who": addr_topic(l["topics"][2]), "tx": l["transactionHash"]})
    return out

def announce_buy(b):
    price = eth_usd()
    usd = b["eth"] * price if price else None
    if usd is not None and usd < MIN_BUY_USD:
        return
    sym = html.escape(b["symbol"] or "?")[:24]
    n_em = max(1, min(50, int(usd // USD_PER_BUY_EMOJI) + 1)) if usd else 1
    lines = [
        BUY_EMOJI * n_em,
        "<b>${} Buy!</b>".format(sym),
        "",
        "💵 <b>{}</b>  ({:.4f} ETH)".format(("$%.2f" % usd) if usd else ("%.4f ETH" % b["eth"]), b["eth"]),
        "🪙 {} {}".format(fmt_amt(b["tokens"]), sym),
        "👤 <a href=\"{}/address/{}\">{}</a>".format(SCAN, b["who"], short(b["who"])),
        "📊 <a href=\"{}/{}\">DexScreener</a> · <a href=\"{}/tx/{}\">Tx ↗</a>".format(DEX, b["pool"], SCAN, b["tx"]),
    ]
    tg_message("\n".join(lines))

# ---------- diagnostic: which chats can this bot see? ----------
def discover_chats():
    if not BOT_TOKEN:
        return
    try:
        req = urllib.request.Request(TG + "/getUpdates", headers={"user-agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read())
        seen = {}
        for u in j.get("result", []):
            for key in ("message", "channel_post", "edited_message", "my_chat_member"):
                m = u.get(key)
                if m and m.get("chat"):
                    c = m["chat"]
                    seen[c["id"]] = "{} ({})".format(c.get("title") or c.get("username") or "?", c.get("type"))
        print("Configured TELEGRAM_CHAT_ID = {!r}".format(CHAT_ID))
        if seen:
            print("--- Chats this bot can see (copy one of these ids) ---")
            for cid, label in seen.items():
                print("    TELEGRAM_CHAT_ID = {}   →  {}".format(cid, label))
            print("------------------------------------------------------")
        else:
            print("This bot sees NO chats yet. Fix: add the bot to your group,")
            print("turn Group Privacy OFF in @BotFather, POST a message IN the group, then redeploy.")
    except Exception as e:
        print("discover_chats error:", e)

# ---------- main loop ----------
def main():
    print("SWOGE FUN new-token bot starting…")
    discover_chats()
    # resilient startup: keep retrying instead of crashing (so Railway doesn't restart-storm)
    tip = None
    while tip is None:
        try:
            tip = to_int(rpc("eth_blockNumber", []))
        except Exception as e:
            print("startup: RPC not ready ({}), retrying in 15s…".format(e)); time.sleep(15)
    if WATCH_BUYS:
        try:
            build_pools(tip); print("tracking {} pools for buy alerts".format(len(POOLS)))
        except Exception as e:
            print("pool scan skipped:", e)
    if BACKLOG:
        try:
            start = max(0, tip - 500000)
            recent = launches_in_range(start, tip)[-5:]
            print("posting {} backlog launches".format(len(recent)))
            for l in recent: announce(l); time.sleep(1)
        except Exception as e:
            print("backlog skipped:", e)
    last = tip
    print("Ready. Watching new tokens + $SWOGE burns" + (" + 🟢 buys" if WATCH_BUYS else "") + " from block", last)
    while True:
        try:
            t = to_int(rpc("eth_blockNumber", []))
            if t > last:
                for l in launches_in_range(last + 1, t):
                    announce(l)
                    if l.get("pool"): POOLS[l["pool"].lower()] = {"token": l["token"], "symbol": l["symbol"]}
                    time.sleep(1)
                for b in swoge_burns_in_range(last + 1, t):
                    announce_burn(b); time.sleep(1)
                if WATCH_BUYS:
                    for bu in buys_in_range(last + 1, t):
                        announce_buy(bu); time.sleep(1)
                last = t
        except Exception as ex:
            print("loop error:", ex)
        time.sleep(POLL)

if __name__ == "__main__":
    main()
