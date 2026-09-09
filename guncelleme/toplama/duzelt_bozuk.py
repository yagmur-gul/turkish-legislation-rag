"""Bozuk metinli belgeleri Playwright ile YENİDEN çeker.

Sorun: topla.py bazı PDF'lerden metni çıkaramadı; sonuç "(cid:12)(cid:18)..."
gibi çöp oldu (PDF'te yazı tipi gömülü değil).

Çözüm: Playwright ile detay sayfasını açıp iframe'deki metni okumak.
Bu yol PDF çözümlemeye hiç girmediği için (cid:) sorunu olmaz.

Kullanım:
    python duzelt_bozuk.py --liste     # bozukları bul, listele (çekmez)
    python duzelt_bozuk.py --test 3    # 3 tanesini dene
    python duzelt_bozuk.py             # hepsini yeniden çek
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

KOK = Path(__file__).resolve().parent.parent.parent   # toplama/ -> guncelleme/ -> kök
CIKTI = KOK / "cikti"
BELGELER = CIKTI / "belgeler"
KATALOG = CIKTI / "katalog.jsonl"
BASE = "https://www.mevzuat.gov.tr"

DELAY_MIN, DELAY_MAX = 2.0, 4.0
NAV_TIMEOUT = 30000
MIN_UZUNLUK = 900      # menü ~671 karakter; gerçek metin bundan uzun olmalı

# Sayfanın menüsü (iframe boşsa bu gelir) — metin sanılmasın
MENU_IZLERI = (
    "MEVZUAT BİLGİ SİSTEMİ",
    "Kanunlar Fihristi",
    "Cumhurbaşkanlığı Kararnameleri Fihristi",
)


def menu_mu(metin: str) -> bool:
    """Gelen metin sadece sitenin menüsü mü (iframe boş kalmış)?"""
    if len(metin) < MIN_UZUNLUK:
        return True
    # Menü izleri var VE madde/içerik belirtisi yoksa -> menü
    izler = sum(1 for iz in MENU_IZLERI if iz in metin)
    icerik_var = any(k in metin for k in ("MADDE", "Madde", "BÖLÜM", "Amaç"))
    return izler >= 2 and not icerik_var


def bozuklari_bul() -> list[Path]:
    """(cid:) içeren .txt dosyalarını bul."""
    bozuk = []
    for f in BELGELER.glob("*/*.txt"):
        try:
            bas = f.read_text(encoding="utf-8", errors="ignore")[:2000]
        except Exception:
            continue
        if "(cid:" in bas:
            bozuk.append(f)
    return sorted(bozuk)


def katalog_yukle() -> dict[str, tuple]:
    kat = {}
    with KATALOG.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            no = str(r.get("mevzuatNo", ""))
            if no:
                kat[no] = (r.get("mevzuatTur", ""), r.get("mevzuatTertip", ""))
    return kat


def cek(page, no: str, tur, tertip) -> str:
    url = f"{BASE}/mevzuat?MevzuatNo={no}&MevzuatTur={tur}&MevzuatTertip={tertip}"
    try:
        resp = page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
        if not resp or resp.status >= 400:
            return ""
        page.wait_for_timeout(2500)
        metin = ""
        for fr in page.frames:
            try:
                t = fr.inner_text("body")
                if len(t) > len(metin):
                    metin = t
            except Exception:
                pass
        return metin
    except Exception:
        return ""


def main(argv: list[str]) -> int:
    # Önceki hatalı çalıştırmada menü yazılmış dosyaları geri al
    if "--geri-al" in argv:
        geri = 0
        for yedek in BELGELER.glob("*/*.txt.bozuk"):
            asil = yedek.with_suffix("")          # .txt.bozuk -> .txt
            try:
                icerik = asil.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if menu_mu(icerik):                    # menü yazılmışsa eskiye dön
                asil.write_text(
                    yedek.read_text(encoding="utf-8", errors="ignore"),
                    encoding="utf-8")
                yedek.unlink()
                geri += 1
        print(f"Menü yazılmış {geri} dosya eski haline döndürüldü "
              f"(tekrar (cid:) bozuk oldular, yeniden denenecekler).")
        return 0

    print("Bozuk belgeler taranıyor...")
    bozuk = bozuklari_bul()
    print(f"Bozuk (cid:) metinli belge: {len(bozuk)}\n")

    if "--liste" in argv:
        for f in bozuk[:30]:
            print(f"  {f.parent.name}/{f.name[:60]}")
        if len(bozuk) > 30:
            print(f"  ... ve {len(bozuk)-30} tane daha")
        return 0

    if "--test" in argv:
        i = argv.index("--test")
        n = int(argv[i + 1]) if i + 1 < len(argv) else 3
        bozuk = bozuk[:n]

    kat = katalog_yukle()
    duzeldi = bos = hata = 0

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="tr-TR",
        )
        page = ctx.new_page()

        for i, f in enumerate(bozuk, 1):
            no = f.stem.split("_", 1)[0]
            tur, tertip = kat.get(no, ("", ""))
            metin = cek(page, no, tur, tertip)

            if metin and not menu_mu(metin):
                # eski bozuk dosyayı yedekle, yenisini yaz
                f.with_suffix(".txt.bozuk").write_text(
                    f.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
                f.write_text(metin, encoding="utf-8")
                duzeldi += 1
                print(f"  [{i}/{len(bozuk)}] ✓ {no} — {len(metin)} karakter", flush=True)
            elif metin:
                bos += 1
                print(f"  [{i}/{len(bozuk)}] ~ {no} — metin sitede yok "
                      f"(sadece menü, {len(metin)} kr)", flush=True)
            else:
                hata += 1
                print(f"  [{i}/{len(bozuk)}] ✗ {no} — çekilemedi", flush=True)

            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        b.close()

    print(f"\n{'='*50}")
    print(f"Düzeltilen      : {duzeldi}")
    print(f"Metin sitede yok: {bos}")
    print(f"Çekilemedi      : {hata}")
    print("Eski bozuk metinler .txt.bozuk olarak saklandı.")
    print("=" * 50)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))