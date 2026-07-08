"""Fetch one openly-licensed photo per native species from Wikimedia, with
attribution, into app/data/trees/. Attribution is saved to photos.json and
shown in the app. Run on a machine with internet."""
import os, re, json, html, requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "app", "data", "trees")
os.makedirs(OUT, exist_ok=True)

SPECIES = [
    ("neem", "Azadirachta indica"),
    ("peepal", "Ficus religiosa"),
    ("banyan", "Ficus benghalensis"),
    ("amaltas", "Cassia fistula"),
    ("jamun", "Syzygium cumini"),
    ("arjun", "Terminalia arjuna"),
    ("siris", "Albizia lebbeck"),
    ("ber", "Ziziphus mauritiana"),
]

S = requests.Session()
S.headers["User-Agent"] = "sabzaar/1.0 (portfolio project; contact safeer.ali.mirani@gmail.com)"
API = "https://en.wikipedia.org/w/api.php"


def strip(t):
    t = re.sub(r"<[^>]+>", "", t or "")
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


out = []
for slug, title in SPECIES:
    try:
        r = S.get(API, params={"action": "query", "titles": title, "prop": "pageimages",
                               "piprop": "name", "format": "json"}, timeout=30).json()
        page = next(iter(r["query"]["pages"].values()))
        fname = page.get("pageimage")
        if not fname:
            print(f"  {slug}: no lead image"); continue
        r2 = S.get(API, params={"action": "query", "titles": "File:" + fname,
                                "prop": "imageinfo", "iiprop": "extmetadata|url",
                                "iiurlwidth": 560, "format": "json"}, timeout=30).json()
        ii = next(iter(r2["query"]["pages"].values()))["imageinfo"][0]
        ex = ii.get("extmetadata", {})
        thumb = ii["thumburl"]
        img = S.get(thumb, timeout=60).content
        with open(os.path.join(OUT, slug + ".jpg"), "wb") as f:
            f.write(img)
        rec = {
            "slug": slug,
            "credit": strip(ex.get("Artist", {}).get("value", "")) or "Wikimedia Commons",
            "license": strip(ex.get("LicenseShortName", {}).get("value", "")) or "see source",
            "licenseUrl": ex.get("LicenseUrl", {}).get("value", ""),
            "source": ii.get("descriptionshorturl") or ii.get("descriptionurl", ""),
        }
        out.append(rec)
        print(f"  {slug}: {len(img)//1024} KB  {rec['license']}  by {rec['credit'][:40]}")
    except Exception as ex:
        print(f"  {slug}: FAIL {ex}")

with open(os.path.join(OUT, "photos.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, indent=2)
print(f"done -> {len(out)} photos")
