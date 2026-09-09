#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parcala.py — Madde bazli parcalama (arama biriminin uretilmesi).

Kovalar (kusur() geciti):
  K5 bozuk          -> atlanir (indekse girmez)
  K1/K3 maddeli     -> madde chunk'lari  (+ kunye chunk'i)
  K2 yapisiz/prose  -> paragraf chunk'lari

Sinir kurallari (hepsi veriyle dogrulandi — bkz. sinir_dene / sinir_say):
  1. Header = MADDE_ANY (capasiz) + guard (satir basi/2+ bosluk, '(' degil)
  2. Baslik ("Amaç","Dayanak") bir SONRAKI maddeye ait -> kesim baslik satirinda
  3. Chunk anahtari <belge>#<sira> (madde_id tekrar/sirasiz olabilir)
  4. Kuyruk (degisiklik-tablosu) guclu isaretten kesilir, degisiklik_eki olur
  5. Ilk maddeden onceki metin = kunye chunk'i
  6. Mulga: tam-madde kaldirmasi (Mülga: / Mülga <tarih>) VE tam-madde İPTAL
     (İptal: / İptal madde: — AYM). "Mülga/İptal ibare/fıkra/cümle/bent" kismi
     kaldirmadir, sayilmaz. Pencere madde sinirinda kesilir.

