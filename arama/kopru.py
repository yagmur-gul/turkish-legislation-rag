#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arama/kopru.py — SORGU KÖPRÜSÜ: vatandaş dili → kanun dili (deterministik).

Teşhis (bak.py + vektör ile kanıtlandı): arama motoru sağlam; kaçaklar
kullanıcının kelimeleri ile kanunun kelimeleri arasındaki uçurumdan.
'ev sahibi tahliye' 50'de yok; 'kiraya verenin gereksinimi... sona erme' r1-r2.

Bu katman, sorguda geçen günlük ifadelerin KANUN karşılıklarını sorguya ekler.
Eşleşmeler gerçek hukuk terminolojisidir (cevaba göre uydurulmadı) — README
'tahmin yok' ilkesi: her eşleşme, o alanın standart kanun dilidir.

Sınır: yalnızca haritadaki terimleri köprüler (genellenmez). Bu, ucuz ve
deterministik ilk katman; genelleme için sonra aynı slota LLM takılabilir.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indeksle import trf

# günlük ifade (anahtar) -> eklenecek kanun terimleri (değer)
# anahtar, sorgu içinde alt-dize olarak (Türkçe-güvenli) aranır.
ESANLAM = {
    # --- kira / TBK ---
    "ev sahibi": ["kiraya veren"],
    "mal sahibi": ["kiraya veren"],
    "kiracıyı çıkar": ["kira sözleşmesini sona erdirme", "tahliye"],
    "kiracıyı at": ["kira sözleşmesini sona erdirme"],
    "tahliye": ["sona erme", "fesih", "gereksinim", "boşaltma"],
    "evden çıkar": ["kira sözleşmesinin sona ermesi", "gereksinim"],
    # --- iş hukuku / 4857 ---
    "işten çıkar": ["iş sözleşmesinin feshi", "haklı nedenle fesih", "sözleşmenin feshi"],
    "işten atma": ["iş sözleşmesinin feshi", "fesih"],
    "kovmak": ["iş sözleşmesinin feshi", "fesih"],
    "tazminatsız": ["haklı nedenle fesih", "bildirimsiz fesih", "derhal fesih"],
    "kıdem tazminatı": ["kıdem tazminatı"],
    # --- sosyal güvenlik / 5510 ---
    "malulen": ["malûllük", "malûl", "malûllük aylığı"],
    "malülen": ["malûllük", "malûl"],
    "dul aylığı": ["ölüm aylığı", "hak sahibi"],
    "yetim aylığı": ["ölüm aylığı", "hak sahibi"],
    # İLKE: köprü yalnız DAR/kesin eşlemede güvenli — bir alana özgü olmayan geniş
    # terim (özellikle vergi/gümrük'te sık geçenler) doğru maddeyi gömer. Zarar
    # verdiği ölçülen geniş eşlemeler eklenmedi. (bkz denemeler.md)
    # --- aile / TMK ---
    "nafaka kaldır": ["nafakanın kaldırılması", "yoksulluk nafakası"],
    "nafaka": ["yoksulluk nafakası", "iştirak nafakası"],
    "velayet": ["velayet"],
    # --- vergi ---
    "vergi levhası": ["vergi levhası"],
    # --- kira / TBK ---
    "depozito": ["kiracının güvence"],                 # TBK m.342 (kira depozitosu)
    "kira kontratı": ["kira sözleşmesi"],
    "kiracı çıkar": ["tahliye", "kira sözleşmesinin sona ermesi"],
    "kira ödemiyor": ["temerrüt", "kira bedelini ödeme"],
    "kirayı ödemiyor": ["temerrüt", "kira bedelini ödeme"],
    # --- iş / 4857: "maaş vermiyor" = ÜCRETİN ÖDENMEMESİ (4857 m.32/34),
    #     TBK 408 "işverenin temerrüdü" değil — dar, ücret-özgü eşleme: ---
    "maaşımı vermiyor": ["ücretin ödenmesi", "ücretin gününde"],
    "maaşım ödenmedi": ["ücretin ödenmesi", "ücretin gününde"],
    "maaş ödenmedi": ["ücretin ödenmesi", "ücretin gününde"],
    "maaşımı alamıyorum": ["ücretin ödenmesi", "ücretin gününde"],
    "ücretim ödenmedi": ["ücretin ödenmesi", "ücretin gününde"],
    "fazla mesai": ["fazla çalışma"],   # 4857 m.41: kanun "fazla çalışma" der (mesai değil)
    # --- icra / İİK-TTK ---
    "senet imzal": ["kambiyo senedi", "bono"],
    "borcumu ödeyemiyorum": ["temerrüt"],
    "borcumu ödemezsem": ["temerrüt"],
    # --- miras / TMK ---
    "mirastan mal kaçırma": ["tenkis", "tasarrufun iptali"],
    "reddi miras": ["mirasın reddi"],
    "miras paylaşımı": ["mirasın paylaşılması"],
    # --- tüketici / TKHK-TBK ---
    "ayıplı ürün": ["ayıplı mal"],
    "internetten al": ["mesafeli sözleşme", "cayma hakkı"],   # TKHK m.48 (internet alışverişi)
    # --- aile / TMK (mal paylaşımı) ---
    "mal paylaşımı": ["mal rejimi", "edinilmiş mallara katılma"],
    "boşanınca mal": ["mal rejimi", "edinilmiş mallara katılma"],
    # --- gayrimenkul / TMK ---
    "hisseli tapu": ["paylı mülkiyet"],
    # --- komşuluk / KMK ---
    "apartman aidatı": ["kat malikleri", "ortak gider"],   # KMK 634 (aidat/gider borcu)
    # --- ceza / TCK ---
    "dolandırıldım": ["dolandırıcılık"],
    # --- trafik / KTK ---
    "ehliyete el": ["sürücü belgesi"],
    "ehliyetime el": ["sürücü belgesi"],

    # ==== FORUM MADENCİLİĞİ (hukuki.net) — hedefler kopru_kontrol ile DF>0 doğrulandı ====
    # --- aile / kişiler / miras (TMK) ---
    "isim değiş": ["adın değiştirilmesi"],
    "ismimi değiş": ["adın değiştirilmesi"],
    "isim eklet": ["adın değiştirilmesi"],
    "veraset ilam": ["mirasçılık belgesi"],
    "nafaka artır": ["yoksulluk nafakası", "iştirak nafakası"],
    "nafaka arttır": ["yoksulluk nafakası", "iştirak nafakası"],
    "mirası reddet": ["mirasın reddi"],
    "vasi tayin": ["vesayet", "vasi atanması"],
    # --- kira (TBK) ---
    "kira tespit": ["kira bedelinin tespiti"],
    "tahliye taahhüt": ["tahliye", "kira sözleşmesinin sona ermesi"],
    "ihtiyaç nedeniyle tahliye": ["gereksinim", "tahliye"],
    # --- icra (İİK) ---
    "takibe itiraz": ["ödeme emrine itiraz"],
    "icra takibine itiraz": ["ödeme emrine itiraz"],
    # --- tüketici (TKHK) ---
    "hakem heyeti": ["tüketici hakem heyeti"],
    "bozuk çıktı": ["ayıplı mal"],
    "arızalı çıktı": ["ayıplı mal"],
    "ayıplı ürün": ["ayıplı mal"],
    # --- kat mülkiyeti (KMK) ---
    "site yönetim": ["kat malikleri kurulu"],
    "apartman yönetim": ["kat malikleri kurulu"],
    "ortak alan": ["ortak yerler"],
    # --- ceza (TCK) ---
    "dövdü": ["kasten yaralama"],
    "darp": ["kasten yaralama"],
    "yaraladı": ["kasten yaralama"],
    "küfür": ["hakaret"],
    "hükmün açıklanmasının geri": ["hükmün açıklanmasının geri bırakılması"],
    # --- infaz (5275) ---
    "sabıka": ["adli sicil"],
    "şartlı tahliye": ["koşullu salıverilme"],
    # --- askerlik ---
    "çürük raporu": ["askerliğe elverişsiz"],
    "yoklama kaçağı": ["yoklama kaçağı", "askerlik yoklaması"],
    # --- vatandaşlık (5901) ---
    "vatandaşlıktan çık": ["Türk vatandaşlığından çıkma"],
    "vatandaşlığa geç": ["Türk vatandaşlığının kazanılması"],
    # --- imar (3194) ---
    "kaçak yapı": ["ruhsatsız yapı", "ruhsata aykırı yapı"],
    "iskan al": ["yapı kullanma izni"],
    # --- iş (4857) ---
    "kovuldum": ["iş sözleşmesinin feshi"],
    "sigortasız çalış": ["hizmet tespiti"],
    "sigortam yapılmadı": ["hizmet tespiti"],
    # --- iş / gayrimenkul: halk dili → kanun dili (kanun bu kelimeyi kullanmaz) ---
    "vardiya": ["postalar halinde çalışma", "gece çalışması"],   # 4857 m.69/76 (kanun "vardiya" demez)
    "eski malik": ["zilyetlik", "tecavüzün önlenmesi"],          # 3091 (eski sahibi tahliye)
    "eski sahibi çıkm": ["zilyetlik", "tecavüzün önlenmesi"],
    "eski ev sahibi çıkm": ["zilyetlik", "tecavüzün önlenmesi"],
    # --- gayrimenkul/miras: eski/doktrin terim → kanun dili ---
    "izale": ["ortaklığın giderilmesi", "paydaşlığın giderilmesi"],  # izale-i şüyu → TMK/HMK paylaşma
    "şufa": ["önalım hakkı", "önalım"],                              # şuf'a → TMK 732 önalım
    "şuf'a": ["önalım hakkı", "önalım"],
    "muris": ["mirasbırakan"],                                       # Yargıtay 'muris' → TMK 'mirasbırakan'
    "trampa": ["mal değişim sözleşmesi"],                            # TBK 282 (trampa → mal değişim)
    # --- ceza / TCK (halk dili → madde başlığı) ---
    "gasp": ["yağma"],                              # TCK 148 "yağma" der, halk "gasp"
    "kapkaç": ["yağma"],
    "ırza geç": ["cinsel saldırı", "cinsel dokunulmazlık"],   # TCK 102
    "cinsel istismar": ["çocukların cinsel istismarı"],        # TCK 103
    "uyuşturucu": ["uyuşturucu veya uyarıcı madde"],           # TCK 188
    "sahte belge": ["resmî belgede sahtecilik"],    # TCK 204
    "zimmete geçir": ["zimmet"],                    # TCK 247
    # --- infaz / 5275 (halk dili → infaz terimi) ---
    "ne kadar yatar": ["koşullu salıverilme"],
    "kaç yıl yatar": ["koşullu salıverilme"],
    "açık cezaev": ["açık ceza infaz kurumu"],
    "denetimli serbest": ["denetimli serbestlik"],
    # --- icra / İİK (halk dili → İİK terimi) ---
    "mal beyan": ["mal beyanı"],                    # İİK 74
    "aciz belge": ["aciz vesikası"],                # İİK 143
    # --- tüketici / TKHK ---
    "garanti belge": ["garanti belgesi"],           # TKHK 56
    "taksitli satış": ["taksitle satış sözleşmesi"],
    # --- aile / kişiler / TMK ---
    "nesep": ["soybağı"],                           # TMK "soybağı" der, eski "nesep"
    "tanıma davası": ["soybağının kurulması", "tanıma"],
}


