#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
degisiklik.py — madde metnindeki değişiklik/iptal işaretlerini AYRIŞTIR.

Tek kaynak: hem batch (tüm corpus -> cikti/degisiklikler.jsonl) hem app.py aynı
`isaretleri_cikar()` fonksiyonunu kullanır.

Kapsanan:
  - 5 ana tür: Değişik / Ek / Mülga / İptal / Yeniden düzenleme (+ küçük harf 'mülga' vb.)
  - Çoklu işaret: "(Ek: ...; Mülga: ...)" -> ';' ile bölünüp HER ikisi de kaydedilir.
  - Önek: "(İkinci fıkra mülga: ...)" -> anahtar parantez başında olmasa da yakalanır.
  - Tarih: hem '/' hem '.' ayraç (10/7/2008 ve 31.1.1973).
  - Araç: Kanun / KHK / Cumhurbaşkanlığı Kararnamesi (CK) / Karar (BKK, Tüzük 'K.') /
          Resmî Gazete (RG) / Anayasa Mahkemesi kararı.
  - GUARD: bir parantez ancak içinde tarih / R.G. / Anayasa Mahkemesi varsa gerçek
    işarettir (yanlış pozitifleri eler: "Ekim", "Eksenel...", "Ek IX, Madde").
  - KAPANMAYAN/UZUN: tam-madde AYM iptal notları çoğu kez 240 krk'yı aşar ya da
    parantezi kapanmaz; _PAREN bunları kaçırıyordu. 2. geçiş bu açılışları
    işaretten kapanışa/+400 krk'lık pencereyle yakalar (tam-madde iptalleri kurtarır).

Sınıf (tur): degisik | ek | mulga | iptal
Kaynak: metin (satır-içi) | cizelge (resmî çizelge, yalnız iptal)

