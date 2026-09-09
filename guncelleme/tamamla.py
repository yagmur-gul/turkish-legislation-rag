"""tamamla.py — mevzuat arşivini tamamlar ve doğrular.

BENZERSIZLIK ANAHTARI  (üç kez düzeltildi, her seferinde veriye bakarak)
    1. mevzuatNo            -> YETERSİZ. Aynı numara hem Kanun hem CB Kararı
                               olabiliyor (7552 = İKLİM KANUNU ve bir CB Kararı).
    2. (no, tur, tertip)    -> ÇOĞUNLUKLA yeter, ama CB Genelgeleri'nde numara
                               HER YIL SIFIRLANIYOR: no=7, tur=22, tertip=5 ile
                               2018'den 2026'ya dokuz ayrı genelge var.
    3. Çakışanlara RG tarihi eklenir -> <no>_<tur>_<tertip>_<GGAAYYYY>_<ad>.txt
       Çakışmayanlar eski adıyla kalır (18.7k dosyayı yeniden adlandırmaya gerek yok).

ÜÇ KADEMELİ ÇEKİM
    1. iframe      -> sayfadaki gömülü metin
    2. PDF         -> katalogdaki 'url' alanı; yoksa <tur>.<tertip>.<no>.pdf kalıbı
    3. OCR kuyruğu -> PDF gövdesi anonim fontluysa PDF diske; sonra ocr_duzelt.py

TASARIM İLKELERİ
    1. TAHMİN YOK. Bozukluk ancak kanıtlanabilir bir izle söylenir. Kısa olmak
       bozukluk DEĞİLDİR — gerçek CB Kararları 400 karakter olabiliyor.
    2. DİSK TEK GERÇEK KAYNAĞIDIR. Checkpoint bir hız aracıdır, doğruluk kaynağı
       değil. Her çalıştırmada dosyalar yeniden denetlenir; yeni bir kural
       eklendiğinde geçmişte yanlış kaydedilmiş dosyalar da yakalanır.

YAKALANAN SESSİZ BOZUKLUKLAR (hepsi 'sağlam' görünüyordu)
    * "Bu mevzuata ait güncelleme çalışmaları devam etmektedir" sayfası.
    * CB Kararları'nın PDF'inde gövde anonim fontlu; pdfplumber sadece
      başlık+imza okuyup 140 karakterlik İSKELET döndürüyor.
    * PDF şablonunun doldurulmamış yer tutucuları ("YönetmelikAdı –1, –2...").
    * Menü sayfası: "Kararnameleri Fihristi" içindeki "Karar" alt-dizesi,
      belge izi sanılıp 120 menü sayfasının sağlam görünmesine yol açmıştı.

KULLANIM
    python tamamla.py --durum      # sadece rapor, hiçbir şeye dokunmaz
    python tamamla.py --tasi       # eski isimli dosyaları yeni isme taşı (ağsız)
    python tamamla.py --test 5     # 5 belge dene
    python tamamla.py              # eksik + bozukları çek
"""
from __future__ import annotations

import io
import json
import random
import re
import shutil
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent   # guncelleme/ -> proje kökü
CIKTI = KOK / "cikti"
BELGELER = CIKTI / "belgeler"
META = CIKTI / "metadata"
PDF_DIZIN = CIKTI / "pdf_ocr_bekleyen"
KATALOG = CIKTI / "katalog.jsonl"
DURUM = CIKTI / "tamamla_durum.json"
RAPOR = CIKTI / "rapor"

BASE = "https://www.mevzuat.gov.tr"
DELAY_MIN, DELAY_MAX = 2.0, 4.0
BATCH, PAUSE_MIN, PAUSE_MAX = 150, 30.0, 60.0
NAV_TIMEOUT = 30000
ART_ARDA_HATA_SINIRI = 10

BOS_ESIGI = 50
GOVDE_ESIGI = 80       # çerçeve atıldıktan sonra bu kadar bile kalmıyorsa iskelet
BAS_UZUNLUK = 4000

