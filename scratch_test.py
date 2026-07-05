import sys
import re
import urllib.request
import urllib.error

def main():
    ff_url = "https://fuckingfast.co/mkx39mgx8koc#PRAGMATA_Cracked_--_fitgirl-repacks.site_--_.part01.rar"
    file_id = "mkx39mgx8koc"
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
            pass
    except Exception as e:
        print("WARNING GET: " + str(e))

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
                print("SUCCESS: " + hx_redirect.strip())
            else:
                print("ERROR: No hx-redirect header found.")
    except urllib.error.HTTPError as e:
        hx_redirect = e.headers.get("hx-redirect") or e.headers.get("HX-Redirect")
        if hx_redirect:
            print("SUCCESS (from error): " + hx_redirect.strip())
        else:
            print("ERROR: HTTP " + str(e.code) + " from POST /go")
    except Exception as e:
        print("ERROR POST: " + str(e))

if __name__ == "__main__":
    main()
