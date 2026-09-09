#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""İLK KURULUM: arama modellerini indirir (bir kere, internet gerektirir).
Sonra sistem çevrimdışı da çalışır. Kullanım:  python modelleri_indir.py"""
import os
os.environ["HF_HUB_OFFLINE"] = "0"          # indirmeye izin ver
os.environ["TRANSFORMERS_OFFLINE"] = "0"
print("1/2  Anlam modeli (bge-m3) indiriliyor — ~2 GB, birkaç dakika...", flush=True)
from sentence_transformers import SentenceTransformer, CrossEncoder
SentenceTransformer("BAAI/bge-m3")
print("2/2  (Opsiyonel) Rerank modeli bge-reranker-base — ~1 GB, yalnız RERANK=1 modunda gerekir...", flush=True)
CrossEncoder("BAAI/bge-reranker-base")   # varsayılan no-rerank; rerank istenirse RERANK=1
print("\n✓ Modeller hazır. Artık: python app.py", flush=True)