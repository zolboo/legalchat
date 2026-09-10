"""Жишээ өгөгдөл дээрх энгийн түлхүүр үгийн хайлт (нөөц горим).

Үндсэн ажиллагаа нь `agentchat.py` дахь локал агент (hybrid эрэл + Ollama).
Энэ модуль нь агент ачаалагдаагүй үед (numpy байхгүй, индекс үүсээгүй г.м)
интерфейсийг амьд байлгах нөөц бөгөөд `python app.py --demo` гэж гараар ч
сонгож болно.

Монгол хэлний нөхцөл, тийн ялгалыг бүрэн задлахгүй тул үгийн эхний
хэсгээр (prefix) харьцуулах аргыг ашиглав.
"""

import re
from collections import Counter

from data import DOCS, DOCS_BY_ID, SUGGESTIONS

DISCLAIMER = (
    "Энэ агуулга нь демо зориулалттай хялбаршуулсан жишээ бөгөөд албан ёсны "
    "хуулийн эх бичвэр биш. Хууль зүйн зөвлөгөө болохгүй."
)

# Хайлтад нөлөөгүй түгээмэл үгс
STOPWORDS = {
    "юу", "вэ", "бэ", "уу", "үү", "нь", "энэ", "тэр", "байх", "болох",
    "хэрхэн", "яаж", "ямар", "хэдэн", "хэд", "тухай", "талаар", "гэж",
    "and", "the", "what", "how", "is", "are", "of", "in", "for",
}

TOKEN_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯөӨүҮёЁ]+", re.UNICODE)

# Түлхүүр үг → баримтын жин (синоним/сэдвийн зураглал)
BOOSTS = {
    "халах": ["labor-80"],
    "халагдах": ["labor-80"],
    "чөлөөлөх": ["labor-80"],
    "амралт": ["labor-101"],
    "татвар": ["tax-10", "tax-22"],
    "тайлан": ["tax-22"],
    "компани": ["company-16", "company-62"],
    "торгууль": ["civil-243"],
}


def tokenize(text):
    return [t.lower() for t in TOKEN_RE.findall(text or "")]


def _stem(token):
    """Монгол хэлний нөхцөлийг ойролцоогоор таслах."""
    return token[:5] if len(token) > 5 else token


def _doc_index(doc):
    """Баримт бүрийн хайлтын индексийг (үг → жин) буцаана."""
    if "_index" not in doc:
        index = Counter()
        fields = (
            (doc["title"], 6),
            (doc["law"], 3),
            (" ".join(doc["tags"]), 5),
            (doc["summary"], 3),
            (" ".join(doc["body"]), 1),
        )
        for text, weight in fields:
            for token in tokenize(text):
                index[_stem(token)] += weight
        doc["_index"] = index
    return doc["_index"]


def search(query, limit=3, relevance=0.35):
    """Асуултад хамгийн ойр баримтуудыг оноогоор эрэмбэлж буцаана.

    `relevance` — хамгийн өндөр оноотой харьцуулахад энэ хувиас доош
    оноотой баримтыг хамааралгүй гэж үзэн хасна.
    """
    tokens = [t for t in tokenize(query) if t not in STOPWORDS and len(t) > 1]
    if not tokens:
        return []

    scored = []
    for doc in DOCS:
        index = _doc_index(doc)
        score = 0
        matched = set()
        for token in tokens:
            stem = _stem(token)
            hit = index.get(stem, 0)
            if hit:
                score += hit
                matched.add(token)
            for key, ids in BOOSTS.items():
                if key.startswith(stem) and doc["id"] in ids:
                    score += 8
                    matched.add(token)
        if score:
            # Асуултын олон үг таарсан баримтыг илүүд үзнэ
            score += 4 * (len(matched) - 1)
            scored.append((score, doc, sorted(matched)))

    if not scored:
        return []

    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    cutoff = scored[0][0] * relevance
    return [
        {"doc": doc, "score": score, "matched": matched}
        for score, doc, matched in scored[:limit]
        if score >= cutoff
    ]


