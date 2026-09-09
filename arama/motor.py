#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arama/motor.py — Konsolide arama motoru (tek çekirdek API).

Tüm hattı (BM25 + vektör + köprü + reranker-füzyon) tek sınıfın arkasına toplar.
Modeller bir kez yüklenir, bellekte kalır → sorgular anlık. REPL (ara.py) ve
ileride web arayüzü bu AYNI motoru kullanır (tekrar kod yok).

    from motor import AramaMotoru
    m = AramaMotoru()
    m.hazirla()                    # modelleri bir kez yükle
    for r in m.ara("ev sahibi kiracıyı çıkarabilir mi", n=5):
        print(r["belge_ad"], "madde", r["madde_id"])
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vektor import hibrit, hibrit_rerank, model_yukle, vek_yukle, DB, VEC
from kopru import genislet


class AramaMotoru:
    """Mevzuat arama motoru. reranker=True (varsayılan) en yüksek kalite;
    reranker=False daha hızlı (bge yüklenmez), biraz daha düşük precision."""

    def __init__(self, reranker=True):
        self.reranker = reranker
        self._hazir = False

    def hazirla(self, sessiz=False):
        """Modelleri ve vektörleri belleğe al (bir kerelik)."""
        if self._hazir:
            return
        if not DB.exists():
            raise SystemExit("HATA: indeks yok. Önce: python indeksle.py")
        if not VEC.exists():
            raise SystemExit("HATA: vektörler yok. Önce: python vektor.py --embed")
        t0 = time.time()
        if not sessiz:
            print("Modeller yükleniyor (bir kerelik, ~1-2 dk)...", flush=True)
        model_yukle()          # bge-m3 embedding (konfig EMBED_MODEL'i buna sabitler)
        vek_yukle()            # vektör matrisi (mmap)
        if self.reranker:
            from rerank import ce_yukle
            ce_yukle()         # cross-encoder reranker
        self._hazir = True
        if not sessiz:
            print(f"Hazır ({time.time()-t0:.0f} sn).", flush=True)

    def ara(self, sorgu, n=10, tur=None, gizle_mulga=False):
        """Sorguyu hattan geçir, sıralı sonuç listesi döndür (dict listesi)."""
        self.hazirla(sessiz=True)
        if self.reranker:
            return hibrit_rerank(sorgu, n=n, tur=tur, gizle_mulga=gizle_mulga)
        return hibrit(sorgu, n=n, tur=tur, gizle_mulga=gizle_mulga, kopru=True)

    def kopru_bilgi(self, sorgu):
        """Köprünün sorguya eklediği kanun terimleri (şeffaflık için)."""
        _, ekle, tetik = genislet(sorgu)
        return ekle, tetik