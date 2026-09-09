#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
faz2_dogrulama.py — FAZ 2: iki BAĞIMSIZ kaynağı çapraz doğrula (legal-grade).

Kaynak 1: satır-içi işaretler (degisiklikler.jsonl, kaynak='metin')  — metnin içinden.
Kaynak 2: resmî değişiklik ÇİZELGESİ (yururluk_kayitlari.jsonl)      — PDF tablosundan.
Bağımsızlar; birbirini doğrularsa güven artar, ayrışırsa nedenini görürüz.

Karşılaştırma (belge + madde + araç-numarası düzeyinde):
  - İKİ KAYNAK DA: her iki kaynakta da olan değişiklik -> güçlü doğrulama.
  - YALNIZ ÇİZELGE: metinde satır-içi işaret bırakmayan (ibare/AYM) -> çizelge tamamlar.
  - YALNIZ METİN: çizelgenin atladığı (ör. eski/çok yeni AYM) -> metin tamamlar.
Ayrıca: çizelge iptali olan ama chunk'ı olmayan madde (bağlama hatası) = KIRMIZI bayrak.

Örnek döküm: 3568 ve 6098 için satır-içi vs çizelge yan yana.
SADECE RAPOR. Kullanım (proje kökünde): python faz2_dogrulama.py
"""
import json, re, collections
from pathlib import Path

BIZ = Path('cikti/degisiklikler.jsonl')
CIZ = Path('cikti/yururluk_kayitlari.jsonl')


def arac_no(arac):
    a = (arac or '').lower()
    if 'anayasa mahkeme' in a or a == 'aym':
        return 'AYM'
    m = re.search(r'\d{2,}', arac or '')
    return m.group(0) if m else ''


# kaynak 1: yalnız satır-içi (metin) kayıtlar + çizelgeden BAĞLANMIŞ iptaller
metin = collections.defaultdict(set)     # (belge,madde) -> {arac_no}
metin_madde = set()
ciz_bagli = set()                        # (belge, tarih, arac_no) — degisiklikler.jsonl'e giren çizelge iptalleri
for line in BIZ.open(encoding='utf-8'):
    o = json.loads(line)
    key = (o['belge'], o['madde_id'])
    metin_madde.add(key)
    for k in o['kayitlar']:
        if k.get('kaynak') == 'metin':
            metin[key].add(arac_no(k.get('arac', '')))
        elif k.get('kaynak') == 'cizelge' and k.get('tur') == 'iptal':
            ciz_bagli.add((o['belge'], k.get('tarih', ''), arac_no(k.get('arac', ''))))

# kaynak 2: çizelge
cizelge = collections.defaultdict(list)  # (belge,madde) -> [(durum,arac_no,arac,tarih)]
for line in CIZ.open(encoding='utf-8'):
    k = json.loads(line)
    key = (k['belge'], str(k['madde_id']))
    cizelge[key].append((k['durum'], arac_no(k.get('arac', '')),
                         k.get('arac', ''), k.get('tarih', '')))

iki = yalniz_ciz = yalniz_ciz_madde_var = 0
baglama_hata = []
for key, kayitlar in cizelge.items():
    for durum, ano, arac, tarih in kayitlar:
        if ano and ano in metin.get(key, set()):
            iki += 1
        else:
            yalniz_ciz += 1
            if key in metin:                 # madde metinde var ama bu araç yok
                yalniz_ciz_madde_var += 1
        if durum == 'iptal':
            belge, mid = key
            taban = mid.split('/')[0].strip()
            kapsandi = ((belge, tarih, ano) in ciz_bagli                 # çizelge kaydı eklendi
                        or 'AYM' in metin.get(key, set())                # satır-içi AYM iptali var
                        or 'AYM' in metin.get((belge, taban), set()))    # ana maddede var
            if not kapsandi:
                baglama_hata.append((key, arac, tarih))

# yalnız metin: metinde olup çizelgede araç-no'su olmayan
ciz_ikili = {(key, a) for key, ks in cizelge.items() for (_, a, _, _) in ks}
yalniz_metin = 0
for key, aracs in metin.items():
    for a in aracs:
        if a and (key, a) not in ciz_ikili:
            yalniz_metin += 1

print("=" * 66)
print("FAZ 2 — ÇAPRAZ DOĞRULAMA (satır-içi ↔ resmî çizelge)")
print("=" * 66)
ciz_top = sum(len(v) for v in cizelge.values())
print(f"çizelge kaydı        : {ciz_top}")
print(f"  İKİ KAYNAK DA      : {iki}   (metin + çizelge aynı değişikliği görüyor)")
print(f"  YALNIZ ÇİZELGE     : {yalniz_ciz}   (metinde satır-içi yok; ibare/AYM tamamlama)")
print(f"    - maddesi metinde var ama o araç yok: {yalniz_ciz_madde_var}")
print(f"YALNIZ METİN (çizelge dışı araç): {yalniz_metin}   (çizelgenin atladıkları)")
print(f"\n⚠ BAĞLAMA HATASI (çizelge iptali var ama madde chunk'ı YOK): {len(baglama_hata)}")
for (belge, mid), arac, tarih in baglama_hata[:20]:
    print(f"    {belge} md.{mid}  <- {arac[:45]} ({tarih})")

# ---- örnek döküm ----
def dokum(belge_on):
    print(f"\n--- ÖRNEK: {belge_on} — satır-içi(M) vs çizelge(Ç) ---")
    keys = sorted({k for k in set(metin) | set(cizelge) if k[0].startswith(belge_on)},
                  key=lambda x: x[1])
    for key in keys:
        m = sorted(a for a in metin.get(key, set()) if a)
        c = [f"{d}:{a}" for d, a, _, _ in cizelge.get(key, [])]
        if not m and not c:
            continue
        print(f"  md.{key[1]:<10} M={m}  Ç={c}")

dokum('3568_')
dokum('6098_')