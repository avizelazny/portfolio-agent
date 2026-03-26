import requests
from bs4 import BeautifulSoup

r = requests.get(
    'http://www.funder.co.il/fund.aspx?id=5136544',
    headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
    timeout=10
)
r.encoding = 'utf-8'
print(f"Status: {r.status_code}, Length: {len(r.text)}")

soup = BeautifulSoup(r.text, 'html.parser')

print("\n--- כל ה-spans עם id שיש בהם תוכן ---")
for s in soup.find_all('span', id=True):
    txt = s.get_text(strip=True)
    if txt and len(txt) < 50:
        print(f"  {s['id']:<60} = '{txt}'")

print("\n--- חיפוש מחיר/NAV לפי טקסט ---")
for tag in soup.find_all(string=True):
    t = tag.strip()
    if t and any(c.isdigit() for c in t) and '.' in t and 0.5 < len(t) < 15:
        parent = tag.parent
        if parent and parent.get('id'):
            print(f"  id={parent['id']}: '{t}'")
