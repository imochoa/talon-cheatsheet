// Collapse all foldable sidebar chapters by default, except the ones along
// the path to the currently active page (so you can still see where you are).
//
// mdBook 0.5.2 renders the table of contents in a separate iframe
// (`toc.html`), not inline in each content page. This script runs in the
// parent page, waits for that iframe to load, then collapses chapters inside
// the iframe's document.
//
// mdBook's own toc.js (running inside the iframe) marks the current page's
// link with `active` and adds `expanded` to every ancestor chapter so the
// active location is revealed. By the time the iframe's `load` event fires
// (which we listen for here), that has already happened, so we can rely on
// `a.active` being present and simply preserve the expanded ancestors.
//
// Per-chapter structure (children live in a *sibling* <li>, not inside the
// chapter <li>):
//   <li class="chapter-item expanded ">…<a class="toggle">…</a></li>
//   <li><ol class="section">…children…</ol></li>
// The CSS rule `.chapter li:not(.expanded) > ol { display: none }` hides the
// `ol.section` when the chapter `<li>` lacks `expanded`, so removing
// `expanded` from a chapter <li> collapses its children.
(function () {
  function collapseInDocument(doc) {
    // Collect every ancestor <li class="chapter-item"> of the active link so
    // we leave that branch expanded.
    var keepOpen = new Set();
    var active = doc.querySelector("a.active");
    if (active) {
      var node = active.parentElement;
      while (node) {
        if (node.tagName === "LI" && node.classList.contains("chapter-item")) {
          keepOpen.add(node);
        }
        node = node.parentElement;
      }
    }

    doc.querySelectorAll("li.chapter-item.expanded").forEach(function (li) {
      if (!li.querySelector("a.toggle")) return; // no children -> not foldable
      if (keepOpen.has(li)) return; // keep the active trail open
      li.classList.remove("expanded");
    });
  }

  function setupIframe(iframe) {
    function tryCollapse() {
      try {
        var doc = iframe.contentDocument;
        if (doc && doc.querySelector("li.chapter-item")) {
          collapseInDocument(doc);
        }
      } catch (e) {
        // Cross-origin iframe; can't touch it. Shouldn't happen for same-origin toc.html.
      }
    }
    iframe.addEventListener("load", tryCollapse);
    // Also try immediately in case it already finished loading.
    tryCollapse();
  }

  function init() {
    var iframe = document.querySelector("iframe.sidebar-iframe-outer");
    if (iframe) {
      setupIframe(iframe);
      return;
    }

    // Fallback: if this page itself is the toc (e.g. toc.html loaded directly),
    // collapse within the current document.
    if (document.querySelector("li.chapter-item")) {
      collapseInDocument(document);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
