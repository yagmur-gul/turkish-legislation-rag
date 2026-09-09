"""Bir kanuna dayanan tüm belgeleri listeler (tür ve tarihle birlikte).

Kullanım:
    python bagli.py 3568           # numaraya göre ara
    python bagli.py MÜŞAVİR        # ada göre ara
"""
import json
import sys

from tamamla import CIKTI, anahtar, katalog_oku

HEDEF_TURLER = {0, 1, 2, 4, 17, 19}

aranan = sys.argv[1] if len(sys.argv) > 1 else ""
kayitlar = katalog_oku()

# 1) Aranan kanunu bul
adaylar = []
for r in kayitlar:
    if r.get("mevzuatTur") not in HEDEF_TURLER:
        continue
    if aranan.isdigit():
        if str(r.get("mevzuatNo")) == aranan:
            adaylar.append(r)
    elif aranan.upper() in (r.get("mevAdi") or "").upper():
        adaylar.append(r)

if not adaylar:
    print(f"'{aranan}' için kanun bulunamadı.")
    sys.exit(1)

print("BULUNAN KANUN(LAR):")
for r in adaylar:
    print(f"  {anahtar(r):14} {r.get('mevAdi','')[:60]}")
print()

# 2) Katalogdan künye indeksi (tür + tarih için)
kunye = {anahtar(r): r for r in kayitlar}

# 3) Bağları oku
hedefler = {anahtar(r) for r in adaylar}
satirlar = []
with (CIKTI / "iliskiler.jsonl").open(encoding="utf-8") as f:
    for s in f:
        k = json.loads(s)
        if k["hedef"] in hedefler:
            satirlar.append(k)

if not satirlar:
    print("Bu kanuna dayanan belge bulunamadı.")
    sys.exit(0)

# 4) Tarihe göre sırala (yeniden eskiye)
def tarih_key(k):
    r = kunye.get(k["kaynak"], {})
    t = (r.get("resmiGazeteTarihi") or "")     # GG/AA/YYYY
    p = t.split("/")
    return (p[2], p[1], p[0]) if len(p) == 3 else ("", "", "")

satirlar.sort(key=tarih_key, reverse=True)

print(f"{'TARİH':11} {'TÜR':30} BELGE")
print("-" * 100)
for k in satirlar:
    r = kunye.get(k["kaynak"], {})
    tarih = r.get("resmiGazeteTarihi") or "?"
    tur = (r.get("mevzuatTurEnumString") or "?")[:29]
    isaret = " " if k["guvenilir"] else "~"
    print(f"{tarih:11} {tur:30} {isaret}{k['kaynak_ad'][:52]}")

print("-" * 100)
print(f"Toplam: {len(satirlar)} belge   (~ = çıkarımla kurulan bağ)")

# Tür dağılımı
from collections import Counter
d = Counter((kunye.get(k["kaynak"], {}).get("mevzuatTurEnumString") or "?")
            for k in satirlar)
print()
for t, n in d.most_common():
    print(f"  {n:4d}  {t}")