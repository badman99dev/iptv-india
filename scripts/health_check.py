#!/usr/bin/env python3
import json, re, os, sys, time
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

def fetch(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return ""

def parse(content):
    chs = []
    for line in content.split("\n"):
        if line.startswith("#EXTINF:"):
            chs.append(line)
    urls = []
    for line in content.split("\n"):
        l = line.strip()
        if l and not l.startswith("#") and l.startswith("http"):
            urls.append(l)
    combined = []
    ci, ui = 0, 0
    for line in content.split("\n"):
        if line.startswith("#EXTINF:"):
            if ui < len(urls):
                combined.append((line, urls[ui]))
                ui += 1
            ci += 1
    return combined

IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
def is_ip(url):
    m = re.match(r"https?://([^:/]+)", url)
    return bool(m and IP_RE.match(m.group(1)))

def info(extinf, url):
    tvg_id = re.search(r'tvg-id="([^"]*)"', extinf)
    tvg_logo = re.search(r'tvg-logo="([^"]*)"', extinf)
    group = re.search(r'group-title="([^"]*)"', extinf)
    name = extinf.split(",")[-1] if "," in extinf else ""
    name = re.sub(r"\s*\[.*?\]\s*$", "", name).strip()
    name = re.sub(r"\s*\(.*?\)\s*$", "", name).strip()
    return {
        "name": name or (tvg_id.group(1) if tvg_id else url),
        "tvg_id": tvg_id.group(1) if tvg_id else "",
        "tvg_logo": tvg_logo.group(1) if tvg_logo else "",
        "group": group.group(1) if group else "Uncategorized",
        "url": url,
        "working": False,
        "http_code": 0,
        "error": "",
    }

def test(ch):
    url = ch["url"]
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.status
            ct = r.headers.get("Content-Type", "")
            body = r.read(8192).strip()
            elapsed = int((time.time() - start) * 1000)
            ch["http_code"] = code
            ch["ms"] = elapsed
            ch["content_type"] = ct
            ch["body_len"] = len(body)

            is_m3u = body[:7] == b"#EXTM3U"
            has_inf = b"#EXTINF" in body
            is_hls_ct = "mpegurl" in ct.lower() or "hls" in ct

            if code == 200 and (is_hls_ct or is_m3u):
                if is_m3u and (has_inf or b"#EXT-X-STREAM-INF" in body):
                    ch["working"] = True
                    ch["quality"] = "verified"
                elif is_hls_ct:
                    ch["working"] = True
                    ch["quality"] = "trusted_ct"
                elif body:
                    ch["working"] = True
                    ch["quality"] = "minimal"
            elif code == 200 and ct.startswith("video/"):
                ch["working"] = True
                ch["quality"] = "direct_video"
    except urllib.error.HTTPError as e:
        ch["http_code"] = e.code
        ch["error"] = f"HTTP {e.code}"
    except Exception as e:
        ch["error"] = str(e)[:100]
    return ch

def main():
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Fetching M3U sources...")

    all_chs = OrderedDict()
    for src in M3U_SOURCES:
        content = fetch(src)
        if not content: continue
        for extinf, url in parse(content):
            if not is_ip(url) and url not in all_chs:
                all_chs[url] = info(extinf, url)

    total = len(all_chs)
    print(f"[{time.strftime('%H:%M:%S')}] {total} domain-based URLs loaded")
    print(f"[{time.strftime('%H:%M:%S')}] Starting health check...")

    clist = list(all_chs.values())
    results = []
    batch_size = 100
    for bs in range(0, len(clist), batch_size):
        batch = clist[bs:bs + batch_size]
        with ThreadPoolExecutor(max_workers=50) as ex:
            for f in as_completed([ex.submit(test, ch) for ch in batch]):
                try: results.append(f.result())
                except: pass

    working = [r for r in results if r.get("working")]
    dead = [r for r in results if not r.get("working")]

    working.sort(key=lambda x: x.get("name", "").lower())

    for name, data in [
        ("channels.json", results),
        ("channels_working.json", working),
        ("summary.json", {
            "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_tested": len(results),
            "working": len(working),
            "dead": len(dead),
            "success_rate": f"{len(working)*100//max(len(results),1)}%",
            "duration_sec": int(time.time() - t0),
        }),
    ]:
        with open(f"{OUTPUT_DIR}/{name}", "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  ✅ {name} ({len(data) if isinstance(data, list) else 'dict'} records)")

    m3u_lines = ['#EXTM3U', f'#PLAYLIST: India IPTV — {time.strftime("%Y-%m-%d %H:%M:%S")}']
    m3u_lines.append(f'# Total working: {len(working)}/{len(results)}')
    m3u_lines.append('')
    for ch in working:
        m3u_lines.append(f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["tvg_logo"]}" group-title="{ch["group"]}",{ch["name"]}')
        m3u_lines.append(ch["url"])
        m3u_lines.append('')
    with open(f"{OUTPUT_DIR}/india.m3u", "w") as f:
        f.write("\n".join(m3u_lines))
    print(f"  ✅ india.m3u ({len(working)} channels)")

    elapsed = int(time.time() - t0)
    print(f"\n===== DONE in {elapsed}s =====")
    print(f"  Working: {len(working)}/{len(results)} ({len(working)*100//max(len(results),1)}%)")
    print(f"  Output: {OUTPUT_DIR}/")

if __name__ == "__main__":
    main()
