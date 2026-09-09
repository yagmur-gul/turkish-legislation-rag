#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
degerlendirme/degerlendir.py — Arama kalitesini bir soru setiyle ölç (overfit koruması).
NOT: `sorulari_yukle` + `isabet_mi` fonksiyonlarını karsilastir.py de kullanır
(paylaşılan modül — bu dosya silinemez).

Amaç: tek soruya göre ayar yapmamak. Çeşitli alanlardan gerçek vatandaş
sorularının tümünde "doğru madde ilk 3'te mi" oranını ölçeriz. Bu CLI BM25/FTS
katmanını ölçer; tam aşama karşılaştırması (hibrit/köprü/rerank) için karsilastir.py.
Bir değişiklik bir soruyu düzeltirken başkasını bozuyorsa, toplam oran bunu gösterir.

Set: degerlendirme/sorular.jsonl  (287 soru, MADDE düzeyi; her satır)
    {"soru": "...", "alan": "kira", "dogru_chunk": ["6098_1_5#12", ...]}
    - dogru_chunk: doğru MADDE(ler)in chunk_id'leri; bir sonucun chunk_id'si
      listedeyse "isabet" sayılır (tam madde eşleşmesi — en güvenilir ölçüt).
    - (isabet_mi geriye dönük olarak dogru=belge anahtarı ve ad_icerir=belge
      adı alt-dizesini de tanır; eski/karma setler de çalışır.)

Kullanım:
    python degerlendirme/degerlendir.py            # özet rapor + etiketsizler için ilk 5
    python degerlendirme/degerlendir.py --detay    # her soru için ilk 5
    python degerlendirme/degerlendir.py --n 10     # ilk kaç sonuca bakılsın (varsayılan 10)

