#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py - Mevzuat Arama web arayuzu (FastAPI + uvicorn).

Arama (/) + belge detay sayfasi (/belge/{belge}). motor.py'deki AramaMotoru'yu
bir kez yukler; detay sayfasi arama/mevzuat.db'yi dogrudan okur (yeniden
parcalamaz - chunks tablosu rowid sirasinda madde sirasini tutar).

Calistirma (proje kokunde):
    pip install fastapi "uvicorn[standard]"
    python app.py
Sonra: http://127.0.0.1:8000   (deneme: /docs)
"""

import os
# NOT: çevrimdışı model ayarları (HF_HUB_OFFLINE vb.) konfig.py'de tek yerden
# yapılır; konfig aşağıda motor import'undan ÖNCE yüklenir.

import html
import re
import sqlite3
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

KOK = Path(__file__).resolve().parent
sys.path.insert(0, str(KOK / "arama"))
DB_YOL = KOK / "arama" / "mevzuat.db"

# Değişiklik/iptal kayıtları — degisiklik.py üretir. Madde başına {tur, tarih, arac,
# kaynak}. Arayüzdeki 🔴 MÜLGA / 🔵 DEĞİŞİK rozetlerinin kaynağı. Yoksa boş: özellik
# sessizce kapanır. Bu bir GÖSTERİM katmanı; corpus/indeks DEĞİŞMEZ.
import json as _json
DEG_YOL = KOK / "cikti" / "degisiklikler.jsonl"
DEGISIKLIK = {}
try:
    _n = 0
    for _satir in DEG_YOL.read_text(encoding="utf-8").splitlines():
        _satir = _satir.strip()
        if not _satir:
            continue
        _o = _json.loads(_satir)
        DEGISIKLIK.setdefault(_o["belge"], {})[_o["madde_id"]] = _o["kayitlar"]
        _n += 1
    print(f"(değişiklik kaydı yüklendi: {_n} madde / {len(DEGISIKLIK)} belge)", flush=True)
except Exception as e:
    DEGISIKLIK = {}
    print(f"(uyari: degisiklikler.jsonl yüklenemedi, notlar gösterilmeyecek: {e})",
          flush=True)


import konfig  # noqa: F401  — çalışma-zamanı env ayarları (motor import'undan ÖNCE)
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from motor import AramaMotoru
import uret            # üretim: kaynak_metni, SISTEM2, ATIF_D, _ana
import orh             # OpenRouter: cagir_stream

# Üretim modeli — ölçümle kilitlendi (deepseek fp8, bkz mevzuat-rag-yigin).
URETIM_MODEL = "deepseek/deepseek-chat"
uret._SAGLAYICI = {"sort": "throughput"}
URETIM_N = 30           # üretime verilen madde sayısı

motor = AramaMotoru(reranker=os.environ.get("RERANK") == "1")  # VARSAYILAN no-rerank (hibrit top-30, ~15-20sn); RERANK=1 → reranker aç (bge-reranker-base, ~40-75sn, ~2-4 puan doğruluk)
_ara_kilit = threading.Lock()      # tek GPU/model: aramalari sirala
_yuk_kilit = threading.Lock()
_yuklendi = False                  # arama modelleri (embedding + reranker) yuklendi mi?
SICAK_TUT_SN = 120                 # bu kadar saniyede bir isinma sorgusu


def _motoru_hazirla():
    """Arama modellerini İLK İHTİYAÇTA yükle (tembel). Böylece madde/değişiklik
    sayfalarında gezinmek modelleri yüklemez -> PC hafif kalır. Yalnız ilk ARAMA
    modelleri (bir kerelik ~1-2 dk) yükler."""
    global _yuklendi
    if _yuklendi:
        return
    with _yuk_kilit:
        if not _yuklendi:
            print("Arama modelleri yukleniyor (ilk arama, bir kerelik)...", flush=True)
            motor.hazirla()
            _yuklendi = True


def _indeks_hazirla():
    """chunks.belge uzerine indeks (bir kerelik).

    Indeks yoksa detay sayfasindaki 'WHERE belge=?' 1.17GB'lik tabloyu bastan
    sona tarar: hem sayfa gec acilir hem de okunan 1GB, vektor matrisini RAM
    onbelleginden atarak SONRAKI aramayi yavaslatir. Indeks ikisini de bitirir.
    """
    try:
        con = sqlite3.connect(str(DB_YOL))
        con.execute("CREATE INDEX IF NOT EXISTS ix_chunks_belge ON chunks(belge)")
        con.commit()
        con.close()
    except Exception as e:
        print(f"(uyari: belge indeksi kurulamadi: {e})", flush=True)


def guvenli_ara(sorgu, n=10, gizle_mulga=False):
    _motoru_hazirla()                  # tembel: ilk aramada modeller yüklenir
    with _ara_kilit:
        return motor.ara(sorgu, n=n, gizle_mulga=gizle_mulga)


def _sicak_tut():
    while True:
        time.sleep(SICAK_TUT_SN)
        if not _yuklendi:              # henüz arama yapılmadıysa modeli yükleme (ısıtma yok)
            continue
        try:
            guvenli_ara("isinma", n=1)
        except Exception:
            pass


def _onyukle():
    """Modelleri BAŞLANGIÇTA, ARKA PLANDA yükle + ısıt. Sunucu hemen yanıt verir;
    ilk gerçek arama beklemez (yükleme çalışırken arka planda tamamlanır). Erken
    gelen arama olursa _motoru_hazirla kilidi onu yükleme bitene dek bekletir."""
    try:
        _motoru_hazirla()
        guvenli_ara("isinma", n=1)     # CUDA/kernel ısıtması -> ilk arama hızlı
        print("Arama modelleri hazir (arka plan yuklemesi bitti).", flush=True)
    except Exception as e:
        print(f"(uyari: arka plan model yuklemesi: {e})", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Belge indeksi kontrol ediliyor...", flush=True)
    _indeks_hazirla()
    # Modelleri başta arka planda yükle (ilk aramayı bekletmemek için).
    threading.Thread(target=_onyukle, daemon=True).start()
    # "Sıcak tutma" her SICAK_TUT_SN'de bir tam arama çalıştırır (CPU'yu meşgul eder).
    # Kısıtlı RAM/GPU'da gereksiz yük; VARSAYILAN KAPALI. Açmak için APP_SICAKTUT=1.
    if os.environ.get("APP_SICAKTUT") == "1":
        threading.Thread(target=_sicak_tut, daemon=True).start()
    print("Sunucu hazir (arama modelleri ARKA PLANDA yukleniyor) -> "
          "http://127.0.0.1:8000   (Ctrl+C ile durdur)", flush=True)
    yield


app = FastAPI(title="Mevzuat Arama", lifespan=lifespan)


def govde_temizle(govde, baslik):
    t = govde or ""
    ilk, _, kalan = t.partition("\n")
    if baslik and ilk.strip() == (baslik or "").strip():
        t = kalan
    return re.sub(r"\s+", " ", t).strip()


def bozuk_mu(metin):
    if not metin or len(metin) < 40:
        return False
    # Kısa mülga/değişiklik künyesi (sayı-ağırlıklı ama TABLO DEĞİL) -> işaretleme
    if re.search(r"\(\s*(?:[Mm][üu]lga|[Dd]eğişik|[Ee]k|[İi]ptal)", metin) and len(metin) < 220:
        return False
    harf_oran = sum(c.isalpha() for c in metin) / len(metin)
    artefakt = bool(re.search(r"(olol|lolo|\|\s*o\s*\||\d{6,})", metin))
    # düşük harf oranı ancak UZUN metinde tabloya işarettir (tablolar uzundur)
    return artefakt or (harf_oran < 0.45 and len(metin) > 220)


def ozetle(metin, n=280):
    return metin if len(metin) <= n else metin[:n].rstrip() + "…"


def kopru_terimleri(sorgu):
    try:
        ekle, _tetik = motor.kopru_bilgi(sorgu)
    except Exception:
        return []
    if not ekle:
        return []
    if isinstance(ekle, (list, tuple, set)):
        return [str(x) for x in ekle]
    return [str(ekle)]


@app.get("/api/ara")
def api_ara(q: str = "", gizle_mulga: bool = False):
    q = (q or "").strip()
    if not q:
        return {"sorgu": q, "sonuclar": [], "kopru": []}
    try:
        ham = guvenli_ara(q, n=10, gizle_mulga=gizle_mulga)
    except Exception as e:
        return JSONResponse(
            {"sorgu": q, "sonuclar": [], "kopru": [], "hata": f"Arama hatasi: {e}"},
            status_code=500,
        )
    sonuclar = []
    for r in ham:
        temiz = govde_temizle(r.get("govde", ""), r.get("baslik", ""))
        bozuk = bozuk_mu(temiz)
        sonuclar.append({
            "baslik": r.get("baslik") or (f"Madde {r.get('madde_id')}" if r.get("madde_id") else "-"),
            "belge_ad": r.get("belge_ad", ""),
            "belge_tur": r.get("belge_tur", ""),
            "tur": r.get("tur", ""),
            "mulga": bool(r.get("mulga")),
            "kaynak": r.get("chunk_id", ""),
            "ozet": "" if bozuk else ozetle(temiz),
            "bozuk": bozuk,
        })
    return {"sorgu": q, "sonuclar": sonuclar, "kopru": kopru_terimleri(q)}


# ------------------------------------------------------------- belge detayi

def _govde_bas_kirp(govde, baslik):
    """Govdenin ilk satiri baslikla ayniysa at (detayda tekrar gorunmesin).
    govde_temizle'den farki: satir yapisini BOZMAZ (pre-wrap ile gosterilecek)."""
    t = govde or ""
    ilk, _, kalan = t.partition("\n")
    if baslik and ilk.strip() == (baslik or "").strip():
        return kalan.lstrip("\n")
    return t


