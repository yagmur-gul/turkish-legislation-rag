"""OCR ile PDF'leri metne çevirir — tamamla.py'nin kuyruğunu boşaltır.

NEDEN OCR
    İki ayrı sebep, aynı sonuç:
    1. CB Kararları'nın PDF'lerinde yazı tipi ANONİM (ToUnicode CMap yok).
       pdfplumber, PyMuPDF ve pdftotext denendi — üçü de ham glif kodu döndürdü.
       Her PDF'in numaralandırması farklı, ortak çözüm tablosu çıkarılamıyor.
    2. Bazı tebliğler siteye GÖRÜNTÜ olarak konmuş (metin değil, taranmış resim).
    Her iki durumda da metin görsel olarak orada; tek yol Tesseract.

TEK KAYNAK İLKESİ
    Anahtar/klasör/kalite mantığı tamamla.py'den IMPORT edilir, kopyalanmaz.
    Daha önce bu iki dosyada aynı mantığın iki kopyası vardı; tamamla.py
    güncellenince ocr_duzelt.py geride kaldı ve 29 belgeyi "katalogda yok"
    sanıp atladı. Aynı kural iki yerde tanımlıysa er geç ayrışır.

GİRDİ   cikti/pdf_ocr_bekleyen/<anahtar>.pdf     (tamamla.py indirdi)
ÇIKTI   cikti/belgeler/<klasor>/<anahtar>_<ad>.txt
DEFTER  cikti/tamamla_durum.json  (ortak) — anahtar ocr_bekliyor -> cekildi

KURULUM
    sudo apt install -y tesseract-ocr tesseract-ocr-tur poppler-utils
    pip install pdf2image pytesseract

KULLANIM
    python ocr_duzelt.py --durum     # kaç PDF bekliyor (işlem yapmaz)
    python ocr_duzelt.py --test 3    # 3 belgeyi dene
    python ocr_duzelt.py             # kuyruğu boşalt
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # guncelleme/ (tamamla burada)

# Tek kaynak: anahtar, klasör ve kalite mantığı tamamla.py'de tanımlı.
from tamamla import (
    CIKTI, DURUM, KATALOG, META, PDF_DIZIN, RAPOR,
    anahtar, hedef_yol, katalog_oku, kusur, meta_yaz,
)

KESIK_LOG = RAPOR / "ocr_kesilen.jsonl"

MIN_UZUNLUK = 100     # OCR bundan az verdiyse başarısız say
DPI = 150             # 150 iyi denge; 300 daha doğru ama ~4x yavaş
MAX_SAYFA = 500        # daha uzun PDF'lerde ilk N sayfa (kesilenler loglanır)


# ------------------------------------------------------------ ortak checkpoint

def durum_oku() -> dict:
    if DURUM.exists():
        try:
            d = json.loads(DURUM.read_text(encoding="utf-8"))
            return {"cekildi": set(d.get("cekildi", [])),
                    "metni_yok": set(d.get("metni_yok", [])),
                    "ocr_bekliyor": set(d.get("ocr_bekliyor", []))}
        except Exception:
            pass
    return {"cekildi": set(), "metni_yok": set(), "ocr_bekliyor": set()}


def durum_yaz(d: dict) -> None:
    DURUM.write_text(json.dumps({k: sorted(v) for k, v in d.items()},
                                ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------- OCR

def ocr_yap(pdf: Path) -> tuple[str, bool]:
    """PDF'i SAYFA SAYFA görüntüye çevirip OCR yapar.

    Tüm sayfaları birden belleğe almak büyük PDF'lerde süreci sessizce
    öldürüyor (3.8 GB RAM). Tek tek çevirip hemen bırakıyoruz.

    Döner: (metin, kesildi_mi)
    """
    from pdf2image import convert_from_path, pdfinfo_from_path
    import pytesseract

    try:
        toplam = int(pdfinfo_from_path(str(pdf)).get("Pages", 1))
    except Exception:
        toplam = 1

    parcalar = []
    for sayfa_no in range(1, min(toplam, MAX_SAYFA) + 1):
        try:
            gorseller = convert_from_path(str(pdf), dpi=DPI,
                                          first_page=sayfa_no, last_page=sayfa_no)
        except Exception:
            continue
        if not gorseller:
            continue
        img = gorseller[0]
        try:
            parcalar.append(pytesseract.image_to_string(img, lang="tur"))
        except Exception:
            try:
                parcalar.append(pytesseract.image_to_string(img))
            except Exception:
                pass
        finally:
            img.close()

    return "\n".join(parcalar).strip(), toplam > MAX_SAYFA


# ---------------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    if not PDF_DIZIN.exists():
        print(f"PDF kuyruğu yok: {PDF_DIZIN}\n"
              f"Önce `python tamamla.py` çalıştır — PDF'leri o indiriyor.")
        return 1

    # katalog_oku() çakışan üçlüleri tespit edip anahtar()'ı doğru kurar.
    # Bu satır ÖNEMLİ: onsuz tarihli anahtarlar (10_22_5_12092018) tanınmaz.
    idx = {anahtar(r): r for r in katalog_oku()}
    d = durum_oku()

    pdfler = sorted(PDF_DIZIN.glob("*.pdf"))
    kalan, oksuz = [], []
    for p in pdfler:
        a = p.stem
        if a in d["cekildi"]:
            continue
        rec = idx.get(a)
        if rec is None:
            oksuz.append(a)
            continue
        kalan.append((a, rec, p))

    print("=" * 55)
    print(f"  Kuyruktaki PDF   : {len(pdfler)}")
    print(f"  Zaten OCR'landı  : {len(pdfler) - len(kalan) - len(oksuz)}")
    print(f"  OCR yapılacak    : {len(kalan)}")
    if oksuz:
        print(f"  Katalogda yok    : {len(oksuz)}  (!!) {oksuz[:3]}")
    print(f"  Tahmini süre     : ~{len(kalan) * 8 / 60:.0f} dk")
    print("=" * 55)

    if "--durum" in argv or "--status" in argv:
        return 0

    if "--test" in argv:
        i = argv.index("--test")
        n = int(argv[i + 1]) if i + 1 < len(argv) else 3
        kalan = kalan[:n]

    if not kalan:
        print("OCR kuyruğu boş.")
        return 0

    KESIK_LOG.parent.mkdir(parents=True, exist_ok=True)
    sayac = Counter()
    t0 = time.time()
    toplam = len(kalan)

    for i, (a, rec, pdf) in enumerate(kalan, 1):
        ad = (rec.get("mevAdi") or "")[:30]
        try:
            metin, kesildi = ocr_yap(pdf)
        except Exception as exc:
            sayac["hata"] += 1
            print(f"  [{i}/{toplam}] ✗ {a} — hata: {str(exc)[:45]}", flush=True)
            continue

        # OCR çıktısı da kalite kontrolünden geçer (menü/iskelet gelmiş olabilir).
        k = kusur(metin) if len(metin) >= MIN_UZUNLUK else "bos"
        if k:
            sayac["bos"] += 1
            d["ocr_bekliyor"].discard(a)
            d["metni_yok"].add(a)
            durum_yaz(d)
            print(f"  [{i}/{toplam}] ~ {a} — OCR okunamadı ({k}) — {ad}", flush=True)
            continue

        yol = hedef_yol(rec)
        yol.parent.mkdir(parents=True, exist_ok=True)
        yol.write_text(metin, encoding="utf-8")
        meta_yaz(rec, kaynak="ocr_tesseract")

        d["ocr_bekliyor"].discard(a)
        d["cekildi"].add(a)
        durum_yaz(d)                     # her belgede kaydet (çökerse kayıp yok)

        sayac["ok"] += 1
        if kesildi:
            sayac["kesik"] += 1
            with KESIK_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"anahtar": a, "ad": rec.get("mevAdi"),
                                    "islenen_sayfa": MAX_SAYFA},
                                   ensure_ascii=False) + "\n")
        print(f"  [{i}/{toplam}] {'✂' if kesildi else '✓'} {a} — {len(metin)} kr "
              f"— {ad}", flush=True)

        if i % 20 == 0:
            hiz = i / (time.time() - t0) * 60
            print(f"     ... {i}/{toplam} — {hiz:.1f} belge/dk, "
                  f"kalan ~{(toplam - i) / max(hiz, 0.1):.0f} dk", flush=True)

    print("=" * 55)
    print(f"  OCR başarılı       : {sayac['ok']}")
    print(f"  Kesilen (uzun PDF) : {sayac['kesik']}  -> {KESIK_LOG}")
    print(f"  Okunamadı          : {sayac['bos']}   (metni_yok sayıldı)")
    print(f"  Hata               : {sayac['hata']}")
    print("=" * 55)
    print("Son kontrol:  python tamamla.py --durum")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))