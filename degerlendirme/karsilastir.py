#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
degerlendirme/karsilastir.py — arama AŞAMALARINI karşılaştır (geliştirme aracı).

Gold soru setinde her aşamanın "doğru madde ilk 3'te mi" oranını yan yana gösterir:
  BM25  |  HİBRİT (BM25+vektör)  |  +KÖPRÜ  |  +RERANK
Bir arama değişikliğinin hangi aşamada işe yaradığını (ya da bozduğunu) görmek için.

Çalıştır (kökten):
    python degerlendirme/karsilastir.py
    python degerlendirme/karsilastir.py --detay        # kaçan soruların ilk-5'i
"""
import argparse
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))                    # konfig
sys.path.insert(0, str(KOK / "arama"))          # vektor, indeksle, ...
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sorulari_yukle, isabet_mi

import konfig  # noqa: F401  — çalışma-zamanı ayarları (vektor import'undan ÖNCE)
from vektor import hibrit, hibrit_rerank, ara_ham
from degerlendir import sorulari_yukle, isabet_mi


def _sira(satirlar, k):
    for i, r in enumerate(satirlar, 1):
        if isabet_mi(r, k):
            return i
    return None


def karsilastir(tur=None, n=10, detay=False):
    print("=" * 96)
    print(f"{'alan':<16} {'BM25':>6} {'HİBRİT':>7} {'+KÖPRÜ':>7} {'+RERANK':>8}   soru")
    print("-" * 96)
    say = {"bm": 0, "hy": 0, "hk": 0, "rr": 0}
    et = 0
    kacan = []
    for k in sorulari_yukle():
        if not (k.get("dogru") or k.get("dogru_chunk") or k.get("ad_icerir")):
            continue
        et += 1
        bm = ara_ham(k["soru"], n=n, tur=tur)[0]
        hy = hibrit(k["soru"], n=n, tur=tur)
        havuz = hibrit(k["soru"], n=n, tur=tur, kopru=True)   # köprü sıralaması
        rr = hibrit_rerank(k["soru"], n=n, tur=tur)           # köprü + reranker füzyonu
        sb, sh, sk, sr = _sira(bm, k), _sira(hy, k), _sira(havuz, k), _sira(rr, k)
        for key, s in (("bm", sb), ("hy", sh), ("hk", sk), ("rr", sr)):
            if s and s <= 3:
                say[key] += 1
        f = lambda s: f"r{s}" if s else "--"
        vur = "  ⬆" if (sr and sr <= 3) and not (sk and sk <= 3) else ""
        print(f"{k.get('alan','?'):<16} {f(sb):>6} {f(sh):>7} {f(sk):>7} {f(sr):>8}   {k['soru'][:28]}{vur}")
        if not (sr and sr <= 3):
            kacan.append((k, rr[:5]))
    print("-" * 96)
    print(f"{'TOP-3':<16} {say['bm']:>5}/{et} {say['hy']:>6}/{et} {say['hk']:>6}/{et} {say['rr']:>7}/{et}")
    print("⬆ = reranker, köprünün ilk 3'e sokamadığını tepeye çekti")

    if not detay:
        print(f"\n(kaçan {len(kacan)} sorunun ilk-5'i için: --detay ekle)")
        return
    for k, satirlar in kacan:
        print("\n" + "=" * 96)
        print(f"KAÇAN: {k['soru']}   (beklenen: {k.get('dogru_chunk') or k.get('dogru') or k.get('ad_icerir')})")
        for i, r in enumerate(satirlar, 1):
            mul = " ⚠MÜLGA" if r.get("mulga") else ""
            g = " ".join((r.get("govde") or "").split())[:150]
            print(f"  [{i}] {r['chunk_id']}  {r.get('belge_ad','')[:44]}{mul}")
            print(f"      …{g}…")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Arama aşamalarını gold sette karşılaştır")
    ap.add_argument("--detay", action="store_true", help="kaçan soruların ilk-5'ini de bas")
    ap.add_argument("--tur", default=None, help="madde | hepsi(=filtre yok)")
    ap.add_argument("--n", type=int, default=10)
    a = ap.parse_args()
    tur = None if a.tur in (None, "hepsi") else a.tur
    karsilastir(tur=tur, n=a.n, detay=a.detay)