def genislet(sorgu: str):
    """Sorguyu kanun terimleriyle genişlet.
    Döner: (genisletilmis_sorgu, eklenen_terimler, tetikleyen_anahtarlar)."""
    n = trf(sorgu)
    ekle, tetik = [], []
    for anahtar, terimler in ESANLAM.items():
        if trf(anahtar) in n:
            tetik.append(anahtar)
            for t in terimler:
                if trf(t) not in n and t not in ekle:
                    ekle.append(t)
    genis = sorgu + ((" " + " ".join(ekle)) if ekle else "")
    return genis, ekle, tetik

# ---------------------------------------------------------------------------
# LLM köprü — gemini-flash soruyu kanun diline çevirir; KURAL köprünün (genislet)
# önerdiği terimler prompt'a YARDIMCI ipucu olarak girer (LLM işine yararsa
# kullanır, alakasızsa yok sayar). Çok anlamlı kelimeleri (havale, uzaklaştırma,
# manevi hak) durumdan çözer — kural köprünün yapamadığı. KOPRU=llm ile açılır.
import os as _os
import json as _json

_kopru_ob = {}   # süreç-içi önbellek: sorgu -> genişletilmiş arama dizesi
KOPRU_MODEL = _os.environ.get("KOPRU_MODEL", "google/gemini-2.5-flash")

