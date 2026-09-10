"""Локал эрх зүйн агентыг (local-llm-assistant) LegalChat-д холбох давхарга.

`local-llm-assistant/agent` доторх `lawagent` багцыг ачаалж, вэбийн хэрэгцээнд
тохирсон хэлбэрт хөрвүүлнэ:

  * **чат** — hybrid эрэл (BM25 + bge-m3 вектор) дээр тулгуурлан `gemma3:12b`
    эшлэлтэй хариулт үүсгэнэ, токен тус бүрээр урсгалаар дамжуулна;
  * **дэлгэрэнгүй самбар** — заалтын бүтэн эх бичвэр, тухайн актын бусад заалт,
    вектор ойролцоо байдлаар олсон холбогдох материал.

Тохиргоог орчны хувьсагчаар өөрчилнө:

    LEGALCHAT_AGENT_DIR   агентын хавтас (үндсэн: ../local-llm-assistant/agent)
    LEGALCHAT_INDEX_DIR   индексийн хавтас (үндсэн: <AGENT_DIR>/index)
    LEGALCHAT_CHAT_MODEL  хариулт үүсгэх модел (үндсэн: gemma3:12b)
    LEGALCHAT_EMBED_MODEL векторжуулах модел (үндсэн: bge-m3)
    LEGALCHAT_TOP_K       контекст болгох хэсгийн тоо (үндсэн: 4)
    LEGALCHAT_NUM_CTX     контекстийн цонх (үндсэн: 8192)
    OLLAMA_HOST           Ollama хаяг (үндсэн: http://localhost:11434)

Энэ модуль numpy болон requests шаарддаг тул серверийг агентын виртуал
орчноор ажиллуулна:

    /home/bllgvvn/local-llm-assistant/.venv/bin/python app.py
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_path(name, default):
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else Path(default)


def _normalize_host(host):
    """`OLLAMA_HOST` нь "127.0.0.1:11434" гэж схемгүй байж болно."""
    host = (host or "").strip()
    if not host:
        return "http://localhost:11434"
    return host if "://" in host else "http://" + host


AGENT_DIR = _env_path("LEGALCHAT_AGENT_DIR", BASE_DIR.parent / "local-llm-assistant" / "agent")
INDEX_DIR = _env_path("LEGALCHAT_INDEX_DIR", AGENT_DIR / "index")
CHAT_MODEL = os.environ.get("LEGALCHAT_CHAT_MODEL", "gemma3:12b")
EMBED_MODEL = os.environ.get("LEGALCHAT_EMBED_MODEL", "bge-m3")
OLLAMA_HOST = _normalize_host(os.environ.get("OLLAMA_HOST"))
TOP_K = int(os.environ.get("LEGALCHAT_TOP_K") or 4)
NUM_CTX = int(os.environ.get("LEGALCHAT_NUM_CTX") or 8192)

RELATED_LIMIT = 6        # вектор ойролцоо байдлаар олох холбогдох материалын тоо
RELATED_MIN_SCORE = 0.5  # косинус ойролцоо байдлын доод хязгаар
SAME_LAW_LIMIT = 40      # нэг актын хэдэн заалтыг жагсаах
SNIPPET_CHARS = 220
LLM_RECHECK_SEC = 5.0    # Ollama унтарсан үед дахин шалгах давтамж

# Индекс дэх акт нь ангилал тус бүрээс цагаан толгойн эхний 10 актыг хамарсан
# тул санал болгох асуултууд ч тэр агуулгад тааруулсан.
SUGGESTIONS = [
    "Хөрөнгө орлогын мэдүүлгээ хэзээ гаргах вэ?",
    "Авлигын эсрэг хуулийн зорилт юу вэ?",
    "Үндсэн хуулиар иргэний үндсэн эрх юу гэж заасан бэ?",
    "Агаарын бохирдлын төлбөрийг хэн төлөх вэ?",
    "Автотээврийн хэрэгсэлд ямар шаардлага тавьдаг вэ?",
    "Ахмад настанд ямар хөнгөлөлт үзүүлдэг вэ?",
]

DISCLAIMER = (
    "Энэ бол legalinfo.mn-ээс татсан эх бичвэр дээр суурилсан лавлагаа "
    "болохоос хууль зүйн зөвлөгөө биш. Хариултыг эх сурвалжтай нь тулгаж "
    "шалгана уу."
)


class AgentUnavailable(RuntimeError):
    """Агентыг ачаалж чадаагүй — дуудагч тал жишээ өгөгдөл рүү шилжинэ."""


# ---------------------------------------------------------------- туслах


def snippet(text, limit=SNIPPET_CHARS):
    """Урт эх бичвэрээс товч хэсэг сугалж авна."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut + "…"