_BOLUM_RE = re.compile(r"^[^\n]{0,50}\b(BÖLÜM|KISIM|FASIL|KİTAP)\b\s*$")
_KUCUK_RE = re.compile(r"[a-zçğıöşü]")


def _bolum_satiri(satir):
    """Buyuk harfli '... BOLUM/KISIM/FASIL' satiri mi? (kucuk harf icermemeli)"""
    t = satir.strip()
    return bool(t) and len(t) <= 60 and _BOLUM_RE.match(t) and not _KUCUK_RE.search(t)


_ISARETLER = (
    (re.compile(r"^\(\d+\)\s"), 1),              # (1) fikra
    (re.compile(r"^[a-zçğıöşü]{2}\)\s"), 3),      # aa) alt bent
    (re.compile(r"^[a-zçğıöşü]\)\s"), 2),         # a) bent
    (re.compile(r"^[A-ZÇĞİÖŞÜ]\)\s"), 2),         # A) bent
    (re.compile(r"^[IVX]+[\.\)]\s"), 2),           # I. / II) bent
    (re.compile(r"^\d+[\.\)]\s"), 3),             # 1) / 2. alt bent
)


_ALT_BASLIK_RE = re.compile(r"^\d+(?:\.\d+)+\.?\s+\S")
# Taslak başlık: "C. Zamanaşımı", "IV. Alıcının temerrüdü", "1. Genel olarak"
# (harf/roma/1-2 hane + '.' + KISA Başlık; cümle değil -> ':'/'.' ile bitmez, ≤48 krk)
_OUTLINE_RE = re.compile(r"^(?:[IVXLC]{1,5}|[A-ZÇĞİÖŞÜ]|\d{1,2})\.\s+[A-ZÇĞİÖŞÜ].{1,45}$")