Batch (proje kökünde): python degisiklik.py
"""
import re, json, sqlite3, collections
from pathlib import Path

DASH = r'\-‐‑‒–—―−'                                            # class içinde güvenli (- kaçışlı)
SEP = rf'[\s.{DASH}/]*'                                        # ayraç: boşluk, tire türleri, nokta, /
_PAREN = re.compile(r'\(([^)]{0,240})\)')
# Kapanmayan/uzun işaret açılışı: '(' + (ilk 40 krk içinde) anahtar. Önek formunu da
# yakalar ('(İkinci fıkra mülga:' — anahtar '(' başında değil).
_OPEN2 = re.compile(r'\([^)]{0,40}?(?:Değişik|Ek|Mülga|İptal|Hükümsüz|Yeniden [Dd]üzenleme|mülga|değişik|iptal|hükümsüz)')
_TARIH = re.compile(r'\d{1,2}[./]\d{1,2}[./]\d{4}')            # '/' veya '.'
# anahtar kelime-sınırıyla (Eklenmiştir'deki 'Ek'i yakalamasın)
_KW = re.compile(r'(Değişik|Ek|Mülga|İptal|Hükümsüz|Yeniden [Dd]üzenleme|mülga|değişik|iptal|hükümsüz)'
                 rf'(?=[\s:.,;)\d{DASH}/]|$)')
_RG = re.compile(r'R\.?\s*G')
_KK = re.compile(r'K\.?\s*:?\s*(\d{4}/\d+)')                   # AYM karar K.:2011/81
_KHK = re.compile(rf'KHK{SEP}(\d+)')
_CK = re.compile(rf'C\.?\s*K{SEP}((?:19|20)\d{{2}}/\d+|\d+)')   # 'CK-2022/310' tam no ya da sade CBK no
_SKARAR = re.compile(r'(\d+)\s*S\.?\s*Karar')                  # "1200 S.Karar", "424 S. Karar"
_KARAR = re.compile(rf'(\d{{1,4}}/\s*\d{{2,5}})\s*(?:[{DASH}]\s*)?(?:sayılı\s*)?(?:K\.|BKK|Karar)')
_RGNUM = re.compile(rf'\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}\s*[{DASH}]?\s*(\d{{4,6}})(?![/\d])')  # RG sayısı
_KANUN = re.compile(rf'\d{{1,2}}[./]\d{{1,2}}[./]\d{{4}}\s*[{DASH}/]*\s*(\d{{2,5}})/')   # kanun/madde; 'tarih/-5728/400' gibi ekstra ayraç da tolere edilir
_TUZUK = re.compile(rf'[{DASH}]\s*(\d{{1,2}}/\d{{3,5}})(?![\d/])')   # eski tüzük/karar 'Y/NNNN' (K. yok)
_LKARAR = re.compile(r'(\d{6,9})\s*K\.')                       # uzun karar sayısı "20168473 K."
_PROSE = re.compile(r'(\d{2,5})\s+sayılı')                     # "6360 sayılı"
_MGK = re.compile(r'(\d{1,4})\s*(?:No\.?\s*lu\s*)?M\.?\s*G\.?\s*K\b')   # "155 No.lu M.G.K. kararı"


def trf(s):
    return s.replace('İ', 'i').replace('I', 'ı').lower()


def _gercek(ham):
    """Gerçek değişiklik işareti mi? (yanlış pozitif guard)"""
    return bool(_TARIH.search(ham) or _RG.search(ham) or 'anayasa mahkeme' in trf(ham))


def _tur(kw):
    k = trf(kw)
    if k.startswith('iptal'):
        return 'iptal'
    if k.startswith('mülga') or k.startswith('mulga') or k.startswith('hükümsüz') or k.startswith('hukumsuz'):
        return 'mulga'       # hükümsüz (butlan) = kaldırılmış hüküm
    if k == 'ek':
        return 'ek'
    return 'degisik'          # değişik, yeniden düzenleme


def _karar_no(ham, yy):
    """Mahkeme karar numarasını çöz (K:YYYY/NNNN). İki karar varsa (Daire iptal +
    İDDK onama) tarih-yılına uyanı = operatif/kesinleşen kararı seçer, yoksa sonuncuyu."""
    ks = re.findall(r'K[.:;\s]{0,4}(\d{4}/\d+)', ham)
    if not ks:
        return ''
    for k in ks:
        if yy and k.split('/')[0] == yy:
            return k
    return ks[-1]


def _ref(ham):
    """(tarih, arac) çöz. Araç: mahkeme (Danıştay/AYM) / Kanun / KHK / CK / BKK / R.G."""
    dm = _TARIH.search(ham)
    tarih = dm.group(0).replace('.', '/') if dm else ''
    yy = tarih.split('/')[-1] if tarih else ''
    t = trf(ham)
    # MAHKEME İPTALİ: Danıştay ya da Anayasa Mahkemesi (kısaltmalar dâhil).
    # iptal mahkemeden gelir -> karar numarası döner, ASLA "sayılı Kanun" değil.
    if 'danıştay' in t or 'danistay' in t:
        kn = _karar_no(ham, yy)
        return tarih, 'Danıştay kararı' + (f' (K:{kn})' if kn else '')
    if 'anayasa mahkeme' in t or 'ana. mah' in t or 'ana.mah' in t:
        kn = _karar_no(ham, yy)
        return tarih, 'Anayasa Mahkemesi kararı' + (f' (K:{kn})' if kn else '')
    m = _KHK.search(ham)
    if m:
        return tarih, f'{m.group(1)} sayılı KHK'
    m = _CK.search(ham)
    if m:
        no = m.group(1)
        # YYYY/NNNN = Cumhurbaşkanı Kararı; sade numara = Cumhurbaşkanlığı Kararnamesi (CBK).
        tip = 'Cumhurbaşkanı Kararı' if '/' in no else 'Cumhurbaşkanlığı Kararnamesi'
        return tarih, f'{no} sayılı {tip}'
    m = _SKARAR.search(ham) or _LKARAR.search(ham)
    if m:
        return tarih, f'{m.group(1)} sayılı Karar'
    m = _KARAR.search(ham)
    if m:
        return tarih, f'{re.sub(r"[^0-9/]", "", m.group(1))} sayılı Karar'
    # BKK adıyla: 'N/NNNN sayılı Bakanlar Kurulu Kararı' -> Karar (tarih parçasını kapma).
    mb = re.search(r'(\d{1,2}/\d{3,5})\s+sayılı\s+bakanlar', t)
    if mb:
        return tarih, f'{mb.group(1)} sayılı Karar'
    # BKK: 'DATE-YYYY/NNNN' (tarih yılı kendini tekrar ediyor) = Bakanlar Kurulu Kararı,
    # Kanun DEĞİL. Bir yıla eşit kanun numarası o yıl çıkarılamayacağından ayrım güvenli.
    if yy:
        mb = re.search(rf'(?<!\d){yy}/(\d+)', ham)
        if mb:
            return tarih, f'{yy}/{mb.group(1)} sayılı Karar'
        if re.search(rf'(?<!\d){yy}/\s*K\.?\b', ham):   # DAR: '2000/K.' — BKK, serisi OCR'da kayıp
            return tarih, f'{yy} sayılı Karar'
    m = _MGK.search(ham)
    if m:
        return tarih, f'{m.group(1)} sayılı MGK Kararı'
    if _RG.search(ham) or _RGNUM.search(ham):
        return tarih, (f'R.G. {tarih}' if tarih else 'R.G.')
    m = _KANUN.search(ham)
    if m:
        return tarih, f'{m.group(1)} sayılı Kanun'
    m = _TUZUK.search(ham)
    if m:
        return tarih, f'{m.group(1)} sayılı Karar'
    m = _PROSE.search(ham)
    if m:
        return tarih, f'{m.group(1)} sayılı Kanun'
    return tarih, ''


def _isle(icerik, out, gorulen):
    """Bir parantez içeriğini işle: çoklu/önek işaret bölünür, tekilleştirilir."""
    if not _gercek(icerik):
        return
    # Her anahtarın segmenti: o anahtardan BİR SONRAKİ anahtara kadar.
    # Böylece ';', ',' ya da ayraçsız çoklu işaret ("Ek: ... Mülga: ...") bölünür,
    # AYM içindeki virgül ("E.:2008/80, K.:2011/81") tek segmentte kalır.
    kws = list(_KW.finditer(icerik))
    for i, km in enumerate(kws):
        son = kws[i + 1].start() if i + 1 < len(kws) else len(icerik)
        seg = icerik[km.start():son]
        if not _gercek(seg):
            seg = icerik                      # kendi tarihi yoksa parantezinki
        tur = _tur(km.group(1))
        tarih, arac = _ref(seg)
        anahtar = (tur, tarih, arac)
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        out.append({'tur': tur, 'tarih': tarih, 'arac': arac,
                    'kaynak': 'metin', 'ref': _tam_ref(seg)})


def isaretleri_cikar(govde):
    """Gövdedeki satır-içi değişiklik/iptal işaretleri -> [{tur, tarih, arac, kaynak, ref}].
    Kaymasız (metnin kendi içinde). Tekilleştirilmiş. Çoklu/önek/nokta-tarih kapsanır.

    2 geçiş:
      (1) Kapalı parantezler (≤240 krk) — önceki davranış; önek formları dâhil.
      (2) KAPANMAYAN / >240 işaret açılışları: '(' den kapanışa YA DA +400 krk'lık
          pencere. _PAREN bunları hiç yakalamıyordu -> tam-madde AYM iptalleri kayıptı.
    """
    g = govde or ''
    out, gorulen = [], set()
    for pm in _PAREN.finditer(g):                       # 1) kapalı & kısa
        _isle(pm.group(1), out, gorulen)
    for om in _OPEN2.finditer(g):                       # 2) kapanmayan / uzun
        st = om.start()
        kapanis = g.find(')', st)
        if kapanis != -1 and kapanis - st <= 240:
            continue                                    # 1) zaten aldı
        _isle(g[st + 1:min(st + 400, kapanis if kapanis != -1 else st + 400)],
              out, gorulen)
    return out


# ---------------- batch ----------------
def _cizelge_kayitlari():
    """Resmî çizelge kayıtlarının TAMAMI (iptal + değişik) -> {belge:{madde:[k]}}."""
    yol = Path('cikti/yururluk_kayitlari.jsonl')
    d = {}
    if not yol.exists():
        return d
    for satir in yol.read_text(encoding='utf-8').splitlines():
        satir = satir.strip()
        if not satir:
            continue
        k = json.loads(satir)
        d.setdefault(k['belge'], {}).setdefault(str(k['madde_id']), []).append(k)
    return d


def _arac_no(arac):
    """Araç kimliği: AYM+karar no ya da düzenleme numarası — tekrarları elemek için.
    Farklı AYM kararları farklı kimlik alır (aksi halde hepsi 'AYM'ye çöker)."""
    a = (arac or '').lower()
    if 'anayasa mahkeme' in a:
        m = re.search(r'[kK]\.?\s*:?\s*(\d{4}/\d+)', arac or '')   # K: 2023/32
        return 'AYM-' + m.group(1) if m else 'AYM'
    m = re.search(r'(\d+)\s*sayılı', arac or '') or re.search(r'\d{2,}', arac or '')
    return m.group(len(m.groups())) if m else ''


# Dipnot cümlesi (detay). Başlangıç: "Bu madde/fıkra/bent...", "Anayasa Mahkeme..."
# ya da "gg/aa/yyyy tarihli ve..." — böylece "Bu maddede yer alan 'X' ibaresi" öneki de girer.
_FOOT = re.compile(
    r'(?:Bu\s+(?:madde|f[ıi]kra|bent|Kanun|K[.\s])\w*|Anayasa\s+Mahkeme\w*|'
    r'\d{1,2}[./]\d{1,2}[./]\d{4}\s+[Tt]arihli\s+ve)'
    r'.{0,2000}?\w(?:mış|miş|muş|müş)t[ıiuü]r\.', re.S)   # uzun dipnotlar da tam girsin


def _dipnot_cumleleri(govde):
    return [re.sub(r'\s+', ' ', m.group(0)).strip() for m in _FOOT.finditer(govde or '')]


def _dipnot_turu(c):
    """Dipnot cümlesinin türü — bitiş fiiline göre (kayıt türüyle eşleştirmek için)."""
    cl = c.lower()
    if 'iptal' in cl or 'hükümsüz' in cl:
        return 'iptal'
    if 'yürürlükten kaldırıl' in cl:
        return 'mulga'
    if 'eklenmiştir' in cl and 'değiş' not in cl:
        return 'ek'
    return 'degisik'


_ORD = r'(?:inci|ıncı|uncu|üncü|nci|ncı|üncü)'


def _tam_ref(s):
    """TAM referans: kanun numarası + o kanunun DEĞİŞTİREN maddesi ('5786/6') ya da
    AYM karar no ('AYM-2026/25'). Sadece kanun no yeterli değil (aynı kanun onlarca
    dipnotta geçer) — bu yüzden değiştiren madde/karar no ile numara takibi yapılır."""
    if 'anayasa mahkeme' in trf(s):
        m = re.search(r'[kK]\.?\s*:?\s*(\d{4}/\d+)', s)
        return 'AYM-' + m.group(1) if m else ''
    m = re.search(r'(\d{2,5})/(\d+)\s*md', s)                       # inline: "5786/6 md."
    if m:
        return f'{m.group(1)}/{m.group(2)}'
    m = re.search(rf'(\d{{2,5}})\s*sayılı[^.]{{0,45}}?(\d+)\s*{_ORD}\s*madde', s)  # dipnot: "5786 sayılı Kanunun 5 inci maddesiyle"
    if m:
        return f'{m.group(1)}/{m.group(2)}'
    return ''                                                        # tam ref yok -> eşleştirme


def _detay_bul(rec, cumleler):
    """Kaydın TAM referansı (kanun/madde ya da AYM-karar) VE türü ile eşleşen dipnot.
    Tam ref yoksa detay EKLENMEZ. Tür de eşleşmeli ki aynı ref'li ek/değişik karışmasın."""
    ref = rec.get('ref', '')
    if not ('/' in ref or ref.startswith('AYM-')):
        return ''
    tur = rec.get('tur')
    for c in cumleler:
        if _tam_ref(c) == ref and _dipnot_turu(c) == tur:
            return c                      # tam dipnot metni (kesme yok — hukuki metin)
    return ''