def paragraphs(text):
    """Эх бичвэрийг догол мөрөөр хуваана (хоосон мөрийг хасна)."""
    return [line.strip() for line in (text or "").split("\n") if line.strip()]


# "[1]", "[2, 3]" хэлбэрийн эшлэлийг олно
CITE_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def link_citations(text, sources):
    """Моделийн бичсэн `[1]` эшлэлийг frontend-ийн `[[doc-id]]` линк болгоно."""
    by_number = {s["n"]: s["id"] for s in sources}

    def replace(match):
        out = []
        for part in match.group(1).split(","):
            doc_id = by_number.get(int(part.strip()))
            out.append("[[%s]]" % doc_id if doc_id else "[%s]" % part.strip())
        return "".join(out)

    return CITE_RE.sub(replace, text)


def _load_lawagent():
    """`lawagent` багцыг sys.path-д нэмж импортлоно."""
    if not AGENT_DIR.is_dir():
        raise AgentUnavailable(
            "Агентын хавтас олдсонгүй: %s\n"
            "LEGALCHAT_AGENT_DIR орчны хувьсагчаар зааж өгнө үү." % AGENT_DIR
        )
    if str(AGENT_DIR) not in sys.path:
        sys.path.insert(0, str(AGENT_DIR))
    try:
        import numpy as np  # noqa: F401 — доор LegalIndex дотор ашиглагдана

        from lawagent.agent import LegalAgent
        from lawagent.chunker import fmt_date
        from lawagent.index import LegalIndex
        from lawagent.ollama_client import Ollama
    except ImportError as exc:
        raise AgentUnavailable(
            "Агентын багцыг импортлож чадсангүй (%s).\n"
            "Серверийг агентын виртуал орчноор ажиллуулна уу:\n"
            "  %s/.venv/bin/python app.py" % (exc, AGENT_DIR.parent)
        ) from exc
    return np, LegalAgent, LegalIndex, Ollama, fmt_date


# ---------------------------------------------------------------- backend