Cikti: cikti/maddeler.jsonl   (her satir bir chunk)
Kullanim:  python3 parcala.py [--test N]
"""
import re, sys, json
from collections import Counter
from pathlib import Path
sys.path.insert(0, '.')
from tamamla import BELGELER, kusur

CIKTI = Path('cikti/maddeler.jsonl')

DASH = r'[-‐‑‒–—―−]'
# re.I YOK: Turkce İ/I katlanmasi re.I'yi bozuyor (GEÇİCİ != geçici). Casing acikca sayilir.
OZEL = r'(?:EK|Ek|ek|GEÇİCİ|Geçici|geçici|MÜKERRER|Mükerrer|mükerrer)\s+'
MAD  = r'(?:MADDE|Madde|madde)'
NUMG = r'\d+(?:\s*/\s*[A-Za-zÇĞİÖŞÜçğıöşü])?'
SEP  = rf'\s*(?:{DASH}|\.|\)|\(|:)'   # tire/nokta/) + '(' ve ':' (Madde 2(Mülga.. / Madde 34:)
HDR  = re.compile(rf'(?P<ozel>{OZEL})?{MAD}\s+(?P<no>{NUMG}){SEP}')
STRICT2 = re.compile(rf'^[ \t]*(?:[^\n]{{0,80}}?\S[ \t]{{2,}})?(?:{OZEL})?{MAD}\s+{NUMG}{SEP}', re.M)
MULGA = re.compile(
    r'[Mm][üÜ]lga(?!\s*(?:ibare|fıkra|cümle|bent|paragraf|başlık|birinci|ikinci|'
    r'üçüncü|dördüncü|beşinci|altıncı|yedinci|son)\b)'
    r'(?=\s*:|\s+\d{1,2}[./]\d{1,2}[./]\d{2,4})')
# İptal (AYM) = tam-madde geçersizliği; mülga ile AYNI geçerlilik sonucu -> bayrak.
# Kısmi iptal ("İptal ibare/fıkra/cümle/bent/ordinal") madde ayakta kalır -> HARİÇ.
# "İptal madde" / "İptal madde ve ekli liste" tam-madde iptalidir -> DÂHİL.
IPTAL = re.compile(
    r'\(\s*[İi]ptal'
    r'(?!\s*(?:ibare|f[ıi]kra|cümle|bent|paragraf|başlık|birinci|ikinci|üçüncü|'
    r'dördüncü|beşinci|altıncı|yedinci|son)\b)'
    r'(?:\s+madde(?:\s+ve\s+ekli\s+liste)?)?\s*:')

# --- Paragraf geçersizlik bayrağı ------------------------------------------
# Madde yapısı olmayan belgeler (tebliğ vb.) paragraf paragraf bölünür; bu
# chunk'ların madde_id'si YOK. Bir paragraf KOMPLE mülga/iptal ise bayraklanır:
#   (a) işaret blok başında (<=15 krk),  (b) kısmi değil (ibare/fıkra/... hariç),
#   (c) işaretten sonra anlamlı metin kalmamış.
# Karışık blok ("p) (Mülga:...) r) (Değişik:...) <geçerli metin>") KASITLA
# bayraklanmaz — geçerli içeriği geçersiz göstermeyelim (İlke: yanlış gösterme).
PARA_KRIT = re.compile(r'\(\s*(?:mülga|iptal)\b[^)]*:')
PARA_KISMI = re.compile(
    r'\(\s*(?:mülga|iptal)\s+(?:ibare|f[ıi]kra|cümle|bent|paragraf|başlık|satır|'
    r'birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|son)\b')
PARA_SONRA = re.compile(r'[\s\d().*…\-—]+')

def trf(s):
    """Turkce-guvenli kucuk harf: İ->i, I->ı, sonra lower()."""
    return s.replace('İ', 'i').replace('I', 'ı').lower()

def paragraf_mulga(p):
    """Paragraf chunk KOMPLE mülga/iptal mi? (CLEAN kuralı — denetim_paragraf2 ile
    doğrulandı: 89 güvenli, karışık blok elenir)."""
    low = trf(p)                                   # trf uzunluk korur -> konumlar hizalı
    mk = PARA_KRIT.search(low)
    if not mk or mk.start() > 15 or PARA_KISMI.search(low):
        return False
    kapanis = p.find(')', mk.start())
    sonra = p[kapanis + 1:] if kapanis != -1 else p[mk.end():]
    return len(PARA_SONRA.sub('', sonra)) <= 25    # sonrasında yalnız dipnot/no -> komple

# ============ Aralık-başlığı + öz-testli üç-durum bayrak ============
# Aralık-çökmesi: "Madde X ilâ Y – (Mülga:...)" tek satıra sıkışmış ardışık ölü
# maddeler. HDR bunu tanımadığı için sonraki maddeye yapışıyordu (bundle). RANGE
# bunları ayrı chunk yapar (madde_id="X-Y").
ILA = r'(?:il[aâ]|İL[AÂ])'
# Aralık TİRE ile biter ("Madde 20 ilâ 25 –"); ")" ile bitmez → "Madde 31- 1)" (fıkra
# numarası) yanlışlıkla aralık sayılmasın. Trailing SEP yalnız tire/nokta.
RANGE = re.compile(rf'(?P<ozel>{OZEL})?{MAD}\s+(?P<no>{NUMG})\s*(?:{ILA}|[-–—])\s*'
                   rf'(?P<no2>{NUMG})\s*(?:{DASH}|\.)')

def _ger_range(m):
    """GERÇEK aralık mı? 'Madde X ilâ Y' ancak Y>X ise aralıktır. 'Madde 241 – 1.'
    (ilk fıkra no'su '1.') aralık DEĞİL — no2(1) < no(241) → tekil madde 241 olmalı.
    Bu ayrım 'N-1' sahte-aralık id artefaktını (441 madde) kökten önler."""
    no2 = m.groupdict().get('no2')
    if not no2:
        return False
    a = re.match(r'\d+', re.sub(r'\s+', '', m.group('no')))
    b = re.match(r'\d+', re.sub(r'\s+', '', no2))
    return bool(a and b and int(b.group()) > int(a.group()))

# Öz-testi: geçersizlik KELİMESİNE değil, parantez/başlık soyulunca canlı hüküm
# KALIP kalmadığına bakar (kelimeden bağımsız — Hükümsüz/kaldırıldı/görülmemiş form).
INVROOT_P = re.compile(r'mülga|iptal|hükümsüz|ilga|kaldır')
TAM_MUL_P = re.compile(
    r'\(\s*(?:mülga|iptal)'
    r'(?!\s*(?:ibare|f[ıi]kra|cümle|bent|paragraf|başlık|satır|tablo|tanım|bölüm|kısım|'
    r'birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|dokuzuncu|onuncu|son|'
    r'alt|ek|dipnot|ifade|iki|üç|dört|ilk)\b)'
    r'(?:\s*madde)?\s*[:\-–]')
YENIDEN_P = re.compile(r'yeniden\s+düzenle')
TARIH_P = re.compile(r'(\d{1,2})[./](\d{1,2})[./](\d{2,4})')
T_DEAD_P, T_LIVE_P, BUNDLE_ESIK = 20, 60, 200

_MAD_HDR = re.compile(r'(?:MADDE|Madde|madde)\s+\d+(?:\s*/\s*[A-Za-zÇĞİÖŞÜçğıöşü])?\s*[-–—.)]')
def _canli_harf(g):
    """Parantez + BÜYÜK-HARF başlık + numara soyulunca kalan CANLI hüküm harfi.
    Baştaki yan-başlık + 'MADDE N –' atılır (başlık hüküm değil; ölü maddede yalnız
    başlık kalınca 'canlı' sanılmasın). Aralık ('Madde X ilâ Y') eşleşmez, korunur."""
    x = g or ''
    hm = _MAD_HDR.search(x)
    if hm:
        x = x[hm.end():]
    for _ in range(6):
        x2 = re.sub(r'\([^()]*\)', ' ', x)
        if x2 == x:
            break
        x = x2
    kalan = []
    for tok in re.findall(r'\S+', x):
        harf = re.sub(r'[^A-Za-zÇĞİÖŞÜçğıöşü]', '', tok)
        if len(harf) < 2:
            continue
        if harf == harf.upper():                       # BÜYÜK HARF = başlık, hüküm değil
            continue
        if trf(harf) in ('madde', 'ek', 'geçici', 'mükerrer', 'bölüm', 'kısım', 'fıkra'):
            continue
        kalan.append(trf(harf))
    return ''.join(kalan)

def _ilk_tarih(s):
    m = TARIH_P.search(s or '')
    if not m:
        return None
    dd, ay, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 1900 if y > 30 else 2000
    return (y, ay, dd)

# Aralık ölü: "Madde X ilâ/–/- Y – (Mülga/İptal/Hükümsüz...)" — başlık/kuyruk canli'yi
# şişirse bile aralık tümden ölüdür (canli testinden ÖNCE karar).
# Parantez OPSİYONEL ama ":" ŞART: "(Mülga:" ve OCR'li parantezsiz "- Mülga:" ikisi de;
# ama "mülga 818 sayılı Kanun" (sıfat/atıf) ":" gelmediği için tetiklenmez.
_ARALIK_OLU = re.compile(r'madde\s+\d+\s*(?:il[aâ]|[-–—])\s*\d+\s*[-–—.]\s*'
                         r'\(?\s*(?:mülga|iptal|hükümsüz)\b[^)(]{0,12}:')
# Baştan-ölü: başlık soyulunca gövde TAM-madde mülgasıyla başlıyor (alt-birim DEĞİL).
BAS_MUL_P = re.compile(
    r'^\s*\(\s*(?:mülga|iptal|hükümsüz)'
    r'(?!\s*(?:ibare|f[ıi]kra|cümle|bent|paragraf|başlık|satır|tablo|tanım|bölüm|kısım|'
    r'birinci|ikinci|üçüncü|dördüncü|beşinci|altıncı|yedinci|sekizinci|dokuzuncu|onuncu|son|'
    r'alt|ek|dipnot|ifade|iki|üç|dört|ilk)\b)')
# KALDIRMA-MADDESİ işareti: başka hükmü KALDIRAN/işleyen, KENDİSİ yürürlükteki madde.
# İçeriği parantezde olduğu için 'canli küçük' çıkar ama ÖLÜ DEĞİLdir → geçersiz deme.
# (Hedefli: yalnız bu başlık/kalıp; 'tüm fıkraları mülga' gerçek-ölüler geçersiz kalır.)
KALDIRMA_MAD = re.compile(
    r"kaldırıl(?:an|acak)\s+(?:kanun\s+)?hüküm"           # kaldırılan (kanun) hükümler
    r"|değiştirilen\s+ve\s+(?:yürürlükten\s+)?kaldırıl"   # değiştirilen ve (yürürlükten) kaldırılan
    r"|yürürlükten\s+kaldırılan\s+hüküm"
    r"|yürürlükten\s+kaldırma\b"                          # 'Yürürlükten kaldırma' başlığı
    r"|yerine\s+işlenmiş"                                 # ...ile ilgili olup yerine işlenmiştir
    r"|kaldırılan\s+kanun")
def madde_durumu(govde):
    """geçerli | geçersiz | belirsiz. KAÇIŞSIZ sıra: aralık-ölü → reenact → baştan-ölü
    → öz-testi. Baştan-ölü, yapışan/kuyruk metin canli'yi şişirse bile ölü sayar."""
    low = trf(govde)
    c = len(_canli_harf(govde))
    if _ARALIK_OLU.search(low):                        # ölü madde-aralığı
        return 'belirsiz' if c > BUNDLE_ESIK else 'geçersiz'   # uzunsa canlı yutmuş -> teyit
    if not INVROOT_P.search(low):
        return 'geçerli'                               # geçersizlik işareti yok
    mul = TAM_MUL_P.search(low)
    if mul:                                            # mülga sonrası yeniden düzenleme=canlı
        yd = YENIDEN_P.search(low, mul.start() + 1)
        if yd:
            t1 = _ilk_tarih(low[mul.start():mul.start() + 70])
            t2 = _ilk_tarih(low[yd.start():yd.start() + 90])
            if t2 is None or t1 is None or t2 >= t1:
                return 'geçerli'
    # BAŞTAN-ÖLÜ: başlık soyulunca gövde tam-madde mülgasıyla başlıyorsa tüm madde ölü.
    # Sonrasına kısa kuyruk/bleed yapışsa bile ölü; AMA çok uzunsa (canlı madde yutmuş
    # bundle) 'kesin ölü' deme -> belirsiz (canlıyı ölü göstermemek için).
    hm = _MAD_HDR.search(low)
    body = low[hm.end():] if hm else low
    if BAS_MUL_P.match(body):
        return 'belirsiz' if c > BUNDLE_ESIK else 'geçersiz'
    if c <= T_DEAD_P:
        # canli küçük: normalde ölü; AMA kaldırma-maddesi (başka hükmü kaldıran, kendisi
        # yürürlükte) ise 'geçersiz' DEME -> belirsiz (canlıyı ölü göstermemek için).
        return 'belirsiz' if KALDIRMA_MAD.search(low) else 'geçersiz'
    if c >= T_LIVE_P:
        return 'geçerli'                               # canlı gövde var -> mülga alt-birime ait
    return 'belirsiz'                                  # gri bölge -> teyit

STRONG = ['yürürlüğe koyan', 'farklı tarihte yürürlüğe giren', 'yürürlüğe giren maddeler',
          'işlenemeyen', 'ek ve değişiklik getiren', 'yürürlüğe giriş tarihini']
KEY_RE = re.compile(r'^(\d+_\d+_\d+(?:_\d{8})?)')

def guard_ok(t, p):
    ls = t.rfind('\n', 0, p) + 1; seg = t[ls:p]
    if seg == '': return True
    if seg.rstrip().endswith('('): return False
    return (len(seg) - len(seg.rstrip(' \t'))) >= 2

def madde_id(m):
    o = trf((m.group('ozel') or '').strip())     # tr-fold: GEÇİCİ/Geçici/geçici -> geçici
    no = re.sub(r'\s+', '', m.group('no'))
    pre = {'ek': 'Ek ', 'geçici': 'Geçici ', 'mükerrer': 'Mükerrer '}.get(o, '')
    if m.groupdict().get('no2'):                  # aralık başlığı "X ilâ Y" -> "X-Y"
        return pre + no + '-' + re.sub(r'\s+', '', m.group('no2'))
    return pre + no

# "yürürlüğe giriş tarihini" hem başlıkta ("Yürürlüğe Giriş Tarihini Gösteren
# Çizelge") hem cümle içinde ("...kararın yürürlüğe giriş tarihini erteleyebilir")
# geçebilir; TEK bu işarette büyük-harf başlığı şartı koşarız (77108/26901 hatalı
# kesimleri sıfırlanır). Diğer 5 işaret kanıtlı özgün mantıkla (gap=3) kesilir.
_STRONG_BUYUK = {'yürürlüğe giriş tarihini'}
# Çizelge başlık-tarihi (BÜYÜK harf = başlık): "4/8/1952 TARİHLİ VE ... SAYILI ..."
_CIZ_BASLIK = re.compile(r'\d{1,2}[./]\d{1,2}[./]\d{4}\s+TAR[İI]H')

def kuyruk_kes(t):
    """Kuyruğu (arka-madde: işlenemeyen/çizelge) güçlü işaretten kes.
    (trf uzunluğu korur; tf konumu t konumuyla birebir hizalı.)"""
    L = len(t)
    if not L: return 0
    tf = trf(t); best = L
    for a in STRONG:
        if a in _STRONG_BUYUK:
            # yalnız BÜYÜK harfle başlayan (başlık) geçişini say
            j = 0
            while True:
                i = tf.find(a, j)
                if i == -1: break
                j = i + 1
                if i / L >= 0.6 and t[i].isupper():
                    if i < best: best = i
                    break
        else:
            i = tf.find(a)
            if i != -1 and i / L >= 0.6 and i < best:
                best = i
    # Başlık-yapışması: kesim işareti çizelge BAŞLIĞININ ortasına denk gelirse
    # ("...TÜZÜĞE [ek ve değişiklik getiren]..."), başlığın başına (tarih-başlık +
    # "SAYILI") çek -> tam başlık çizelgeye gider, son maddede kalmaz.
    if best < L:
        pen = max(0, best - 300)
        onc = t[pen:best]
        tm = None
        for m in _CIZ_BASLIK.finditer(onc):
            tm = m
        if tm and 'SAYILI' in onc[tm.start():]:
            aday = pen + tm.start()
            if 0 <= aday < best and aday / L >= 0.5:
                best = aday
    return best

def kuyruk_sinif(kesilen):
    """Kesilen arka-metni sınıfla (denetim_kuyruk3 ile AYNI kural):
      ek_hukum -> İşlenemeyen Hükümler (gerçek geçici madde, geri koy)
      cizelge  -> değişiklik/RG çizelgesi (koru, tablo göster)
      yanlis   -> gerçek madde (guard sonrası olmamalı; olursa kesme)
      cop      -> kısa maddesiz tablo (atmak güvenli)."""
    low = trf(kesilen)
    if 'işlenemeyen' in low[:250]:
        return 'ek_hukum'
    if ('yayımlandığı resmi gazete' in low or 'değişiklik yapan' in low
            or 'yürürlüğe koyan' in low or 'ek ve değişiklik getiren' in low):
        return 'cizelge'
    if any(guard_ok(kesilen, m.start()) for m in HDR.finditer(kesilen)):
        return 'yanlis'
    return 'cop'


# ---- Başlık satırı testi (çok satırlı başlıkları yukarı doğru toplamak için) ----
_BAS_MAX = 4          # bir maddenin üstünde en fazla kaç başlık satırı toplanır
_BOS_MAX = 2          # başlık ararken atlanabilecek ardışık boş satır sayısı
# NOT: "liste isareti" filtresi DENENDI ve KALDIRILDI — Turkce mevzuat basliklari
# zaten "1.", "I -", "A)", "II - " ile basliyor ("I - Genel kural:", "2. Birden cok
# borcta"). Liste ogesini ayiran sey isaret degil, sondaki VIRGUL.


def _bas_satiri(sat):
    """Bu satır, altındaki maddenin başlığının bir parçası mı?

    HAYIR diyeceğimiz meşru durumlar (ölçüldü, korunması gerekiyor):
      - virgülle biten liste öğesi   "F - Askerlik muamelesi,"   (460 vaka)
      - tire ile biten bölünmüş kelime  "...almış sa-"           (168 vaka)
      - değişiklik cetveli satırı    "22/3/2019 30722"          (1.184 vaka)
      - imza/unvan bloğu             "CUMHURBAŞKANI"            (1.139 vaka)
      - cümle sonu (. ;)  → gövdenin son cümlesi
    ':' ile bitmek başlık için NORMALDİR (Türkçe mevzuat başlıkları çoğu kez ':').
    """
    if not sat or len(sat) > 100:
        return False
    if sat.endswith(('.', ';', ',', '-', '–', '—')):
        return False
    if HDR.match(sat) or RANGE.match(sat):
        return False
    if _KUNYE_SAT.search(sat):
        return False
    if re.search(r'\d{1,2}[./]\d{1,2}[./]\d{2,4}\s+\d{4,5}', sat):   # değişiklik cetveli
        return False
    if sum(c.isdigit() for c in sat) > len(sat) / 3:
        return False
    if re.fullmatch(r'[A-ZÇĞİÖŞÜ\s.]{4,40}', sat) and len(sat.split()) <= 2 \
            and not _yapisal(sat):                                    # imza/unvan bloğu
        return False
    # BELGE BAŞLIK BLOĞU: uzun ve tamamı büyük harf satırlar belgenin kendi adıdır
    # ("TÜRKİYE ELEKTROMEKANİK SANAYİ ANONİM ŞİRKETİ ANA STATÜSÜ"). Yukarı yürüyüş
    # bunları yutunca belgenin künye chunk'ı yok oluyor ve 1. maddenin gövdesi belge
    # adıyla başlıyordu (1.743 belge). Belge adı zaten belge_ad olarak indeksli.
    # Bedeli ölçüldü: gerçekten böyle görünen madde başlığı 257.941'de yalnız 92 (%0,04).
    if len(sat) > 40 and re.fullmatch(r'[^a-zçğıöşü]+', sat) and not _yapisal(sat):
        return False
    return True


def blok_ve_baslik(t, m):
    """Kesim noktasi + baslik. Baslik bir sonraki maddeye ait."""
    mls = t.rfind('\n', 0, m.start()) + 1
    if m.start() > mls:                                   # inline: "Amaç  MADDE 1"
        onceki = t[mls:m.start()]
        if HDR.search(onceki) or RANGE.search(onceki):    # aynı satırda ÖNCE header (aralık)
            return m.start(), ''                          # -> blok kendi konumunda başlar
        return mls, onceki.strip()
    pe = mls - 1                                          # onceki satiri incele
    if pe <= 0: return mls, ''

    # ÇOK SATIRLI BAŞLIK: başlık iki satıra yayılmışsa (ya da üstünde bölüm başlığı
    # varsa) üst satırların ÖNCEKİ maddenin gövdesinde kalmaması gerekir — yoksa
    # gövde bir SONRAKİ maddenin başlık parçasıyla biter (ör. "...2. Birden çok borçta").
    # Yukarı doğru başlık satırları toplanır. Kesim en üst başlık satırından
    # yapılır; `baslik` yine maddenin KENDİ (en alt) başlık satırıdır, ata satırlar
    # baslik_yol'a gider.
    # BOŞ SATIR ATLAMA: kaynak metinlerde başlık ile 'MADDE N' arasında çoğu kez
    # boş satır var ("...açılmıştır.\n\nUygulama\n\nMADDE 2-"). Boş satırda durulursa
    # başlık önceki maddede kalıyor — kalan sızıntının %70'i buydu.
    kes, satirlar, bos = mls, [], 0
    q = pe
    while len(satirlar) < _BAS_MAX and bos <= _BOS_MAX:
        if q <= 0: break
        qs = t.rfind('\n', 0, q) + 1
        sat = t[qs:q].strip()
        if not sat:                       # boş satır: yut, başlık hakkı harcama
            bos += 1
            q = qs - 1
            continue
        if not _bas_satiri(sat): break
        satirlar.append(sat)
        bos = 0
        kes, q = qs, qs - 1
    if not satirlar:
        return mls, ''
    kendi = satirlar[0]                       # maddeye en yakın satır = kendi başlığı
    if _yapisal(kendi):                       # bölüm/kısım satırı başlık DEĞİL, yol'a ait
        kendi = ''
    return kes, kendi

# ---- BAŞLIK YOLU (ata başlıklar + miras) ---------------------------------
# blok_ve_baslik yalnız maddenin BİR üstündeki satırı alır. TMK/TBK gibi
# hiyerarşik kanunlarda maddenin üstünde 3-4 satır başlık vardır; ata başlıklar
# kaybolur (ölçüm: TMK %35, TBK %33, kanunlarda genel %18). Kaybedilen tam da
# vatandaşın kullandığı kelime: "BOŞANMA", "Sebepsiz Zenginleşmeden Doğan Borç
# İlişkileri". Ayrıca maddelerin %28'i hiç başlıksızdır — bir önceki başlığın
# kapsamındadır (miras).
#
# Burada belge boyunca bir başlık YIĞINI tutulur: her madde o anki yığını alır,
# kendi başlık satırları yığını günceller. Kesim noktalarına DOKUNULMAZ.
# Yapısal başlık: yalnız BÜYÜK harf biçimi, ya da 'İkinci Bölüm' gibi sıra sayısı + kelime.
# Aksi halde 'Bölüm Başkanlığının Görevleri' yapısal sanılıp altına başlık biriktiriyordu.
_YAPI_BUYUK = re.compile(r'\b(?:BÖLÜM|KISIM|KİTAP|FASIL|AYIRIM|AYRIM)\b')
_ORD = (r'(?:BİRİNCİ|İKİNCİ|ÜÇÜNCÜ|DÖRDÜNCÜ|BEŞİNCİ|ALTINCI|YEDİNCİ|SEKİZİNCİ|DOKUZUNCU|ONUNCU|'
        r'Birinci|İkinci|Üçüncü|Dördüncü|Beşinci|Altıncı|Yedinci|Sekizinci|Dokuzuncu|Onuncu|EK|Ek|GEÇİCİ|Geçici)')
_YAPI_ORD = re.compile(_ORD + r'\s+(?:BÖLÜM|KISIM|KİTAP|FASIL|AYIRIM|AYRIM|Bölüm|Kısım|Kitap|Fasıl|Ayırım|Ayrım)\b')
def _yapisal(s):
    return bool(_YAPI_BUYUK.search(s) or _YAPI_ORD.search(s))
_ROMEN    = re.compile(r'^([IVXLCDM]+)[\.\)]\s')
_HARF_B   = re.compile(r'^[A-ZÇĞİÖŞÜ][\.\)]\s')
_SAYI_B   = re.compile(r'^\d+[\.\)]\s')
_HARF_K   = re.compile(r'^[a-zçğıöşü][\.\)]\s')

# Künye/yayım satırları başlık DEĞİLDİR ("Yayımlandığı Düstur : Tertip: 5 Cilt: 7").
# Kısa oldukları ve nokta ile bitmedikleri için ölçütü geçiyorlardı.
_KUNYE_SAT = re.compile(
    r'Yayımlandığı|Yayimlandigi|Düstur|Dustur|Resmî\s*Gazete|Resmi\s*Gazete|'
    r'Tertip\s*:|Cilt\s*:|Sayfa\s*:|Sayı\s*:|Kanun\s*(?:No|Numarası)\s*:|'
    r'Kabul\s*Tarihi\s*:|R\.G\.')

def bas_gibi(s):
    """blok_ve_baslik ile AYNI ölçüt; farkı: BÖLÜM/KISIM satırlarını da kabul eder,
    künye/yayım satırlarını ise reddeder."""
    s = s.strip()
    if not s or len(s) > 70: return False
    # sondaki tırnak/parantez/dipnot kırpılmadan bakılırsa 'uygulanır.”' ya da
    # 'uygulanır.(1)' cümlesi başlık sanılıyor
    kirp = re.sub(r'[\s”"\'’\(\)\[\]\{\}\d]+$', '', s)
    if kirp.endswith(('.', ';', ',')): return False
    if HDR.match(s) or RANGE.match(s): return False
    if _KUNYE_SAT.search(s): return False
    if sum(c.isdigit() for c in s) > len(s) / 3: return False   # sayı ağırlıklı satır
    return True

def bas_seviye(s):
    """Hiyerarşi seviyesi (küçük = üst). Türk mevzuat düzeni: BÖLÜM > A. > I. > 1. > a."""
    s = s.strip()
    if _yapisal(s): return 0
    m = _ROMEN.match(s)
    if m and len(m.group(1)) >= 2: return 2            # II. III. IV. VI.
    if re.match(r'^[IVX][\.\)]\s', s): return 2        # tek I. V. X. -> romen
    if _HARF_B.match(s): return 1                      # A. B. C. D.
    if _SAYI_B.match(s): return 3                      # 1. 2. 3.
    if _HARF_K.match(s): return 4                      # a. b. c.
    return 5                                           # numarasız alt başlık

def ust_basliklar(t, pos):
    """pos'un hemen üstündeki ARDIŞIK başlık satırları (üstten alta sıralı)."""
    out = []
    j = t.rfind('\n', 0, pos)
    while j > 0 and len(out) < 8:
        ps = t.rfind('\n', 0, j) + 1
        satir = t[ps:j]
        if not bas_gibi(satir): break
        out.append(satir.strip())
        j = ps - 1
    out.reverse()
    return out

def yol_guncelle(yigin, satirlar, kendi=None):
    """Yığını güncelle: seviye L gelirse L ve altındakiler düşer.

    kendi: maddenin KENDİ başlığı. Yapısal girdiye YAPIŞTIRILMAZ — yapışırsa
    yol_metni onu ayıklayamaz ve gövdedeki başlık mükerrer gömülür."""
    for i, s in enumerate(satirlar):
        lv = bas_seviye(s)
        son_ve_kendi = (kendi is not None and i == len(satirlar) - 1
                        and s.strip() == kendi.strip())
        # "İKİNCİ BÖLÜM" + "BOŞANMA" ikilisi: numarasız satır yapısal başlığa yapışır
        if (lv == 5 and yigin and yigin[-1][0] == 0 and not son_ve_kendi
                and not yigin[-1][2]):                      # yalnız BİR kez yapışsın
            yigin[-1] = (0, (yigin[-1][1] + ' ' + s).strip(), True)
            continue
        while yigin and yigin[-1][0] >= lv:
            yigin.pop()
        yigin.append((lv, s, False))
    return yigin

def yol_metni(yigin, kendi_baslik):
    """Maddenin ATA başlık yolu. Kendi başlığı gövdede zaten var -> dışarıda bırakılır."""
    ad = [e[1] for e in yigin]
    if kendi_baslik and ad and ad[-1].strip() == kendi_baslik.strip():
        ad = ad[:-1]
    yol = ' > '.join(ad)
    return yol[:220]                    # emniyet tavanı: kaçak birikme gömmeyi boğmasın


def kova(t):
    if kusur(t): return 'K5'
    if STRICT2.search(t): return 'K1'
    if HDR.search(t): return 'K3'   # capasiz da olsa madde var
    return 'K2'

def paragraflar(t):
    for p in re.split(r'\n\s*\n', t):
        p = p.strip()
        if len(p) >= 20:
            yield p

def main():
    limit = None
    if '--test' in sys.argv:
        limit = int(sys.argv[sys.argv.index('--test') + 1])
    belge_filtre = None                                   # --belge KEY: sadece o belgeyi işle, YAZMA, chunk'ları bas
    if '--belge' in sys.argv:
        belge_filtre = sys.argv[sys.argv.index('--belge') + 1]

    st = Counter()
    chunk_tur = Counter()
    durum_say = Counter()
    n_chunk = 0; n_tail = 0; n_bundle = 0; n_mulga = 0; sifir_chunk = 0
    n_ekh = 0; n_ciz = 0
    gorulen_id = set(); cakisma = 0
    dosyalar = sorted(BELGELER.glob('*/*.txt'))
    if belge_filtre:
        dosyalar = [f for f in dosyalar if f.name.startswith(belge_filtre)]
    if limit: dosyalar = dosyalar[:limit]

    hedef = CIKTI
    if '--cikti' in sys.argv:
        hedef = Path(sys.argv[sys.argv.index('--cikti') + 1])
    out = None if (limit or belge_filtre) else hedef.open('w', encoding='utf-8')
    for f in dosyalar:
        t = f.read_text(encoding='utf-8', errors='ignore')
        # Unicode boşluk/görünmez karakter normalizasyonu. Bazı tebliğ/yönetmelikler
        # başlıkları nbsp (\xa0) ile girintiliyor; guard_ok bunları boşluk saymadığı için
        # TÜM 'MADDE N' başlıkları elenip belge yanlışlıkla paragraf'a (K2) düşüyordu
        # (486 belge/~6680 madde kaybı). Tüm unicode boşlukları normal boşluğa; görünmez
        # birleştirici/soft-hyphen/BOM'u sil; satır-ayraçlarını \n'e.
        t = t.translate({
            0x00A0: ' ', 0x2000: ' ', 0x2001: ' ', 0x2002: ' ', 0x2003: ' ', 0x2004: ' ',
            0x2005: ' ', 0x2006: ' ', 0x2007: ' ', 0x2008: ' ', 0x2009: ' ', 0x200A: ' ',
            0x202F: ' ', 0x205F: ' ', 0x3000: ' ', 0x180E: ' ',
            0x00AD: None, 0x200B: None, 0x200C: None, 0x200D: None, 0xFEFF: None,
            0x2028: 0x0A, 0x2029: 0x0A})
        km = KEY_RE.match(f.name)
        belge = km.group(1) if km else f.stem
        k = kova(t); st[k] += 1
        if k == 'K5':
            continue

        chunks = []
        if k in ('K1', 'K3'):
            tail = kuyruk_kes(t)
            if tail < len(t): n_tail += 1
            _raw = list(HDR.finditer(t)) + [m for m in RANGE.finditer(t) if _ger_range(m)]
            _best = {}                                        # aynı konumda uzun (RANGE) kazanır
            for _m in _raw:
                if _m.start() not in _best or _m.end() > _best[_m.start()].end():
                    _best[_m.start()] = _m
            hdrs = [m for m in sorted(_best.values(), key=lambda x: x.start())
                    if guard_ok(t, m.start()) and m.start() < tail]
            if not hdrs:
                k = 'K2'   # guard/tail sonrasi header kalmadi -> prose gibi ele
            else:
                bloklar = [blok_ve_baslik(t, m) for m in hdrs]
                # kunye chunk'i (ilk maddeden once)
                kunye = t[:bloklar[0][0]].strip()
                if len(kunye) >= 30:
                    chunks.append(('kunye', '', kunye))
                ids = []
                yigin = []                                # belge boyu başlık yığını
                for i, m in enumerate(hdrs):
                    bs = bloklar[i][0]; baslik = bloklar[i][1]
                    # kendi başlık satırlarıyla yığını güncelle; yoksa MİRAS al
                    # blok_ve_baslik künye satırını da başlık döndürebiliyor (mevcut
                    # davranış, korunuyor) — yığına İTMEDEN önce süz.
                    _bs = baslik if (baslik and bas_gibi(baslik)) else ''
                    _yeni = ust_basliklar(t, bs) + ([_bs] if _bs else [])
                    if _yeni: yol_guncelle(yigin, _yeni, baslik or None)
                    _yol = yol_metni(yigin, baslik)
                    son = bloklar[i + 1][0] if i + 1 < len(hdrs) else tail
                    govde = t[bs:son].strip()
                    mid = madde_id(m); ids.append(mid)
                    # Geçersizlik: öz-testli üç-durum (konum-regex yerine sonuç-testi).
                    dur = madde_durumu(govde)
                    mul = (dur == 'geçersiz')
                    if mul: n_mulga += 1
                    chunks.append(('madde', mid, govde, baslik, mul, dur, _yol))
                if len(ids) != len(set(ids)): n_bundle += 1
                # --- KUYRUK: işlenemeyen/çizelgeyi ATMAK yerine geri kazan.
                # Maddeler zaten tail'e kadar parçalandı (arka-metin madde SAYILMAZ,
                # sahte madde_id üretmez). Arka-metni AYRI, NÖTR chunk olarak yaz:
                # geçerli/geçersiz rozeti YOK -> yanlış etiketleme yerine doğru başlık.
                if tail < len(t):
                    kesilen = t[tail:].strip()
                    if len(kesilen) >= 30:
                        sinif = kuyruk_sinif(kesilen)
                        if sinif == 'ek_hukum':
                            for p in paragraflar(kesilen):
                                chunks.append(('ek_hukum', '', p)); n_ekh += 1
                        elif sinif == 'cizelge':
                            chunks.append(('cizelge', '', kesilen)); n_ciz += 1
                        # 'cop' -> at (güvenli). 'yanlis' -> guard sonrası olmamalı.

        if k == 'K2':
            for p in paragraflar(t):
                dur = madde_durumu(p)
                pm = (dur == 'geçersiz')
                if pm: n_mulga += 1
                chunks.append(('paragraf', '', p, pm, dur))

        if not chunks:
            sifir_chunk += 1
            continue

        for sira, ch in enumerate(chunks, 1):
            cid = f'{belge}#{sira}'
            if cid in gorulen_id: cakisma += 1
            gorulen_id.add(cid)
            tur = ch[0]; chunk_tur[tur] += 1; n_chunk += 1
            rec = {'chunk_id': cid, 'belge': belge, 'tur': tur}
            if tur == 'madde':
                rec['madde_id'] = ch[1]; rec['baslik'] = ch[3]
                rec['govde'] = ch[2]; rec['mulga'] = ch[4]; rec['durum'] = ch[5]
                if ch[6]: rec['baslik_yol'] = ch[6]
            else:
                rec['govde'] = ch[2]
                if tur == 'paragraf':
                    rec['mulga'] = ch[3]; rec['durum'] = ch[4]
            if out: out.write(json.dumps(rec, ensure_ascii=False) + '\n')
            if belge_filtre and tur in ('madde', 'paragraf'):
                _mid = str(rec.get('madde_id', ''))
                _dur = rec.get('durum', '-')
                durum_say[_dur] += 1
                # yalnız İLGİNÇ chunk'ları bas: aralık (X-Y) veya geçersiz/belirsiz
                if ('-' in _mid) or (_dur in ('geçersiz', 'belirsiz')):
                    _g = re.sub(r'\s+', ' ', rec.get('govde', '')).strip()[:88]
                    print(f"  [{tur}] m.{_mid or '—'} durum={_dur} mulga={rec.get('mulga','-')}: {_g}")
    if out: out.close()

    if belge_filtre:
        print('=' * 84)
        print(f"BELGE TEST: {belge_filtre}  (yukarıda yalnız aralık + geçersiz/belirsiz basıldı)")
        print(f"  durum dağılımı (tüm madde/paragraf): {dict(durum_say)}")

    print('=' * 84)
    print('PARCALA — SELF-CHECK' + ('  [TEST modu, dosya YAZILMADI]' if limit else ''))
    print('=' * 84)
    print(f'  taranan belge:        {sum(st.values())}')
    print(f'  kova dagilimi:        {dict(st)}   toplam={sum(st.values())}')
    print(f'  K5 atlanan:           {st["K5"]}')
    print(f'  0-chunk (K5 disi):    {sifir_chunk}  (0 olmali)')
    print(f'  toplam chunk:         {n_chunk}')
    print(f'  chunk tur dagilimi:   {dict(chunk_tur)}')
    print(f'  kuyruk kesilen belge: {n_tail}')
    print(f'  kuyruk geri kazanım:  ek_hukum={n_ekh} chunk  cizelge={n_ciz} chunk')
    print(f'  bundle (id tekrari):  {n_bundle}')
    print(f'  mulga/iptal chunk:    {n_mulga}  (madde + paragraf)')
    print(f'  benzersiz chunk_id:   {len(gorulen_id)}   cakisma={cakisma}  (0 olmali)')
    if not limit and not belge_filtre:
        print(f'\n  yazildi -> {hedef}')

if __name__ == '__main__':
    main()