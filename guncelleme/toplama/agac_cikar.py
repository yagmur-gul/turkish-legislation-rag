"""Mevzuat .txt dosyalarını AĞAÇ yapısına ayrıştırır.

Her belgeyi şu yapıya çevirir:
    belge
    ├── künye (no, ad, tür, RG tarih/sayı)
    └── bölümler[]
        └── maddeler[]  (no, başlık, metin, fıkralar)

Çıktı: cikti/agac/<no>.json

Neden gerekli:
  - RAG için doğru bölümleme (madde madde, rastgele kesme yok)
  - Referans gösterme ("İklim Kanunu, MADDE 5")
  - Madde bazlı arama

Kullanım:
    python agac_cikar.py --test 5      # 5 belgeyi dene, ekrana bas
    python agac_cikar.py               # hepsini ayrıştır -> cikti/agac/
    python agac_cikar.py --status
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent.parent   # toplama/ -> guncelleme/ -> kök
CIKTI = KOK / "cikti"
BELGELER = CIKTI / "belgeler"
METADATA = CIKTI / "metadata"
AGAC = CIKTI / "agac"

# ── Kalıplar ────────────────────────────────────────────────────────────────
# MADDE 1-  /  Madde 1-  /  Madde 1 –  /  EK MADDE 2-  /  GEÇİCİ MADDE 1-
MADDE_RE = re.compile(
    r"^\s*((?:EK|GEÇİCİ|Ek|Geçici)\s+)?(MADDE|Madde)\s*(\d+)\s*[-–—]?\s*(.*)$"
)
# BİRİNCİ BÖLÜM / İKİNCİ KISIM
BOLUM_RE = re.compile(
    r"^\s*((?:BİR|İKİ|ÜÇ|DÖRD|BEŞ|ALTI|YEDİ|SEKİZ|DOKUZ|ON)\w*)\s+(BÖLÜM|KISIM)\s*$",
    re.IGNORECASE,
)
# (1) (2) fıkra
FIKRA_RE = re.compile(r"^\s*\((\d+)\)\s*(.*)$")
# Künye satırı: "Kanun Numarası : 6706"
KUNYE_RE = re.compile(r"^\s*[\wÇĞİÖŞÜçğıöşü\s]+\s*:\s*.+$")
# Mülga (yürürlükten kalkmış)
MULGA_RE = re.compile(r"\(\s*Mülga", re.IGNORECASE)
# Kalıp madde metinleri (ayırt edicilik taşımaz)
KALIP_METIN_RE = re.compile(
    r"(yürürlü[ğg]e girer|hükümlerini.{0,60}yürütür|hükümleri.{0,40}yürütür)",
    re.IGNORECASE,
)
KALIP_BASLIK = {"yürürlük", "yürütme", "yürürlük ve yürütme"}
# Bent/liste başlangıcı: "a)", "1 -", "G - "
BENT_RE = re.compile(r"^\s*(?:\d+|[a-zA-ZçğıöşüÇĞİÖŞÜ])\s*[\)\-–—]")

TR_BUYUK = "ABCÇDEFGĞHIİJKLMNOÖPRSŞTUÜVYZ"


def _baslik_mi(satir: str) -> bool:
    """Bu satır gerçekten bir madde başlığı mı, yoksa metin parçası mı?

    Gerçek başlık: kısa, büyük harfle başlar, cümle sonu noktası yok.
    Metin parçası: uzun, küçük harfle başlar (satır kaydırması) veya nokta ile biter.
    """
    s = satir.strip()
    if not s or len(s) > 70:
        return False
    if len(s.split()) > 10:
        return False
    if s[0] not in TR_BUYUK:          # küçük harfle başlıyorsa: devam satırı
        return False
    if s.endswith("."):               # cümle sonu: metin parçası
        return False
    if BENT_RE.match(s):              # "a)", "1 -" gibi liste öğesi
        return False
    if _kunye_mi(s):                  # "Yayımlandığı Düstur : ..." gibi
        return False
    return True


def _kategori(baslik: str, metin: str, mulga: bool) -> str:
    """icerik | kalip | mulga"""
    if mulga:
        return "mulga"
    if baslik.strip().lower().rstrip(":") in KALIP_BASLIK:
        return "kalip"
    if len(metin) < 200 and KALIP_METIN_RE.search(metin):
        return "kalip"
    return "icerik"


def _kunye_mi(satir: str) -> bool:
    """Başlıktan sonraki 'Kanun Numarası : ...' türü künye satırı mı?"""
    if not KUNYE_RE.match(satir):
        return False
    anahtarlar = ("numarası", "tarihi", "yayımlandığı", "kabul", "dayandığı",
                  "yetki", "düstur", "no", "kararının")
    return any(a in satir.lower() for a in anahtarlar)


def ayristir(metin: str, kunye: dict) -> dict:
    satirlar = [s.rstrip() for s in metin.splitlines()]

    baslik_satirlari: list[str] = []
    kunye_satirlari: list[str] = []
    bolumler: list[dict] = []
    aktif_bolum: dict | None = None
    aktif_madde: dict | None = None
    onceki_satir = ""
    yapisal_basladi = False

    def bolum_ac(ad: str) -> dict:
        b = {"baslik": ad, "maddeler": []}
        bolumler.append(b)
        return b

    for satir in satirlar:
        s = satir.strip()
        if not s:
            onceki_satir = ""
            continue

        # 1) BÖLÜM / KISIM
        m_bolum = BOLUM_RE.match(s)
        if m_bolum:
            yapisal_basladi = True
            aktif_madde = None
            aktif_bolum = bolum_ac(s)
            onceki_satir = ""
            continue

        # 2) MADDE
        m_madde = MADDE_RE.match(s)
        if m_madde:
            yapisal_basladi = True
            on_ek = (m_madde.group(1) or "").strip()
            no = int(m_madde.group(3))
            ilk_metin = m_madde.group(4).strip()

            if aktif_bolum is None:
                aktif_bolum = bolum_ac("")  # bölümsüz belge

            # Önceki satır GERÇEKTEN başlık mı? Değilse madde başlıksızdır.
            baslik = onceki_satir.strip(" :") if _baslik_mi(onceki_satir) else ""

            # Başlık ise, önceki maddenin metnine de eklenmişti; oradan çıkar.
            if baslik and aktif_madde is not None:
                if aktif_madde["metin"].endswith(onceki_satir):
                    aktif_madde["metin"] = (
                        aktif_madde["metin"][: -len(onceki_satir)].rstrip("\n").rstrip()
                    )

            mulga = bool(MULGA_RE.search(ilk_metin))
            aktif_madde = {
                "no": no,
                "tip": on_ek.upper() if on_ek else "MADDE",
                "baslik": baslik,
                "metin": ilk_metin,
                "mulga": mulga,
                "kategori": "mulga" if mulga else "icerik",  # sonda güncellenecek
            }
            aktif_bolum["maddeler"].append(aktif_madde)
            onceki_satir = ""
            continue

        # 3) Madde içeriği (fıkra, bent, devam satırı)
        if aktif_madde is not None:
            aktif_madde["metin"] += ("\n" + s if aktif_madde["metin"] else s)
            onceki_satir = s
            continue

        # 4) Henüz yapı başlamadı: başlık ya da künye
        if not yapisal_basladi:
            if _kunye_mi(s):
                kunye_satirlari.append(s)
            elif not kunye_satirlari:      # künye başlamadan önceki satırlar = başlık
                baslik_satirlari.append(s)
            else:
                kunye_satirlari.append(s)

        onceki_satir = s

    # Madde bulunamadıysa: belge düz metin (bazı tebliğler böyle)
    duz_metin = not any(b["maddeler"] for b in bolumler)

    # Kategori ataması (metin tamamlandıktan sonra)
    sayim = {"icerik": 0, "kalip": 0, "mulga": 0}
    for b in bolumler:
        for m in b["maddeler"]:
            m["kategori"] = _kategori(m["baslik"], m["metin"], m["mulga"])
            sayim[m["kategori"]] += 1

    return {
        "no": kunye.get("mevzuatNo") or kunye.get("no", ""),
        "ad": kunye.get("ad") or " ".join(baslik_satirlari)[:200],
        "tur": kunye.get("tur", ""),
        "rg_tarih": kunye.get("rg_tarih", ""),
        "rg_sayi": kunye.get("rg_sayi", ""),
        "kunye_satirlari": kunye_satirlari,
        "duz_metin": duz_metin,
        "tam_metin": metin if duz_metin else "",
        "bolumler": [b for b in bolumler if b["maddeler"]],
        "madde_sayisi": sum(len(b["maddeler"]) for b in bolumler),
        "sayim": sayim,
    }


def meta_oku(no: str) -> dict:
    f = METADATA / f"{no}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def tum_dosyalar() -> list[Path]:
    return sorted(BELGELER.glob("*/*.txt"))


def main(argv: list[str]) -> int:
    dosyalar = tum_dosyalar()

    if "--status" in argv:
        cikan = len(list(AGAC.glob("*.json"))) if AGAC.exists() else 0
        print("=" * 55)
        print("AĞAÇ ÇIKARMA DURUMU")
        print(f"  Belge (.txt)  : {len(dosyalar)}")
        print(f"  Ayrıştırılmış : {cikan}")
        print(f"  Kalan         : {len(dosyalar) - cikan}")
        print("=" * 55)
        return 0

    test = None
    if "--test" in argv:
        i = argv.index("--test")
        test = int(argv[i + 1]) if i + 1 < len(argv) else 5
        dosyalar = dosyalar[:test]

    AGAC.mkdir(parents=True, exist_ok=True)
    ok = duz = 0
    genel = {"icerik": 0, "kalip": 0, "mulga": 0}
    basliksiz = 0

    for i, f in enumerate(dosyalar, 1):
        no = f.stem.split("_", 1)[0]
        metin = f.read_text(encoding="utf-8", errors="ignore")
        agac = ayristir(metin, meta_oku(no))

        for k in genel:
            genel[k] += agac["sayim"][k]
        for b in agac["bolumler"]:
            for m in b["maddeler"]:
                if not m["baslik"]:
                    basliksiz += 1

        if test:
            print(f"\n{'='*60}")
            print(f"DOSYA: {f.name[:55]}")
            print(f"  ad    : {agac['ad'][:55]}")
            print(f"  madde : {agac['madde_sayisi']}  "
                  f"(içerik {agac['sayim']['icerik']}, "
                  f"kalıp {agac['sayim']['kalip']}, "
                  f"mülga {agac['sayim']['mulga']})")
            print(f"  düz metin mi: {agac['duz_metin']}")
            for b in agac["bolumler"][:2]:
                print(f"  ── {b['baslik'] or '(bölümsüz)'}")
                for m in b["maddeler"][:4]:
                    ilk = m["metin"][:55].replace("\n", " ")
                    bs = f"'{m['baslik']}'" if m["baslik"] else "(başlıksız)"
                    print(f"       {m['tip']} {m['no']} [{m['kategori']}] {bs}")
                    print(f"          {ilk}...")
        else:
            (AGAC / f"{no}.json").write_text(
                json.dumps(agac, ensure_ascii=False, indent=1), encoding="utf-8")

        ok += 1
        if agac["duz_metin"]:
            duz += 1
        if not test and i % 2000 == 0:
            print(f"  {i}/{len(dosyalar)} ayrıştırıldı...")

    toplam_madde = sum(genel.values())
    print(f"\n{'='*55}")
    print(f"Belge         : {ok}")
    print(f"  madde yapılı: {ok - duz}")
    print(f"  düz metin   : {duz}")
    print(f"Toplam madde  : {toplam_madde}")
    print(f"  içerik      : {genel['icerik']}  <- indekslenecek")
    print(f"  kalıp       : {genel['kalip']}  <- saklanır, indekslenmez")
    print(f"  mülga       : {genel['mulga']}  <- saklanır, varsayılan aramada gizli")
    print(f"  başlıksız   : {basliksiz}")
    if not test:
        print(f"\nÇıktı: cikti/agac/")
    print("=" * 55)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))