def _alt_baslik_mi(satir):
    """Numaralı/taslak ara başlık mı? ('2.4. ...' ya da 'C. Zamanaşımı')."""
    t = satir.strip()
    if not t or len(t) > 120:
        return False
    if _ALT_BASLIK_RE.match(t):
        return True
    return (len(t) <= 48 and not t.endswith((":", ".", ";", ","))
            and bool(_OUTLINE_RE.match(t)))


def _seviye(satir):
    """Satir bir madde-ici isaretle mi basliyor? (girinti seviyesi)"""
    t = satir.lstrip()
    for rx, sv in _ISARETLER:
        if rx.match(t):
            return sv
    return None


_TUR_ETIKET = {
    "iptal": ("İPTAL", "not-iptal"),
    "mulga": ("YÜRÜRLÜKTEN KALDIRMA", "not-iptal"),
    "uygulama": ("GEÇİCİ UYGULAMA", "not-uygulama"),
    "ek": ("EK HÜKÜM", "not-degisik"),
    "degisik": ("DEĞİŞİKLİK", "not-degisik"),
}
_TUR_ONCE = {"iptal": 0, "uygulama": 1, "mulga": 2, "ek": 3, "degisik": 4}


def _yururluk_kutusu(belge, madde_id):
    """Maddenin değişiklik/iptal geçmişini kırmızı dipnot kutusu olarak döndür.

    Kaynak: degisiklikler.jsonl. Kayıt: {tur, tarih, arac, kaynak, asil_madde?}.
    İPTAL/mülga kırmızı ve üstte. Kayıt yoksa boş string. Gösterim katmanı; corpus DEĞİŞMEZ.
    """
    kayitlar = DEGISIKLIK.get(belge, {}).get(str(madde_id))
    if not kayitlar:
        return ""
    kayitlar = sorted(kayitlar, key=lambda k: (_TUR_ONCE.get(k.get("tur"), 9),
                                               k.get("tarih", "")))
    iptal_var = any(k.get("tur") in ("iptal", "mulga") for k in kayitlar)
    uyg_var = any(k.get("tur") == "uygulama" for k in kayitlar)
    onem = iptal_var or uyg_var
    satir = []
    for k in kayitlar:
        ad, sinif = _TUR_ETIKET.get(k.get("tur"), ("NOT", "not-degisik"))
        arac = html.escape(k.get("arac", "") or "")
        tarih = html.escape(k.get("tarih", "") or "")
        if k.get("tur") == "uygulama":
            metin = "Bu maddenin uygulanmasına ilişkin süreli/geçici hüküm var"
        else:
            metin = arac if arac else "—"
            if tarih:
                metin += f" ({tarih})"
        if k.get("asil_madde"):
            metin += (f' <span class="not-asil">madde '
                      f'{html.escape(str(k["asil_madde"]))}</span>')
        detay = k.get("detay")
        detay_html = (f'<div class="not-detay">{html.escape(detay)}</div>'
                      if detay else "")
        satir.append(f'<div class="not-satir {sinif}">'
                     f'<span class="not-rozet">{ad}</span>'
                     f'<span class="not-metin">{metin}{detay_html}</span></div>')
    bas = ("Bu maddede İPTAL / yürürlükten kaldırma var" if iptal_var else
           ("Bu maddenin uygulanması sınırlandırılmış olabilir" if uyg_var else
            "Değişiklik geçmişi"))
    return (f'<div class="not-kutu{" not-kutu-onem" if onem else ""}">'
            f'<div class="not-baslik">{bas}</div>{"".join(satir)}'
            f'<div class="not-dip">Kaynak: madde metni + resmî değişiklik/iptal '
            f'çizelgesi. Güncel durumu mevzuat.gov.tr\'de teyit edin.</div></div>')