# "Geçici uygulama" / İşlenemeyen Hüküm dipnotu: "Bu madde 1/7/2012 tarihinden itibaren
# 8 yıl süreyle uygulanmaz. ... uygulanır." Bir dipnot, "²" işaretiyle BİRÇOK maddede
# kullanılır; çizelgede madde numarası olmadığından ancak işaret eşlemesiyle bağlanır.
_GECICI = re.compile(r'Bu\s+(?:madde|Kanun|f[ıi]kra|bent|h[üu]k[üu]m)\w*[^.]{0,90}?'
                     r'\d{1,2}/\d{1,2}/\d{4}\s+tarihinden\s+itibaren'
                     r'.{0,700}?uygulan[ıi]r\.', re.S)


def _gecici_notlar(govde):
    """(dipnot_no, tur, metin) — gövdedeki ilgili dipnotlar. tur:
       'uygulama' = askı ("... uygulanmaz/ertelen..."),
       'yururluk' = yürürlük/uygulama tarihi ("... tarihinden itibaren uygulanır")."""
    out = []
    for m in _GECICI.finditer(govde or ''):
        txt = re.sub(r'\s+', ' ', m.group(0)).strip()
        nm = re.search(r'(\d{1,3})\s*$', (govde[:m.start()]).rstrip())
        low = txt.lower()
        # ASKI ancak gerçek "uygulanmaz/uygulanmayacak/ertelen" ise; "uygulanması/uygulaması
        # ile ilgili … bakınız" bir ASKI değil (tarife/yürürlük atfı) -> yururluk.
        askı = ('uygulanmaz' in low or 'uygulanmayacak' in low or 'ertelenmiş' in low
                or 'ertelenir' in low or 'ertelenmez' in low)
        out.append((nm.group(1) if nm else '', 'uygulama' if askı else 'yururluk', txt))
    return out


