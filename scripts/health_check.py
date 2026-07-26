#!/usr/bin/env python3
import json, re, os, sys, time, urllib.parse
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
        if line.startswith("#EXTINF:"):
            if ui < len(urls):
                combined.append((line, urls[ui]))
                ui += 1
    return combined

IP_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
def is_ip(url):
    m = re.match(r"https?://([^:/]+)", url)
    return bool(m and IP_RE.match(m.group(1)))

def resolve_url(base, maybe_rel):
    if maybe_rel.startswith("http"):
        return maybe_rel
    return urllib.parse.urljoin(base, maybe_rel)

def get_first_segment_url(base_url, body):
    text = body.decode("utf-8", errors="replace")
    is_master = "#EXT-X-STREAM-INF" in text
    if is_master:
        for line in text.split("\n"):
            l = line.strip()
            if l.startswith("http") and not l.startswith("#"):
                return resolve_url(base_url, l)
        return None
    for line in text.split("\n"):
        l = line.strip()
        if l.startswith("http") and not l.startswith("#"):
            return resolve_url(base_url, l)
    return None

def test(ch):
    url = ch["url"]
    start = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            code = r.status
            ct = r.headers.get("Content-Type", "")
            body = r.read(16384).strip()
            elapsed = int((time.time() - start) * 1000)
            ch["http_code"] = code
            ch["ms"] = elapsed
            ch["content_type"] = ct

            if code != 200:
                ch["error"] = f"HTTP {code}"
                return ch

            is_m3u = body[:7] == b"#EXTM3U"
            has_inf = b"#EXTINF" in body
            is_hls_ct = "mpegurl" in ct.lower() or "hls" in ct.lower()

            if not (is_hls_ct or is_m3u or ct.startswith("video/")):
                ch["error"] = f"bad_ct:{ct[:30]}"
                return ch

            ch["playlist_ok"] = True
            ch["segment_ok"] = False

            if is_m3u:
                seg_url = get_first_segment_url(url, body)
                if seg_url:
                    try:
                        sreq = urllib.request.Request(seg_url, headers={"User-Agent": UA})
                        sreq.method = "HEAD"
                        with urllib.request.urlopen(sreq, timeout=10) as sr:
                            if sr.status in (200, 206, 302, 301):
                                ch["segment_ok"] = True
                                ch["segment_url"] = seg_url
                                ch["segment_code"] = sr.status
                    except urllib.error.HTTPError as e:
                        if e.code in (403, 401):
                            ch["segment_ok"] = True
                            ch["segment_url"] = seg_url
                            ch["segment_code"] = e.code
                            ch["segment_note"] = "auth_required"
                        else:
                            ch["segment_error"] = f"HTTP{e.code}"
                    except Exception as e:
                        ch["segment_error"] = str(e)[:80]
                else:
                    ch["segment_note"] = "no_seg_url_found"

            has_any_seg = ch.get("segment_ok") or ch.get("segment_note") == "auth_required"
            if is_hls_ct or ct.startswith("video/"):
                ch["working"] = True
            elif has_any_seg:
                ch["working"] = True
            elif is_m3u and has_inf:
                ch["working"] = True
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
                    "url": url, "working": False, "http_code": 0, "error": "",
                }

    total = len(all_chs)
    print(f"[{time.strftime('%H:%M:%S')}] {total} domain-based URLs loaded")
    print(f"[{time.strftime('%H:%M:%S')}] Starting health check (playlist + segment)...")

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
    seg_ok = [r for r in working if r.get("segment_ok")]
    playlist_only = [r for r in working if not r.get("segment_ok") and r.get("playlist_ok")]
    dead = [r for r in results if not r.get("working")]
    working.sort(key=lambda x: x.get("name", "").lower())

    summary = {
        "scan_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_tested": len(results),
        "working": len(working),
        "segment_confirmed": len(seg_ok),
        "playlist_only": len(playlist_only),
        "dead": len(dead),
        "success_rate": f"{len(working)*100//max(len(results),1)}%",
        "segment_rate": f"{len(seg_ok)*100//max(len(working),1)}%",
        "duration_sec": int(time.time() - t0),
    }

    for name, data in [
        ("channels.json", results),
        ("channels_working.json", working),
        ("channels_segment_ok.json", seg_ok),
        ("summary.json", summary),
    ]:
        with open(f"{OUTPUT_DIR}/{name}", "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  ✅ {name} ({len(data) if isinstance(data, list) else 'summary'} records)")

    m3u_lines = ['#EXTM3U', f'#PLAYLIST: India IPTV — {time.strftime("%Y-%m-%d %H:%M:%S")}']
    m3u_lines.append(f'# Total working: {len(working)}/{len(results)} (segment verified: {len(seg_ok)})')
    m3u_lines.append('')
    for ch in working:
        tag = ch.get("segment_ok") and "SEG_OK" or "PLAYLIST_OK"
        m3u_lines.append(f'#EXTINF:-1 tvg-id="{ch["tvg_id"]}" tvg-logo="{ch["tvg_logo"]}" group-title="{ch["group"]}",{ch["name"]}')
        m3u_lines.append(ch["url"])
        m3u_lines.append('')
    with open(f"{OUTPUT_DIR}/india.m3u", "w") as f:
        f.write("\n".join(m3u_lines))
    print(f"  ✅ india.m3u ({len(working)} channels)")

    elapsed = int(time.time() - t0)
    print(f"\n===== DONE in {elapsed}s =====")
    print(f"  Segment verified: {len(seg_ok)}/{len(working)}")
    print(f"  Playlist only:    {len(playlist_only)}/{len(working)}")
    print(f"  Dead:             {len(dead)}/{len(results)}")
    print(f"  Output: {OUTPUT_DIR}/")

if __name__ == "__main__":
    main()
