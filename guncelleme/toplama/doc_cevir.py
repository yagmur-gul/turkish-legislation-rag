"""Toplu .doc -> .txt çevirici (offline, internet/ban gerektirmez).

cikti/belgeler/ altındaki her .doc dosyasının metnini libreoffice ile çıkarıp
yanına .txt olarak yazar. Zaten .txt'si olan .doc'ları atlar -> tekrar
çalıştırınca KALDIĞI YERDEN devam eder.

Kullanım:
    python doc_cevir.py            # tümünü çevir
    python doc_cevir.py --status   # kaç .doc var, kaçı çevrildi

Gerekli: libreoffice (WSL'de:  sudo apt install libreoffice --no-install-recommends)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent.parent   # toplama/ -> guncelleme/ -> kök
DOCS_DIR = BASE / "cikti" / "belgeler"
# libreoffice aynı anda çok çağrılınca çakışır; tek tek, ayrı profil ile çağırıyoruz.
SOFFICE = "libreoffice"
CONVERT_FILTER = "txt:Text (encoded):UTF8"


def find_docs() -> list[Path]:
    """Tüm .doc dosyalarını bulur."""
    return sorted(DOCS_DIR.rglob("*.doc"))


def needs_convert(doc: Path) -> bool:
    """Bu .doc için .txt yok (ya da boş) mu?"""
    txt = doc.with_suffix(".txt")
    return not (txt.exists() and txt.stat().st_size > 20)


def convert_one(doc: Path) -> bool:
    """Tek bir .doc'u aynı klasöre .txt olarak çevirir. Başarılıysa True."""
    try:
        subprocess.run(
            [SOFFICE, "--headless", "--convert-to", CONVERT_FILTER,
             "--outdir", str(doc.parent), str(doc)],
            check=True, capture_output=True, timeout=90,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"  HATA: {doc.name} -> {exc}")
        return False
    txt = doc.with_suffix(".txt")
    return txt.exists() and txt.stat().st_size > 20


def status() -> None:
    docs = find_docs()
    done = sum(1 for d in docs if not needs_convert(d))
    print("=" * 50)
    print("DOC ÇEVİRME DURUMU")
    print(f"  Toplam .doc  : {len(docs)}")
    print(f"  Çevrildi     : {done}")
    print(f"  Kalan        : {len(docs) - done}")
    print("=" * 50)


def main(argv: list[str]) -> int:
    if "--status" in argv:
        status()
        return 0

    if not DOCS_DIR.exists():
        print(f"Klasör yok: {DOCS_DIR}")
        return 1

    docs = [d for d in find_docs() if needs_convert(d)]
    total = len(docs)
    print(f"Çevrilecek .doc sayısı: {total}\n")

    ok = fail = 0
    start = time.monotonic()
    for i, doc in enumerate(docs, 1):
        if convert_one(doc):
            ok += 1
        else:
            fail += 1
        if i % 50 == 0 or i == total:
            elapsed = time.monotonic() - start
            hız = i / elapsed if elapsed else 0
            print(f"  [{i}/{total}] çevrildi={ok} hata={fail} "
                  f"(~{hız:.1f} belge/sn)")

    print(f"\nBitti. Çevrilen: {ok}, hata: {fail}")
    status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))