#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arama/indeksle.py — Madde bazlı arama indeksi (SQLite + FTS5).

Girdi:  cikti/maddeler.jsonl   (482.051 chunk)
Çıktı:  arama/mevzuat.db       (SQLite, FTS5)

Kullanım:
    python arama/indeksle.py                       # indeksi (yeniden) kur + self-check
    python arama/indeksle.py --ara "meslek mensubu birden fazla şirkete ortak"
    python arama/indeksle.py --ara "..." --n 5 --tur madde --gizle-mulga
    python arama/indeksle.py --ara "..." --acikla   # sorgu genişletmesini de göster

Tasarım ilkeleri (README ile uyumlu):
  1. Türkçe İ/I: trf() ile AÇIKÇA normalize edilir. Ne re.I'ye ne de tokenizer'ın
     kendi case-fold'una güvenilir — parçalamada İ/I tuzağı bunu ispatladı.
  2. Disk tek gerçek kaynak: her çalıştırmada indeks jsonl'den sıfırdan kurulur.
  3. FTS5'te Türkçe stemmer YOK. İki tuzak elle çözülür:
       - casing (İ/I)      → trf() ön-normalizasyon (indeks + sorgu aynı fonksiyon)
       - ek eklenmesi      → ek-eritme (light suffix strip) + prefix eşleşmesi
                             (kullanıcı "kanunda" yazar, metinde "kanun" geçer)
  4. Self-check: yüklenen satır sayısı jsonl ile birebir tutmalı; tutmazsa hata.

Not: Bu betik ilk sürüm. Ek-eritme listesi ve BM25 ağırlıkları GERÇEK çıktıya
bakılarak ayarlanacak (README ilke 1: tahmin etme, veriye bak). --acikla ile
sorgunun nasıl genişletildiğini görüp birlikte kalibre edeceğiz.
"""

import argparse, os
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

# --- Yollar ---------------------------------------------------------------
# Proje kökünü tahmin etmeyiz (README ilke 1): cikti/maddeler.jsonl'i
# betiğin ve çalışma dizininin üstlerine doğru arayıp buluruz. Böylece betik
# ister project/ ister project/arama/ içinde olsun, doğru kökü bulur.
def _kok_bul():
    adaylar = []
    here = Path(__file__).resolve().parent
    cwd = Path.cwd().resolve()
    for baslangic in (here, cwd):
        p = baslangic
        for _ in range(6):  # 6 seviye yukarı yeterli
            adaylar.append(p)
            if p.parent == p:
                break
            p = p.parent
    gorulen = set()
    for a in adaylar:
        if a in gorulen:
            continue
        gorulen.add(a)
        # Kök işareti: build ortamında cikti/maddeler.jsonl, teslim (runtime)
        # ortamında arama/mevzuat.db. İkisinden biri varsa orası köktür.
        if (a / "cikti" / "maddeler.jsonl").exists() or (a / "arama" / "mevzuat.db").exists():
            return a
    # bulunamazsa: betiğin bulunduğu yeri kök say (net hata mesajı için)
    return here


KOK = _kok_bul()
# Girdi/çıktı yolları env ile değiştirilebilir (ör. ayrı bir DB'ye indekslemek için).
JSONL = KOK / os.environ.get("INDEKS_GIRDI", "cikti/maddeler.jsonl")
DB = KOK / os.environ.get("INDEKS_DB", "arama/mevzuat.db")
KATALOG = KOK / "cikti" / "katalog.jsonl"  # belge->ad eşlemesi (varsa; opsiyonel)

BEKLENEN_TOPLAM = 472_737  # nbsp/unicode-boşluk normalizasyonu sonrası (486 belge madde'ye kurtarıldı)


# --- Türkçe normalizasyon -------------------------------------------------
_SAPKA = {"Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u"}


def trf(s: str) -> str:
    """Türkçe-güvenli küçültme: İ→i, I→ı, şapkalı ünlüleri katla, sonra lower().
    - Python'un str.lower()'ı 'İ'yi 'i̇' (birleşik nokta) yapar; onu engelliyoruz.
    - Şapkalı ünlüler (â/î/û) sadeye katlanır: mevzuat "malûl/kâr/tâbi" yazar,
      kullanıcı "malul/kar/tabi" — eşleşmezlerse arama kaçırır (bkz. malulen vakası).
    - ç ş ğ ı ö ü KORUNUR (anlam taşırlar)."""
    for k, v in _SAPKA.items():
        s = s.replace(k, v)
    return s.replace("İ", "i").replace("I", "ı").lower()


# token = Türkçe harf/rakam dizisi. Sayılar önemli ("5510 sayılı", "3568").
_TOKEN_RE = re.compile(r"[0-9a-zçğıöşü]+")


def tokenle(s: str):
    """trf() sonrası tokenlara böl."""
    return _TOKEN_RE.findall(trf(s))


def norm_metin(s: str) -> str:
    """FTS'e girecek normalize, boşlukla ayrılmış token dizisi."""
    return " ".join(tokenle(s or ""))