# Gövdeye karışan DİPNOT cümlesi: "(3) 17/1/2019 tarihli ve 7161 sayılı ... değiştirilmiştir."
# ya da "Anayasa Mahkemesinin ... iptal edilmiştir." Satır-içi '(Değişik: ...)' işaretleri
# parantez içinde ve -mıştır ile bitmediği için KORUNUR.
_DIPNOT_CUMLE = re.compile(
    r'\s*(?:\d{1,3}\s+)?'
    r'(?:Bu\s+(?:madde|f[ıi]kra|bent|Kanun|K[.\s])\w*|Anayasa\s+Mahkeme\w*|'
    r'\d{1,2}/\d{1,2}/\d{4}\s+[Tt]arihli\s+ve)'
    r'.{0,2000}?(?:\w(?:mış|miş|muş|müş)t[ıiuü]r|bakınız)\.', re.S)
# Geçici uygulama dipnotu: "Bu madde 1/7/2012 tarihinden itibaren 8 yıl ... uygulanır."
_DIPNOT_UYG = re.compile(
    r'\s*(?:\d{1,3}\s+)?Bu\s+madde\s+\d{1,2}/\d{1,2}/\d{4}\s+tarihinden\s+itibaren'
    r'.{0,500}?uygulan[ıi]r\.', re.S)
# Bold: satır-içi değişiklik işareti (içinde tarih olan) + "Madde N –" başlık öneki.
_ISARET_BOLD = re.compile(
    r'(\((?:Değişik|Ek|Mülga|İptal|Yeniden düzenleme)\b[^)]*?\d[^)]*?\))')
_MADDE_BAS = re.compile(
    r'((?:(?:EK|GEÇİCİ|MÜKERRER|Ek|Geçici|Mükerrer)\s+)?'
    r'(?:MADDE|Madde)\s+\d+(?:/[A-Za-zÇĞİÖŞÜ])?\s*[–\-])')


def _dipnot_temizle(govde):
    """Dipnot cümlelerini GÖRÜNTÜDEN çıkar (veri değişmez) — metin okunaklı olsun.
    Ayrıca kelimeye yapışık üst-simge işaret rakamlarını ('Belirlenmesi234',
    'olarak2') sil. Kanun numaraları/atıflar (önünde boşluk) etkilenmez."""
    t = _DIPNOT_CUMLE.sub(' ', govde or '')
    t = _DIPNOT_UYG.sub(' ', t)
    t = re.sub(r'(?<=[A-Za-zÇĞİÖŞÜçğıöşü])\d{1,3}(?=[\s.,;:)\]]|$)', '', t)
    return re.sub(r'[ \t]{2,}', ' ', t)


def _vurgula(h):
    """HTML içinde değişiklik işaretlerini ve 'Madde N –' önekini kalınlaştır."""
    h = _ISARET_BOLD.sub(r'<strong class="isaret">\1</strong>', h)
    h = _MADDE_BAS.sub(r'<strong class="madde-no">\1</strong>', h)
    return h


# Chunk'ın SONUNDA kalan başlık(lar) aslında SONRAKİ maddeyi tanıtır (bölüm başlığı,
# "IV. Kiralananın kullanılmaması" gibi alt-başlıklar). Bunları gövdeden ayırıp
# maddeler ARASINA taşırız; değişiklik kutusu da madde içeriğinin hemen altında kalır.
_KUYRUK = re.compile(
    r'((?:<div class="(?:bolum|bolum-ad|altbaslik)">.*?</div>)+)</div>\s*$', re.S)


def _govde_bol(govde_blok):
    """Chunk sonundaki (sonraki maddeye ait) başlık div'lerini gövdeden ayır.
    Döner: (temiz_govde, kuyruk_basliklar)."""
    m = _KUYRUK.search(govde_blok)
    if m:
        return govde_blok[:m.start(1)] + "</div>", m.group(1)
    return govde_blok, ""


