#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
uret.py — getirilen maddelerden CEVAP üretir (üretim kütüphanesi).

cevapla.py ve app.py bunu kullanır: maddeleri numaralı kaynak bloğuna çevirir
(kaynak_metni), sonra OpenRouter (orh) üzerinden LLM'e verip cevabı alır (cagir).

TASARIM — üç kısıt (ölçüm ayrıntısı: denemeler.md):
  1 DAYANAK   yalnız verilen maddelere dayan, kendi hukuk bilgini kullanma
              (yoksa model ezberinden uydurur ve uydurduğu DOĞRU GÖRÜNÜR).
  2 ATIF      her hükmü [3] biçiminde kaynağa bağla (kullanıcı doğrulayabilsin).
  3 ÇEKİMSER  kaynaklarda yoksa cevap veremediğini söyle (hukukta uydurmak,
              cevapsız kalmaktan kötüdür).
"""
import re
import time
from pathlib import Path

KOK = Path(__file__).resolve().parent

# Canlı üretim prompt'u. (Önceki v1/v3 varyantları A/B ile elendi — bkz denemeler.md.)
SISTEM2 = """Sen Türk mevzuatı üzerinde çalışan bir hukuki bilgi asistanısın.

Sana bir vatandaş sorusu ve numaralandırılmış KANUN MADDELERİ verilecek.

ÖNCE ŞUNU YAP (kısa tut, en fazla 2 cümle):
İLGİLİ MADDELER: Sorunun cevabını hangi maddeler veriyor? Numaralarını yaz ve
her biri için tek cümleyle neden ilgili olduğunu belirt. Konuyla ilgili
GÖRÜNEN ama soruyu cevaplamayan maddeleri buraya YAZMA — çoğu madde böyledir.

SONRA cevabı yaz.

KURALLAR — istisnasız:

1. YALNIZ verilen maddelere dayan. Kendi hukuk bilgini KULLANMA. Verilen
   maddelerde olmayan bir kural, süre, oran veya sonuç YAZMA.

2. Kullandığın her hükmü kaynağına bağla: "... yapılabilir [3]." Birden çok
   maddeye dayanıyorsan hepsini yaz: "[2][7]".

3. Maddeler soruyu KISMEN cevaplıyorsa, cevaplayabildiğin kadarını yaz ve
   eksik kalan noktayı sonda tek cümleyle belirt. Baştan "cevaplamıyor" deme.
   Yalnız HİÇBİR madde ilgili değilse "Verilen maddeler bu soruyu
   cevaplamıyor." yaz.

4. Hukuki tavsiye verme, mevzuatın ne dediğini aktar. Somut olayın sonucu
   için avukata başvurulması gerektiğini belirt.

BİÇİM:
  İLGİLİ MADDELER: ...
  CEVAP: 3-8 cümle, sade Türkçe, terimi parantezle ver"""


ATIF_D = re.compile(r"\[(\d{1,3})\]")

# OpenRouter provider yönlendirmesi (ör. fp4'ü dışla, fp8'e git). app.py/cevapla.py ayarlar.
_SAGLAYICI = None


def _ana(cid):
    return cid.split("/")[0] if "/" in cid.split("#")[-1] else cid


def kaynak_metni(satirlar, sira="iyi_once"):
    """Maddeleri numaralandırılmış kaynak bloğuna çevir.
    ORTADA KAYBOLMA: modeller listenin başını/sonunu ortasından iyi hatırlar;
    sıra 'iyi_once' (reranker sırası) canlıda kullanılır."""
    if sira == "iyi_sonra":
        satirlar = list(reversed(satirlar))
    p = []
    for i, r in enumerate(satirlar, 1):
        bas = (r.get("baslik") or "").strip()
        ad = (r.get("belge_ad") or "").replace("\n", " ").strip()
        p.append(f"[{i}] {ad} — {bas}\n{(r.get('govde') or '').strip()}")
    return "\n\n".join(p), satirlar


def cagir(model, sis, kul, max_tokens=900):
    """OpenRouter (orh) üzerinden üretim çağrısı. Model 'saglayici/model' biçiminde
    (ör. deepseek/deepseek-chat)."""
    import orh
    c, cost, g, ck, mh = orh.cagir(model, sis, kul, max_tokens, saglayici=_SAGLAYICI)
    return c, {"prompt_tokens": g, "completion_tokens": ck,
               "completion_tokens_details": {"reasoning_tokens": mh}, "cost": cost}


def cagir_retry(model, sis, kul, max_tokens, dene=3):
    """Geçici hatada (ağ/rate-limit) 3 kez dener, artan bekleme ile."""
    son = None
    for i in range(dene):
        try:
            return cagir(model, sis, kul, max_tokens)
        except Exception as e:
            son = e
            time.sleep(2 * (i + 1))
    raise son