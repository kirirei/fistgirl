import sys
import re
import urllib.request
import urllib.error

def main():
    if len(sys.argv) < 2:
        print("ERROR: No URL provided", file=sys.stderr)
        sys.exit(1)

    ff_url = sys.argv[1]

    # Extract file ID from URL like https://fuckingfast.co/mkx39mgx8koc#...
    m = re.search(r'fuckingfast\.co/([a-zA-Z0-9]+)(?:[#?]|$)', ff_url)
    if not m:
        print("ERROR: Could not extract file ID from URL: " + ff_url, file=sys.stderr)
        sys.exit(1)

    file_id = m.group(1)
    post_url = "https://fuckingfast.co/f/" + file_id + "/go"

    headers_common = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    urllib.request.install_opener(opener)

    try:
        req = urllib.request.Request(ff_url, headers=headers_common)
        with opener.open(req, timeout=15) as resp:
            pass  # Just need the cookies
    except Exception as e:
        pass # Continue anyway - the POST might still work

    post_headers = {
        **headers_common,
        "Accept": "*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "HX-Request": "true",
        "HX-Current-URL": ff_url,
        "HX-Target": "body",
        "Origin": "https://fuckingfast.co",
        "Referer": ff_url,
    }

    try:
        req = urllib.request.Request(post_url, data=b"", headers=post_headers, method="POST")
        with opener.open(req, timeout=15) as resp:
            hx_redirect = resp.headers.get("hx-redirect") or resp.headers.get("HX-Redirect")
            if hx_redirect:
                print(hx_redirect.strip())
                sys.exit(0)
            else:
                body = resp.read(500).decode("utf-8", errors="replace")
                print("ERROR: No hx-redirect header in response. Body: " + body, file=sys.stderr)
                sys.exit(1)
    except urllib.error.HTTPError as e:
        hx_redirect = e.headers.get("hx-redirect") or e.headers.get("HX-Redirect")
        if hx_redirect:
            print(hx_redirect.strip())
            sys.exit(0)
        print("ERROR: HTTP " + str(e.code) + " from POST /go: " + str(e.reason), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print("ERROR: POST /go failed: " + str(e), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