MENU_IZLERI = ("Kanunlar Fihristi", "Cumhurbaşkanlığı Kararnameleri Fihristi",
               "Yönetmelikler Fihristi", "MEVZUAT BİLGİ SİSTEMİ",
               "Tüm Hakları Saklıdır", "Bize Ulaşın",
               "Hukuk ve Mevzuat Genel Müdürlüğü")

PLACEHOLDER_IZLERI = (
    "güncelleme çalışmaları devam etmektedir",
    "guncelleme calismalari devam etmektedir",
    "işlenmemiştir", "islenmemistir", "henüz işlenmemiş",
)

# Gerçek GÖVDE işareti. Alt-dize DEĞİL, kalıp:
# "Cumhurbaşkanlığı Kararnameleri Fihristi" içindeki "Karar" bir belge izi
# değildir — bu yüzden 120 menü sayfası sağlam sanılmıştı.
BELGE_IZ_KALIBI = re.compile(
    r"(MADDE\s*\d|Madde\s*\d|BÖLÜM|karar\s+verilmiştir|karar\s+verilmesi|"
    r"yürürlüğe\s+girer|hükümlerini\s+yürüt|dayanılarak\s+hazırlan|"
    r"amaç\s+ve\s+kapsam)",
    re.IGNORECASE)

# Belgenin GÖVDESİ değil ÇERÇEVESİ olan satırlar. Bunlar atılınca geriye bir şey
# kalmıyorsa elimizdeki metin belge değil, iskelettir.
CERCEVE_KALIPLARI = [
    r"\d{1,2}\s+\w+\s+\d{4}\s+\w+\s*Resm.?\s*Gazete\s*Say.s.\s*:?\s*\d+",
    r"Resm.?\s*Gazete\s*(Tarihi|Say.s.)\s*:?\s*[\d\.\/]+",
    r"CUMHURBA\w?KANI\s*(KARARI|KARARNAMES.|GENELGES.)?",
    r"Karar\s*Say.s.\s*:?\s*\d+",
    r"Genelge\s*(No|Say.s.)\s*:?\s*[\d\/]+",
    r"Recep\s+Tayyip\s+ERDO\w+AN",
    r"\d{1,2}\s+\w+\s+\d{4}",
    r"Say.\s*:?\s*\d+",
    r"(Yönetmelik|Karar|Tebli.|Mevzuat|Madde|Genelge)(Adı|Metni)\s*[–—-]?\s*\d*",
]

TUR_KLASOR = {
    1: "Kanunlar", 2: "CB_Kararnameleri", 3: "KHK", 4: "Tuzukler",
    5: "Yonetmelikler", 6: "CB_Yonetmelikleri", 7: "Yonetmelikler",
    8: "Yonetmelikler", 9: "Teblig_ve_Kararlar", 19: "Cumhurbaskani_Kararlari",
    20: "Cumhurbaskani_Kararlari", 21: "Usul_ve_Esaslar",
    22: "CB_Genelgeleri",
}


# ------------------------------------------------------- anahtar (kritik kısım)

# Katalogda birden fazla kez geçen (no, tur, tertip) üçlüleri. katalog_oku()
# doldurur; anahtar() bunlara RG tarihini ekler.
_CAKISAN_UCLU: set[tuple] = set()


def _uclu(rec: dict) -> tuple:
    return (str(rec.get("mevzuatNo")), rec.get("mevzuatTur"),
            str(rec.get("mevzuatTertip")))


def _rg_tarih_kodu(rec: dict) -> str:
    """04/07/2026 -> 04072026"""
    return re.sub(r"\D", "", rec.get("resmiGazeteTarihi") or "")[:8]


def anahtar(rec: dict) -> str:
    temel = (f"{rec.get('mevzuatNo')}_{rec.get('mevzuatTur')}"
             f"_{rec.get('mevzuatTertip')}")
    if _uclu(rec) in _CAKISAN_UCLU:
        t = _rg_tarih_kodu(rec)
        if len(t) == 8:
            return f"{temel}_{t}"
    return temel


