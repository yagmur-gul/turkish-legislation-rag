#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
konfig.py — merkezi çalışma-zamanı ayarı.

motor/vektor/rerank İMPORT EDİLMEDEN ÖNCE import edilmeli (ayarlar modül
yüklenirken okunuyor). cevapla.py ve app.py bunu ilk satırda import eder →
tek doğru kaynak, iki yerde tekrar yok.

BÖLÜNMEMİŞ korpus (924MB vektör + FAISS): 4GB VRAM + ~6GB RAM'lik hedef
donanıma sığar. Split'e dönmek için: MEVZUAT_SPLIT=1.
"""
import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("EMBED_MODEL", "BAAI/bge-m3")
os.environ.setdefault("EMBED_SEQ", "512")
os.environ.setdefault("EMBED_KES", "1400")
# 4GB VRAM'de iki bge-m3 (embed + reranker) çakışır → sorgu encoder CPU'ya,
# reranker GPU'da tek kalır.
os.environ.setdefault("EMBED_DEVICE", "cpu")
# embed ağırlığını yarıya indir (1.17GB→585MB): kısıtlı RAM'de DB/FTS cache'e yer açar.
os.environ.setdefault("EMBED_FP16", "1")
# FAISS yaklaşık arama: dense matmul ~18sn→~0.5sn. Kur: python arama/ann.py --kur
os.environ.setdefault("ANN", "1")
# Köprü (sorgu çevirisi): LLM (gemini-flash) — çok anlamlı kelimeleri durumdan
# çözer (havale→sebepsiz zenginleşme). Kural köprü prompt'a yardımcı ipucu girer,
# LLM/ağ hatasında otomatik kurala düşer. Kural köprüye dönmek için: KOPRU=kural
os.environ.setdefault("KOPRU", "llm")

if os.environ.get("MEVZUAT_SPLIT") == "1":
    os.environ.setdefault("INDEKS_DB", "arama/mevzuat_bolunmus.db")
    os.environ.setdefault("EMBED_ETIKET", "bolunmus")