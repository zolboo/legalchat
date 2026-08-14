(function () {
  "use strict";

  var messagesEl = document.getElementById("messages");
  var suggestionsEl = document.getElementById("suggestions");
  var formEl = document.getElementById("composer");
  var inputEl = document.getElementById("input");
  var sendBtn = document.getElementById("sendBtn");
  var clearBtn = document.getElementById("clearBtn");
  var detailEl = document.getElementById("doc");
  var emptyEl = document.getElementById("emptyState");

  var docCache = {};

  // ---------- туслах ----------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function scrollDown() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // ---------- чатны мессеж ----------

  function addUserMessage(text) {
    var wrap = el("div", "msg user");
    wrap.appendChild(el("div", "bubble", text));
    messagesEl.appendChild(wrap);
    scrollDown();
  }

  function addTyping() {
    var wrap = el("div", "msg bot");
    var bubble = el("div", "bubble");
    var dots = el("div", "typing");
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    dots.appendChild(el("span"));
    bubble.appendChild(dots);
    wrap.appendChild(bubble);
    messagesEl.appendChild(wrap);
    scrollDown();
    return wrap;
  }

  /**
   * [[doc-id]] орлуулагчийг дарж болох линк болгож текстийг зурна.
   * innerHTML ашиглахгүй тул агуулга нь HTML болж тайлагдахгүй.
   */
  function renderAnswerText(container, text, titleById) {
    var pattern = /\[\[([\w-]+)\]\]/g;
    var last = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) {
        container.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      var id = match[1];
      var link = el("button", "doc-link", titleById[id] || id);
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

  function addBotMessage(result) {
    var titleById = {};
    (result.sources || []).forEach(function (s) {
      titleById[s.id] = s.law + " " + s.article;
    });

    var wrap = el("div", "msg bot");
    var bubble = el("div", "bubble");
    renderAnswerText(bubble, result.text, titleById);
    wrap.appendChild(bubble);

    if (result.sources && result.sources.length) {
      var list = el("div", "sources");
      result.sources.forEach(function (s) {
        var card = el("button", "source-card");
        card.type = "button";
        card.dataset.docId = s.id;
        card.appendChild(el("div", "sc-title", s.title));
        card.appendChild(el("div", "sc-meta", s.law + " · " + s.article));
        card.appendChild(el("div", "sc-snip", s.snippet));
        list.appendChild(card);
      });
      wrap.appendChild(list);
    }

    messagesEl.appendChild(wrap);
    scrollDown();
  }

  function addErrorMessage(text) {
    var wrap = el("div", "msg bot");
    wrap.appendChild(el("div", "bubble", "⚠ " + text));
    messagesEl.appendChild(wrap);
    scrollDown();
  }

  // ---------- дэлгэрэнгүй самбар ----------

  function renderDoc(doc) {
    detailEl.textContent = "";

    detailEl.appendChild(el("div", "doc-crumbs", doc.law + " › " + doc.article));
    detailEl.appendChild(el("h2", null, doc.title));

    var meta = el("div", "doc-meta");
    meta.appendChild(el("span", "chip", doc.article));
    meta.appendChild(el("span", "chip plain", "Хүчин төгөлдөр: " + doc.effective));
    doc.tags.forEach(function (t) {
      meta.appendChild(el("span", "chip plain", "#" + t));
    });
    detailEl.appendChild(meta);

    detailEl.appendChild(el("p", "doc-summary", doc.summary));

    var body = el("div", "doc-body");
    doc.body.forEach(function (p) {
      body.appendChild(el("p", null, p));
    });
    detailEl.appendChild(body);

    if (doc.related.length) {
      detailEl.appendChild(el("h3", "doc-section-title", "Холбогдох зүйл, заалт"));
      var rel = el("div", "related-list");
      doc.related.forEach(function (r) {
        var btn = el("button", "source-card", "");
        btn.type = "button";
        btn.dataset.docId = r.id;
        btn.style.width = "auto";
        btn.appendChild(el("div", "sc-title", r.title));
        btn.appendChild(el("div", "sc-meta", r.article));
        rel.appendChild(btn);
      });
      detailEl.appendChild(rel);
    }

    detailEl.appendChild(
      el(
        "p",
        "doc-note",
        "Тайлбар: энэ агуулга нь демо зориулалттай хялбаршуулсан жишээ бөгөөд " +
          "албан ёсны хуулийн эх бичвэр биш. Хууль зүйн зөвлөгөө болохгүй."
      )
    );

    emptyEl.hidden = true;
    detailEl.hidden = false;
    detailEl.parentElement.scrollTop = 0;
  }

  function openDoc(docId) {
    if (docCache[docId]) {
      renderDoc(docCache[docId]);
      if (location.hash !== "#doc/" + docId) location.hash = "doc/" + docId;
      return;
    }
    fetch("/api/doc/" + encodeURIComponent(docId))
      .then(function (r) {
        if (!r.ok) throw new Error("Баримт олдсонгүй");
        return r.json();
      })
      .then(function (doc) {
        docCache[docId] = doc;
        renderDoc(doc);
        if (location.hash !== "#doc/" + docId) location.hash = "doc/" + docId;
      })
      .catch(function (err) {
        addErrorMessage(err.message);
      });
  }

  // ---------- асуулт илгээх ----------

  function ask(question) {
    question = (question || "").trim();
    if (!question) return;

    addUserMessage(question);
    inputEl.value = "";
    sendBtn.disabled = true;
    var typing = addTyping();

    fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question })
    })
      .then(function (r) {
        return r.json().then(function (data) {
          if (!r.ok) throw new Error(data.error || "Алдаа гарлаа");
          return data;
        });
      })
      .then(function (result) {
        typing.remove();
        addBotMessage(result);
      })
      .catch(function (err) {
        typing.remove();
        addErrorMessage(err.message);
      })
      .finally(function () {
        sendBtn.disabled = false;
        inputEl.focus();
      });
  }

  // ---------- үйл явдал ----------

  // Чат болон дэлгэрэнгүй самбар доторх бүх линкийг нэг дор барина
  document.addEventListener("click", function (ev) {
    var target = ev.target.closest("[data-doc-id]");
    if (target) openDoc(target.dataset.docId);
  });

  formEl.addEventListener("submit", function (ev) {
    ev.preventDefault();
    ask(inputEl.value);
  });

  clearBtn.addEventListener("click", function () {
    messagesEl.textContent = "";
    greet();
  });

  window.addEventListener("hashchange", function () {
    var m = location.hash.match(/^#doc\/([\w-]+)$/);
    if (m) openDoc(m[1]);
  });

  // ---------- эхлэл ----------

  function greet() {
    addBotMessage({
      text:
        "Сайн байна уу. Хөдөлмөр, иргэний эрх зүй, компани, татварын " +
        "асуудлаар асууж болно. Хариултад гарах линк дээр дарвал зүүн " +
        "талд дэлгэрэнгүй мэдээлэл нээгдэнэ.",
      sources: []
    });
  }

  fetch("/api/suggestions")
    .then(function (r) { return r.json(); })
    .then(function (data) {
      (data.suggestions || []).forEach(function (q) {
        var btn = el("button", "suggestion", q);
        btn.type = "button";
        btn.addEventListener("click", function () { ask(q); });
        suggestionsEl.appendChild(btn);
      });
    })
    .catch(function () { /* санал байхгүй байсан ч чат ажиллана */ });

  greet();

  var initial = location.hash.match(/^#doc\/([\w-]+)$/);
  if (initial) openDoc(initial[1]);

  inputEl.focus();
})();
