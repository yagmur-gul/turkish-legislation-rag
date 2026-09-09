# Turkish Legislation RAG Assistant

A question-answering system over the full body of Turkish legislation. A user asks a question in
everyday language; the system finds the relevant statutory articles and produces an answer that is
**grounded strictly in those articles**, links every statement to its source, and abstains rather
than inventing an answer when the source is missing.

Built from scratch in Python — no off-the-shelf RAG framework — and deployed as a 24/7 service.

> **Portfolio note.** This repository contains the **source code only**. The corpus data
> (~472k articles, embeddings, FAISS index) and all API keys are intentionally excluded; the system
> reads that data from local files at runtime.

---

## The problem

Citizens don't speak in legal terms. Someone asks *"can my landlord kick me out?"* while the law
says *"eviction"*, *"termination of the lease agreement"*. This language gap is what makes finding
the correct **article** (not just the right document) hard — and article-level precision is the
whole point of a legal assistant.

## Architecture

```
                        User question
                              |
                              v
              +-----------------------------+
              |        Query bridge         |   plain language -> legal terms
              |     (gemini-2.5-flash)      |
              +-----------------------------+
                              |
                 +------------+------------+
                 v                         v
          BM25 keyword               Dense semantic
          (SQLite FTS5)              (bge-m3 + FAISS)
                 |                         |
                 +------------+------------+
                              v
                          RRF fusion
                              |
                              v
              Optional reranker  (off by default)
                              |
                              v
              +-----------------------------+
              |      Answer generation      |   grounded, cited, abstains
              |       (deepseek-chat)       |
              +-----------------------------+
                              |
                              v
                Answer with article citations
```

**Data pipeline** (conceptual): scrape mevzuat.gov.tr → OCR scanned PDFs → parse documents into
articles → embed → build keyword + vector indexes. An incremental update path lets new legislation
be added without rebuilding everything.

## Key engineering decisions

Every decision below was driven by measurement on a hand-built 287-question gold set, not by
guesswork. Retrieval and answer-generation were **measured separately**, because a model that is
good at one can be bad at the other.

- **The real bottleneck is query translation, not the search engine.** Article-level accuracy
  started at ~48%. Diagnostics showed most misses were *recall* (the correct article never entered
  the candidate pool), and that legal-language queries fixed 17 of 19 failures. This reframed the
  whole project around a **query bridge**.
- **Query bridge: rule dictionary → LLM (gemini-flash).** This single change moved accuracy from
  **~35% to ~62%**. The query is *replaced* with its legal-language form, not concatenated (mixing
  the citizen's narrative back in diluted the signal); adding a statute reference helped, but asking
  the model for an article *number* hurt (invented numbers derailed search).
- **"Don't merge texts — merge ranked lists."** Fusing ranked result lists (RRF) helped; injecting
  extra text into the query always hurt. Techniques that first looked like failures (HyDE, RM3)
  turned out to fail only because of *where* they were wired in, not what they were.
- **Answer model: deepseek-chat, with 0% hallucination.** Compared against several models. One
  strong alternative produced ~8% hallucinated citations — unacceptable for a legal tool. Reasoning
  ("thinking") models raised accuracy but burned tokens and sometimes returned an empty, billed
  answer; the same effect was recovered with a prompt (~80 tokens instead of ~3,000).
- **Runs on CPU. Reranker off by default.** On a CPU-only server the cross-encoder reranker was the
  main bottleneck (~40–75 s). With a small candidate pool it only re-orders ~30 articles the LLM
  already reads, so its contribution was ~2–4 points. Turning it off gives a ~3× speedup
  (~15–20 s per answer) with no measurable quality loss. It can be re-enabled with `RERANK=1`.
- **No text truncation, anywhere.** Truncating the query-translation prompt was silently dropping
  ~45% of questions mid-sentence (the actual question often sits at the end). Removed as a hard rule.

## Results

| Metric | Value |
|---|---|
| Corpus | **472,737** legislation articles |
| Answer accuracy | **~70%** user-experience (~65% strict, exact-article match) |
| Hallucinated citations | **0%** (grounded generation, abstains when unsure) |
| Response time | ~15–20 s (CPU, no-rerank default) |
| Retrieval ceiling | recall @30 57% · @100 69% · @500 77% |

The retrieval ceiling (~77%) is honest: for ~23% of questions the correct article is never
retrieved, so the generation prompt is instructed to abstain rather than guess.

## Tech stack

**Python** · **FastAPI** (web + SSE streaming) · **SQLite / FTS5** (BM25 keyword search) ·
**bge-m3** embeddings + **FAISS** (semantic search) · **OpenRouter** (gemini-2.5-flash bridge +
deepseek-chat generation) · **Playwright** + **Tesseract OCR** (data pipeline) · **systemd** (24/7
service).

## Repository structure

```
app.py              FastAPI web app + SSE answer streaming
konfig.py           central configuration
orh.py              OpenRouter gateway (LLM calls)
uret.py             answer generation (grounded, cited)
cevapla.py          answer service
arama/              search engine: BM25 index, bge-m3 embeddings, FAISS,
                    RRF fusion, reranker, query bridge
guncelleme/         data pipeline: scraping, OCR, parsing, change tracking
degerlendirme/      evaluation harness (accuracy measurement)
web/                UI pages (ask, search, document view, stats)
```

## Trust & UX features

- Grounded (cited) statements are marked distinctly from the model's own knowledge.
- Repealed / amended articles are flagged; the answer abstains when the corpus lacks a basis.
- User feedback (👍/👎) and a usage/statistics page with per-legal-domain breakdown.
- Light/dark theme, question history, raw article search alongside Q&A.