def _llm_kopru(sorgu):
    import sys as _sys
    from pathlib import Path as _Path
    _kok = _Path(__file__).resolve().parent.parent
    _sys.path.insert(0, str(_kok))
    try:
        import orh
        from sorgu_llm import SISTEM as _SIS
        _, ekle, _tetik = genislet(sorgu)
        yardim = ", ".join(ekle) if ekle else "(öneri yok)"
        kul = (f"SORU: {sorgu}\n\n"
               f"Kural-tabanlı bir sistemin önerdiği terimler (YARDIMCI — "
               f"alakalıysa kullan, değilse yok say): {yardim}")
        icerik, _cost = orh.cagir(KOPRU_MODEL, _SIS, kul, max_tokens=300)[:2]
        b, s = icerik.index("{"), icerik.rindex("}") + 1
        o = _json.loads(icerik[b:s])
        cev = ((o.get("terim") or "").strip() + " " + (o.get("atif") or "").strip()).strip()
        return (sorgu + " " + cev).strip() if cev else genislet(sorgu)[0]
    except Exception:
        return genislet(sorgu)[0]   # LLM/ağ hatasında kural köprüye düş

def aktif_kopru(sorgu):
    """Aktif köprü: KOPRU=llm ise LLM (kural yardımcı), yoksa kural. Önbellekli
    → aynı sorgu için LLM en fazla bir kez çağrılır."""
    if sorgu in _kopru_ob:
        return _kopru_ob[sorgu]
    s = _llm_kopru(sorgu) if _os.environ.get("KOPRU") == "llm" else genislet(sorgu)[0]
    _kopru_ob[sorgu] = s
    return s

if __name__ == "__main__":
    import sys as _s
    q = " ".join(_s.argv[1:]) or "ev sahibi kiracıyı hangi durumlarda tahliye edebilir"
    g, ekle, tetik = genislet(q)
    print(f"soru   : {q}")
    print(f"tetik  : {tetik}")
    print(f"eklendi: {ekle}")
    print(f"geniş  : {g}")