def _govde_html(govde, kaynak_url=None):
    """Govdeyi okunakli HTML'e cevir (SADECE goruntu katmani).

    Yaptigi isler:
      0. Gövdeye karışan dipnot cümlelerini çıkarır (metin okunur olur).
      1. Bolum basliklarini (BOLUM/KISIM/FASIL) metinden ayirir.
      2. Fikra/bent/alt-bent isaretlerine gore GIRINTI verir.
      3. Cumle-ortasi satir kesmelerini birlestirir.
      4. '(Değişik: ...)' işaretlerini ve 'Madde N –' önekini kalınlaştırır.
    maddeler.jsonl / indeks / arama DEGISMEZ; parcalama yeniden calistirilmaz.
    """
    ham = _dipnot_temizle(govde or "")
    if bozuk_mu(ham):
        link = ((f' <a href="{kaynak_url}" target="_blank" rel="noopener">'
                 f'Tam metni mevzuat.gov.tr\'de görün</a>.') if kaynak_url else '')
        return ('<div class="tablo-uyari">Bu madde bir tablo/liste içeriyor; '
                'düz metne düzgün çevrilemedi.' + link + '</div>'
                '<div class="ham">' + html.escape(ham) + "</div>")

    parcalar = []
    blok = None   # [seviye, [satirlar]]

    def bosalt():
        nonlocal blok
        if blok and blok[1]:
            metin = " ".join(x.strip() for x in blok[1] if x.strip())
            if metin:
                parcalar.append(f'<p class="s{blok[0]}">{html.escape(metin)}</p>')
        blok = None

    satirlar = ham.split("\n")
    i = 0
    while i < len(satirlar):
        sat = satirlar[i]
        if _bolum_satiri(sat):
            bosalt()
            parcalar.append(f'<div class="bolum">{html.escape(sat.strip())}</div>')
            if i + 1 < len(satirlar):
                sonraki = satirlar[i + 1].strip()
                if 0 < len(sonraki) <= 90 and not _bolum_satiri(sonraki) \
                        and not sonraki.endswith((".", ";")):
                    parcalar.append(f'<div class="bolum-ad">{html.escape(sonraki)}</div>')
                    i += 1
        elif _alt_baslik_mi(sat):
            bosalt()
            _b = re.sub(r'\s*\d{1,3}$', '', sat.strip())     # sondaki dipnot işareti
            parcalar.append(f'<div class="altbaslik">{html.escape(_b)}</div>')
        elif not sat.strip():
            bosalt()
        else:
            sv = _seviye(sat)
            if sv is not None:
                bosalt()
                blok = [sv, [sat]]
            elif blok is None:
                blok = [0, [sat]]
            else:
                blok[1].append(sat)
        i += 1
    bosalt()
    return _vurgula('<div class="metin">' + "".join(parcalar) + "</div>")


_CIZ_TITLE = re.compile(r'\d{1,2}[./]\d{1,2}[./]\d{4}\s+TAR[İI]H')

def _arka_html(raw, pdf_metin=None):
    """İşlenemeyen/çizelge NÖTR gösterim. İşlenemeyen düzyazısı görünür kalır;
    çizelge <details> içinde katlanır. Çizelge metni için PDF-metni (satır-sonlu,
    okunur) varsa onu, yoksa .doc→txt katlanmış metni kullan. Tablo YENİDEN KURULMAZ
    (yanlış hizalama riski). 'tablo/bozuk' uyarısı bu türlerde gösterilmez."""
    raw = raw or ""
    kw = raw.find('ÇİZELGE')
    if kw == -1:                                        # çizelgesiz işlenemeyen düzyazı
        return f'<div class="arka-metin">{html.escape(raw)}</div>'
    bol = kw                                            # çizelge başlığının başı
    for m in _CIZ_TITLE.finditer(raw[:kw + 10]):
        bol = m.start()
    once = raw[:bol].strip()
    ciz = pdf_metin if pdf_metin else raw[bol:]
    once_html = f'<div class="arka-metin">{html.escape(once)}</div>' if once else ''
    etiket = "PDF metni" if pdf_metin else "ham metin"
    return (once_html +
            f'<details class="ciz-katla"><summary>Değişiklik çizelgesi — {etiket} (aç)'
            f'</summary><pre class="cizelge-pre">{html.escape(ciz)}</pre></details>')


