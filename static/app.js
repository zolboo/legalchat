(function () {
  "use strict";

  var els = {
    messages: document.getElementById("messages"),
    suggestions: document.getElementById("suggestions"),
    form: document.getElementById("composer"),
    input: document.getElementById("input"),
    send: document.getElementById("sendBtn"),
    stop: document.getElementById("stopBtn"),
    clear: document.getElementById("clearBtn"),
    detailBar: document.getElementById("detailBar"),
    detailBarLabel: document.getElementById("detailBarLabel"),
    back: document.getElementById("backBtn"),
    scroll: document.getElementById("detailScroll"),
    empty: document.getElementById("emptyState"),
    materials: document.getElementById("materials"),
    doc: document.getElementById("doc"),
    badge: document.getElementById("statusBadge"),
    brandSub: document.getElementById("brandSub")
  };

  var state = {
    status: null,     // /api/status-ын хариу
    sources: [],      // сүүлийн хариултын эх сурвалж
    question: "",     // сүүлийн асуулт
    docs: {},         // doc_id → дэлгэрэнгүй (кэш)
    view: "empty",    // empty | materials | doc
    shownDoc: null,   // одоо нээлттэй байгаа заалт
    loading: null,    // татагдаж байгаа заалт
    busy: false,
    ctrl: null        // идэвхтэй хүсэлтийг таслах AbortController
  };

  // ---------- туслах ----------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function nearBottom() {
    var box = els.messages;
    return box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  }

  function scrollDown(force) {
    if (force || nearBottom()) els.messages.scrollTop = els.messages.scrollHeight;
  }

  function elapsedText(ms) {
    var s = Math.round(ms / 1000);
    var m = Math.floor(s / 60);
    return m + ":" + String(s % 60).padStart(2, "0");
  }

  // ---------- чатны мессеж ----------

  function addUserMessage(text) {
    var wrap = el("div", "msg user");
    wrap.appendChild(el("div", "bubble", text));
    els.messages.appendChild(wrap);
    scrollDown(true);
  }

  function addBotMessage() {
    var wrap = el("div", "msg bot");
    var bubble = el("div", "bubble");
    var status = el("div", "msg-status");
    wrap.appendChild(bubble);
    wrap.appendChild(status);
    els.messages.appendChild(wrap);
    scrollDown(true);
    return { wrap: wrap, bubble: bubble, status: status };
  }

  function addErrorMessage(text) {
    var wrap = el("div", "msg bot");
    wrap.appendChild(el("div", "bubble error", "⚠ " + text));
    els.messages.appendChild(wrap);
    scrollDown(true);
  }

  function addTyping(bubble) {
    var dots = el("div", "typing");
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    bubble.appendChild(dots);
  }

  /**
   * [[doc-id]] орлуулагчийг дарж болох эшлэл болгож зурна.
   * Агент "[1]" гэж эшилдэг тул линк нь жижиг дугаартай тэмдэг болно;
   * жишээ өгөгдлийн горимд хуулийн нэрээр гарна.
   */
  function renderAnswerText(container, text, sources) {
    var labels = {};
    (sources || []).forEach(function (s) {
      labels[s.id] = s.cite || String(s.n || "");
    });

    var pattern = /\[\[([\w.\-]+)\]\]/g;
    var last = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) {
        container.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      var id = match[1];
      var label = labels[id] || id;
      var numeric = /^\d+$/.test(label);
      var link = el("button", numeric ? "cite" : "doc-link", label);
      link.type = "button";
      link.dataset.docId = id;
      link.title = "Дэлгэрэнгүй харах";
      container.appendChild(link);
      last = pattern.lastIndex;
    }
    if (last < text.length) {
      container.appendChild(document.createTextNode(text.slice(last)));
    }
  }

  function sourceCard(s) {
    var card = el("button", "source-card");
    card.type = "button";
    card.dataset.docId = s.id;

    var head = el("div", "sc-head");
    if (s.n) head.appendChild(el("span", "sc-num", String(s.n)));
    head.appendChild(el("span", "sc-title", s.law));
    card.appendChild(head);

    var meta = [s.article, s.category, s.enacted].filter(Boolean).join(" · ");
    card.appendChild(el("div", "sc-meta", meta));
    if (s.snippet) card.appendChild(el("div", "sc-snip", s.snippet));

    if (s.lexical || s.semantic) {
      var tags = el("div", "sc-tags");
      if (s.lexical) tags.appendChild(el("span", "tag", "түлхүүр үг"));
      if (s.semantic) tags.appendChild(el("span", "tag", "утгын хайлт"));
      card.appendChild(tags);
    }
    return card;
  }

  // ---------- зүүн самбар ----------

  function setView(view) {
    state.view = view;
    els.empty.hidden = view !== "empty";
    els.materials.hidden = view !== "materials";
    els.doc.hidden = view !== "doc";
    els.detailBar.hidden = view !== "doc";
    els.scroll.scrollTop = 0;
  }

  /**
   * Олдсон материалыг зүүн самбарт жагсаана.
   * `keepView` — хэрэглэгч заалт нээчихсэн байвал түүнийг нь хаахгүй.
   */
  function showMaterials(sources, question, keepView) {
    state.sources = sources || [];
    state.question = question || state.question;

    els.materials.textContent = "";
    els.materials.appendChild(el("div", "doc-crumbs", "Холбогдох материал"));
    els.materials.appendChild(
      el("h2", null, state.sources.length
        ? "Индексээс " + state.sources.length + " материал олдлоо"
        : "Тохирох материал олдсонгүй")
    );
    if (state.question) {
      els.materials.appendChild(el("p", "materials-q", "«" + state.question + "»"));
    }

    if (!state.sources.length) {
      els.materials.appendChild(el("p", "doc-note",
        "Хуулийн нэр эсвэл өөр түлхүүр үг дурдаж дахин асууж үзнэ үү."));
    } else {
      els.materials.appendChild(el("p", "materials-hint",
        "Аль нэг дээр нь дарвал бүтэн эх бичвэр, тухайн актын бусад заалт " +
        "болон утгаараа ойролцоо материал доор нээгдэнэ."));
      var list = el("div", "materials-list");
      state.sources.forEach(function (s) { list.appendChild(sourceCard(s)); });
      els.materials.appendChild(list);
    }
    if (!(keepView && state.view === "doc")) setView("materials");
  }

  function docLinkRow(item, className) {
    var btn = el("button", className || "source-card");
    btn.type = "button";
    btn.dataset.docId = item.id;
    btn.appendChild(el("div", "sc-title", item.law || item.article));
    var meta = [item.law ? item.article : null, item.category].filter(Boolean).join(" · ");
    if (meta) btn.appendChild(el("div", "sc-meta", meta));
    if (item.snippet) btn.appendChild(el("div", "sc-snip", item.snippet));
    return btn;
  }

  function renderDoc(doc) {
    els.doc.textContent = "";

    var crumbs = [doc.category, doc.law].filter(Boolean).join(" › ");
    els.doc.appendChild(el("div", "doc-crumbs", crumbs));
    els.doc.appendChild(el("h2", null, doc.article || doc.law));

    var meta = el("div", "doc-meta");
    if (doc.chapter) meta.appendChild(el("span", "chip", doc.chapter));
    if (doc.enacted) meta.appendChild(el("span", "chip plain", "Батлагдсан: " + doc.enacted));
    if (doc.enforcement && doc.enforcement !== doc.enacted) {
      meta.appendChild(el("span", "chip plain", "Хүчин төгөлдөр: " + doc.enforcement));
    }
    (doc.tags || []).forEach(function (t) {
      meta.appendChild(el("span", "chip plain", "#" + t));
    });
    if (doc.url) {
      var link = el("a", "chip link", "legalinfo.mn ↗");
      link.href = doc.url;
      link.target = "_blank";
      link.rel = "noopener";
      meta.appendChild(link);
    }
    if (meta.childNodes.length) els.doc.appendChild(meta);

    if (doc.summary) els.doc.appendChild(el("p", "doc-summary", doc.summary));

    var body = el("div", "doc-body");
    (doc.body || []).forEach(function (p) { body.appendChild(el("p", null, p)); });
    els.doc.appendChild(body);

    var same = doc.same_law || {};
    if (same.items && same.items.length > 1) {
      els.doc.appendChild(el("h3", "doc-section-title",
        "Энэ актын зүйл, бүлэг (" + same.total + ")"));
      var chips = el("div", "article-list");
      same.items.forEach(function (item) {
        var btn = el("button", "article-chip" + (item.active ? " active" : ""), item.article);
        btn.type = "button";
        btn.dataset.docId = item.id;
        if (item.chapter) btn.title = item.chapter;
        chips.appendChild(btn);
      });
      els.doc.appendChild(chips);
    }

    if (doc.related && doc.related.length) {
      els.doc.appendChild(el("h3", "doc-section-title", "Холбогдох материал"));
      var rel = el("div", "materials-list");
      doc.related.forEach(function (r) { rel.appendChild(docLinkRow(r)); });
      els.doc.appendChild(rel);
    }

    if (doc.note) els.doc.appendChild(el("p", "doc-note", doc.note));

    els.detailBarLabel.textContent = doc.law || "";
    els.back.textContent = state.sources.length ? "← Олдсон материал" : "← Буцах";
    state.shownDoc = doc.id;
    setView("doc");
  }

  function openDoc(docId) {
    if (state.loading === docId) return;                       // татагдаж байна
    if (state.view === "doc" && state.shownDoc === docId) return;
    if (location.hash !== "#doc/" + docId) location.hash = "doc/" + docId;

    if (state.docs[docId]) {
      renderDoc(state.docs[docId]);
      return;
    }
    state.loading = docId;
    fetch("/api/doc/" + encodeURIComponent(docId))
      .then(function (r) {
        if (!r.ok) throw new Error("Баримт олдсонгүй");
        return r.json();
      })
      .then(function (doc) {
        state.docs[docId] = doc;
        renderDoc(doc);
      })
      .catch(function (err) { addErrorMessage(err.message); })
      .then(function () { state.loading = null; });
  }

  // ---------- асуулт илгээх ----------

  function setBusy(busy) {
    state.busy = busy;
    els.send.disabled = busy;
    els.input.disabled = busy;
    els.stop.hidden = !busy;
    if (!busy) els.input.focus();
  }

  /** SSE мөрүүдийг (event/data) задлан ажиллуулна. */
  function handleFrame(raw, handlers) {
    var event = "message";
    var data = "";
    raw.split("\n").forEach(function (line) {
      if (line.indexOf("event:") === 0) event = line.slice(6).trim();
      else if (line.indexOf("data:") === 0) data += line.slice(5).trim();
    });
    if (!data) return;
    var payload;
    try { payload = JSON.parse(data); } catch (e) { return; }
    if (handlers[event]) handlers[event](payload);
  }

  function streamAnswer(question, handlers) {
    state.ctrl = new AbortController();
    return fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
      signal: state.ctrl.signal
    }).then(function (r) {
      if (!r.ok) {
        return r.json().then(function (d) { throw new Error(d.error || "Алдаа гарлаа"); });
      }
      if (!r.body || !r.body.getReader) throw new Error("no-stream");

      var reader = r.body.getReader();
      var decoder = new TextDecoder();
      var buffer = "";

      return reader.read().then(function step(res) {
        if (res.done) return;
        buffer += decoder.decode(res.value, { stream: true });
        var idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          handleFrame(buffer.slice(0, idx), handlers);
          buffer = buffer.slice(idx + 2);
        }
        return reader.read().then(step);
      });
    });
  }

  /** Урсгал дэмжихгүй хөтөч дээрх нөөц зам. */
  function plainAnswer(question, handlers) {
    return fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question })
    }).then(function (r) {
      return r.json().then(function (d) {
        if (!r.ok) throw new Error(d.error || "Алдаа гарлаа");
        handlers.sources({ sources: d.sources, llm: d.llm, note: d.note });
        handlers.done(d);
      });
    });
  }

  function ask(question) {
    question = (question || "").trim();
    if (!question || state.busy) return;

    addUserMessage(question);
    els.input.value = "";
    setBusy(true);

    var msg = addBotMessage();
    addTyping(msg.bubble);
    msg.status.textContent = "Индексээс эх сурвалж хайж байна…";

    var started = Date.now();
    var timer = null;
    var streaming = false;
    var buffer = "";
    var sources = [];

    function stopTimer() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    var handlers = {
      sources: function (payload) {
        sources = payload.sources || [];
        showMaterials(sources, question);
        var model = (state.status && state.status.chat_model) || "модел";
        var label = payload.llm
          ? model + " хариулт бичиж байна"
          : "Хайлтын үр дүнг бэлтгэж байна";
        stopTimer();
        timer = setInterval(function () {
          msg.status.textContent = label + " · " + elapsedText(Date.now() - started);
        }, 1000);
        msg.status.textContent = label + " · " + elapsedText(Date.now() - started);
        scrollDown(true);
      },
      delta: function (payload) {
        var stick = nearBottom();
        if (!streaming) {
          streaming = true;
          msg.bubble.textContent = "";
        }
        buffer += payload.text;
        msg.bubble.textContent = buffer;
        if (stick) scrollDown(true);
      },
      done: function (payload) {
        var stick = nearBottom();
        stopTimer();
        msg.bubble.textContent = "";
        renderAnswerText(msg.bubble, payload.text || buffer, payload.sources || sources);
        var secs = payload.elapsed != null
          ? payload.elapsed + " сек"
          : elapsedText(Date.now() - started);
        msg.status.textContent = (payload.llm
          ? (state.status && state.status.chat_model) || "локал модел"
          : "зөвхөн хайлт") + " · " + secs;
        showMaterials(payload.sources || sources, question, true);
        if (stick) scrollDown(true);
      },
      error: function (payload) {
        stopTimer();
        msg.wrap.remove();
        addErrorMessage(payload.error || "Алдаа гарлаа");
      }
    };

    streamAnswer(question, handlers)
      .catch(function (err) {
        if (err && err.name === "AbortError") {
          stopTimer();
          msg.status.textContent = "тасарсан";
          if (!buffer) msg.wrap.remove();
          return;
        }
        if (err && err.message === "no-stream") return plainAnswer(question, handlers);
        throw err;
      })
      .catch(function (err) {
        stopTimer();
        msg.wrap.remove();
        addErrorMessage(err.message || "Сервертэй холбогдож чадсангүй");
      })
      .then(function () {
        state.ctrl = null;
        setBusy(false);
      });
  }

  // ---------- үйл явдал ----------

  document.addEventListener("click", function (ev) {
    var target = ev.target.closest("[data-doc-id]");
    if (target) openDoc(target.dataset.docId);
  });

  els.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    ask(els.input.value);
  });

  els.stop.addEventListener("click", function () {
    if (state.ctrl) state.ctrl.abort();
  });

  els.back.addEventListener("click", function () {
    if (location.hash) location.hash = "";
    state.shownDoc = null;
    setView(state.sources.length ? "materials" : "empty");
  });

  els.clear.addEventListener("click", function () {
    if (state.busy) return;
    els.messages.textContent = "";
    state.sources = [];
    state.question = "";
    state.shownDoc = null;
    if (location.hash) location.hash = "";
    setView("empty");
    fetch("/api/clear", { method: "POST" }).catch(function () {});
    greet();
  });

  window.addEventListener("hashchange", function () {
    var m = location.hash.match(/^#doc\/([\w.\-]+)$/);
    if (m) openDoc(m[1]);
  });

  // ---------- эхлэл ----------

  function greet() {
    var s = state.status;
    var wrap = el("div", "msg bot");
    var bubble = el("div", "bubble");
    var text;
    if (s && s.mode === "agent") {
      text = "Сайн байна уу. legalinfo.mn-ээс татсан " + s.documents +
        " акт (" + s.chunks + " хэсэг) дээр хайж, эшлэлтэй хариулна. " +
        "Олдсон материал зүүн талд гарч ирэх бөгөөд эшлэл дээр дарвал " +
        "тухайн заалт бүтэн эхээрээ нээгдэнэ.";
      if (!s.llm) {
        text += "\n\n⚠ " + s.note;
      } else {
        text += "\n\nCPU дээр нэг хариулт 2-4 минут болохыг анхаарна уу — " +
          "олдсон материал нь эхний секундэд шууд харагдана.";
      }
    } else {
      text = "Сайн байна уу. Одоогоор жишээ өгөгдлийн горимд ажиллаж байна. " +
        "Хөдөлмөр, иргэний эрх зүй, компани, татварын асуудлаар асууж болно.";
    }
    bubble.textContent = text;
    wrap.appendChild(bubble);
    els.messages.appendChild(wrap);
    scrollDown(true);
  }

  function applyStatus(s) {
    state.status = s;
    if (s.mode === "agent") {
      els.badge.textContent = s.llm
        ? "Локал агент · " + s.chat_model
        : "Зөвхөн хайлт";
      els.badge.className = "status-badge" + (s.llm ? " ok" : "");
      els.badge.title = s.llm
        ? s.chunks + " хэсэг · эрэл: BM25 + " + s.embed_model + " · " + s.host
        : s.note;
      els.brandSub.textContent =
        "legalinfo.mn — " + s.documents + " акт, " + s.chunks + " хэсэг · локал, офлайн";
    } else {
      els.badge.textContent = "Жишээ өгөгдөл";
      els.badge.title = s.note || "";
    }
  }

  fetch("/api/status")
    .then(function (r) { return r.json(); })
    .then(applyStatus)
    .catch(function () {
      els.badge.textContent = "Холбогдсонгүй";
    })
    .then(function () {
      greet();
      var initial = location.hash.match(/^#doc\/([\w.\-]+)$/);
      if (initial) openDoc(initial[1]);
    });

  fetch("/api/suggestions")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      (data.suggestions || []).forEach(function (q) {
        var btn = el("button", "suggestion", q);
        btn.type = "button";
        btn.addEventListener("click", function () { ask(q); });
        els.suggestions.appendChild(btn);
      });
    })
    .catch(function () { /* санал байхгүй байсан ч чат ажиллана */ });

  els.input.focus();
})();