# --- Ek-eritme (kaba, deterministik Türkçe kök yaklaştırma) ---------------
# Türkçe stemmer değil; sık hâl/iyelik/çoğul eklerini sondan kırpar.
# Amaç: metindeki çekimli biçimle kullanıcının yazdığı kök arasında köprü.
# GERÇEK çıktıya bakarak genişletilecek/kısıtlanacak.
_EKLER = sorted(
    [
        "larını", "lerini", "larının", "lerinin",
        "ları", "leri", "ların", "lerin", "lara", "lere",
        "ndan", "nden", "tan", "ten", "dan", "den",
        "nın", "nin", "nun", "nün", "ının", "inin",
        "sını", "sini", "sının", "sinin",
        "yla", "yle", "yla", "yle", "ile",
        "lar", "ler", "da", "de", "ta", "te",
        "sı", "si", "su", "sü", "yı", "yi", "yu", "yü",
        "ın", "in", "un", "ün", "ı", "i", "u", "ü", "a", "e",
    ],
    key=len,
    reverse=True,
)

# Kırpmayacağımız kısa/işlevsel kelimeler (çoğu FTS'te de zaten gürültü).
_STOP = {
    "ve", "veya", "ya", "da", "de", "ki", "mi", "mı", "mu", "mü",
    "bir", "bu", "şu", "o", "ile", "için", "gibi", "ama", "en",
    "olabilir", "olur", "olan", "midir", "mıdır",
}


def govde_koku(tok: str) -> str:
    """En uzun eşleşen eki bir kez kırp; kalan >= 4 harf olmalı."""
    for ek in _EKLER:
        if tok.endswith(ek) and len(tok) - len(ek) >= 4:
            return tok[: -len(ek)]
    return tok


def fts_sorgusu(q: str):
    """Doğal dil sorgusunu FTS5 MATCH ifadesine çevir.
    - < 3 harf ve stop kelimeler atılır (mi, ve, bir...).
    - Her token için: kökü kırp → 'token* OR kök*' (prefix, çekim yakalar).
    - Terimler OR'lanır; sıralamayı BM25 yapar (AND doğal dilde recall'ı öldürür).
    Döndürür: (match_ifadesi, [açıklama satırları])
    """
    parcalar, aciklama = [], []
    for tok in tokenle(q):
        if len(tok) < 3 or tok in _STOP:
            aciklama.append(f"  {tok:<16} → atlandı (kısa/stop)")
            continue
        kok = govde_koku(tok)
        if kok != tok and len(kok) >= 4:
            parcalar.append(f"({tok}* OR {kok}*)")
            aciklama.append(f"  {tok:<16} → {tok}* OR {kok}*")
        else:
            parcalar.append(f"{tok}*")
            aciklama.append(f"  {tok:<16} → {tok}*")
    return " OR ".join(parcalar), aciklama


# --- Belge adları (belge anahtarı -> (ad, tür adı)) ------------------------
# Kaynak: cikti/katalog.jsonl (tek dosya, hızlı). Anahtar composed:
#   f"{mevzuatNo}_{mevzuatTur}_{mevzuatTertip}"  ör. "4650_7_5"
# Çakışma kopyaları (belge sonunda _GGAAYYYY) aynı kanunun kopyası olduğu için
# tarih ekini kırpıp aynı ada bağlarız.
_TARIH_EKI = re.compile(r"_\d{6,8}$")


