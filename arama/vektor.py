#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arama/vektor.py — Tam embed + HİBRİT arama (BM25 + vektör, RRF ile).

Neden hibrit: vektör anlamı yakalar (ev sahibi≈kiraya veren), BM25 kesin
eşleşmeyi yakalar (sayılar, belge adları). İkisini Reciprocal Rank Fusion (RRF)
ile birleştiririz — biri kazanırken diğeri kaybettiğinde en sağlamı.

Adımlar:
    1) Tam embed (bir kerelik, GPU'da ~15 dk):
         python arama/vektor.py --embed
       → arama/vektorler.npy (float16) + arama/vektor_ids.npy üretir.
       Sıra maddeler.jsonl ile birebir (SQLite rowid ile de hizalı).

    2) Hibrit arama:
         python arama/vektor.py --ara "ev sahibi kiracıyı ne zaman çıkarabilir"
         python arama/vektor.py --ara "..." --tur madde --gizle-mulga

    3) 13 soruda BM25 / VEKTÖR / HİBRİT karşılaştırması:
         python arama/vektor.py --degerlendir
         python arama/vektor.py --degerlendir --tur hepsi

Gerektirir: sentence-transformers (torch). indeksle.py indeksi kurulu olmalı.
"""

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indeksle import (JSONL, DB, iter_jsonl, ara_ham, tokenle, _snippet)
from kopru import genislet, aktif_kopru
from rerank import rerank

MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")   # gömme modeli (konfig.py sabitler)
SEQ_LEN = int(os.environ.get("EMBED_SEQ", "512"))
GOV_KES = int(os.environ.get("EMBED_KES", "1400"))     # madde medyanı ~479 krk, %32'si 800'ü aşar
ETIKET  = os.environ.get("EMBED_ETIKET", "")           # ayrı vektör dosyasına yazmak için (opsiyonel)

_slug = MODEL.split("/")[-1] + (("-" + ETIKET) if ETIKET else "")
VEC = DB.parent / f"vektorler_{_slug}.npy"
IDS = DB.parent / f"vektor_ids_{_slug}.npy"

# Norm hiyerarşisi (otorite) boost — hibrit_rerank'te uygulanır. TÜR kodu (belge'nin
# 2. parçası): 1 Kanun, 3 KHK, 19 CBK, 2/4 Tüzük öne; yönetmelik/tebliğ 0.
YETKI_BOOST = 0.05
YETKI_KAT = {"1": 1.0, "3": 0.7, "19": 0.7, "2": 0.5, "4": 0.5}

_KOL = ["chunk_id", "belge", "tur", "madde_id", "baslik", "mulga",
        "govde", "belge_ad", "belge_tur"]


# ----- Model (lazy) -------------------------------------------------------
_MODEL = None


def model_yukle():
    global _MODEL
    if _MODEL is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise SystemExit("HATA: pip install sentence-transformers")
        # bge-m3 (gömme) + bge-reranker 4GB GPU'ya birlikte sığmaz. Sorgu encoder'ı
        # CPU'da (1 kısa sorgu, ucuz), reranker GPU'da kalır → konfig EMBED_DEVICE=cpu.
        # Bulk embed ise GPU'da yapılır (embed_hepsi EMBED_DEVICE=cuda ayarlar).
        dev = os.environ.get("EMBED_DEVICE", "")
        _MODEL = SentenceTransformer(MODEL, device=dev) if dev else SentenceTransformer(MODEL)
        _MODEL.max_seq_length = SEQ_LEN
        # fp16: bulk embed'de GPU'da ~5x hız; ağırlığı yarıya indirir (bge-m3
        # ~1.17GB→585MB), kısıtlı RAM'de DB/FTS cache'e yer açar.
        if (dev or "").startswith("cuda") or os.environ.get("EMBED_FP16") == "1":
            _MODEL = _MODEL.half()
    return _MODEL


# ----- Tam embed ----------------------------------------------------------
def embed_hepsi(batch=None, blok=20000, kes=None):
    batch = batch or int(os.environ.get("EMBED_BATCH", "128"))
    kes = GOV_KES if kes is None else kes
    os.environ.setdefault("EMBED_DEVICE", "cuda")                # bulk embed GPU'da (sorgu CPU olsa da)
    if not JSONL.exists():
        raise SystemExit(f"HATA: {JSONL} yok.")
    model = model_yukle()
    try:
        import torch
        cihaz = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        cihaz = "cpu"
    print(f"cihaz: {cihaz}  ·  model: {MODEL}")

    N = sum(1 for _ in iter_jsonl(JSONL))
    print(f"toplam {N:,} parça embed edilecek...")

    V = None
    ids = np.empty(N, dtype=object)
    pos = 0
    tampon = []
    t0 = time.time()

    def flush():
        nonlocal V, pos
        emb = model.encode([t[:kes] for t in tampon],
                           batch_size=batch, normalize_embeddings=True,
                           convert_to_numpy=True, show_progress_bar=False).astype(np.float16)
        if V is None:
            V = np.zeros((N, emb.shape[1]), dtype=np.float16)
        V[pos:pos + len(emb)] = emb
        pos += len(emb)
        tampon.clear()

    for k, o in enumerate(iter_jsonl(JSONL)):
        ids[k] = o.get("chunk_id")
        tampon.append(o.get("govde") or "")
        if len(tampon) >= blok:
            flush()
            print(f"\r  {pos:,}/{N:,}  ({100*pos/N:.0f}%)  {time.time()-t0:.0f}sn", end="", flush=True)
    if tampon:
        flush()

    np.save(VEC, V)
    np.save(IDS, ids)
    print(f"\nkaydedildi: {VEC.name} {V.shape}  ·  {IDS.name}")
    print(f"süre: {time.time()-t0:.0f} sn")


# ----- Vektör arama -------------------------------------------------------
_V = None
_IDS = None


def vek_yukle():
    global _V, _IDS
    if _V is None:
        if not VEC.exists():
            raise SystemExit("HATA: vektörler yok. Önce: python arama/vektor.py --embed")
        _V = np.load(VEC, mmap_mode="r")
        _IDS = np.load(IDS, allow_pickle=True)
    return _V, _IDS


_ANN = os.environ.get("ANN") == "1"   # FAISS yaklaşık arama (kaba kuvvet yerine)


def vek_ara(sorgu, K=100, blok=50000):
    model = model_yukle()
    q = model.encode([sorgu], normalize_embeddings=True,
                     convert_to_numpy=True)[0].astype(np.float32)
    _, ids = vek_yukle()
    if _ANN:
        # FAISS IVF+SQ8: 472K vektörü tek tek taramak yerine akıllı kısayol.
        # ~18 sn → ~0.5 sn. İndeks yoksa sessizce kaba kuvvete düşer.
        import ann as _ann
        if _ann.INDEKS.exists():
            I, D = _ann.ara(q, K=K)
            return [(str(ids[i]), float(d)) for i, d in zip(I, D) if i >= 0]
    V, _ = vek_yukle()
    N = V.shape[0]
    skor = np.empty(N, dtype=np.float32)
    for i in range(0, N, blok):
        skor[i:i + blok] = V[i:i + blok].astype(np.float32) @ q
    K = min(K, N)
    ust = np.argpartition(-skor, K - 1)[:K]
    ust = ust[np.argsort(-skor[ust])]
    return [(str(ids[i]), float(skor[i])) for i in ust]


def satir_getir(chunk_ids):
    if not chunk_ids:
        return {}
    con = sqlite3.connect(DB)
    q = ("SELECT chunk_id,belge,tur,madde_id,baslik,mulga,govde,belge_ad,belge_tur "
         "FROM chunks WHERE chunk_id IN (%s)" % ",".join("?" * len(chunk_ids)))
    rows = con.execute(q, chunk_ids).fetchall()
    con.close()
    return {r[0]: dict(zip(_KOL, r)) for r in rows}


# ----- Hibrit (RRF) -------------------------------------------------------
def hibrit(sorgu, n=10, tur=None, gizle_mulga=False, K=100, c=60, kopru=False):
    """BM25 top-K + Vektör top-K → RRF füzyonu. Filtreler füzyondan sonra.
    kopru=True: sorgu önce kanun diline genişletilir (kopru.genislet)."""
    # Köprü HEM BM25 HEM vektöre uygulanır: alan-özgü hedeflerde (malûllük, kiraya
    # veren) her iki katmana da yardım eder, boğmaz. (Deney: köprüyü vektörden almak
    # malulen/dul-yetim kazancını kaybettirdi.) Boğulma sorunu ALAN-ÖZGÜ hedef seçimiyle
    # çözülür, mimariyle değil — o yüzden köprü girişleri dar/alan-özgü tutulur.
    aramastr = aktif_kopru(sorgu) if kopru else sorgu
    bm = ara_ham(aramastr, n=K, tur=tur, gizle_mulga=gizle_mulga)[0]
    bm_ids = [r["chunk_id"] for r in bm]
    vk_ids = [cid for cid, _ in vek_ara(aramastr, K=K)]

    puan = {}
    for rank, cid in enumerate(bm_ids, 1):
        puan[cid] = puan.get(cid, 0.0) + 1.0 / (c + rank)
    for rank, cid in enumerate(vk_ids, 1):
        puan[cid] = puan.get(cid, 0.0) + 1.0 / (c + rank)
    sirali = sorted(puan, key=lambda x: -puan[x])

    meta = {r["chunk_id"]: r for r in bm}
    eksik = [cid for cid in sirali if cid not in meta]
    meta.update(satir_getir(eksik))

    out = []
    for cid in sirali:
        r = meta.get(cid)
        if not r:
            continue
        if tur and r["tur"] != tur:
            continue
        if gizle_mulga and r["mulga"]:
            continue
        r = dict(r)
        r["skor"] = puan[cid]
        out.append(r)
        if len(out) >= n:
            break
    return out


def hibrit_rerank(sorgu, n=10, tur=None, gizle_mulga=False, K=int(os.environ.get("ARAMA_HAVUZ", "200")), c=60):   # K 50→200 (+7 @10)
    """Köprü-hibrit havuzunu reranker ile RRF-füzyonlar.
    - Reranker'a KÖPRÜLÜ (kanun-dili) sorgu verilir → 'tahliye' gibi kelime
      yanlılığını geri getirmez.
    - Reranker sıralaması, köprü-hibrit sıralamasıyla RRF ile birleştirilir →
      reranker ezmez, sadece iyileştirir (felaket düşüşleri engeller)."""
    genis = aktif_kopru(sorgu)
    havuz = hibrit(sorgu, n=K, tur=tur, gizle_mulga=gizle_mulga, kopru=True)
    if not havuz:
        return havuz
    rr = rerank(genis, list(havuz), n=len(havuz))
    RR_W = 2.0                                   # reranker ağırlığı (A/B: 2 en iyi)
    puan = {}
    for rank, r in enumerate(havuz, 1):
        puan[r["chunk_id"]] = puan.get(r["chunk_id"], 0.0) + 1.0 / (c + rank)
    for rank, r in enumerate(rr, 1):
        puan[r["chunk_id"]] = puan.get(r["chunk_id"], 0.0) + RR_W * 1.0 / (c + rank)
    meta = {r["chunk_id"]: r for r in havuz}
    # NORM HİYERARŞİSİ (otorite) boost: aynı konuda kanun > tüzük/KHK/CBK >
    # yönetmelik/tebliğ. Vatandaş sorusunun cevabı genelde KANUN ama kanun maddeleri
    # kısa olduğu için yönetmelikler öne geçiyordu. Ölçümle kalibre (kopru_degerlendir):
    # 0.005 -> vatandaş %47→%80, temiz %86→%83. (belge = no_TÜR_tertip)
    for cid, r in meta.items():
        t = (r.get("belge") or "").split("_")
        puan[cid] += YETKI_BOOST * YETKI_KAT.get(t[1] if len(t) > 1 else "", 0.0)
    return sorted(meta.values(), key=lambda r: -puan[r["chunk_id"]])[:n]


def vek_satirlar(sorgu, n=10, tur=None, gizle_mulga=False, K=60):
    """Saf vektör sonuçları (filtreli) — karşılaştırma için."""
    ham = vek_ara(sorgu, K=K)
    meta = satir_getir([cid for cid, _ in ham])
    out = []
    for cid, sk in ham:
        r = meta.get(cid)
        if not r:
            continue
        if tur and r["tur"] != tur:
            continue
        if gizle_mulga and r["mulga"]:
            continue
        r = dict(r)
        r["skor"] = sk
        out.append(r)
        if len(out) >= n:
            break
    return out


# ----- CLI: arama & değerlendirme ----------------------------------------
def _bas(satirlar, sorgu):
    stok = tokenle(sorgu)
    for i, r in enumerate(satirlar, 1):
        mul = "  ⚠MÜLGA" if r["mulga"] else ""
        bas = f" — {r['baslik']}" if r["baslik"] else ""
        print(f"[{i}] {r['chunk_id']}  ({r['tur']} {r['madde_id']}){bas}{mul}")
        print(f"    {r['belge']}  {r.get('belge_ad','')}  [{r.get('belge_tur','')}]")
        print(f"    skor: {r['skor']:.4f}")
        print(f"    …{_snippet(r['govde'], stok)}…\n")


def main():
    ap = argparse.ArgumentParser(description="Tam embed + hibrit arama")
    ap.add_argument("--embed", action="store_true", help="tüm parçaları embed et (bir kerelik)")
    ap.add_argument("--ara", metavar="SORGU", help="hibrit arama")
    ap.add_argument("--tur", default=None, help="madde | hepsi(=filtre yok). Vars: filtre yok")
    ap.add_argument("--gizle-mulga", action="store_true")
    ap.add_argument("--n", type=int, default=10)
    args = ap.parse_args()
    tur = None if args.tur in (None, "hepsi") else args.tur

    if args.embed:
        embed_hepsi()
    elif args.ara:
        genis, ekle, tetik = genislet(args.ara)
        if ekle:
            print(f"köprü + [{', '.join(tetik)}] → {', '.join(ekle)}\n")
        _bas(hibrit_rerank(args.ara, n=args.n, tur=tur, gizle_mulga=args.gizle_mulga), genis)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()