def _isaret_atif(run, no):
    """Başlık sonu işaret dizisi 'run', 'no' numaralı dipnota atıf yapıyor mu?
    'run'=='2' -> evet; 'run'=='234' (tek haneli dizi) -> '2' içeriyorsa evet;
    2-haneli 'run' ('12','19') tek bir başka footnote'tur -> tek haneli no ile eşleşmez."""
    if not run or not no:
        return False
    if run == no:
        return True
    if len(run) >= 3 and len(no) == 1:
        return no in run
    return False


def _coz_madde(cmid, ids, norm):
    """Çizelge madde_id'sini mevcut bir chunk madde_id'sine çöz: tam -> harf/boşluk
    normalize -> ana madde ('8/B' -> '8'). Yoksa None."""
    if cmid in ids:
        return cmid
    n = cmid.replace(' ', '').lower()
    if n in norm:
        return norm[n]
    taban = cmid.split('/')[0].strip()          # '8/B' -> '8', '362/A' -> '362'
    if taban != cmid and taban in ids:
        return taban
    # parcala id-artefaktı: "Madde N – 1." tek maddesi chunk'ta 'N-1' olarak etiketlenmiş.
    # Çizelge sade 'N' der -> 'N-1' chunk'ına bağla (aksi halde AYM iptali AI'a ulaşmaz).
    if cmid.isdigit() and (cmid + '-1') in ids and (cmid + '-2') not in ids:
        return cmid + '-1'
    return None