def belge_adlari_yukle():
    """belge anahtarı -> (ad, tur_adi). Katalog yoksa boş döner (kritik değil)."""
    ad = {}
    if not KATALOG.exists():
        print(f"  (katalog yok: {KATALOG} — belge adları indekslenmeyecek)",
              file=sys.stderr)
        return ad
    with KATALOG.open(encoding="utf-8") as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                o = json.loads(satir)
            except json.JSONDecodeError:
                continue
            no = o.get("mevzuatNo")
            tur = o.get("mevzuatTur")
            ter = o.get("mevzuatTertip")
            if no is None or tur is None or ter is None:
                continue
            anahtar = f"{no}_{tur}_{ter}"
            isim = o.get("mevAdi") or o.get("ad") or ""
            turad = o.get("mevzuatTurEnumString") or o.get("tur_adi") or ""
            ad[anahtar] = (isim, turad)
    return ad


def belge_ad_bul(adlar: dict, belge: str):
    """belge anahtarını ada çevir; çakışma kopyasıysa tarih ekini kırpıp dene."""
    if belge in adlar:
        return adlar[belge]
    kirpik = _TARIH_EKI.sub("", belge or "")
    return adlar.get(kirpik, ("", ""))


# --- İndeks kurulumu ------------------------------------------------------
def iter_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for n, satir in enumerate(f, 1):
            satir = satir.strip()
            if not satir:
                continue
            try:
                yield json.loads(satir)
            except json.JSONDecodeError as e:
                raise SystemExit(f"HATA: {path} satır {n} bozuk JSON: {e}")


def indeksle():
    print(f"proje kökü: {KOK}")
    if not JSONL.exists():
        raise SystemExit(f"HATA: girdi yok: {JSONL}\n"
                         f"  (cikti/maddeler.jsonl bulunamadı; betiği proje kökünden çalıştır)")
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()  # ilke 2: sıfırdan kur

    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    con.execute("""
        CREATE TABLE chunks(
            rowid    INTEGER PRIMARY KEY,
            chunk_id TEXT UNIQUE,
            belge    TEXT,
            tur      TEXT,
            madde_id TEXT,
            baslik   TEXT,
            mulga    INTEGER,
            durum    TEXT,
            govde    TEXT,
            belge_ad TEXT,
            belge_tur TEXT
        )""")
    # Contentless FTS5: metni burada tutmayız, rowid ile chunks'a bağlarız.
    # prefix='2 3 4' → 2/3/4 harflik önekler indekslenir (prefix sorgusu hızlı).
    # belge_n = üst-belgenin adı + tür adı → alan disambiguasyonu (genel sinyal).
    con.execute("""
        CREATE VIRTUAL TABLE fts USING fts5(
            baslik_n, govde_n, belge_n,
            content='',
            prefix='2 3 4',
            tokenize='unicode61 remove_diacritics 0'
        )""")

    adlar = belge_adlari_yukle()
    print(f"  belge adı yüklendi: {len(adlar):,} anahtar")

    t0 = time.time()
    say = {"madde": 0, "paragraf": 0, "kunye": 0}
    toplam = 0
    ad_bulunan = 0
    cbatch, fbatch = [], []
    BATCH = 5000

    def bosalt():
        con.executemany(
            "INSERT INTO chunks(rowid,chunk_id,belge,tur,madde_id,baslik,mulga,durum,govde,"
            "belge_ad,belge_tur) VALUES(?,?,?,?,?,?,?,?,?,?,?)", cbatch)
        con.executemany(
            "INSERT INTO fts(rowid,baslik_n,govde_n,belge_n) VALUES(?,?,?,?)", fbatch)
        cbatch.clear()
        fbatch.clear()

    ILERLEME = 25_000  # her bu kadar satırda bir ilerleme yaz
    for o in iter_jsonl(JSONL):
        toplam += 1
        rid = toplam
        tur = o.get("tur", "")
        baslik = o.get("baslik") or ""
        govde = o.get("govde") or ""
        mulga = 1 if o.get("mulga") else 0
        durum = o.get("durum")   # madde/paragraf: geçerli|geçersiz|belirsiz; nötr chunk: None
        say[tur] = say.get(tur, 0) + 1
        belge = o.get("belge")
        b_ad, b_tur = belge_ad_bul(adlar, belge)
        if b_ad:
            ad_bulunan += 1
        cbatch.append((rid, o.get("chunk_id"), belge, tur,
                       o.get("madde_id"), baslik, mulga, durum, govde, b_ad, b_tur))
        fbatch.append((rid, norm_metin(baslik), norm_metin(govde),
                       norm_metin(b_ad + " " + b_tur)))
        if len(cbatch) >= BATCH:
            bosalt()
        if toplam % ILERLEME == 0:
            gecen = time.time() - t0
            hiz = toplam / gecen if gecen else 0
            yuzde = 100 * toplam / BEKLENEN_TOPLAM
            # \r ile aynı satırı güncelle (canlı ilerleme)
            print(f"\r  işlenen: {toplam:>7,} / ~{BEKLENEN_TOPLAM:,} "
                  f"(%{yuzde:4.1f})  {hiz:,.0f} satır/sn  {gecen:4.0f}sn",
                  end="", flush=True)
    if cbatch:
        bosalt()
    print()  # ilerleme satırından sonra yeni satıra geç
    print("  FTS optimize ediliyor... (birkaç saniye)")

    con.commit()
    con.execute("INSERT INTO fts(fts) VALUES('optimize')")
    con.commit()
    con.close()

    dt = time.time() - t0
    print(f"\nİndeks kuruldu: {DB}")
    print(f"  süre           : {dt:.1f} sn")
    print(f"  toplam chunk   : {toplam:,}")
    print(f"    → madde      : {say.get('madde', 0):,}")
    print(f"    → paragraf   : {say.get('paragraf', 0):,}")
    print(f"    → künye      : {say.get('kunye', 0):,}")
    orn = 100 * ad_bulunan / toplam if toplam else 0
    print(f"  belge adı eşleşen chunk: {ad_bulunan:,} (%{orn:.1f})")

    # --- Self-check (ilke 4) ---
    if toplam != BEKLENEN_TOPLAM:
        print(f"  ⚠ UYARI: toplam {toplam:,} ≠ beklenen {BEKLENEN_TOPLAM:,} "
              f"(jsonl değişmiş olabilir — kasıtlıysa BEKLENEN_TOPLAM'ı güncelle)")
    else:
        print(f"  ✔ self-check : toplam beklenenle birebir tutuyor")
    return toplam