def _belge_maddeleri(belge):
    """Belgenin tum chunk'larini parcalama sirasinda (rowid) getir."""
    con = sqlite3.connect(str(DB_YOL))
    try:
        kolonlar = ("SELECT chunk_id, tur, madde_id, baslik, mulga, govde, "
                    "belge_ad, belge_tur FROM chunks ")
        try:
            # INDEXED BY: planlayici 'ORDER BY rowid' gorunce siralama yapmamak icin
            # tum tabloyu taramayi secebiliyor; indeksi acikca zorluyoruz.
            cur = con.execute(kolonlar + "INDEXED BY ix_chunks_belge "
                              "WHERE belge=? ORDER BY rowid", (belge,))
        except sqlite3.Error:
            cur = con.execute(kolonlar + "WHERE belge=? ORDER BY rowid", (belge,))
        kol = ["chunk_id", "tur", "madde_id", "baslik", "mulga", "govde", "belge_ad", "belge_tur"]
        return [dict(zip(kol, r)) for r in cur.fetchall()]
    finally:
        con.close()


@app.get("/belge/{belge}", response_class=HTMLResponse)
def belge_detay(belge: str, vurgu: str = ""):
    maddeler = _belge_maddeleri(belge)
    if not maddeler:
        return HTMLResponse(
            f"<p style='font-family:sans-serif;padding:24px'>Belge bulunamadı: {html.escape(belge)}"
            f" &nbsp; <a href='/'>&larr; Aramaya dön</a></p>", status_code=404)
    belge_ad = maddeler[0].get("belge_ad") or belge
    belge_tur = maddeler[0].get("belge_tur") or ""
    _p = belge.split("_")
    if len(_p) >= 3 and _p[0].isdigit() and _p[1].isdigit() and _p[2].isdigit():
        kaynak_url = (f"https://www.mevzuat.gov.tr/mevzuat?MevzuatNo={_p[0]}"
                      f"&MevzuatTur={_p[1]}&MevzuatTertip={_p[2]}")
    else:
        kaynak_url = "https://www.mevzuat.gov.tr/"
    parcalar = []
    ek_basladi = False; ciz_basladi = False
    for m in maddeler:
        cid = m["chunk_id"]
        eid = "m-" + cid.replace("#", "-")
        vur = cid == vurgu
        sinif = " vurgu" if vur else ""
        etiket = '<span class="vurgu-etiket">Aradığın madde</span>' if vur else ""
        baslik_ham = m.get("baslik") or ""
        ham_govde = _govde_bas_kirp(m.get("govde") or "", baslik_ham)
        # h3 başlığındaki sondaki dipnot işaretini temizle ("Belirlenmesi234" -> "Belirlenmesi")
        baslik = re.sub(r"\s*\d{1,3}$", "", baslik_ham.strip())
        govde = html.escape(ham_govde)
        govde_blok = _govde_html(ham_govde, kaynak_url)
        if m["tur"] == "madde":
            rozet = ' <span class="mulga">MÜLGA</span>' if m.get("mulga") else ""
            bas_html = f'<h3>{html.escape(baslik)}{rozet}</h3>' if baslik else (
                f'<h3 class="sade">Madde {html.escape(str(m.get("madde_id") or ""))}{rozet}</h3>'
                if m.get("mulga") else "")
            not_blok = _yururluk_kutusu(belge, m.get("madde_id"))
            temiz_govde, kuyruk_baslik = _govde_bol(govde_blok)
            parcalar.append(
                f'<section id="{eid}" class="madde{sinif}">{etiket}{bas_html}'
                f'{temiz_govde}{not_blok}</section>')
            if kuyruk_baslik:                    # sonraki maddeyi tanıtan başlık: aralara
                parcalar.append(kuyruk_baslik)
        elif m["tur"] == "kunye":
            parcalar.append(f'<section id="{eid}" class="onsoz{sinif}">{etiket}{govde}</section>')
        elif m["tur"] == "ek_hukum":
            # İşlenemeyen Hükümler: ana metne işlenememiş; NÖTR göster (rozet YOK).
            if not ek_basladi:
                parcalar.append('<div class="arka-baslik">İşlenemeyen Hükümler'
                                '<span>ana metne işlenememiş — bilgi amaçlı</span></div>')
                ek_basladi = True
            parcalar.append(f'<section id="{eid}" class="arka{sinif}">{etiket}'
                            f'{_arka_html(m.get("govde"))}</section>')
        elif m["tur"] == "cizelge":
            # Değişiklik/yürürlük çizelgesi: metadata; NÖTR göster, veri duvarı katlanır.
            if not ciz_basladi:
                parcalar.append('<div class="arka-baslik">Değişiklik / Yürürlük Çizelgesi'
                                '<span>bilgi amaçlı</span></div>')
                ciz_basladi = True
            parcalar.append(f'<section id="{eid}" class="arka cizelge{sinif}">{etiket}'
                            f'{_arka_html(m.get("govde"))}</section>')
        else:
            p_rozet = ('<span class="mulga">MÜLGA</span> ' if m.get("mulga") else "")
            parcalar.append(
                f'<section id="{eid}" class="madde{sinif}">{etiket}{p_rozet}'
                f'{govde_blok}</section>')
    return (DETAY_SAYFA
            .replace("__BASLIK__", html.escape(belge_ad))
            .replace("__TUR__", html.escape(belge_tur))
            .replace("__KAYNAK__", html.escape(belge))
            .replace("__KAYNAK_URL__", kaynak_url)
            .replace("__ICERIK__", "\n".join(parcalar)))


