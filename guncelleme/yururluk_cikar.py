#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
yururluk_cikar.py — Kanun PDF'lerinden DEĞİŞİKLİK/İPTAL tablolarını yapısal çıkar.

Her satır: (araç, maddeler, tarih). Sınıflandırma:
  araç "Anayasa Mahkemesi" içeriyorsa  -> durum=iptal  (yürürlükte değil -> mülga adayı)
  araç kanun/KHK numarasıysa           -> durum=degisik (yürürlükte, gövdeye işlenmiş)

Sadece AYM iptali OLAN kanunları işler (hızlı; asıl boşluk bu). pdfplumber tabloyu
sütun-farkında çıkarır (linearize metin madde↔karar'ı bozardı).

Çıktı: cikti/yururluk_kayitlari.jsonl   (her satır bir madde-değişiklik kaydı)
Proje kökünde: python yururluk_cikar.py
"""
import json
import re
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("HATA: pip install pdfplumber")

KDIR = Path("cikti/belgeler/Kanunlar")
OUT = Path("cikti/yururluk_kayitlari.jsonl")
BAS = re.compile(r"İPTAL EDİLEN HÜKÜMLER|EK VE DEĞİŞİKLİK GETİREN")
AYM = re.compile(r"[Aa]nayasa\s+Mahkeme")
TARIH = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")

def _norm(s):
    """Başlık harflerini bırak (tr-fold) — PDF ('657_AD') ve txt ('657_1_5_AD')
    farklı kimlik şemalarını başlık üzerinden köprülemek için."""
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"[^a-zçğıöşü]", "", s)


def pdf_belge_eslestir():
    """Çizelgesi (BAS başlığı) olan her txt'yi DOĞRU tam-anahtara + PDF'e bağla.
    Numara çakışması (ör. iki farklı 657) olduğunda başlık eşleşmesi şart;
    eşleşmezse yanlış bağlamaktansa ATLA. Döner: [(belge, pdf_path)]."""
    from collections import defaultdict
    pdf_no = defaultdict(list)                     # no -> [(title_norm, path)]
    for pdf in KDIR.glob("*.pdf"):
        pr = pdf.stem.split("_")
        pdf_no[pr[0]].append((_norm("_".join(pr[1:])), pdf))
    txt_no = defaultdict(list)                      # no -> [(belge, title_norm)]
    for f in KDIR.glob("*.txt"):
        t = f.read_text(encoding="utf-8", errors="ignore")
        if not BAS.search(t):
            continue
        p = f.stem.split("_")
        if len(p) >= 3 and p[1].isdigit() and p[2].isdigit():
            txt_no[p[0]].append((f"{p[0]}_{p[1]}_{p[2]}", _norm("_".join(p[3:]))))

    def uyar(a, b):
        return bool(a) and bool(b) and (a[:20] == b[:20] or a.startswith(b[:20]) or b.startswith(a[:20]))

    ciftler = []
    for no, txtler in txt_no.items():
        pdfler = pdf_no.get(no, [])
        if not pdfler:
            continue
        cakisma = len(txtler) > 1 or len(pdfler) > 1
        for belge, tnorm in txtler:
            esles = [p for tn, p in pdfler if uyar(tn, tnorm)]
            if esles:
                ciftler.append((belge, esles[0]))
            elif not cakisma:
                ciftler.append((belge, pdfler[0][1]))   # çakışma yok -> güvenli
            # çakışma + başlık uyuşmadı -> ATLA (yanlış bağlamaktansa boş)
    return ciftler


def madde_listesi(hucre):
    """'19, 49, Geçici Madde 13' / '8/A' / 'Kanunun Adı, 1, 3' -> madde_id listesi."""
    out, prefix = [], ""
    for parca in (hucre or "").split(","):
        p = parca.strip()
        if not p:
            continue
        low = p.lower()
        if "geçici madde" in low:
            prefix = "Geçici "; p = re.sub(r".*?[Mm]adde", "", p, count=1).strip()
        elif "ek madde" in low:
            prefix = "Ek "; p = re.sub(r".*?[Mm]adde", "", p, count=1).strip()
        elif "mükerrer madde" in low:
            prefix = "Mükerrer "; p = re.sub(r".*?[Mm]adde", "", p, count=1).strip()
        m = re.match(r"(\d+(?:\s*/\s*[A-Za-zÇĞİÖŞÜçğıöşü])?)", p)
        if m:
            out.append(prefix + re.sub(r"\s+", "", m.group(1)))
    return out


def tablo_satirlari(pdf_yol):
    """PDF'teki değişiklik tablosunun satırlarını (araç, maddeler, tarih) döndür.

    Çizelge birden çok sayfaya yayılabilir; başlık yalnız ilk sayfadadır. Başlık
    sayfası bulununca sonraki tablolu sayfaları da (DEVAM) alırız, tablosuz sayfa
    görünce dururuz.
    """
    satir = []
    aktif = False
    with pdfplumber.open(pdf_yol) as pdf:
        # Çizelge her zaman PDF sonunda -> yalnız son ~30 sayfayı tara (hız/ısı).
        sayfalar = pdf.pages[-30:] if len(pdf.pages) > 30 else pdf.pages
        for page in sayfalar:
            t = page.extract_text() or ""
            basli = bool(BAS.search(t))
            tablar = page.extract_tables() or []
            if basli:
                aktif = True
            elif aktif and not tablar:
                aktif = False
            if not (basli or (aktif and tablar)):
                continue
            for tab in tablar:
                for row in tab:
                    h = [(c or "").replace("\n", " ").strip() for c in row]
                    if len(h) < 3:
                        continue
                    arac, maddeler, tarih = h[0], h[1], h[2]
                    if not arac or "Değiştiren" in arac or "Maddeleri" in maddeler:
                        continue  # başlık satırı
                    satir.append((arac, maddeler, tarih))
    return satir


def yururluk_tarihi(hucre):
    """Tarih hücresinden asıl yürürlük tarihini seç: parantezli varsa onu (asıl
    geçerlilik), yoksa ilk tarihi. Ör. '...(9/7/2018)' -> 9/7/2018."""
    paren = re.findall(r"\((\d{1,2}/\d{1,2}/\d{4})\)", hucre or "")
    if paren:
        return paren[-1]
    m = TARIH.search(hucre or "")
    return m.group(0) if m else (hucre or "")[:40]


def main():
    # Çizelgesi olan her txt -> DOĞRU tam-anahtar + PDF (numara çakışması güvenli).
    ciftler = pdf_belge_eslestir()
    print(f"çizelge+PDF eşleşen belge: {len(ciftler)}", flush=True)

    kayit = []
    islenen = iptal_madde = degisik_madde = hata = 0
    for i, (belge, pdf) in enumerate(ciftler, 1):
        try:
            satirlar = tablo_satirlari(pdf)
        except Exception:
            hata += 1
            continue
        islenen += 1
        for arac, maddeler, tarih in satirlar:
            durum = "iptal" if AYM.search(arac) else "degisik"
            th = yururluk_tarihi(tarih)
            for mid in madde_listesi(maddeler):
                kayit.append({"belge": belge, "madde_id": mid, "durum": durum,
                              "arac": re.sub(r"\s+", " ", arac)[:120],
                              "tarih": th})
                if durum == "iptal":
                    iptal_madde += 1
                else:
                    degisik_madde += 1
        if i % 50 == 0:
            print(f"  ... {i}/{len(ciftler)} kanun işlendi", flush=True)

    with OUT.open("w", encoding="utf-8") as f:
        for k in kayit:
            f.write(json.dumps(k, ensure_ascii=False) + "\n")

    print("=" * 60)
    print(f"işlenen kanun     : {islenen}   (pdf hatası: {hata})")
    print(f"toplam kayıt      : {len(kayit)}")
    print(f"  iptal (AYM)     : {iptal_madde} madde-kaydı")
    print(f"  değişik         : {degisik_madde} madde-kaydı")
    print(f"yazıldı -> {OUT}")
    print("\n-- DOĞRULAMA: 6098 kayıtları (15,584,256,407,344,Geçici 1/2 olmalı) --")
    for k in kayit:
        if k["belge"].startswith("6098_"):
            print(f"  madde {k['madde_id']:<10} [{k['durum']}] <- {k['arac'][:40]} ({k['tarih']})")
    print("\n-- DOĞRULAMA: 3568'in iptal kayıtları --")
    for k in kayit:
        if k["belge"].startswith("3568_") and k["durum"] == "iptal":
            print(f"  madde {k['madde_id']:<10} <- {k['arac'][:55]} ({k['tarih']})")


if __name__ == "__main__":
    main()