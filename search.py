"""Энгийн түлхүүр үгэнд суурилсан хайлт (гуравдагч сан ашиглаагүй).

Монгол хэлний нөхцөл, тийн ялгалыг бүрэн задлахгүй тул үгийн эхний
хэсгээр (prefix) харьцуулах аргыг ашиглав. Бодит төсөлд үүнийг
embedding/vector search-ээр солино.
"""

import re
from collections import Counter

from data import DOCS, DOCS_BY_ID

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
        "sources": [
            {
                "id": h["doc"]["id"],
                "title": h["doc"]["title"],
                "law": h["doc"]["law"],
                "article": h["doc"]["article"],
                "snippet": snippet(h["doc"], h["matched"]),
                "score": h["score"],
            }
            for h in hits
        ],
    }


def get_doc(doc_id):
    doc = DOCS_BY_ID.get(doc_id)
    if not doc:
        return None
    return {
        "id": doc["id"],
        "law": doc["law"],
        "article": doc["article"],
        "title": doc["title"],
        "summary": doc["summary"],
        "effective": doc["effective"],
        "tags": doc["tags"],
        "body": doc["body"],
        "related": [
            {
                "id": r,
                "title": DOCS_BY_ID[r]["title"],
                "article": DOCS_BY_ID[r]["article"],
            }
            for r in doc["related"]
            if r in DOCS_BY_ID
        ],
    }