# ---------------------------------------------------------------- yardımcılar

def slug(metin: str, maxlen: int = 90) -> str:
    metin = re.sub(r"[^\w\s-]", "", metin or "", flags=re.UNICODE).strip()
    metin = re.sub(r"\s+", "_", metin)
    return metin[:maxlen] or "belge"


def sade(metin: str) -> str:
    metin = unicodedata.normalize("NFKD", metin or "")
    metin = "".join(c for c in metin if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z0-9]", "", metin).upper()


def hedef_yol(rec: dict) -> Path:
    tur = rec.get("mevzuatTur")
    klasor = TUR_KLASOR.get(int(tur) if str(tur).isdigit() else 0, "Diger")
    return BELGELER / klasor / f"{anahtar(rec)}_{slug(rec.get('mevAdi', ''))}.txt"


def pdf_adresi(rec: dict) -> str:
    """Katalog 'url' veriyorsa ONU kullan.

    CB Genelgeleri'nin adresi tarih bazlı:
        .../CumhurbaskanligiGenelgeleri/20260704-7.pdf
    Bizim kalıbımıza (22.5.7.pdf) hiç uymuyor — tahminle kurulsa yanlış adrese
    giderdik.
    """
    u = (rec.get("url") or "").strip()
    if u.lower().endswith(".pdf"):
        return u if u.startswith("http") else f"{BASE}/{u.lstrip('/')}"
    return (f"{BASE}/MevzuatMetin/{rec.get('mevzuatTur')}."
            f"{rec.get('mevzuatTertip')}.{rec.get('mevzuatNo')}.pdf")


# ------------------------------------------------------------ kalite kontrolü

def iskelet_mi(metin: str) -> bool:
    """Çerçeve satırları atılınca geriye anlamlı gövde kalıyor mu?

    Uzunluk tahmini DEĞİL: doğrudan 'gövde yok' kanıtı. Gerçek ama kısa bir karar
    ("...kamulaştırılmasına karar verilmiştir") bu testi geçer.
    """
    govde = metin
    for kalip in CERCEVE_KALIPLARI:
        govde = re.sub(kalip, " ", govde, flags=re.IGNORECASE)
    govde = re.sub(r"[\s\W_]+", "", govde)
    return len(govde) < GOVDE_ESIGI


def kusur(metin: str, boyut: int | None = None) -> str | None:
    """Kanıtlanabilir bozukluk varsa sebebini döndür, yoksa None."""
    s = (metin or "").strip()
    if boyut is not None and boyut < BOS_ESIGI:
        return "bos"
    if len(s) < BOS_ESIGI:
        return "bos"

    bas = s[:2500]
    dus = bas.lower()

    if "(cid:" in bas:
        return "cid_bozuk"
    if s.startswith(("ÿş", "ÿþ", "ÿ")) or "h#t#m#l" in bas:
        return "encoding_cop"
    if any(iz in dus for iz in PLACEHOLDER_IZLERI):
        return "metin_pdfte"
    if (sum(1 for iz in MENU_IZLERI if iz in bas) >= 2
            and not BELGE_IZ_KALIBI.search(bas)):
        return "menu_sayfasi"
    if iskelet_mi(s):
        return "iskelet"
    return None


def ait_mi(rec: dict, metin: str) -> bool:
    iz = sade(rec.get("mevAdi", ""))[:20]
    if len(iz) < 12:
        return True
    return iz in sade(metin[:2500])


# ------------------------------------------------------------------- katalog

