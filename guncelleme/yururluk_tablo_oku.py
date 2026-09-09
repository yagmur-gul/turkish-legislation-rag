#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
yururluk_tablo_oku.py — Bir kanun PDF'inden DEĞİŞİKLİK/İPTAL tablosunu YAPISAL çıkar.

Linearize düz metin tabloyu bozuyor (madde↔karar karışıyor). pdfplumber tabloyu
satır/sütun koruyarak çeker. AŞAMA 1: 3568 üstünde yöntemi kanıtla, gözle doğrula.

Kullanım: python yururluk_tablo_oku.py "<PDF yolu>"
  (indirdiğin PDF ör: /mnt/c/Users/<sen>/Downloads/3568...pdf)
"""
import re
import sys

try:
    import pdfplumber
except ImportError:
    sys.exit("HATA: pip install pdfplumber")

if len(sys.argv) < 2:
    sys.exit("kullanım: python yururluk_tablo_oku.py \"<PDF yolu>\"")

pdf_path = sys.argv[1]
BAS = re.compile(r"EK VE DEĞİŞİKLİK GETİREN|İPTAL EDİLEN HÜKÜMLER", re.IGNORECASE)

with pdfplumber.open(pdf_path) as pdf:
    bulundu = False
    aktif = False        # başlık sayfası bulundu; devam sayfalarını da al
    for pi, page in enumerate(pdf.pages, 1):
        txt = page.extract_text() or ""
        basli = bool(BAS.search(txt))
        tablolar = page.extract_tables()
        if basli:
            aktif = True
        elif aktif and not tablolar:
            aktif = False   # tablosuz sayfa -> çizelge bitti
        if not (basli or (aktif and tablolar)):
            continue
        bulundu = True
        etiket = "başlık" if basli else "DEVAM"
        print(f"\n===== SAYFA {pi} ({etiket}): değişiklik/iptal tablosu =====")
        if not tablolar:
            print("(pdfplumber tablo çıkaramadı — sayfa ham metni:)\n")
            print(txt)
            continue
        for ti, t in enumerate(tablolar, 1):
            print(f"\n--- tablo {ti} ({len(t)} satır) — sütunlar '|' ile ---")
            for row in t:
                hücre = [(c or "").replace("\n", " ").strip() for c in row]
                print("  " + " | ".join(hücre))
    if not bulundu:
        print("Bu PDF'te değişiklik/iptal tablosu başlığı yok "
              "(belki bu kanun hiç değiştirilmemiş).")