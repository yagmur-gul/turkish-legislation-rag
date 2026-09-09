#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arama/rerank.py — CROSS-ENCODER RERANKER (precision katmanı).

Yer: köprü (recall) → RERANK (precision). Köprü doğru maddeyi aday havuzuna
sokar (kira r10, malulen r4); reranker soru+madde metnini BİRLİKTE okuyup
gerçekten cevaplayanı tepeye çeker. Bi-encoder (gömme) sorgu ve maddeyi ayrı ayrı
kodlar; cross-encoder ikisini beraber işlediği için çok daha keskindir — ama
pahalı, o yüzden sadece top-K aday üstünde çalışır.

Model: BAAI/bge-reranker-base (VARSAYILAN — CPU'da v2-m3'ten ~3-4x hızlı; canlı
testte doğruluk denk: @30 %61, cevaplar ayırt edilemez, ~35-43 sn/soru). GPU'da ya
da en yüksek doğruluk için: RERANK_MODEL=BAAI/bge-reranker-v2-m3 (çok dilli, biraz
daha iyi sıralama). İlk çalıştırmada iner, sonra çevrimdışı (HF_HUB_OFFLINE=1).

HIZ NOTU (4GB GPU): çıkarım fp16 ile yapılır → VRAM ~2.3GB (kartta güvenli),
allocator tıkanması olmaz, arama hızlı kalır; fp16 çıkarım kaliteyi düşürmez.
(Ayar geçmişi için bkz denemeler.md.) Env ile değiştirilebilir:
    RERANK_MODEL   (varsayılan bge-reranker-base; GPU/en iyi: bge-reranker-v2-m3)
    RERANK_MAXLEN  (varsayılan 512)
    RERANK_KES     (varsayılan 1500)
    RERANK_BATCH   (varsayılan 16)
    RERANK_FP16    ("0" ile fp16 kapatılır)
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-base")
MAX_LEN = int(os.environ.get("RERANK_MAXLEN", "512"))
# Madde metninden cross-encoder'a verilecek karakter sınırı. Madde medyanı ~479 krk,
# %32'si 800'ü aşar → 1500 ile precision katmanı maddeyi tam okur. (bkz denemeler.md)
KES = int(os.environ.get("RERANK_KES", "1500"))
BATCH = int(os.environ.get("RERANK_BATCH", "16"))
FP16 = os.environ.get("RERANK_FP16", "1") != "0"
_CE = None


def ce_yukle():
    global _CE
    if _CE is None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            raise SystemExit("HATA: pip install sentence-transformers")
        import torch
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        _CE = CrossEncoder(RERANK_MODEL, max_length=MAX_LEN, device=dev)
        if os.environ.get("RERANK_QUANT")=="1" and dev=="cpu":
            import torch as _t; _CE.model=_t.quantization.quantize_dynamic(_CE.model, {_t.nn.Linear}, dtype=_t.qint8)
        # NOT: model.half() ile ağırlıkları fp16 yapmak bazı torch/ST sürümlerinde
        # "mat1 and mat2 must have the same dtype (Float and Half)" hatası veriyor
        # (girdi fp32, ağırlık fp16). Bunun yerine model fp32 kalır, ÇIKARIM autocast
        # ile fp16 yapılır (bkz. rerank()) — dtype uyumlu + fp16 hız/VRAM avantajı.
    return _CE


def rerank(sorgu, satirlar, n=10, kes=None):
    """satirlar (aday dict listesi) → cross-encoder skoruyla yeniden sıralı ilk n.
    sorgu: KULLANICININ orijinal sorusu (kanun-dili genişletmesi değil) —
    cross-encoder anlamı zaten kendi köprülüyor."""
    if not satirlar:
        return satirlar
    kes = KES if kes is None else kes
    ce = ce_yukle()
    ciftler = [[sorgu, ((r.get("baslik") or "") + " " + (r.get("govde") or "")).strip()[:kes]]
               for r in satirlar]
    import torch
    if FP16 and torch.cuda.is_available():
        with torch.autocast("cuda", dtype=torch.float16):     # fp16 çıkarım, dtype uyumlu
            skorlar = ce.predict(ciftler, batch_size=BATCH, show_progress_bar=False)
    else:
        skorlar = ce.predict(ciftler, batch_size=BATCH, show_progress_bar=False)
    for r, s in zip(satirlar, skorlar):
        r["rerank_skor"] = float(s)
    return sorted(satirlar, key=lambda r: -r["rerank_skor"])[:n]