def katalog_oku() -> list[dict]:
    """İki geçişli: önce çakışan üçlüleri bul, sonra doğru anahtarla tekille."""
    ham = []
    with KATALOG.open(encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if satir:
                ham.append(json.loads(satir))

    sayim = Counter(_uclu(r) for r in ham)
    _CAKISAN_UCLU.clear()
    _CAKISAN_UCLU.update(u for u, n in sayim.items() if n > 1)

    kayitlar, gorulen = [], set()
    for r in ham:
        a = anahtar(r)
        if a in gorulen:          # anahtar hâlâ çakışıyorsa GERÇEK mükerrer
            continue
        gorulen.add(a)
        kayitlar.append(r)

    if _CAKISAN_UCLU:
        print(f"  çakışan (no,tur,tertip): {len(_CAKISAN_UCLU)} — "
              f"bunlara RG tarihi eklendi", flush=True)
    return kayitlar


# --------------------------------------------------------------- disk indeksi

def diski_indeksle() -> tuple[dict[str, Path], dict[str, list[Path]]]:
    """Diskteki .txt'leri anahtara göre indeksle.

    TUZAK: dosya adından anahtar çıkarmak kırılgan. Bazı belge ADLARI tarihle
    başlıyor ("11.10.1983 tarih ve 2913 sayılı Kanun...") ve slug'landığında
    8 haneli bir sayı üretiyor -> tarihli anahtar sanılıyordu. Dosya bulunamayıp
    sonsuza kadar yeniden çekiliyordu.
    ÇÖZÜM: tahmin etme. Hem sade hem tarihli anahtarı kaydet; sinifla() hangisini
    arıyorsa onu bulur.
    """
    yeni: dict[str, Path] = {}
    eski: dict[str, list[Path]] = defaultdict(list)
    for f in BELGELER.glob("*/*.txt"):
        p = f.stem.split("_")
        if len(p) >= 4 and p[1].isdigit() and p[2].isdigit():
            sade_a = f"{p[0]}_{p[1]}_{p[2]}"
            if sade_a in yeni:
                # Aynı anahtara iki dosya: SAĞLAM olanı seç. "İlk geleni al"
                # demek, glob sırasına göre çöpü seçmek demekti.
                if kusur(bas_oku(yeni[sade_a])[0]) and not kusur(bas_oku(f)[0]):
                    yeni[sade_a] = f
            else:
                yeni[sade_a] = f              # ilk gelen kalsın
            if len(p) >= 5 and p[3].isdigit() and len(p[3]) == 8:
                yeni[f"{sade_a}_{p[3]}"] = f        # tarihli anahtar da olsun
        elif p:
            eski[p[0]].append(f)
    return yeni, dict(eski)


def bas_oku(f: Path) -> tuple[str, int]:
    try:
        boyut = f.stat().st_size
        with f.open(encoding="utf-8", errors="ignore") as fh:
            return fh.read(BAS_UZUNLUK), boyut
    except OSError:
        return "", 0


# ---------------------------------------------------------------- checkpoint

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
    DURUM.parent.mkdir(parents=True, exist_ok=True)
    DURUM.write_text(json.dumps({k: sorted(v) for k, v in d.items()},
                                ensure_ascii=False), encoding="utf-8")


# ------------------------------------------------------------- sınıflandırma

def sinifla(kayitlar: list[dict], d: dict) -> dict:
    yeni, eski = diski_indeksle()
    print(f"  diskte: {len(yeni)} yeni biçim, "
          f"{sum(len(v) for v in eski.values())} eski biçim", flush=True)

    no_sayisi = Counter(str(r.get("mevzuatNo")) for r in kayitlar)
    kova = {"saglam": [], "tasinacak": [], "bozuk": [], "eksik": [],
            "metni_yok": [], "ocr_bekliyor": []}
    sebepler = Counter()
    sahiplenilen: set[Path] = set()

    for rec in kayitlar:
        a = anahtar(rec)

        # Dosyası olmayan iki özel durum -> checkpoint'e bakmak zorundayız.
        if a in d["ocr_bekliyor"]:
            kova["ocr_bekliyor"].append(rec)
            continue
        if a in d["metni_yok"]:
            kova["metni_yok"].append(rec)
            continue

        # Doğru isimli dosya var mı? Varsa sahibi kesin -> içeriğini DENETLE.
        f = yeni.get(a)
        if f is not None:
            metin, boyut = bas_oku(f)
            k = kusur(metin, boyut)
            if k:
                sebepler[k] += 1
                kova["bozuk"].append(rec)
            else:
                kova["saglam"].append(rec)
            continue

        # Eski isimli dosya var mı?
        adaylar = [p for p in eski.get(str(rec.get("mevzuatNo")), [])
                   if p not in sahiplenilen]
        if not adaylar:
            kova["eksik"].append(rec)
            continue

        f = adaylar[0]
        metin, boyut = bas_oku(f)
        k = kusur(metin, boyut)
        if k:
            sebepler[k] += 1
            kova["bozuk"].append(rec)
            sahiplenilen.add(f)
            continue

        tekil = no_sayisi[str(rec.get("mevzuatNo"))] == 1
        if tekil or ait_mi(rec, metin):
            kova["tasinacak"].append((rec, f))
            sahiplenilen.add(f)
        else:
            kova["eksik"].append(rec)

    kova["_sebepler"] = sebepler
    return kova


def rapor_yaz(kova: dict) -> None:
    RAPOR.mkdir(parents=True, exist_ok=True)
    for ad in ("eksik", "bozuk", "metni_yok", "ocr_bekliyor"):
        with (RAPOR / f"{ad}.jsonl").open("w", encoding="utf-8") as f:
            for rec in kova[ad]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def ozet(kayitlar: list[dict], kova: dict) -> None:
    cekilecek = len(kova["eksik"]) + len(kova["bozuk"])
    print("=" * 58)
    print(f"  Katalogdaki benzersiz belge  : {len(kayitlar):>6}")
    print(f"  Sağlam                       : {len(kova['saglam']):>6}")
    print(f"  Taşınacak (isim eski)        : {len(kova['tasinacak']):>6}  (ağsız)")
    print(f"  BOZUK                        : {len(kova['bozuk']):>6}")
    print(f"  EKSİK                        : {len(kova['eksik']):>6}")
    print(f"  OCR bekliyor (PDF indi)      : {len(kova['ocr_bekliyor']):>6}")
    print(f"  Sitede metni yok (doğrulandı): {len(kova['metni_yok']):>6}")
    print("-" * 58)
    print(f"  ÇEKİLECEK                    : {cekilecek:>6}"
          f"   (~{cekilecek * 6 / 3600:.1f} saat)")
    print("=" * 58)
    if kova["_sebepler"]:
        print("  Bozukluk sebepleri:")
        for k, v in kova["_sebepler"].most_common():
            print(f"    {v:>6}  {k}")
    if kova["eksik"]:
        print("  Eksiklerin tür dağılımı:")
        for k, v in Counter(r.get("mevzuatTurEnumString") or r.get("mevzuatTur")
                            for r in kova["eksik"]).most_common(8):
            print(f"    {v:>6}  {k}")
    print(f"\n  Ayrıntı: {RAPOR}/")


# --------------------------------------------------------------------- taşıma

def meta_yaz(rec: dict, kaynak: str) -> None:
    META.mkdir(parents=True, exist_ok=True)
    (META / f"{anahtar(rec)}.json").write_text(json.dumps({
        "anahtar": anahtar(rec),
        "mevzuatNo": rec.get("mevzuatNo"),
        "mevzuatTur": rec.get("mevzuatTur"),
        "mevzuatTertip": rec.get("mevzuatTertip"),
        "ad": rec.get("mevAdi"),
        "tur_adi": rec.get("mevzuatTurEnumString"),
        "rg_tarih": rec.get("resmiGazeteTarihi"),
        "rg_sayi": rec.get("resmiGazeteSayisi"),
        "kaynak": kaynak,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def tasi(kova: dict) -> int:
    n = 0
    for rec, kaynak in kova["tasinacak"]:
        hedef = hedef_yol(rec)
        if hedef.exists():
            continue
        hedef.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(kaynak), str(hedef))
        meta_yaz(rec, kaynak="mevcut_dosya_tasindi")
        n += 1
    print(f"  {n} dosya yeni isme taşındı.")
    return n


# ----------------------------------------------------------- kademe 1: iframe

def iframeden_cek(page, rec: dict) -> str:
    """Belge metnini İÇ iframe'den al.

    DİKKAT: 'en uzun çerçeveyi seç' YANLIŞTI. Dış kabuk (menü + altbilgi) ~670
    karakter. Kısa bir tebliğ (~250 karakter) bundan kısa olduğu için menü
    kazanıyor ve belge atılıyordu — 120 'menu_sayfasi' bu yüzden çıktı.
    Doğrusu: belge her zaman ALT çerçevededir; ana çerçeve sadece kabuktur.
    """
    url = (f"{BASE}/mevzuat?MevzuatNo={rec.get('mevzuatNo')}"
           f"&MevzuatTur={rec.get('mevzuatTur')}"
           f"&MevzuatTertip={rec.get('mevzuatTertip')}")
    try:
        resp = page.goto(url, wait_until="networkidle", timeout=NAV_TIMEOUT)
        if not resp or resp.status >= 400:
            return ""
        page.wait_for_timeout(2000)

        # Önce ALT çerçeveler (belge burada)
        en_uzun = ""
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                t = fr.inner_text("body")
                if len(t.strip()) > len(en_uzun):
                    en_uzun = t
            except Exception:
                pass
        if en_uzun.strip():
            return en_uzun

        # Alt çerçeve yoksa ana çerçeveye düş (kusur() menüyü zaten eler)
        try:
            return page.main_frame.inner_text("body")
        except Exception:
            return ""
    except Exception:
        return ""


# -------------------------------------------------------------- kademe 2: PDF

def pdften_cek(page, rec: dict) -> tuple[str, bytes | None]:
    """PDF'i tarayıcı oturumuyla indir (çerez geçerli -> 403 yok), metne çevir."""
    try:
        import pdfplumber
    except ImportError:
        print("     !! pdfplumber kurulu değil: pip install pdfplumber", flush=True)
        return "", None

    try:
        resp = page.context.request.get(pdf_adresi(rec), timeout=NAV_TIMEOUT)
        if not resp.ok:
            return "", None
        ham = resp.body()
        if not ham.startswith(b"%PDF"):
            return "", None
    except Exception:
        return "", None

    try:
        with pdfplumber.open(io.BytesIO(ham)) as pdf:
            parcalar = [(s.extract_text() or "") for s in pdf.pages]
        return "\n".join(parcalar).strip(), ham
    except Exception:
        return "", ham


# --------------------------------------------------------------------- çekim

def cekim_dongusu(gerek: list[dict], d: dict) -> None:
    from playwright.sync_api import sync_playwright

    sayac = Counter()
    art_arda = 0
    t0 = time.time()
    toplam = len(gerek)

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="tr-TR",
        )
        page = ctx.new_page()

        for i, rec in enumerate(gerek, 1):
            a = anahtar(rec)
            ad = (rec.get("mevAdi") or "")[:30]
            kaynak = "iframe"

            metin = iframeden_cek(page, rec)
            k = kusur(metin) if metin else "bos"

            # Kademe 2: iframe'de gerçek gövde yok -> PDF.
            if k in ("metin_pdfte", "bos", "menu_sayfasi", "iskelet"):
                pdf_metin, ham = pdften_cek(page, rec)
                pdf_k = kusur(pdf_metin) if pdf_metin else "bos"
                if pdf_metin and not pdf_k:
                    metin, k, kaynak = pdf_metin, None, "pdf"
                elif ham:
                    PDF_DIZIN.mkdir(parents=True, exist_ok=True)
                    (PDF_DIZIN / f"{a}.pdf").write_bytes(ham)
                    d["ocr_bekliyor"].add(a)
                    d["cekildi"].discard(a)
                    durum_yaz(d)
                    sayac["ocr"] += 1
                    art_arda = 0
                    print(f"  [{i}/{toplam}] ⧗ {a} — gövde anonim font, OCR "
                          f"kuyruğunda — {ad}", flush=True)
                    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                    continue

            if not metin:
                sayac["hata"] += 1
                art_arda += 1
                print(f"  [{i}/{toplam}] ✗ {a} — çekilemedi", flush=True)
            elif k:
                sayac["yok"] += 1
                art_arda = 0
                d["metni_yok"].add(a)
                print(f"  [{i}/{toplam}] ~ {a} — metin yok ({k}) — {ad}", flush=True)
            else:
                yol = hedef_yol(rec)
                yol.parent.mkdir(parents=True, exist_ok=True)
                yol.write_text(metin, encoding="utf-8")
                meta_yaz(rec, kaynak=f"playwright_{kaynak}")
                d["cekildi"].add(a)
                sayac[kaynak] += 1
                art_arda = 0
                isaret = "✓" if ait_mi(rec, metin) else "?"
                etiket = "PDF" if kaynak == "pdf" else "   "
                print(f"  [{i}/{toplam}] {isaret} {etiket} {a} — {len(metin)} kr "
                      f"— {ad}", flush=True)

            durum_yaz(d)

            if art_arda >= ART_ARDA_HATA_SINIRI:
                print(f"\n  !! Art arda {art_arda} hata — duruyorum (ban olabilir). "
                      f"Checkpoint kaydedildi; aynı komut kaldığı yerden devam eder.",
                      flush=True)
                break

            if i % 20 == 0:
                hiz = i / (time.time() - t0) * 60
                print(f"     ... {i}/{toplam} — {hiz:.1f} belge/dk, "
                      f"kalan ~{(toplam - i) / max(hiz, 0.1):.0f} dk", flush=True)

            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            if i % BATCH == 0:
                mola = random.uniform(PAUSE_MIN, PAUSE_MAX)
                print(f"     ... mola ({mola:.0f} sn) ...", flush=True)
                time.sleep(mola)

        b.close()

    print("=" * 58)
    print(f"  iframe'den çekildi  : {sayac['iframe']}")
    print(f"  PDF'ten çekildi     : {sayac['pdf']}")
    print(f"  OCR kuyruğunda      : {sayac['ocr']}   -> {PDF_DIZIN}")
    print(f"  Metni gerçekten yok : {sayac['yok']}")
    print(f"  Hata                : {sayac['hata']}")
    print("=" * 58)
    if sayac["ocr"]:
        print("Sonraki adım:  python ocr_duzelt.py")


# ---------------------------------------------------------------------- main

def main(argv: list[str]) -> int:
    if not KATALOG.exists():
        print(f"Katalog yok: {KATALOG}")
        return 1

    d = durum_oku()
    kayitlar = katalog_oku()
    print(f"Katalog: {len(kayitlar)} benzersiz belge")
    print("Disk taranıyor...", flush=True)

    kova = sinifla(kayitlar, d)
    rapor_yaz(kova)
    ozet(kayitlar, kova)

    if "--durum" in argv:
        return 0

    if kova["tasinacak"]:
        print("\nTaşınıyor (ağ gerekmez)...")
        tasi(kova)
    if "--tasi" in argv:
        return 0

    gerek = kova["bozuk"] + kova["eksik"]
    if "--test" in argv:
        i = argv.index("--test")
        n = int(argv[i + 1]) if i + 1 < len(argv) else 5
        gerek = gerek[:n]

    if not gerek:
        print("\nÇekilecek belge yok — arşiv tam.")
        return 0

    print(f"\n{len(gerek)} belge çekilecek (iframe -> PDF -> OCR)\n")
    cekim_dongusu(gerek, d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))