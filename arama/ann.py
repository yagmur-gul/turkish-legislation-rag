#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arama/ann.py — YAKLAŞIK EN YAKIN KOMŞU indeksi (vektör aramasını hızlandırır).

SORUN: vek_ara() her sorguda 472.737 vektörün TAMAMINI tarıyor (kaba kuvvet).
Ölçüldü: ısınmış hâlde ~5 sn/sorgu. 20 sn'lik bütçenin dörtte biri buraya gidiyor
ve bu süre kaliteye hiç dönüşmüyor.

ÇÖZÜM: faiss IVF + skaler nicemleme (SQ8).
  - IVF: vektörler nlist kümeye bölünür, sorguda yalnız nprobe küme taranır
  - SQ8: her boyut 4 bayt yerine 1 bayt → 1.8 GB yerine ~480 MB
    (5.9 GB RAM'de reranker ile birlikte yaşayabilmesi için gerekli)

YAKLAŞIKTIR: küçük bir recall kaybı olur. nprobe bu kaybı ayarlar.
KULLANMADAN ÖNCE A/B ŞART — `--dogrula` kaba kuvvetle karşılaştırır.

  python arama/ann.py --kur                 # indeksi kur (~10-15 dk)
  python arama/ann.py --dogrula --n 30      # kaba kuvvetle recall karşılaştır
"""
import argparse, os, sys, time
from pathlib import Path

import numpy as np

KOK = Path(__file__).resolve().parent
sys.path.insert(0, str(KOK))
from vektor import VEC, IDS                        # noqa: E402

# Index adı vektör dosyasından türetilir → bölünmüş/bölünmemiş için ayrı dosya.
# vektorler_bge-m3.npy → ann_bge-m3.faiss ; vektorler_bge-m3-bolunmus.npy → ann_bge-m3-bolunmus.faiss
INDEKS = KOK / (VEC.stem.replace("vektorler", "ann") + ".faiss")
NLIST = int(os.environ.get("ANN_NLIST", "4096"))   # küme sayısı (~sqrt(N)*6)
NPROBE = int(os.environ.get("ANN_NPROBE", "64"))   # sorguda taranacak küme
EGITIM = int(os.environ.get("ANN_EGITIM", "200000"))   # eğitim örneklemi

_IX = None


def yukle():
    global _IX
    if _IX is None:
        import faiss
        if not INDEKS.exists():
            raise SystemExit(f"HATA: {INDEKS} yok. Önce: python arama/ann.py --kur")
        _IX = faiss.read_index(str(INDEKS))
        _IX.nprobe = NPROBE
    return _IX


def ara(qv, K=200):
    """qv: (1024,) normalize float32 → [(satir_indeksi, skor)] ilk K."""
    ix = yukle()
    D, I = ix.search(qv.reshape(1, -1).astype(np.float32), K)
    return I[0], D[0]


def kur():
    import faiss
    V = np.load(VEC, mmap_mode="r")
    N, d = V.shape
    print(f"vektör: {N:,} × {d}   nlist={NLIST}  SQ8", flush=True)
    t0 = time.time()

    kuantizer = faiss.IndexFlatIP(d)               # kosinüs = iç çarpım (vektörler normalize)
    ix = faiss.IndexIVFScalarQuantizer(kuantizer, d, NLIST,
                                       faiss.ScalarQuantizer.QT_8bit,
                                       faiss.METRIC_INNER_PRODUCT)
    rng = np.random.default_rng(42)
    ornek = np.sort(rng.choice(N, size=min(EGITIM, N), replace=False))
    print(f"eğitim örneklemi: {len(ornek):,} vektör yükleniyor...", flush=True)
    E = np.asarray(V[ornek], dtype=np.float32)
    print(f"eğitiliyor... ({time.time()-t0:.0f} sn)", flush=True)
    ix.train(E)
    del E
    print(f"eğitim bitti ({time.time()-t0:.0f} sn). ekleniyor...", flush=True)

    blok = 50000
    for i in range(0, N, blok):
        ix.add(np.asarray(V[i:i + blok], dtype=np.float32))
        print(f"  {min(i+blok,N):,}/{N:,}  ({time.time()-t0:.0f} sn)", flush=True)
    faiss.write_index(ix, str(INDEKS))
    mb = INDEKS.stat().st_size / 2**20
    print(f"\n→ {INDEKS}  ({mb:.0f} MB, {time.time()-t0:.0f} sn)")


def dogrula(n=30, K=200, nprobe_list=(16, 32, 64, 128)):
    """Kaba kuvvet ile ANN'i aynı sorgularda karşılaştır: ilk K örtüşmesi + süre."""
    import faiss
    sys.path.insert(0, str(KOK.parent))
    from kopru import genislet
    from vektor import model_yukle
    import json
    gold = [json.loads(l) for l in
            open(KOK.parent / "cikti" / "gold_madde_temiz.jsonl", encoding="utf-8")][:n]
    SP = Path("/tmp/claude-1000/-home-yagmur/d408e912-8684-4475-b28a-dac265933f3a/scratchpad")
    yen = {json.loads(l)["id"]: json.loads(l)
           for l in open(SP / "sorgu_yeniden.jsonl", encoding="utf-8") if l.strip()}
    q = [genislet((yen[k["orijinal_id"]]["terim"] + " " + yen[k["orijinal_id"]]["atif"]).strip())[0]
         for k in gold]
    m = model_yukle()
    Q = m.encode(q, normalize_embeddings=True, convert_to_numpy=True,
                 batch_size=4).astype(np.float32)
    import vektor as _v
    _v._MODEL = None
    del m

    V = np.load(VEC, mmap_mode="r")
    N = V.shape[0]
    ids = np.load(IDS, allow_pickle=True)
    hedef = [{h["chunk_id"] for h in g["hedefler"]} for g in gold]

    print("kaba kuvvet referansı hesaplanıyor...", flush=True)
    t0 = time.time()
    KK = []
    for j in range(len(Q)):
        s = np.empty(N, dtype=np.float32)
        for i in range(0, N, 50000):
            s[i:i + 50000] = V[i:i + 50000].astype(np.float32) @ Q[j]
        top = np.argpartition(-s, K - 1)[:K]
        KK.append(set(top[np.argsort(-s[top])]))
    kaba_sn = (time.time() - t0) / len(Q)
    kaba_isabet = sum(1 for j in range(len(Q))
                      if {str(ids[x]) for x in KK[j]} & hedef[j])
    print(f"  kaba kuvvet: {kaba_sn:.2f} sn/sorgu · hedef ilk-{K}'de {kaba_isabet}/{len(Q)}\n")

    ix = faiss.read_index(str(INDEKS))
    print(f"{'nprobe':>7} | {'ortusme':>8} | {'hedef ilk-K':>12} | {'sn/sorgu':>9} | hizlanma")
    print("-" * 62)
    for np_ in nprobe_list:
        ix.nprobe = np_
        ix.search(Q[:2], K)                                   # ısınma
        t0 = time.time()
        _, I = ix.search(Q, K)
        sn = (time.time() - t0) / len(Q)
        ort = np.mean([len(KK[j] & set(I[j])) / K for j in range(len(Q))])
        isb = sum(1 for j in range(len(Q)) if {str(ids[x]) for x in I[j]} & hedef[j])
        print(f"{np_:>7} | {100*ort:7.1f}% | {isb:>6}/{len(Q):<5} | {sn:8.3f} | {kaba_sn/sn:6.0f}x")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--kur", action="store_true")
    ap.add_argument("--dogrula", action="store_true")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--K", type=int, default=200)
    a = ap.parse_args()
    if a.kur:
        kur()
    elif a.dogrula:
        dogrula(a.n, a.K)
    else:
        ap.print_help()