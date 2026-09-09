#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""orh.py — OpenRouter yardımcısı. Muhakeme modellerini ve GERÇEK maliyeti yönetir.

OpenRouter her çağrıda usage.cost döndürüyor -> tahmin yok, tam rakam.
Muhakeme modelleri (glm-flash, kimi-thinking) cevabı content'e, düşünceyi
reasoning'e yazıyor; max_tokens ikisini birden kapsar, o yüzden bol ver.
"""
import json, urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parent
_ANAHTAR = None


def anahtar():
    global _ANAHTAR
    if _ANAHTAR is None:
        for l in (KOK / ".env").read_text(encoding="utf-8").splitlines():
            l = l.strip()
            if l.startswith("OPENROUTER_API_KEY="):
                _ANAHTAR = l.split("=", 1)[1].strip().strip('"').strip("'")
        if not _ANAHTAR:
            raise SystemExit("HATA: .env içinde OPENROUTER_API_KEY yok")
    return _ANAHTAR


def cagir(model, sistem, kullanici, max_tokens=1600, timeout=300, saglayici=None):
    """(content, cost, gir_tok, cik_tok, muhakeme_tok) döndürür.
    saglayici: OpenRouter provider yönlendirme dict'i (örn. kuantizasyon filtresi)."""
    msg = []
    if sistem:
        msg.append({"role": "system", "content": sistem})
    msg.append({"role": "user", "content": kullanici})
    gov = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": msg,
           "usage": {"include": True}}
    if saglayici:
        gov["provider"] = saglayici
    r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                               data=json.dumps(gov).encode(),
                               headers={"Authorization": "Bearer " + anahtar(),
                                        "Content-Type": "application/json",
                                        "HTTP-Referer": "https://mevzuat-asistan.local",
                                        "X-Title": "Mevzuat Asistan"})
    d = json.load(urllib.request.urlopen(r, timeout=timeout))
    m = d["choices"][0]["message"]
    u = d.get("usage", {})
    cd = u.get("completion_tokens_details", {}) or {}
    return ((m.get("content") or "").strip(), float(u.get("cost", 0.0)),
            u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
            cd.get("reasoning_tokens", 0))


def cagir_stream(model, sistem, kullanici, max_tokens=1600, timeout=300,
                 saglayici=None, usage_out=None):
    """Akışlı üretim: cevap parçalarını (token) tek tek yield eder.
    Bittiğinde usage_out sözlüğüne {cost, gir, cik} yazar (verildiyse)."""
    msg = []
    if sistem:
        msg.append({"role": "system", "content": sistem})
    msg.append({"role": "user", "content": kullanici})
    gov = {"model": model, "max_tokens": max_tokens, "temperature": 0, "messages": msg,
           "stream": True, "usage": {"include": True}}
    if saglayici:
        gov["provider"] = saglayici
    r = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
                               data=json.dumps(gov).encode(),
                               headers={"Authorization": "Bearer " + anahtar(),
                                        "Content-Type": "application/json",
                                        "HTTP-Referer": "https://mevzuat-asistan.local",
                                        "X-Title": "Mevzuat Asistan"})
    with urllib.request.urlopen(r, timeout=timeout) as yanit:
        for ham in yanit:
            satir = ham.decode("utf-8").strip()
            if not satir or not satir.startswith("data:"):
                continue
            veri = satir[5:].strip()
            if veri == "[DONE]":
                break
            try:
                d = json.loads(veri)
            except json.JSONDecodeError:
                continue
            secim = d.get("choices") or [{}]
            delta = (secim[0].get("delta") or {}).get("content")
            if delta:
                yield delta
            u = d.get("usage")
            if u and usage_out is not None:
                usage_out.update({"cost": float(u.get("cost", 0.0)),
                                  "gir": u.get("prompt_tokens", 0),
                                  "cik": u.get("completion_tokens", 0)})


def bakiye():
    r = urllib.request.Request("https://openrouter.ai/api/v1/key",
                               headers={"Authorization": "Bearer " + anahtar()})
    d = json.load(urllib.request.urlopen(r, timeout=20))["data"]
    lim = d.get("limit")
    return (lim - d.get("usage", 0)) if lim is not None else None, d.get("usage", 0)


if __name__ == "__main__":
    kalan, kul = bakiye()
    print(f"OpenRouter: kullanılan ${kul:.4f}"
          + (f", kalan ${kalan:.4f}" if kalan is not None else ", limitsiz"))