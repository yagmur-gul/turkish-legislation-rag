# -*- coding: utf-8 -*-
"""
geri_al.py — temizle.py'nin GERİ-AL'ı (opsiyonel bakım aracı).

temizle.py'nin cikti/_cop/ (çöp) klasörüne taşıdığı belgeleri asıl yerine
(cikti/belgeler/<tür klasörü>/) geri taşır. temizle.py yanlışlıkla istediğin bir
kopyayı attıysa güvenlik ağıdır. Belge id'sindeki tür numarasından doğru klasörü
bulur; aynı adlı dosya zaten varsa üzerine yazmaz, atlar.

Çalıştır: python arsiv/toplama/geri_al.py   (klasör kökünden)
"""
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # guncelleme/ (tamamla burada)
from tamamla import BELGELER, TUR_KLASOR

cop = Path('cikti/_cop')
geri = 0
for f in cop.glob('*.txt'):
    p = f.stem.split('_')
    if len(p) < 4:
        continue
    try:
        tur = int(p[1])
    except ValueError:
        continue
    klasor = BELGELER / TUR_KLASOR.get(tur, 'Diger')
    klasor.mkdir(parents=True, exist_ok=True)
    hedef = klasor / f.name
    if not hedef.exists():
        shutil.move(str(f), str(hedef))
        geri += 1

print(f'{geri} dosya geri getirildi')
print('kalan _cop:', len(list(cop.glob("*.txt"))))