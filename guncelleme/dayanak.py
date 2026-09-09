"""dayanak.py — belgeler arası DAYANAK/ATIF ilişkisini çıkarır.

NEDEN KLASÖR DEĞİL GRAF
    Klasör bir AĞAÇTIR: her dosyanın tek ebeveyni olur. Mevzuat ilişkisi bir
    GRAFTIR — bir belge çoğu zaman BİRDEN FAZLA kanuna dayanır. Örnek:
    10130 sayılı CB Kararı tek cümlede ALTI kanuna atıf yapıyor (1567, 474,
    3283, 4458, 2976, 7498). Klasöre koysak birini seçer, beşini kaybederdik.
    İlişki bir VERİDİR, bir konum değil.

İKİ FARKLI ATIF DİLİ  (ikisi de gerçek metne bakılarak bulundu)
    Yönetmelik/tebliğ (%100):
        "...2547 sayılı Yükseköğretim Kanununun 7 nci maddesine
         DAYANILARAK HAZIRLANMIŞTIR."
    CB Kararları (4.220 belgenin %91'i):
        "...4458 sayılı Gümrük Kanununun 16 ncı maddesi
         GEREĞİNCE KARAR VERİLMİŞTİR."
    İkinci kalıp eklenmeden önce 4.220 CB Kararı graf dışındaydı.

NUMARA KİMLİK DEĞİL  (bu projenin tekrar eden dersi)
    657  -> "DEVLET MEMURLARI KANUNU" (tertip 5)
         -> "HARİTA GENEL MÜDÜRLÜĞÜ..." (tertip 3)
    3350 -> hem bir Kanun hem bir Cumhurbaşkanı Kararı olabilir

    DÖRT KADEMELİ EŞLEŞTİRME (azalan güvenle):
      1. numara        -> o numarada tek aday                        [kanıt]
      2. numara+tur    -> metin türü söylüyor ("...Cumhurbaşkanı Kararı") [kanıt]
      3. numara+ad     -> metin adı söylüyor ("...Gümrük Kanununun")  [kanıt]
      4. numara+tertip -> hiçbiri yok; en yüksek tertibi seç          [ÇIKARIM]
    Kademe 4 guvenilir=false ile işaretlenir. Tahminle kanıt karışmaz.

TÜR KODLARI KATALOGDAN DOĞRULANDI (varsayılmadı)
    1=Kanun  19=CB Kararnamesi  4=KHK  2=Tüzük  17=İç Tüzük  0=Osmanlı Kanunu
    20=CB Kararı (hem kaynak hem hedef: birbirlerini değiştiriyorlar)
    3/5/6/7/8/21=Yönetmelik  9=Tebliğ  22=CB Genelgesi
    Dikkat: tur=2 TÜZÜK'tür, CB Kararnamesi DEĞİL (o tur=19).

ÇIKTI
    cikti/iliskiler.jsonl        her satır bir kenar
    cikti/rapor/dayanak_ozet.json

KULLANIM
    python dayanak.py --test 20
    python dayanak.py
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from tamamla import BELGELER, CIKTI, RAPOR, anahtar, katalog_oku

CIKTI_DOSYA = CIKTI / "iliskiler.jsonl"
OZET_DOSYA = RAPOR / "dayanak_ozet.json"

# İki dil birden. CB Kararları "dayanılarak" demiyor, "gereğince karar
# verilmiştir" diyor — bu kalıp olmadan 4.220 belge graf dışında kalıyordu.
DAYANAK_FIIL = re.compile(
    r"dayan[ıi]larak\s+haz[ıi]rlan|dayan[ıi]larak\s+d[üu]zenlen|"
    r"dayan[ıi]larak\s+y[üu]r[üu]rl[üu][ğg]e|"
    r"gere[ğg]ince\s+karar\s+veril|uyar[ıi]nca\s+karar\s+veril|"
    r"h[üu]k[üu]mleri\s+gere[ğg]ince|g[öo]re\s+karar\s+veril",
    re.IGNORECASE)

# TEK HANE DAHİL: "1 sayılı", "4 sayılı" -> en çok atıf alan iki CBK
NUMARA_KALIBI = re.compile(r"(\d{1,5})\s*say[ıi]l[ıi]\s*(.{0,90})",
                           re.IGNORECASE | re.S)

AD_KALIBI = re.compile(
    r"((?:[A-ZÇĞİÖŞÜ][\wçğıöşüÇĞİÖŞÜ]+\s+){1,6}"
    r"(?:Kanun|Kararname)\w{0,8})\b")

# Metin belge TÜRÜNÜ söylüyor. Addan çok daha net bir sinyal.
# SIRA ÖNEMLİ: "Kanun Hükmünde Kararname" ve "Cumhurbaşkanı Kararı" önce
# eşleşmeli, yoksa alttaki genel "Kanun" kalıbı yanlış yakalar.
TUR_IPUCU: list[tuple[re.Pattern, set[int]]] = [
    (re.compile(r"Kanun\s+H[üu]km[üu]nde\s+Kararname", re.IGNORECASE), {4}),
    (re.compile(r"Cumhurba[şs]kanl[ıi][ğg][ıi]\s+Kararname|Kararname",
                re.IGNORECASE), {19, 4}),
    (re.compile(r"Cumhurba[şs]kan[ıi]\s+Karar[ıi]", re.IGNORECASE), {20}),
    (re.compile(r"T[üu]z[üu][ğgk]", re.IGNORECASE), {2, 6, 17}),
    (re.compile(r"Kanun", re.IGNORECASE), {1, 0}),
]

# CB Kararları (20) HEM kaynak HEM hedef: birbirlerine atıf yapıyorlar
# ("...3350 sayılı Cumhurbaşkanı Kararı ile yürürlüğe konulan İthalat Rejimi
#   Kararına ekli...")
KAYNAK_TURLER = {3, 5, 6, 7, 8, 9, 20, 21, 22}
HEDEF_TURLER = {0, 1, 2, 4, 17, 19, 20}

PENCERE = 900        # CB Kararları'nda atıf cümlesi uzun (bir kararda 6 kanun)
GUVENILIR_YONTEMLER = {"numara", "numara+tur", "numara+ad", "ad"}


def sadelestir(metin: str) -> str:
    """\xa0 (kırılmaz boşluk) -> normal boşluk; bitişik harfleri aç."""
    return unicodedata.normalize("NFKC", metin).replace("\xa0", " ")


def ad_sade(ad: str) -> str:
    """Karşılaştırma formu: aksan/noktalama at, büyüt, çekim ekini kırp.

    "Gümrük Kanununun" -> GUMRUKKANUN
    "GÜMRÜK KANUNU"    -> GUMRUKKANUN     (eşleşir)
    """
    s = unicodedata.normalize("NFKD", ad or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]", "", s).upper()
    s = re.sub(r"(KANUN|KARARNAME|KARAR)\w{0,8}$", r"\1", s)
    return s


def dayanak_pencereleri(metin: str) -> list[str]:
    m = sadelestir(metin)
    return [m[max(0, e.start() - PENCERE):e.end()]
            for e in DAYANAK_FIIL.finditer(m)]


def tur_ipucu(parca: str) -> set[int] | None:
    """Numaradan sonraki metindeki tür ifadesinden aday türleri daralt."""
    for kalip, turler in TUR_IPUCU:
        if kalip.search(parca):
            return turler
    return None


def ad_ortusuyor(parca: str, katalog_adi: str) -> bool:
    a, b = ad_sade(parca), ad_sade(katalog_adi)
    if not a or len(b) < 8:
        return False
    return b[:16] in a or a[:16] in b


def tertip_no(rec: dict) -> int:
    try:
        return int(str(rec.get("mevzuatTertip") or 0))
    except ValueError:
        return 0


def main(argv: list[str]) -> int:
    kayitlar = katalog_oku()

    # Numara -> ADAY LİSTESİ (tek değer DEĞİL)
    no_adaylari: dict[str, list[dict]] = defaultdict(list)
    ad_to_hedef: dict[str, str] = {}
    hedef_ad: dict[str, str] = {}
    hedef_tur: dict[str, str] = {}
    for r in kayitlar:
        if r.get("mevzuatTur") not in HEDEF_TURLER:
            continue
        a = anahtar(r)
        no_adaylari[str(r.get("mevzuatNo"))].append(r)
        hedef_ad[a] = r.get("mevAdi", "")
        hedef_tur[a] = r.get("mevzuatTurEnumString", "")
        sad = ad_sade(r.get("mevAdi", ""))
        if len(sad) > 10:
            ad_to_hedef.setdefault(sad, a)

    coklu = sum(1 for v in no_adaylari.values() if len(v) > 1)
    print(f"Hedef: {len(no_adaylari)} numara "
          f"({coklu} tanesi birden fazla belgeye ait)")

    dosya_indeks: dict[str, Path] = {}
    for f in BELGELER.glob("*/*.txt"):
        p = f.stem.split("_")
        if len(p) >= 4 and p[1].isdigit() and p[2].isdigit():
            dosya_indeks.setdefault(f"{p[0]}_{p[1]}_{p[2]}", f)
            if len(p) >= 5 and p[3].isdigit() and len(p[3]) == 8:
                dosya_indeks[f"{p[0]}_{p[1]}_{p[2]}_{p[3]}"] = f

    kaynaklar = [r for r in kayitlar
                 if r.get("mevzuatTur") in KAYNAK_TURLER
                 and anahtar(r) in dosya_indeks]
    print(f"Kaynak belge: {len(kaynaklar)}")

    test = "--test" in argv
    if test:
        i = argv.index("--test")
        kaynaklar = kaynaklar[:int(argv[i + 1]) if i + 1 < len(argv) else 20]

    kenarlar, sayac = [], Counter()
    kaynak_tur_sayac = Counter()

    for rec in kaynaklar:
        a = anahtar(rec)
        try:
            metin = dosya_indeks[a].read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        pencereler = dayanak_pencereleri(metin)
        if not pencereler:
            sayac["dayanak_cumlesi_yok"] += 1
            continue

        bulunan: dict[str, tuple[str, str]] = {}
        for parca in pencereler:
            alinti = parca[-180:].strip()

            # --- Yol 1: numara (tür / ad / tertip ile doğrulanır) ---
            for no, sonrasi in NUMARA_KALIBI.findall(parca):
                adaylar = no_adaylari.get(no, [])
                if not adaylar:
                    continue

                # Kademe 2: metin türü söylüyor mu?
                ipucu = tur_ipucu(sonrasi)
                if ipucu:
                    daraltilmis = [c for c in adaylar
                                   if c.get("mevzuatTur") in ipucu]
                    if daraltilmis:
                        adaylar = daraltilmis

                if len(adaylar) == 1:
                    secim = adaylar[0]
                    yontem = "numara+tur" if ipucu else "numara"
                else:
                    # Kademe 3: metin adı söylüyor mu?
                    eslesen = [c for c in adaylar
                               if ad_ortusuyor(sonrasi, c.get("mevAdi", ""))]
                    if len(eslesen) == 1:
                        secim, yontem = eslesen[0], "numara+ad"
                    else:
                        # Kademe 4: ÇIKARIM (yürürlükteki metin genelde en
                        # yüksek tertipte; 3. Tertip eski/mülga)
                        secim = max(adaylar, key=tertip_no)
                        yontem = "numara+tertip"

                h = anahtar(secim)
                if h != a:
                    bulunan.setdefault(h, (yontem, alinti))

            # --- Yol 2: numarasız ad ("Vergi Usul Kanununun") ---
            for ad in AD_KALIBI.findall(parca):
                h = ad_to_hedef.get(ad_sade(ad))
                if h and h != a:
                    bulunan.setdefault(h, ("ad", alinti))

        if not bulunan:
            sayac["eslesme_yok"] += 1
            continue

        sayac["baglandi"] += 1
        kaynak_tur_sayac[rec.get("mevzuatTurEnumString", "?")] += 1
        for h, (yontem, alinti) in bulunan.items():
            sayac[f"yontem_{yontem}"] += 1
            kenarlar.append({
                "kaynak": a,
                "kaynak_ad": (rec.get("mevAdi") or "")[:70],
                "kaynak_tur": rec.get("mevzuatTurEnumString", ""),
                "hedef": h,
                "hedef_ad": hedef_ad.get(h, "")[:70],
                "hedef_tur": hedef_tur.get(h, ""),
                "iliski": "dayanak",
                "yontem": yontem,
                "guvenilir": yontem in GUVENILIR_YONTEMLER,
                "alinti": alinti,
            })

        if test:
            print("=" * 62)
            print(f"{a} [{rec.get('mevzuatTurEnumString','')[:18]}] "
                  f"{(rec.get('mevAdi') or '')[:38]}")
            for h, (yontem, _) in bulunan.items():
                isaret = "~>" if yontem == "numara+tertip" else "->"
                print(f"  {isaret} {h:14} [{yontem}] {hedef_ad.get(h, '')[:36]}")

    if not test:
        with CIKTI_DOSYA.open("w", encoding="utf-8") as f:
            for k in kenarlar:
                f.write(json.dumps(k, ensure_ascii=False) + "\n")

        gelen = Counter(k["hedef"] for k in kenarlar)
        OZET_DOSYA.parent.mkdir(parents=True, exist_ok=True)
        OZET_DOSYA.write_text(json.dumps({
            "kaynak_belge": len(kaynaklar),
            "baglanan": sayac["baglandi"],
            "kenar": len(kenarlar),
            "guvenilir_kenar": sum(1 for k in kenarlar if k["guvenilir"]),
            "cikarimla_kurulan": sayac["yontem_numara+tertip"],
            "baglanan_tur_dagilimi": dict(kaynak_tur_sayac.most_common()),
            "en_cok_dayanak_alan": [
                {"anahtar": h, "ad": hedef_ad.get(h, ""),
                 "tur": hedef_tur.get(h, ""), "sayi": n}
                for h, n in gelen.most_common(25)],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    t, b = len(kaynaklar), sayac["baglandi"]
    guv = sum(1 for k in kenarlar if k["guvenilir"])
    print("=" * 62)
    print(f"  Kaynak belge           : {t}")
    print(f"  Dayanak bulundu        : {b}  ({b / max(t, 1) * 100:.1f}%)")
    print(f"  Dayanak cümlesi yok    : {sayac['dayanak_cumlesi_yok']}")
    print(f"  Cümle var, eşleşme yok : {sayac['eslesme_yok']}")
    print(f"  Toplam kenar           : {len(kenarlar)}")
    print(f"    numara (tek aday)    : {sayac['yontem_numara']}")
    print(f"    numara + TÜR         : {sayac['yontem_numara+tur']}")
    print(f"    numara + ad          : {sayac['yontem_numara+ad']}")
    print(f"    sadece ad (numarasız): {sayac['yontem_ad']}")
    print(f"    numara + tertip (~)  : {sayac['yontem_numara+tertip']}"
          f"   <- ÇIKARIM")
    print(f"  GÜVENİLİR KENAR        : {guv} / {len(kenarlar)}"
          f"  ({guv / max(len(kenarlar), 1) * 100:.1f}%)")
    if not test and kaynak_tur_sayac:
        print("  Bağlanan belgelerin türü:")
        for tur, n in kaynak_tur_sayac.most_common(8):
            print(f"    {n:6d}  {tur}")
    print("=" * 62)
    if not test:
        print(f"  -> {CIKTI_DOSYA}")
        print(f"  -> {OZET_DOSYA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))