# --- Arama ----------------------------------------------------------------
def _snippet(govde: str, sorgu_tokenlari, genislik=160):
    """govde içinde ilk eşleşen kök çevresinden kısa bir alıntı üret."""
    n = norm_metin(govde)
    yer = -1
    for tok in sorgu_tokenlari:
        i = n.find(tok[:5])  # köke yakın ilk 5 harfle kaba konum
        if i != -1 and (yer == -1 or i < yer):
            yer = i
    # normalize konumu ham metne birebir taşımak zor; kabaca oransal al
    if yer == -1 or not n:
        parca = govde[:genislik]
    else:
        oran = yer / max(len(n), 1)
        h = int(oran * len(govde))
        bas = max(0, h - genislik // 2)
        parca = govde[bas: bas + genislik]
    return " ".join(parca.split())


# BM25 ağırlıkları: (baslik_n, govde_n, belge_n).
# belge_n (üst-belge adı) alan disambiguasyonu için; gövdeyi ezmeyecek kadar.
# Bu ağırlıklar değerlendirme setiyle kalibre edilecek (tek soruyla değil).
BM25_W = (5.0, 1.0, 2.5)

# Mülga (yürürlükten kalkmış) maddeye YUMUŞAK sıralama cezası. skor negatiftir
# (küçük=iyi); pozitif ceta eklemek onu aşağı iter. Gömmez, sadece yürürlükteki
# eşdeğer cevabın altına düşürür ("göster ama öne alma"). Eval'le kalibre edilir.
MULGA_CEZA = 4.0

_KOLONLAR = ["chunk_id", "belge", "tur", "madde_id", "baslik", "mulga", "durum",
             "govde", "belge_ad", "belge_tur", "skor"]


def ara_ham(sorgu: str, n=10, tur=None, gizle_mulga=False):
    """Yazdırmayan çekirdek. (satirlar, aciklama, match) döner.
    satirlar = dict listesi (_KOLONLAR). degerlendir.py bunu kullanır."""
    if not DB.exists():
        raise SystemExit("HATA: indeks yok. Önce: python arama/indeksle.py")
    match, aciklama = fts_sorgusu(sorgu)
    if not match:
        return [], aciklama, match

    where = ["fts MATCH ?"]
    params = [match]
    if tur:
        where.append("c.tur = ?")
        params.append(tur)
    if gizle_mulga:
        where.append("c.mulga = 0")

    # Sıralama = BM25 + mülga cezası. skor sütunu ham BM25'i (şeffaflık için) taşır;
    # ORDER BY ayrıca mülga cezasını uygular.
    sql = f"""
        SELECT c.chunk_id, c.belge, c.tur, c.madde_id, c.baslik, c.mulga, c.durum, c.govde,
               c.belge_ad, c.belge_tur, bm25(fts, {BM25_W[0]}, {BM25_W[1]}, {BM25_W[2]}) AS skor
        FROM fts JOIN chunks c ON c.rowid = fts.rowid
        WHERE {' AND '.join(where)}
        ORDER BY bm25(fts, {BM25_W[0]}, {BM25_W[1]}, {BM25_W[2]})
                 + CASE WHEN c.mulga THEN {MULGA_CEZA} ELSE 0 END
        LIMIT ?"""
    params.append(n)

    con = sqlite3.connect(DB)
    try:
        rows = con.execute(sql, params).fetchall()
    finally:
        con.close()
    return [dict(zip(_KOLONLAR, r)) for r in rows], aciklama, match


def ara(sorgu: str, n=10, tur=None, gizle_mulga=False, acikla=False):
    satirlar, aciklama, match = ara_ham(sorgu, n=n, tur=tur, gizle_mulga=gizle_mulga)
    if not match:
        print("Sorguda aranabilir terim yok.")
        return []
    if acikla:
        print("Sorgu genişletmesi:")
        for a in aciklama:
            print(a)
        print(f"FTS MATCH: {match}\n")

    stoklar = tokenle(sorgu)
    for i, r in enumerate(satirlar, 1):
        mbayrak = "  ⚠MÜLGA" if r["mulga"] else ""
        basl = f" — {r['baslik']}" if r["baslik"] else ""
        print(f"[{i}] {r['chunk_id']}  ({r['tur']} {r['madde_id']}){basl}{mbayrak}")
        if r["belge_ad"]:
            print(f"    belge: {r['belge']}  {r['belge_ad']}  [{r['belge_tur']}]")
        else:
            print(f"    belge: {r['belge']}")
        print(f"    skor : {r['skor']:.3f}")
        print(f"    …{_snippet(r['govde'], stoklar)}…\n")
    if not satirlar:
        print("Sonuç yok.")
    return satirlar


# --- Başarı sorusu smoke testi -------------------------------------------
BASARI_SORUSU = "Meslek mensubu birden fazla mali müşavirlik şirketine ortak olabilir mi?"


def smoke():
    print("\n" + "=" * 70)
    print("SMOKE TEST — başarı sorusu (ilk 3 sonuç)")
    print(f"Soru: {BASARI_SORUSU}")
    print("=" * 70)
    ara(BASARI_SORUSU, n=3, acikla=True)


# --- CLI ------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Mevzuat madde arama indeksi (SQLite+FTS5)")
    ap.add_argument("--ara", metavar="SORGU", help="arama yap (indekslemez)")
    ap.add_argument("--n", type=int, default=10, help="sonuç sayısı (varsayılan 10)")
    ap.add_argument("--tur", choices=["madde", "paragraf", "kunye", "ek_hukum", "cizelge"],
                    help="tür filtresi")
    ap.add_argument("--gizle-mulga", action="store_true", help="mülga maddeleri gizle")
    ap.add_argument("--acikla", action="store_true", help="sorgu genişletmesini göster")
    ap.add_argument("--smoke", action="store_true", help="başarı sorusu testini çalıştır")
    args = ap.parse_args()

    if args.ara:
        ara(args.ara, n=args.n, tur=args.tur,
            gizle_mulga=args.gizle_mulga, acikla=args.acikla)
    elif args.smoke:
        smoke()
    else:
        indeksle()
        smoke()


if __name__ == "__main__":
    main()