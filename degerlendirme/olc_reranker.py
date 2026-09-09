#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
degerlendirme/olc_reranker.py — reranker A/B: SÜRE + retrieval doğruluğu (BEDAVA).

Farklı reranker modeli/havuz ayarını karşılaştırmak için. Cevap ÜRETMEZ (deepseek
çağırmaz) → bakiye harcamaz. Köprü kural modunda (KOPRU=kural) LLM de çağırmaz.

Ölçtüğü: hibrit_rerank sonrası doğru maddenin (dogru_chunk) kaçıncı sırada geldiği
→ @1/@3/@10/@30/@50/@100 oranı + soru başına süre. ASIL METRİK @30 (LLM'e giden
top-30'a doğru madde girdi mi). @50/@100, havuz o kadar değilse @havuz'a eşittir.

Ayarlar (env):
  RERANK_MODEL   reranker (vars. BAAI/bge-reranker-v2-m3; hafif: BAAI/bge-reranker-base)
  ARAMA_HAVUZ    rerank havuzu (vars. 200; hızlı: 50)
  RERANK_MAXLEN  okuma uzunluğu (vars. 512)
  KOPRU          kural (bedava) | llm
  PER_ALAN       alan başına soru (vars. 3 -> 93)

Çalıştır (kökten, venv):
  RERANK_MODEL=BAAI/bge-reranker-v2-m3 ARAMA_HAVUZ=200 KOPRU=kural python degerlendirme/olc_reranker.py
  RERANK_MODEL=BAAI/bge-reranker-base  ARAMA_HAVUZ=50  KOPRU=kural python degerlendirme/olc_reranker.py
"""
import sys, os, json, time, collections, statistics
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KOK))
sys.path.insert(0, str(KOK / "arama"))
import konfig  # noqa: F401
from vektor import hibrit_rerank
import uret

PER_ALAN = int(os.environ.get("PER_ALAN", "3"))
LIMIT = int(os.environ.get("LIMIT", "0"))
GOLD = KOK / "degerlendirme" / "sorular.jsonl"
RMODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
HAVUZ = os.environ.get("ARAMA_HAVUZ", "200")
MAXLEN = os.environ.get("RERANK_MAXLEN", "512")
KOPRU = os.environ.get("KOPRU", "kural")


def rank_of(satir, hedef_ana):
    for i, r in enumerate(satir, 1):
        if uret._ana(r.get("chunk_id", "")) in hedef_ana:
            return i
    return None


def main():
    gold = [json.loads(l) for l in open(GOLD, encoding="utf-8") if l.strip()]
    by = collections.OrderedDict()
    for k in gold:
        by.setdefault(k.get("alan", "?"), []).append(k)
    secili = [k for ks in by.values() for k in ks[:PER_ALAN]]
    if LIMIT:
        secili = secili[:LIMIT]
    print(f"{len(secili)} soru · reranker={RMODEL} · havuz={HAVUZ} · maxlen={MAXLEN} · kopru={KOPRU}", flush=True)

    KADEME = (1, 3, 10, 30, 50, 100)
    top = {K: 0 for K in KADEME}
    sureler = []
    tot = 0
    for i, k in enumerate(secili, 1):
        soru = " ".join(k["soru"].split())
        hedef_ana = {uret._ana(c) for c in k.get("dogru_chunk", [])}
        try:
            t0 = time.time()
            satir = hibrit_rerank(soru, n=300)     # tüm rerank'li liste (havuz kadar)
            dt = time.time() - t0
        except Exception as e:
            print(f"[{i}] HATA: {e}", flush=True)
            continue
        tot += 1
        sureler.append(dt)
        r = rank_of(satir, hedef_ana)
        for K in KADEME:
            if r and r <= K:
                top[K] += 1
        srr = f"r{r}" if r else "--"
        print(f"[{i}/{len(secili)}] {k['alan']:<14} {srr:<4} {dt:.1f}sn | "
              f"@30 {top[30]}/{tot}=%{100*top[30]/tot:.0f}", flush=True)
    print("=" * 58, flush=True)
    if tot:
        ort = statistics.mean(sureler)
        med = statistics.median(sureler)
        print(f"SONUC · {RMODEL} · havuz={HAVUZ} maxlen={MAXLEN}", flush=True)
        print(f"  n={tot}  ·  sure ort {ort:.1f}sn / medyan {med:.1f}sn", flush=True)
        print("  " + " · ".join(f"@{K} %{100*top[K]/tot:.1f}" for K in KADEME), flush=True)


if __name__ == "__main__":
    main()