Not: skor/ağırlık değişiklikleri indeksi bozmaz; ama belge_n gibi indeks-zamanı
alanları değişince önce `python arama/indeksle.py` ile yeniden kur.
"""

import argparse
import json
import sys
from pathlib import Path

# Arama çekirdeği bir üst dizindeki arama/ klasöründe.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "arama"))
try:
    from indeksle import ara_ham, trf
except ImportError:
    raise SystemExit("HATA: arama/indeksle.py bulunamadı (kökten mi çalıştırdınız?).")


def _sorular_yolu():
    yerel = Path(__file__).resolve().parent / "sorular.jsonl"
    if yerel.exists():
        return yerel
    alt = Path.cwd() / "sorular.jsonl"
    if alt.exists():
        return alt
    raise SystemExit("HATA: sorular.jsonl bulunamadı (betik yanında ya da cwd'de olmalı).")


def sorulari_yukle():
    """JSONL (her satır bir nesne) ya da JSON dizisi ([...]) — ikisini de kabul eder."""
    yol = _sorular_yolu()
    metin = yol.read_text(encoding="utf-8").strip()
    if not metin:
        raise SystemExit(f"HATA: {yol} boş.")
    # 1) Tüm dosyayı tek JSON olarak dene (köşeli parantezli + virgüllü hali).
    try:
        veri = json.loads(metin)
        if isinstance(veri, list):
            return veri
        if isinstance(veri, dict):
            return [veri]
    except json.JSONDecodeError:
        pass
    # 2) JSONL yedeği: satır satır; baştaki/sondaki [ ] ve satır sonu virgüllerini yut.
    kayit = []
    for satir in metin.splitlines():
        s = satir.strip().rstrip(",").strip()
        if not s or s in ("[", "]"):
            continue
        try:
            kayit.append(json.loads(s))
        except json.JSONDecodeError as e:
            raise SystemExit(f"HATA: sorular satırı okunamadı: {s[:60]}...\n  {e}")
    return kayit


def isabet_mi(r, kayit) -> bool:
    """Bir sonuç satırı bu sorunun doğru cevabına uyuyor mu?
    Öncelik sırası:
      1. dogru_chunk varsa → SADECE tam madde (chunk_id) eşleşmesi sayılır.
         En güvenilir ölçüt; "cevap ilk 3'te mi"yi gerçekten ölçer.
      2. Yoksa dogru: tam belge anahtarı ("657_1_5") ya da baş numara ("6098").
      3. Yoksa ad_icerir: belge adında geçen alt-dize (çerçeve/çıkarım soruları).
    """
    chunklar = kayit.get("dogru_chunk", [])
    if chunklar:
        return r.get("chunk_id") in chunklar
    belge = r.get("belge") or ""
    bas_no = belge.split("_")[0]
    for d in kayit.get("dogru", []):
        if belge == d or bas_no == d:
            return True
    ad = (r.get("belge_ad") or "").upper()
    for alt in kayit.get("ad_icerir", []):
        if alt.upper() in ad:
            return True
    # govde_icerir: madde BAŞLIĞI/GÖVDESİ ifadeyi içeriyor mu (Türkçe-güvenli).
    # Çerçeve sorular için: cevap "onlarca benzer yönetmelikten biri" olduğunda,
    # belge anahtarı yerine maddenin içeriğine göre isabet sayılır.
    if kayit.get("govde_icerir"):
        icerik = trf((r.get("baslik") or "") + " " + (r.get("govde") or ""))
        for alt in kayit["govde_icerir"]:
            if trf(alt) in icerik:
                return True
    return False


def ilk_isabet_sirasi(satirlar, kayit):
    """kayda uyan ilk sonucun 1-tabanlı sırası; yoksa None."""
    for i, r in enumerate(satirlar, 1):
        if isabet_mi(r, kayit):
            return i
    return None


def top5_bas(satirlar):
    for i, r in enumerate(satirlar[:5], 1):
        ad = r["belge_ad"] or "(ad yok)"
        mul = " ⚠MÜLGA" if r["mulga"] else ""
        print(f"      [{i}] {r['belge']:<14} md.{str(r['madde_id']):<6} "
              f"{ad[:52]}{mul}")


def main():
    ap = argparse.ArgumentParser(description="Arama kalitesi değerlendirmesi")
    ap.add_argument("--detay", action="store_true", help="her soru için ilk 5")
    ap.add_argument("--n", type=int, default=10, help="bakılacak sonuç derinliği")
    ap.add_argument("--tur", default="madde",
                    help="tür filtresi (varsayılan madde; 'hepsi' = filtre yok)")
    args = ap.parse_args()

    tur = None if args.tur == "hepsi" else args.tur
    kayitlar = sorulari_yukle()

    etiketli = top1 = top3 = 0
    mrr_top = 0.0  # mean reciprocal rank (etiketli sorular üzerinde)
    etiketsizler = []

    print(f"=== DEĞERLENDİRME — {len(kayitlar)} soru (derinlik n={args.n}, tür={args.tur}) ===\n")

    for k in kayitlar:
        soru = k["soru"]
        alan = k.get("alan", "?")
        dogru = k.get("dogru", [])
        ad_icerir = k.get("ad_icerir", [])
        etiketli_mi = bool(dogru or ad_icerir or k.get("dogru_chunk"))
        satirlar, _, match = ara_ham(soru, n=args.n, tur=tur)

        if not etiketli_mi:
            etiketsizler.append(k)
            print(f"[· etiketsiz] {alan:<16} {soru}")
            if not match:
                print("      (sorguda aranabilir terim yok)")
            else:
                top5_bas(satirlar)
            print()
            continue

        etiketli += 1
        sira = ilk_isabet_sirasi(satirlar, k)
        if sira == 1:
            top1 += 1
            top3 += 1
            mrr_top += 1.0
            durum = "✓ top1"
        elif sira and sira <= 3:
            top3 += 1
            mrr_top += 1.0 / sira
            durum = "✓ top3"
        elif sira:
            mrr_top += 1.0 / sira
            durum = f"~ r{sira}"
        else:
            durum = "✗ yok "

        srr = f"r{sira}" if sira else "--"
        bekl = ",".join(dogru + k.get("dogru_chunk", [])) + (" +ad:" + "/".join(ad_icerir) if ad_icerir else "")
        print(f"[{durum}] {alan:<16} {srr:<4} beklenen={bekl}")
        print(f"           {soru}")
        if args.detay or not sira or sira > 3:
            top5_bas(satirlar)
        print()

    print("=" * 60)
    if etiketli:
        print(f"Etiketli soru : {etiketli}")
        print(f"  top-1       : {top1}  (%{100*top1/etiketli:.0f})")
        print(f"  top-3       : {top3}  (%{100*top3/etiketli:.0f})   ← ASIL METRİK")
        print(f"  MRR         : {mrr_top/etiketli:.3f}")
    if etiketsizler:
        print(f"Etiketsiz     : {len(etiketsizler)} "
              f"(yukarıdaki ilk-5'lerden doğru maddeyi sorular.jsonl'e yaz)")


if __name__ == "__main__":
    main()