def main():
    DB = 'arama/mevzuat.db'
    OUT = Path('cikti/degisiklikler.jsonl')
    con = sqlite3.connect(DB)
    rows = con.execute(
        "SELECT belge, madde_id, mulga, baslik, govde FROM chunks WHERE tur='madde' "
        "ORDER BY rowid").fetchall()          # belge/madde sırası (drift penceresi için)
    cizelge = _cizelge_kayitlari()

    # 1) satır-içi geçiş
    sonuc = {}                                  # (belge, mid) -> [kayıt]
    mulga_bayrak = {}
    dipnot_by_key = {}                          # (belge, mid) -> [dipnot cümlesi]
    baslik_by_key = {}                          # (belge, mid) -> başlık (işaret için)
    gecici_by_belge = collections.defaultdict(dict)   # belge -> {dipnot_no: metin}
    sira = []                                   # sıralı (belge, mid) — "sonraki chunk" için
    ids_by_belge = collections.defaultdict(set)
    for belge, mid, mulga, baslik, govde in rows:
        mid = str(mid)
        ids_by_belge[belge].add(mid)
        baslik_by_key[(belge, mid)] = baslik or ''
        for no, gtur, txt in _gecici_notlar(govde):
            if no:
                gecici_by_belge[belge][no] = (gtur, txt)
        fc = _dipnot_cumleleri(govde)
        if fc:
            dipnot_by_key[(belge, mid)] = fc
        sira.append((belge, mid))
        recs = isaretleri_cikar(govde)
        if recs:
            # Aynı madde_id birden çok chunk'ta olabilir (bundle=id tekrarı). ÜZERİNE
            # YAZMA -> BİRLEŞTİR ki kaymasız işaretler (özellikle mega-belge geçici
            # maddeleri) kaybolmasın. (tur, tarih, arac) ile tekilleştir.
            var = sonuc.setdefault((belge, mid), [])
            _v = {(r['tur'], r['tarih'], r['arac']) for r in var}
            for r in recs:
                a = (r['tur'], r['tarih'], r['arac'])
                if a not in _v:
                    var.append(r); _v.add(a)
        if mulga:
            mulga_bayrak[(belge, mid)] = True

    # Aday dipnotlar: drift İLERİ olduğu için her madde = kendi chunk'ı + SONRAKİ chunk
    # (aynı belge). Tam-ref eşleşmesi yanlış bağlamayı zaten engelliyor.
    aday = {}
    for i, (belge, mid) in enumerate(sira):
        c = list(dipnot_by_key.get((belge, mid), []))
        if i + 1 < len(sira) and sira[i + 1][0] == belge:
            c += dipnot_by_key.get(sira[i + 1], [])
        if c:
            aday[(belge, mid)] = c

    # satır-içi kayıtlara detay (aday penceresinden)
    for (belge, mid), recs in sonuc.items():
        for r in recs:
            d = _detay_bul(r, aday.get((belge, mid), []))
            if d:
                r['detay'] = d

    # 2) çizelge kayitlarini (iptal + değişik) ÇÖZÜLMÜŞ maddeye ekle — inline'da
    #    ZATEN olan aracı (numarasına göre) TEKRARLAMA. Böylece dipnot-tipi (satır-içi
    #    işaret bırakmayan) değişiklikler de görünür, çift kayıt olmaz.
    ciz_baglanmadi = 0
    for belge, mids in cizelge.items():
        ids = ids_by_belge.get(belge, set())
        norm = {m.replace(' ', '').lower(): m for m in ids}
        for cmid, kayitlar in mids.items():
            hedef = _coz_madde(cmid, ids, norm)
            if hedef is None:
                ciz_baglanmadi += 1
                continue
            var = sonuc.setdefault((belge, hedef), [])
            mevcut = {_arac_no(k.get('arac', '')) for k in var}
            for c in kayitlar:
                # BOZUK çizelge satırı: 'tarih' geçerli bir tarih değil (düz sayı) ve 'arac'
                # cümle parçası -> kullanılamaz künye. Yanlış künye vermektense atla (inline
                # parser gerçek değişikliği zaten ayrıca yakalıyor).
                if not _TARIH.search(c.get('tarih', '') or ''):
                    continue
                ano = _arac_no(c.get('arac', ''))
                if ano and ano in mevcut:
                    continue                     # inline ya da önceki çizelge kapsıyor
                tur = 'iptal' if c.get('durum') == 'iptal' else 'degisik'
                r = {'tur': tur, 'tarih': c.get('tarih', ''),
                     'arac': c.get('arac', ''), 'kaynak': 'cizelge',
                     'ref': _tam_ref(c.get('arac', ''))}
                if hedef != cmid:
                    r['asil_madde'] = cmid       # değişiklik aslında bu alt-maddede
                d = _detay_bul(r, aday.get((belge, hedef), []))
                if d:
                    r['detay'] = d
                var.append(r)
                mevcut.add(ano)

    # 2b) GEÇİCİ UYGULAMA notu: paylaşımlı dipnotu, "²" işaretiyle atıf yapan TÜM maddelere
    #     bağla (çizelgede maddesiz "İşlenemeyen Hüküm" olduğu için tek yol işaret eşlemesi).
    gu_say = collections.Counter()
    for belge, notlar in gecici_by_belge.items():
        for mid in ids_by_belge.get(belge, ()):
            rm = re.search(r'(\d{1,4})\s*$', baslik_by_key.get((belge, mid), '').strip())
            run = rm.group(1) if rm else ''
            for no, (gtur, txt) in notlar.items():
                if _isaret_atif(run, no):
                    sonuc.setdefault((belge, mid), []).append(
                        {'tur': gtur, 'tarih': '', 'arac': '',
                         'kaynak': 'dipnot', 'detay': txt})
                    gu_say[gtur] += 1

    # 3) yaz + istatistik
    say = collections.Counter()
    arac_bos = mulga_uyumsuz = 0
    with OUT.open('w', encoding='utf-8') as f:
        for (belge, mid), kayitlar in sonuc.items():
            for k in kayitlar:
                say[k['tur']] += 1
                if k['tur'] != 'iptal' and not k['arac']:
                    arac_bos += 1
            if mulga_bayrak.get((belge, mid)) and not any(k['tur'] in ('mulga', 'iptal') for k in kayitlar):
                mulga_uyumsuz += 1
            f.write(json.dumps({'belge': belge, 'madde_id': mid,
                                'kayitlar': kayitlar}, ensure_ascii=False) + '\n')

    print('=' * 60)
    print('DEĞİŞİKLİK AYRIŞTIRMA')
    print('=' * 60)
    print(f'kayıtlı madde        : {len(sonuc)}')
    print(f'tür dağılımı         : {dict(say)}')
    print(f'araç okunamayan      : {arac_bos}')
    print(f'mülga uyumsuz        : {mulga_uyumsuz}')
    print(f'çizelge bağlanamayan : {ciz_baglanmadi}  (madde chunk\'ı hiç yok)')
    print(f'geçici/yürürlük notu : {dict(gu_say)}  (işaret eşlemesiyle bağlanan)')
    print(f'yazıldı -> {OUT}')


if __name__ == '__main__':
    main()