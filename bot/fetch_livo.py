"""livo.ge: owner-only Tbilisi flat rentals -> raw/livo.json.
Livo is a TNET front over the same statements base as myhome.ge (same ids, fields and tnet.ge photos), but its search
pages answer plain HTTP: the page streams the results in the Next.js flight data (self.__next_f), parsed here. Items keep
the myhome item shape, so ingest reuses the myhome conversion; the same flat from both sites is merged by dedupe.
Phones are not stored."""
import json
import re

import httpx

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
URL = "https://livo.ge/en/s?deal_types=2&real_estate_types=1&cities=1&owner_type=physical&currency_id=1&page={}"
PAGES = 4  # 24 per page


def flight(html):
    """Concatenated self.__next_f payload of a Next.js app-router page."""
    return "".join(json.loads(f'"{c}"') for c in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S))


def statements(html):
    """Search result statements embedded in the page (objects that start with id + metro_station_id)."""
    txt, dec, out = flight(html), json.JSONDecoder(), []
    for m in re.finditer(r'\{"id":\d+,"metro_station_id"', txt):
        try:
            out.append(dec.raw_decode(txt, m.start())[0])
        except ValueError:
            continue
    return out


def main():
    items, seen = [], set()
    with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True) as c:
        for page in range(1, PAGES + 1):
            r = c.get(URL.format(page))
            r.raise_for_status()
            batch = [i for i in statements(r.text) if i["deal_type_id"] == 2 and i["id"] not in seen]
            seen.update(i["id"] for i in batch)
            items += batch
            print("page", page, len(batch))
    assert items, "no rentals parsed: the page layout changed"
    json.dump(items, open("raw/livo.json", "w"), ensure_ascii=False)
    print("saved", len(items))


if __name__ == "__main__":
    main()
