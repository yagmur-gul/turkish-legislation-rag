#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cevapla.py — Uçtan uca canlı soru-cevap çekirdeği (motor servisi).

Boru hattını TEK fonksiyonda birleştirir:
    SORU → [AramaMotoru: köprü + BM25 + vektör + rerank] → top-N madde
         → [deepseek üretim] → cevap + atıflar

app.py (web arayüzü) ve komut satırı bu AYNI çekirdeği kullanır. Modeller
(embedding + reranker) AramaMotoru'da bir kez yüklenir; üretim OpenRouter
API çağrısıdır (yerel model yok).

    from cevapla import Cevaplayici
    c = Cevaplayici(); c.hazirla()
    sonuc = c.cevapla("kiracı depozitosunu geri alabilir mi?")
    print(sonuc["cevap"])

CLI hızlı test:
    python cevapla.py "kiracı depozitosunu geri alabilir mi?"
"""
import os
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parent
sys.path.insert(0, str(KOK / "arama"))

import konfig  # noqa: F401  — çalışma-zamanı env ayarları (motor import'undan ÖNCE)
import uret            # kaynak_metni, cagir_retry, SISTEM2, ATIF_D, _ana
from motor import AramaMotoru

# Üretim ayarları — ölçümle kilitlendi (bkz mevzuat-rag-yigin):
# deepseek fp8 (OpenRouter'da fp4 backend'i dışla), v2 prompt.
MODEL = "deepseek/deepseek-chat"
uret._SAGLAYICI = {"quantizations": ["fp8", "fp16", "bf16"], "sort": "throughput"}


class Cevaplayici:
    """Canlı soru-cevap servisi. Modeller bir kez yüklenir, bellekte kalır."""

    def __init__(self, n=30, model=MODEL, max_tokens=1600):
        self.n = n
        self.model = model
        self.max_tokens = max_tokens
        self.motor = AramaMotoru(reranker=True)

    def hazirla(self, sessiz=False):
        self.motor.hazirla(sessiz=sessiz)

    def cevapla(self, soru):
        """Tek soruyu baştan sona çalıştırır, yapılandırılmış sonuç döndürür."""
        soru = " ".join((soru or "").split())
        t0 = time.time()
        satirlar = self.motor.ara(soru, n=self.n)          # canlı arama + rerank
        t_arama = time.time() - t0

        kaynak, satir = uret.kaynak_metni(satirlar)
        kul = f"KAYNAKLAR:\n\n{kaynak}\n\n---\n\nSORU: {soru}"

        t1 = time.time()
        cevap, u = uret.cagir_retry(self.model, uret.SISTEM2, kul, self.max_tokens)
        t_uret = time.time() - t1

        # Atıfları çözümle: [N] → kaçıncı madde kullanıldı
        nolar = [int(x) for x in uret.ATIF_D.findall(cevap)]
        kullanilan = sorted({k for k in nolar if 1 <= k <= len(satir)})
        uydurma = sorted({k for k in nolar if not (1 <= k <= len(satir))})

        kaynaklar = [{
            "no": i + 1,
            "belge_ad": (r.get("belge_ad") or "").strip(),
            "baslik": (r.get("baslik") or "").strip(),
            "madde_id": r.get("madde_id"),
            "belge": r.get("belge"),
            "chunk_id": r.get("chunk_id"),
            "tur": r.get("tur"),
            "mulga": r.get("mulga"),
            "govde": (r.get("govde") or "").strip(),
            "kullanildi": (i + 1) in kullanilan,
        } for i, r in enumerate(satir)]

        return {
            "soru": soru,
            "cevap": cevap,
            "kaynaklar": kaynaklar,
            "atif_nolari": kullanilan,
            "uydurma_atif": uydurma,
            "sure": {"arama": round(t_arama, 1), "uretim": round(t_uret, 1),
                     "toplam": round(time.time() - t0, 1)},
            "maliyet": round(u.get("cost", 0.0), 6),
            "model": self.model,
        }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("kullanım: python cevapla.py \"soru metni\"")
        raise SystemExit(1)
    soru = " ".join(sys.argv[1:])
    c = Cevaplayici()
    c.hazirla()
    s = c.cevapla(soru)
    print("\n" + "=" * 70)
    print("SORU:", s["soru"])
    print("=" * 70)
    print("\nCEVAP:\n" + s["cevap"])
    print("\n" + "-" * 70)
    print(f"süre: arama {s['sure']['arama']}sn + üretim {s['sure']['uretim']}sn "
          f"= {s['sure']['toplam']}sn · maliyet ${s['maliyet']:.5f}")
    kul = [k for k in s["kaynaklar"] if k["kullanildi"]]
    print(f"\nKULLANILAN KAYNAKLAR ({len(kul)}):")
    for k in kul:
        print(f"  [{k['no']}] {k['belge_ad']} — {k['baslik']}")
    if s["uydurma_atif"]:
        print(f"\n⚠ UYDURMA atıf (kaynakta yok): {s['uydurma_atif']}")