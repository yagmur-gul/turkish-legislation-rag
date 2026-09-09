"""mevzuat.gov.tr toplayıcı — iki aşamalı, checkpoint'li, ban-korumalı.

AŞAMA 1: MevzuatDatatable (POST) uçundan 18.991 belgenin KÜNYESİNİ çeker
         (ad, no, tür, link). Temiz JSON, birkaç dakika, düşük risk.
         -> katalog.jsonl  (her satır bir belge künyesi)

AŞAMA 2: Katalogtaki her belgenin METİN sayfasını (aramasonuc) tek tek
         çeker, HTML'den metni ayıklar, tek tek diske yazar.
         -> belgeler/<Tür>/<no>.txt  ve  metadata/<no>.json

DAYANIKLILIK:
  * Her belge alınır alınmaz diske yazılır (toplu değil) -> ban gelse bile
    önceki emek durur.
  * İlerleme durum.json'da tutulur -> tekrar çalıştırınca KALDIĞI YERDEN
    devam eder, baştan başlamaz.
  * Art arda hata gelirse "ban olabilir" deyip nazikçe DURUR, özet yazar.
  * Dengeli hız: her istek arası bekleme + küçük rastgelelik (insansı).

NOT: Bu araç mevzuat.gov.tr'ye otomatik erişir; site robots.txt ile buna
kapalıdır. Kurum/amir onayıyla ve sorumluluğuyla çalıştırılır.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# --- Sabitler ------------------------------------------------------------
BASE = "https://mevzuat.gov.tr"
LIST_URL = f"{BASE}/anasayfa/MevzuatDatatable"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Dengeli hız: belge arası rastgele bekleme (insansı, ban'a nazik).
DELAY_MIN, DELAY_MAX = 3.0, 5.0
# "Arada mola": her BATCH_SIZE belgede bir, BATCH_PAUSE_* sn dinlen. İnsan
# gibi ara vermek, saatlerce kesintisiz istekten daha az dikkat çeker.
BATCH_SIZE = 250
BATCH_PAUSE_MIN, BATCH_PAUSE_MAX = 90.0, 180.0
LIST_PAGE_SIZE = 100          # her liste isteğinde kaç künye
CONNECT_TIMEOUT, READ_TIMEOUT = 15, 45
CONSECUTIVE_FAIL_LIMIT = 8    # art arda bu kadar hata -> "ban olabilir", dur

# Çıktı yolları
OUT = Path(__file__).resolve().parent.parent.parent / "cikti"   # toplama/ -> guncelleme/ -> kök
CATALOG = OUT / "katalog.jsonl"
STATE = OUT / "durum.json"
DOCS = OUT / "belgeler"
META = OUT / "metadata"

# mevzuatTur -> okunur ad (JSON'daki mevzuatTurEnumString zaten veriyor ama
# güvenlik için haritayı da tutuyoruz)
TUR_KLASOR = {
    1: "Kanunlar", 2: "CB_Kararnameleri", 3: "KHK", 4: "Tuzukler",
    5: "Yonetmelikler", 6: "CB_Yonetmelikleri", 7: "Yonetmelikler",
    8: "Teblig_ve_Kararlar", 9: "Teblig_ve_Kararlar",
}


def _slug(text: str, maxlen: int = 120) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    text = re.sub(r"\s+", "_", text)
    return text[:maxlen] or "belge"


# --- Durum (checkpoint) --------------------------------------------------
@dataclass
class State:
    catalog_done: bool = False        # AŞAMA 1 bitti mi
    total: int = 0                    # katalogtaki toplam
    done_ids: list[str] | None = None # metni alınmış belge no'ları

    @classmethod
    def load(cls) -> "State":
        if STATE.exists():
            data = json.loads(STATE.read_text(encoding="utf-8"))
            return cls(**data)
        return cls(done_ids=[])

    def save(self) -> None:
        STATE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                         encoding="utf-8")


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": BROWSER_UA,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "tr-TR,tr;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE,
        "Referer": f"{BASE}/anasayfa",
    })
    return s


def _sleep() -> None:
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


# --- AŞAMA 1: KATALOG ----------------------------------------------------
def _list_payload(start: int, length: int) -> dict:
    """MevzuatDatatable POST gövdesi (tarayıcının gönderdiğinin sadeleştirilmişi)."""
    return {
        "draw": 1,
        "columns": [],
        "order": [],
        "start": start,
        "length": length,
        "search": {"value": "", "regex": False},
        "parameters": {
            "AranacakIfade": "",
            "AranacakYer": "Baslik",
            "TamCumle": False,
            "MevzuatTurler": [1, 2, 3, 4, 5, 6, 7, 8, 9],  # tüm türler
        },
    }


def build_catalog(session: requests.Session, limit: int | None = None) -> int:
    """Tüm künyeleri MevzuatDatatable'dan çekip katalog.jsonl'e yazar."""
    OUT.mkdir(parents=True, exist_ok=True)
    start, total, written = 0, None, 0
    with CATALOG.open("w", encoding="utf-8") as f:
        while True:
            page_len = LIST_PAGE_SIZE if limit is None else min(LIST_PAGE_SIZE, limit - written)
            if page_len <= 0:
                break
            try:
                r = session.post(LIST_URL, json=_list_payload(start, page_len),
                                 timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
                r.raise_for_status()
                data = r.json()
            except (requests.RequestException, ValueError) as exc:
                print(f"  [KATALOG] hata (start={start}): {exc}")
                return written
            if total is None:
                total = data.get("recordsTotal", 0)
                print(f"  [KATALOG] toplam kayıt: {total}")
            rows = data.get("data", [])
            if not rows:
                break
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            print(f"  [KATALOG] {written}/{total if limit is None else limit} künye alındı")
            start += page_len
            if (limit is not None and written >= limit) or (total and start >= total):
                break
            _sleep()
    return written


# --- AŞAMA 2: METİN ------------------------------------------------------
def _build_pdf_url(rec: dict) -> str | None:
    """Katalog kaydından doğrudan PDF URL'sini kurar (detay sayfası olmadan).

    Kalıp: MevzuatMetin/<Tur>.<Tertip>.<No>.pdf  (ör. 1.5.7579.pdf)
    Kurulamazsa None döner; o zaman fallback (detay sayfası) devreye girer.
    """
    tur = rec.get("mevzuatTur")
    tertip = rec.get("mevzuatTertip")
    no = rec.get("mevzuatNo")
    if tur is None or not tertip or not no:
        return None
    return f"{BASE}/MevzuatMetin/{tur}.{tertip}.{no}.pdf"


def _find_download_links(html: str) -> tuple[str | None, str | None]:
    """Detay sayfasından PDF ve DOC indirme linklerini bulur (pdf, doc)."""
    soup = BeautifulSoup(html, "html.parser")
    pdf = doc = None
    for a in soup.find_all("a", href=True):
        h = a["href"].strip()
        if "MevzuatMetin" not in h:
            continue
        if h.lower().endswith(".pdf"):
            pdf = h
        elif h.lower().endswith(".doc"):
            doc = h
    return pdf, doc


def _pdf_to_text(raw: bytes) -> str:
    """PDF baytlarından metni çıkarır (pdfplumber, yoksa pypdf)."""
    import io
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return "\n".join((p.extract_text() or "") for p in pdf.pages).strip()
    except Exception:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages).strip()
        except Exception:
            return ""


def _download(session: requests.Session, url: str) -> bytes | None:
    """Bir dosyayı indirir; başarısızsa None."""
    try:
        r = session.get(url, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def fetch_document(session: requests.Session, rec: dict) -> bool:
    """Belgenin PDF'ini indirip metnini diske yazar. Başarılıysa True.

    OPTİMİZE + FALLBACK:
      A) Önce katalogdan PDF URL'sini KUR ve doğrudan indir (TEK istek, hızlı).
      B) Olmazsa detay sayfasını çek, linki oradan oku (2 istek, garantili).
    Böylece çoğu belge tek istekle gelir (ban riski yarıya), kalıba uymayan
    azınlık da güvenli yoldan yine toplanır (belge kaçmaz).
    """
    rel = rec.get("url", "")
    if not rel:
        return False

    raw_bytes = None
    file_url = None

    # --- A) Hızlı yol: kalıptan PDF (detay sayfası yok) ---
    guessed = _build_pdf_url(rec)
    if guessed:
        raw_bytes = _download(session, guessed)
        if raw_bytes and raw_bytes[:4] == b"%PDF":  # gerçekten PDF mi?
            file_url = guessed
        else:
            raw_bytes = None  # kalıp tutmadı -> fallback

    # --- B) Fallback: detay sayfasından linki oku ---
    if raw_bytes is None:
        try:
            page = session.get(f"{BASE}/{rel}", timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
            page.raise_for_status()
        except requests.RequestException as exc:
            print(f"    sayfa hatası ({rec.get('mevzuatNo')}): {exc}")
            return False
        pdf_url, doc_url = _find_download_links(page.text)
        file_url = pdf_url or doc_url
        if not file_url:
            return False
        raw_bytes = _download(session, file_url)
        if raw_bytes is None:
            print(f"    dosya hatası ({rec.get('mevzuatNo')})")
            return False

    # --- Metni çıkar ---
    text = _pdf_to_text(raw_bytes) if file_url.lower().endswith(".pdf") else ""
    raw_ext = ".pdf" if file_url.lower().endswith(".pdf") else ".doc"

    if len(text) < 50 and raw_ext == ".pdf":
        return False

    tur = TUR_KLASOR.get(int(rec.get("mevzuatTur", 0)),
                         rec.get("mevzuatTurEnumString", "Diger"))
    no = str(rec.get("mevzuatNo", "x"))
    ad = rec.get("mevAdi", "")
    folder = DOCS / tur
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{no}_{_slug(ad)}"

    # Ham dosyayı her zaman sakla (PDF ya da doc) — ileride tekrar işlenebilir.
    (folder / f"{stem}{raw_ext}").write_bytes(raw_bytes)
    # Metin çıkarılabildiyse .txt de yaz (RAG bunu kullanır).
    if text:
        (folder / f"{stem}.txt").write_text(text, encoding="utf-8")

    META.mkdir(parents=True, exist_ok=True)
    (META / f"{no}.json").write_text(
        json.dumps({
            "mevzuatNo": no, "ad": ad, "tur": tur,
            "resmiGazeteTarihi": rec.get("resmiGazeteTarihi"),
            "resmiGazeteSayisi": rec.get("resmiGazeteSayisi"),
            "kaynak_url": f"{BASE}/{rel}",
            "dosya_url": file_url,
            "metin_var": bool(text),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def collect_texts(session: requests.Session, state: State, limit: int | None) -> None:
    """Katalogtaki belgelerin metnini, kaldığı yerden, tek tek toplar."""
    records = [json.loads(l) for l in CATALOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    done = set(state.done_ids or [])
    consecutive_fail = 0
    processed = 0

    for rec in records:
        no = str(rec.get("mevzuatNo", ""))
        if no in done:
            continue
        if limit is not None and processed >= limit:
            print(f"\n  Test limiti ({limit}) doldu, duruyorum.")
            break

        ok = fetch_document(session, rec)
        processed += 1
        if ok:
            done.add(no)
            state.done_ids = sorted(done)
            state.save()  # HER belgede checkpoint -> ban gelse bile kayıp yok
            consecutive_fail = 0
            print(f"  [{len(done)}/{state.total}] alındı: {no} - {rec.get('mevAdi','')[:50]}")
        else:
            consecutive_fail += 1
            print(f"  başarısız ({consecutive_fail}/{CONSECUTIVE_FAIL_LIMIT}): {no}")
            if consecutive_fail >= CONSECUTIVE_FAIL_LIMIT:
                print("\n  !! Art arda çok hata — BAN olabilir. Nazikçe duruyorum.")
                print(f"  Şimdiye kadar: {len(done)}/{state.total} belge toplandı.")
                print("  IP açılınca aynı komutu tekrar çalıştır; KALDIĞIN YERDEN devam eder.")
                return
        _sleep()

        # Arada mola: her BATCH_SIZE başarılı belgede bir, birkaç dakika dinlen.
        if processed > 0 and processed % BATCH_SIZE == 0:
            pause = random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX)
            print(f"  ... mola ({pause:.0f} sn) — insansı ara, ban'a karşı ...")
            time.sleep(pause)

    print(f"\n  Bu tur bitti. Toplam toplanan: {len(done)}/{state.total}")


# --- Durum raporu --------------------------------------------------------
def show_status(state: State) -> None:
    done = len(state.done_ids or [])
    print("=" * 55)
    print("DURUM")
    print(f"  Katalog hazır mı : {'evet' if state.catalog_done else 'hayır'}")
    print(f"  Toplam belge     : {state.total}")
    print(f"  Toplanan metin   : {done}")
    print(f"  Kalan            : {max(state.total - done, 0)}")
    print("=" * 55)


# --- Ana akış ------------------------------------------------------------
def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="mevzuat.gov.tr toplayıcı")
    p.add_argument("--test", type=int, metavar="N",
                   help="Sadece N belgeyle test et (ör. --test 50)")
    p.add_argument("--status", action="store_true", help="Durumu göster ve çık")
    p.add_argument("--katalog", action="store_true",
                   help="SADECE katalog (künye listesi) çek, belge metinlerini İNDİRME "
                        "(yeni belge var mı bakmak için). Belge çekme işini tamamla.py yapar.")
    args = p.parse_args(argv)

    state = State.load()
    if args.status:
        show_status(state)
        return 0

    session = _session()

    # AŞAMA 1: katalog (yoksa, test için, ya da --katalog ile zorlanınca yenile)
    if not state.catalog_done or not CATALOG.exists() or args.katalog:
        print("AŞAMA 1: Katalog (künye listesi) çekiliyor...")
        # testte küçük katalog yeter; tam çalıştırmada limitsiz
        n = build_catalog(session, limit=args.test)
        state.total = n
        state.catalog_done = args.test is None  # test kataloğunu 'tam' sayma
        state.save()
        print(f"AŞAMA 1 bitti: {n} künye katalogda.\n")

    if args.katalog:                    # sadece katalog istendi → belge metinlerini çekme
        print("(--katalog) Yalnız katalog güncellendi; belge metinleri indirilmedi.\n"
              "Eksik/yeni belgeleri çekmek için: python guncelleme/tamamla.py")
        return 0

    # AŞAMA 2: metinler
    print("AŞAMA 2: Belge metinleri toplanıyor (kaldığı yerden)...")
    collect_texts(session, state, limit=args.test)
    show_status(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))