@app.get("/", response_class=HTMLResponse)
def index():
    return SORU_SAYFA          # kök = soru-cevap arayüzü (asıl sayfa)


@app.get("/ara", response_class=HTMLResponse)
def ara_sayfa():
    return SAYFA               # sade madde/kanun araması (ikincil)


SAYFA = (KOK / "web" / "ara.html").read_text(encoding="utf-8")


DETAY_SAYFA = (KOK / "web" / "belge.html").read_text(encoding="utf-8")


# ======================================================================
# SORU-CEVAP (üretim katmanı): arama+rerank → deepseek → akışlı cevap
# ======================================================================

def _kaynak_ozet(r):
    """Detay listesi için kısa madde özeti."""
    g = govde_temizle(r.get("govde") or "", r.get("baslik") or "")
    return ozetle(g, 200)


ALANLAR = ["Aile", "Miras", "Kira", "Gayrimenkul", "Borçlar/Sözleşme",
           "Ticaret/Şirket", "İcra-İflas", "İş Hukuku", "SGK/Sosyal Güvenlik",
           "Ceza", "Ceza Muhakemesi", "İnfaz", "Tüketici", "Vergi", "Bankacılık",
           "Fikri Mülkiyet", "İdare", "İmar", "Kişiler/Vatandaşlık",
           "Kat Mülkiyeti", "Bilişim", "Askerlik", "İş Sağlığı-Güvenliği", "Diğer"]
_ALAN_ESLE = {a.lower(): a for a in ALANLAR}
_ALAN_SIS = ("Aşağıdaki hukuki soruyu ŞU ALANLARDAN TAM BİRİNE ata. YALNIZ alan adını "
             "yaz, başka hiçbir şey ekleme.\nAlanlar: " + " · ".join(ALANLAR))


def _alan_sinifla(soru):
    """Soruyu 31→24 okunur hukuk alanından birine atar (köprüyle aynı model, ~1sn)."""
    try:
        c = (orh.cagir("google/gemini-2.5-flash", _ALAN_SIS, soru, max_tokens=12)[0] or "").strip().strip(".")
        if c.lower() in _ALAN_ESLE:
            return _ALAN_ESLE[c.lower()]
        for a in ALANLAR:
            if a.lower() in c.lower() or c.lower() in a.lower():
                return a
    except Exception:
        pass
    return "Diğer"


def _cevap_akisi(soru):
    """SSE üreteci: önce arama, sonra cevabı token token akıtır, en son
    kaynakları + arama detayını + süre/maliyeti gönderir."""
    soru = " ".join((soru or "").split())
    if not soru:
        yield "data: " + _json.dumps({"tip": "hata", "mesaj": "Soru boş."}) + "\n\n"
        return
    try:
        t0 = time.time()
        _sonuc={}
        def _ara_isi(): _sonuc["r"]=guvenli_ara(soru, n=URETIM_N)
        def _alan_isi(): _sonuc["alan"]=_alan_sinifla(soru)   # aramayla PARALEL → ek gecikme yok
        _th=threading.Thread(target=_ara_isi, daemon=True); _th.start()
        threading.Thread(target=_alan_isi, daemon=True).start()
        while _th.is_alive():
            _th.join(timeout=8); yield ": canli-tutma\n\n"
        satirlar=_sonuc.get("r") or []          # arama+rerank (kilitli)
        alan = _sonuc.get("alan") or "Diğer"
        t_arama = time.time() - t0
        _soru_kaydet(soru, alan)                # soruyu alanıyla logla
        kaynak, satir = uret.kaynak_metni(satirlar)
        kul = f"KAYNAKLAR:\n\n{kaynak}\n\n---\n\nSORU: {soru}"

        usage = {}
        parcalar = []
        t1 = time.time()
        for delta in orh.cagir_stream(URETIM_MODEL, uret.SISTEM2, kul,
                                      max_tokens=1600, saglayici=uret._SAGLAYICI,
                                      usage_out=usage):
            parcalar.append(delta)
            yield "data: " + _json.dumps({"tip": "parca", "metin": delta}) + "\n\n"
        cevap = "".join(parcalar)
        t_uret = time.time() - t1

        # Atıfları çözümle: [N] → kaçıncı madde kullanıldı
        nolar = [int(x) for x in uret.ATIF_D.findall(cevap)]
        kullanilan = {n for n in nolar if 1 <= n <= len(satir)}
        def _durum(r):
            belge = r.get("belge") or ""
            mid = str(r.get("madde_id"))
            mulga = bool(r.get("mulga"))
            degisik = mid in DEGISIKLIK.get(belge, {})
            return mulga, degisik
        kaynaklar = []
        for i, r in enumerate(satir):
            mulga, degisik = _durum(r)
            kaynaklar.append({
                "no": i + 1,
                "belge_ad": (r.get("belge_ad") or "").strip(),
                "baslik": (r.get("baslik") or "").strip(),
                "madde_id": r.get("madde_id"),
                "belge": r.get("belge"),
                "chunk_id": r.get("chunk_id"),
                "ozet": _kaynak_ozet(r),
                "govde": govde_temizle(r.get("govde") or "", r.get("baslik") or ""),
                "kullanildi": (i + 1) in kullanilan,
                "mulga": mulga,
                "degisik": degisik,
            })

        yield "data: " + _json.dumps({
            "tip": "son",
            "alan": alan,
            "kaynaklar": kaynaklar,
            "sure": {"arama": round(t_arama, 1), "uretim": round(t_uret, 1),
                     "toplam": round(time.time() - t0, 1)},
            "maliyet": round(usage.get("cost", 0.0), 5),
        }, ensure_ascii=False) + "\n\n"
    except Exception as e:
        yield "data: " + _json.dumps({"tip": "hata", "mesaj": str(e)[:200]}) + "\n\n"