class AgentBackend:
    """Вэб серверийн ашиглах нэгдсэн интерфейс (`app.py` үүнийг л мэднэ)."""

    mode = "agent"

    def __init__(
        self,
        index_dir=INDEX_DIR,
        chat_model=CHAT_MODEL,
        embed_model=EMBED_MODEL,
        host=OLLAMA_HOST,
        top_k=TOP_K,
        num_ctx=NUM_CTX,
        sources_only=False,
    ):
        np, LegalAgent, LegalIndex, Ollama, fmt_date = _load_lawagent()
        self._np = np
        self._fmt_date = fmt_date

        try:
            index = LegalIndex.load(Path(index_dir))
        except Exception as exc:  # noqa: BLE001 — FileNotFoundError, pickle алдаа г.м
            raise AgentUnavailable(str(exc)) from exc

        self.agent = LegalAgent(
            index=index,
            ollama=Ollama(host),
            chat_model=chat_model,
            embed_model=embed_model,
            top_k=top_k,
            num_ctx=num_ctx,
        )
        self.index_dir = str(index_dir)
        self.sources_only = sources_only

        # Нэг зэрэг хоёр хариулт үүсгэхгүй: Ollama CPU дээр аль ч байсан
        # дараалуулах бөгөөд `agent.history` нь хуваалцсан төлөв.
        self._lock = threading.RLock()

        self._llm_ok = False
        self._llm_note = ""
        self._llm_checked = 0.0

        self._build_ids()

    # ------------------------------------------------------------ индекс

    @property
    def chunks(self):
        return self.agent.index.chunks

    def _build_ids(self):
        """chunk_id ("367#0012") → URL-д тохирох doc_id ("367-0012")."""
        self._ids = []
        self._pos = {}
        self._by_law = {}
        self._pos_by_object = {id(c): i for i, c in enumerate(self.chunks)}
        for i, chunk in enumerate(self.chunks):
            base = re.sub(r"[^\w-]+", "-", chunk.chunk_id.replace("#", "-"))
            doc_id, n = base, 2
            while doc_id in self._pos:  # ижил lawId-тай хоёр файл байж болно
                doc_id, n = "%s-%d" % (base, n), n + 1
            self._ids.append(doc_id)
            self._pos[doc_id] = i
            self._by_law.setdefault(chunk.law_id, []).append(i)

        self.categories = sorted({c.category for c in self.chunks if c.category})
        self.document_count = len(self._by_law)
        self._build_runs()

    def _build_runs(self):
        """Нэг зүйл заалт хэд хэдэн хэсэг болж хуваагдсан байдаг.

        Дараалсан ижил (бүлэг, зүйл)-тэй хэсгүүдийг нэг "зүйл" болгон
        бүлэглэнэ — ингэснээр дэлгэрэнгүй самбарт зүйл бүтнээрээ гарч,
        зүүн жагсаалтад нэг зүйл нэг л удаа харагдана.
        """
        self._runs_by_law = {}
        self._run_of = {}
        for law_id, positions in self._by_law.items():
            runs = []
            prev_key = None
            for pos in positions:
                chunk = self.chunks[pos]
                key = (chunk.chapter, chunk.section)
                if runs and key == prev_key:
                    runs[-1].append(pos)
                else:
                    runs.append([pos])
                prev_key = key
            self._runs_by_law[law_id] = runs
            for i, run in enumerate(runs):
                for pos in run:
                    self._run_of[pos] = i

    def _article_label(self, chunk):
        return chunk.section or chunk.chapter or "Ерөнхий хэсэг"

    # ------------------------------------------------------------ Ollama

    def _llm_state(self):
        """(ажиллах эсэх, тайлбар). Ollama дараа асаасан ч өөрөө сэргэнэ."""
        if self.sources_only:
            return False, "Зөвхөн эх сурвалж харуулах горим (--sources-only)."
        if self._llm_ok:
            return True, ""
        if time.monotonic() - self._llm_checked < LLM_RECHECK_SEC:
            return False, self._llm_note
        self._llm_checked = time.monotonic()

        ollama = self.agent.ollama
        if not ollama.available():
            self._llm_note = (
                "Ollama %s дээр ажиллахгүй байна. `ollama serve` ажиллуулбал "
                "хариулт үүсгэх горим өөрөө асна." % ollama.host
            )
            return False, self._llm_note
        try:
            ollama.require(self.agent.chat_model, self.agent.embed_model)
        except Exception as exc:  # noqa: BLE001 — модел татагдаагүй
            self._llm_note = str(exc)
            return False, self._llm_note

        self._llm_ok, self._llm_note = True, ""
        return True, ""

    # ------------------------------------------------------------ төлөв

    def status(self):
        llm, note = self._llm_state()
        return {
            "mode": self.mode,
            "llm": llm,
            "note": note,
            "chat_model": self.agent.chat_model,
            "embed_model": self.agent.embed_model,
            "host": self.agent.ollama.host,
            "chunks": len(self.chunks),
            "documents": self.document_count,
            "categories": self.categories,
            "top_k": self.agent.top_k,
            "index_dir": self.index_dir,
            "disclaimer": DISCLAIMER,
        }

    def suggestions(self):
        return SUGGESTIONS

    def reset(self):
        """Чатны түүхийг цэвэрлэнэ ("Цэвэрлэх" товч)."""
        with self._lock:
            self.agent.history.clear()

    # ------------------------------------------------------------ эх сурвалж

    def _source(self, hit, n):
        chunk = hit.chunk
        return {
            "n": n,
            "cite": str(n),
            "id": self._ids[self._pos_of(chunk)],
            "law": chunk.title,
            "article": self._article_label(chunk),
            "category": chunk.category,
            "enacted": self._fmt_date(chunk.enacteddate),
            "url": chunk.url,
            "snippet": snippet(chunk.text),
            "score": round(float(hit.score), 4),
            "lexical": hit.bm25_rank is not None,
            "semantic": hit.dense_rank is not None,
        }

    def _pos_of(self, chunk):
        """Hit доторх chunk-ийн индекс дэх байрлалыг олно."""
        return self._pos_by_object[id(chunk)]

    # ------------------------------------------------------------ хариулт

    def stream_answer(self, question, category=None):
        """(event, payload) хосыг урсгалаар үүсгэнэ.

        event: ``sources`` → ``delta``* → ``done`` (эсвэл ``error``).
        """
        llm, note = self._llm_state()
        started = time.monotonic()

        with self._lock:
            if llm:
                hits, stream = self.agent.answer_stream(question, category)
            else:
                hits, stream = self.agent.retrieve(question, category), None

            sources = [self._source(h, i) for i, h in enumerate(hits, 1)]
            yield "sources", {"sources": sources, "llm": llm, "note": note}

            if stream is None:
                text = self._sources_only_text(sources, note)
                yield "delta", {"text": text}
                yield "done", {
                    "text": link_citations(text, sources),
                    "sources": sources,
                    "llm": False,
                    "elapsed": round(time.monotonic() - started, 1),
                }
                return

            parts = []
            for piece in stream:
                parts.append(piece)
                yield "delta", {"text": piece}

            yield "done", {
                "text": link_citations("".join(parts), sources),
                "sources": sources,
                "llm": True,
                "elapsed": round(time.monotonic() - started, 1),
            }

    def _sources_only_text(self, sources, note):
        if not sources:
            return (
                "Энэ асуултад тохирох заалт индексээс олдсонгүй. Хуулийн нэр "
                "эсвэл өөр түлхүүр үг дурдаж дахин асуугаад үзнэ үү."
            )
        lines = [note.strip(), "", "Индексээс олдсон холбогдох материал:", ""]
        for s in sources:
            lines.append("[%d] %s — %s" % (s["n"], s["law"], s["article"]))
        lines += ["", "Зүүн талын самбараас бүтэн эх бичвэрийг нь харна уу."]
        return "\n".join(x for x in lines if x is not None).strip()

    def answer(self, question, category=None):
        """Урсгалгүй хувилбар (`POST /api/chat`)."""
        result = {"text": "", "sources": [], "llm": False}
        stream = self.stream_answer(question, category)
        try:
            for event, payload in stream:
                if event in ("sources", "done"):
                    result.update(
                        {k: v for k, v in payload.items() if k != "text"}
                    )
                if event == "done":
                    result["text"] = payload["text"]
        finally:
            stream.close()
        return result

    # ------------------------------------------------------------ дэлгэрэнгүй

    def get_doc(self, doc_id):
        pos = self._pos.get(doc_id)
        if pos is None:
            return None
        chunk = self.chunks[pos]
        run = self._runs_by_law[chunk.law_id][self._run_of[pos]]
        return {
            "id": doc_id,
            "law": chunk.title,
            "article": self._article_label(chunk),
            "chapter": chunk.chapter,
            "section": chunk.section,
            "category": chunk.category,
            "enacted": self._fmt_date(chunk.enacteddate),
            "enforcement": self._fmt_date(chunk.enforcementdate),
            "url": chunk.url,
            "source_file": chunk.source_file,
            "body": self._merge_body(run),
            "parts": len(run),
            "same_law": self._same_law(pos),
            "related": self._related(pos),
            "note": DISCLAIMER,
        }

    def _merge_body(self, positions):
        """Нэг зүйлийн хэсгүүдийг нийлүүлнэ (chunker-ийн давхцлыг хасаж)."""
        out = []
        for pos in positions:
            paras = paragraphs(self.chunks[pos].text)
            skip = 0
            for n in range(min(len(paras), len(out)), 0, -1):
                if out[-n:] == paras[:n]:
                    skip = n
                    break
            out.extend(paras[skip:])
        return out

    def _same_law(self, pos):
        """Тухайн актын бусад зүйл — актыг дэс дараалан уншихад.

        Урт актад бүх зүйлийг жагсаахгүй, идэвхтэй зүйлийн эргэн тойрны
        цонхыг харуулна (ингэснээр идэвхтэй нь үргэлж жагсаалтад орно).
        """
        chunk = self.chunks[pos]
        runs = self._runs_by_law.get(chunk.law_id, [])
        here = self._run_of.get(pos, 0)
        start = max(0, min(here - SAME_LAW_LIMIT // 2, len(runs) - SAME_LAW_LIMIT))

        out = []
        for i, run in enumerate(runs[start:start + SAME_LAW_LIMIT], start):
            first = self.chunks[run[0]]
            out.append({
                "id": self._ids[run[0]],
                "article": self._article_label(first),
                "chapter": first.chapter,
                "active": i == here,
            })
        return {"items": out, "total": len(runs), "law": chunk.title}

    def _related(self, pos):
        """Өөр актаас вектор ойролцоо байдлаар холбогдох материал олно.

        Нэг актаас нэг л заалт авна — эс тэгвэл ижил хуулийн зэргэлдээ
        хэсгүүд жагсаалтыг дүүргэчихдэг.
        """
        np = self._np
        vectors = self.agent.index.vectors
        law_id = self.chunks[pos].law_id

        sims = vectors @ vectors[pos]
        order = np.argsort(-sims)[:200]

        out, seen = [], {law_id}
        for i in order:
            i = int(i)
            other = self.chunks[i]
            if other.law_id in seen or float(sims[i]) < RELATED_MIN_SCORE:
                continue
            seen.add(other.law_id)
            out.append({
                "id": self._ids[i],
                "law": other.title,
                "article": self._article_label(other),
                "category": other.category,
                "snippet": snippet(other.text, 160),
                "score": round(float(sims[i]), 3),
            })
            if len(out) >= RELATED_LIMIT:
                break
        return out


def load_backend(**kwargs):
    """Агентыг ачаална; болохгүй бол `AgentUnavailable` шиднэ."""
    return AgentBackend(**kwargs)
