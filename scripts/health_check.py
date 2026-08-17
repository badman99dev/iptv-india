#!/usr/bin/env python3
import json, re, os, time
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

M3U_SOURCES = [
    "https://iptv-org.github.io/iptv/countries/in.m3u",
    "https://iptv-org.github.io/iptv/languages/hin.m3u",
    "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/in.m3u",
]

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")

def is_ip(url):
    m = re.match(r"https?://([^:/]+)", url)
    return bool(m and IP_RE.match(m.group(1)))

def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except:
        return ""

def parse(content):
    urls = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#") and l.strip().startswith("http")]
    combined = []
    ui = 0
    for line in content.split("\n"):
        if line.startswith("#EXTINF:") and ui < len(urls):
            combined.append((line, urls[ui]))
            ui += 1
    return combined

HLS_CTS = {"application/vnd.apple.mpegurl", "application/x-mpegURL", "audio/mpegurl", "video/mpegurl", "video/x-mpegurl"}

def test(ch):
    url = ch["url"]
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.status
            ct = r.headers.get("Content-Type", "").lower().split(";")[0].strip()
            body = r.read(1024)
            ms = int((time.time() - t0) * 1000)
            ch["http_code"] = code
            ch["ms"] = ms
            ch["content_type"] = ct
            if code == 200:
                if body[:7] == b"#EXTM3U":
                    ch["working"] = True
                    if ct in HLS_CTS:
                        ch["content_type"] = ct
                    else:
                        ch["content_type"] = ct or "m3u8_detected"
    except urllib.error.HTTPError as e:
        ch["http_code"] = e.code
        ch["content_type"] = str(e.code)
    except Exception as e:
        ch["error"] = str(e)[:80]
    return ch

def main():
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Fetching M3U sources...")

    all_chs = OrderedDict()
    for src in M3U_SOURCES:
        for extinf, url in parse(fetch(src)):
            if not is_ip(url) and url not in all_chs:
                tvg_id = re.search(r'tvg-id="([^"]*)"', extinf)
                tvg_logo = re.search(r'tvg-logo="([^"]*)"', extinf)
                group = re.search(r'group-title="([^"]*)"', extinf)
                name = extinf.split(",")[-1] if "," in extinf else ""
                name = re.sub(r"\s*\[.*?\]\s*$", "", name).strip()
                name = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
                all_chs[url] = {
                    "name": name or (tvg_id.group(1) if tvg_id else url),
                    "tvg_id": tvg_id.group(1) if tvg_id else "",
                    "tvg_logo": tvg_logo.group(1) if tvg_logo else "",
                    "group": group.group(1) if group else "Uncategorized",
                    "url": url, "working": False, "http_code": 0, "content_type": "",
                }

    total = len(all_chs)
    print(f"[{time.strftime('%H:%M:%S')}] {total} domain-based URLs loaded")
    print(f"[{time.strftime('%H:%M:%S')}] Testing...")

    results = []
    clist = list(all_chs.values())
    for bs in range(0, len(clist), 100):
        batch = clist[bs:bs + 100]
        with ThreadPoolExecutor(max_workers=50) as ex:
            for f in as_completed([ex.submit(test, ch) for ch in batch]):
                try: results.append(f.result())
                except: pass

    working = sorted([r for r in results if r.get("working")], key=lambda x: x["name"].lower())
    dead = [r for r in results if not r.get("working")]

    summary = {
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_tested": len(results),
        "working": len(working),
        "dead": len(dead),
        "success_rate": f"{len(working)*100//max(len(results),1)}%",
        "duration_sec": int(time.time() - t0),
    }

    for name, data in [
        ("channels.json", results),
        ("channels_working.json", working),
        ("summary.json", summary),
    ]:
        with open(f"{OUTPUT_DIR}/{name}", "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  ✅ {name}")

    m3u = ['#EXTM3U', f'#PLAYLIST: India IPTV — {time.strftime("%Y-%m-%d %H:%M:%S")}',
           f'# Total: {len(working)}/{len(results)} working', '']
    for ch in working:
        m3u.append(f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["tvg_logo"]}" group-title="{ch["group"]}",{ch["name"]}')
        m3u.append(ch["url"])
        m3u.append('')
    with open(f"{OUTPUT_DIR}/india.m3u", "w") as f:
        f.write("\n".join(m3u))
    print(f"  ✅ india.m3u ({len(working)} channels)")

    elapsed = int(time.time() - t0)
    print(f"\n===== DONE in {elapsed}s =====")
    print(f"  Working: {len(working)}/{len(results)} ({len(working)*100//max(len(results),1)}%)")

if __name__ == "__main__":
    main()