SORU_YOL = KOK / "cikti" / "soru_log.jsonl"


def _soru_kaydet(soru, alan="?"):
    try:
        with SORU_YOL.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({"zaman": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "soru": soru[:2000], "alan": alan}, ensure_ascii=False) + "\n")
    except Exception:
        pass


@app.get("/api/cevap")
def api_cevap(q: str = ""):
    return StreamingResponse(_cevap_akisi(q), media_type="text/event-stream")


GB_YOL = KOK / "cikti" / "geri_bildirim.jsonl"


@app.post("/api/geri_bildirim")
async def api_geri_bildirim(req: Request):
    """Kullanıcı geri bildirimi → cikti/geri_bildirim.jsonl (gelecek değerlendirme seti)."""
    try:
        d = await req.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    kayit = {
        "zaman": time.strftime("%Y-%m-%d %H:%M:%S"),
        "soru": (d.get("soru") or "")[:2000],
        "alan": (d.get("alan") or "?")[:40],
        "begeni": d.get("begeni"),                 # "iyi" | "kotu"
        "dogru_madde": (d.get("dogru_madde") or "")[:300],
        "cevap": (d.get("cevap") or "")[:4000],
    }
    try:
        with GB_YOL.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(kayit, ensure_ascii=False) + "\n")
    except Exception as e:
        return JSONResponse({"ok": False, "hata": str(e)[:100]}, status_code=500)
    return {"ok": True}


@app.get("/api/istatistik")
def api_istatistik():
    """Toplam soru, geri bildirim, olumlu/olumsuz oranı + ALAN kırılımı."""
    import collections
    soru_alan = collections.Counter()
    toplam_soru = 0
    try:
        for l in SORU_YOL.open(encoding="utf-8"):
            if not l.strip():
                continue
            toplam_soru += 1
            soru_alan[_json.loads(l).get("alan") or "?"] += 1
    except Exception:
        pass
    olumlu = olumsuz = tg = 0
    gb_iyi = collections.Counter()
    gb_kotu = collections.Counter()
    try:
        for l in GB_YOL.open(encoding="utf-8"):
            if not l.strip():
                continue
            d = _json.loads(l)
            tg += 1
            a = d.get("alan") or "?"
            if d.get("begeni") == "iyi":
                olumlu += 1
                gb_iyi[a] += 1
            elif d.get("begeni") == "kotu":
                olumsuz += 1
                gb_kotu[a] += 1
    except Exception:
        pass
    alanlar = [{"alan": a, "soru": n, "olumlu": gb_iyi[a], "olumsuz": gb_kotu[a]}
               for a, n in soru_alan.most_common()]
    return {"toplam_soru": toplam_soru, "toplam_geribildirim": tg,
            "olumlu": olumlu, "olumsuz": olumsuz,
            "olumlu_oran": round(100 * olumlu / tg) if tg else None,
            "alanlar": alanlar}


@app.get("/istatistik", response_class=HTMLResponse)
def istatistik():
    return ISTAT_SAYFA


@app.get("/sor", response_class=HTMLResponse)
def sor():
    return SORU_SAYFA


SORU_SAYFA = (KOK / "web" / "sor.html").read_text(encoding="utf-8")
ISTAT_SAYFA = (KOK / "web" / "istatistik.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    import uvicorn
    # Yerelde 127.0.0.1 (sadece bu makine). Sunucuda dışarıdan erişim için:
    #   HOST=0.0.0.0 python app.py   (PORT ile port da değiştirilebilir)
    _host = os.environ.get("HOST", "127.0.0.1")
    _port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=_host, port=_port)