def snippet(doc, matched, width=160):
    """Таарсан үг агуулсан богино хэсгийг сугалж авна."""
    text = " ".join(doc["body"])
    for token in matched:
        pos = text.lower().find(token[:5])
        if pos != -1:
            start = max(0, pos - width // 3)
            piece = text[start:start + width].strip()
            prefix = "…" if start > 0 else ""
            suffix = "…" if start + width < len(text) else ""
            return prefix + piece + suffix
    return doc["summary"]


def answer(query):
    """Чатны хариултыг бүтэцтэй хэлбэрээр буцаана.

    `text` дотор [[doc-id]] хэлбэрийн орлуулагч байх бөгөөд үүнийг
    frontend линк болгон хөрвүүлнэ.
    """
    hits = search(query)
    if not hits:
        return {
            "text": (
                "Уучлаарай, энэ асуултад тохирох зүйл, заалт олдсонгүй. "
                "Асуултаа өөр үгээр, эсхүл хуулийн нэр дурдаж бичээд үзнэ үү."
            ),
            "sources": [],
        }

    top = hits[0]["doc"]
    lines = [
        "Таны асуултыг [[{}]] зохицуулсан байна.".format(top["id"]),
        "",
        top["summary"],
    ]

    others = [h["doc"] for h in hits[1:]]
    if others:
        lines.append("")
        lines.append(
            "Мөн холбогдох: "
            + ", ".join("[[{}]]".format(d["id"]) for d in others)
            + "."
        )

    lines.append("")
    lines.append("Дэлгэрэнгүйг харахын тулд линк дээр дарна уу.")

    return {
        "text": "\n".join(lines),
        "llm": False,
        "sources": [
            {
                "n": i,
                "cite": h["doc"]["law"] + " " + h["doc"]["article"],
                "id": h["doc"]["id"],
                "law": h["doc"]["law"],
                "article": h["doc"]["article"] + " · " + h["doc"]["title"],
                "category": "Жишээ өгөгдөл",
                "enacted": h["doc"]["effective"],
                "url": "",
                "snippet": snippet(h["doc"], h["matched"]),
                "score": h["score"],
            }
            for i, h in enumerate(hits, 1)
        ],
    }


def get_doc(doc_id):
    """Дэлгэрэнгүй самбарын нэгдсэн бүтэц (agentchat.get_doc-той ижил)."""
    doc = DOCS_BY_ID.get(doc_id)
    if not doc:
        return None
    return {
        "id": doc["id"],
        "law": doc["law"],
        "article": doc["article"],
        "chapter": "",
        "section": doc["title"],
        "category": "Жишээ өгөгдөл",
        "enacted": doc["effective"],
        "enforcement": "",
        "url": "",
        "source_file": "data.py",
        "summary": doc["summary"],
        "tags": doc["tags"],
        "body": doc["body"],
        "same_law": {"items": [], "total": 0, "law": doc["law"]},
        "related": [
            {
                "id": r,
                "law": DOCS_BY_ID[r]["law"],
                "article": DOCS_BY_ID[r]["article"] + " · " + DOCS_BY_ID[r]["title"],
                "category": "Жишээ өгөгдөл",
                "snippet": DOCS_BY_ID[r]["summary"],
                "score": None,
            }
            for r in doc["related"]
            if r in DOCS_BY_ID
        ],
        "note": DISCLAIMER,
    }


# ---------------------------------------------------------------- backend


class DemoBackend:
    """`agentchat.AgentBackend`-тай ижил интерфейс — жишээ өгөгдөл дээр."""

    mode = "demo"

    def status(self):
        return {
            "mode": self.mode,
            "llm": False,
            "note": DISCLAIMER,
            "chat_model": "",
            "embed_model": "",
            "host": "",
            "chunks": len(DOCS),
            "documents": len({d["law"] for d in DOCS}),
            "categories": [],
            "top_k": 3,
            "index_dir": "data.py",
            "disclaimer": DISCLAIMER,
        }

    def suggestions(self):
        return SUGGESTIONS

    def reset(self):
        pass

    def get_doc(self, doc_id):
        return get_doc(doc_id)

    def answer(self, question, category=None):
        return answer(question)

    def stream_answer(self, question, category=None):
        result = answer(question)
        yield "sources", {"sources": result["sources"], "llm": False, "note": ""}
        yield "delta", {"text": result["text"]}
        yield "done", {
            "text": result["text"],
            "sources": result["sources"],
            "llm": False,
            "elapsed": 0,
        }
