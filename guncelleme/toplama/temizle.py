# -*- coding: utf-8 -*-
"""
temizle.py — MÜKERRER BELGE AYIKLAYICI (opsiyonel bakım aracı).

Aynı belge (belge_id) çekme sırasındaki tekrar denemeler yüzünden birden çok kez
inmiş olabilir. Bu betik her belge için EN İYİ kopyayı tutar, gerisini
cikti/_cop/ (çöp) klasörüne taşır. "En iyi" = önce kalite kontrolünden (kusur)
geçen, eşitlikte en büyük dosya. Kalıcı silme yok — sadece çöpe taşıma; yanlışsa
geri_al.py ile geri alınır. Günlük güncelleme için şart değildir; yalnız çekme
sonrası çok tekrar biriktiyse elle çalıştırılır.

Çalıştır: python arsiv/toplama/temizle.py   (klasör kökünden)
"""
import shutil
import sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # guncelleme/ (tamamla burada)
from tamamla import BELGELER, bas_oku, kusur

cop = Path('cikti/_cop')
cop.mkdir(parents=True, exist_ok=True)

gruplar = defaultdict(list)
for f in BELGELER.glob('*/*.txt'):
    p = f.stem.split('_')
    if len(p) >= 4 and p[1].isdigit() and p[2].isdigit():
        gruplar[f'{p[0]}_{p[1]}_{p[2]}'].append(f)

tasinan = 0
for a, dosyalar in gruplar.items():
    if len(dosyalar) < 2:
        continue
    puanli = [(kusur(bas_oku(f)[0]) is None, f.stat().st_size, f) for f in dosyalar]
    puanli.sort(reverse=True)
    print(f'{a}: {len(dosyalar)} dosya, tutulan: {puanli[0][2].name[:50]}')
    for _, _, f in puanli[1:]:
        shutil.move(str(f), str(cop / f.name))
        tasinan += 1

print(f'\n{tasinan} cop kopya cikti/_cop/ klasorune tasindi')