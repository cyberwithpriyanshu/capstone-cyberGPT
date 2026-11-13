/*
  app/static/markdown.js

  PURPOSE: Turns the AI's answer (written in "Markdown", where **bold** and # headings are
  plain-text codes) into nicely formatted HTML: headings, lists, tables, code blocks.

  SECURITY (important for a security tool!): the AI's text is UNTRUSTED. Imagine an answer
  containing <script>steal()</script>. If we put that into the page as-is, the browser would
  RUN it. So this file ALWAYS escapes the text first (turning "<" into "&lt;") and only
  afterwards adds the few HTML tags we choose ourselves. Links are only allowed to start
  with http://, https:// or mailto:, so "javascript:" links cannot sneak through.

  Usage:  element.innerHTML = renderMarkdown(answerText);
*/
(function (global) {
  "use strict";

  // Turn the five special HTML characters into harmless text versions.
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  // ---------- inline formatting: `code`, **bold**, *italic*, [links](https://...) ----------
  function renderInline(raw) {
    // 1. Pull out `code spans` first and park them under placeholders, so nothing inside
    //    them gets formatted. (\u0000 is an invisible character that cannot appear in the
    //    text, because renderMarkdown removes it from the input.)
    const codes = [];
    let s = raw.replace(/`([^`\n]+)`/g, function (_, code) {
      codes.push(code);
      return "\u0000" + (codes.length - 1) + "\u0000";
    });

    // 2. Escape everything, so no HTML from the AI survives.
    s = escapeHtml(s);

    // 2b. Models often write <br> to start a new line inside a table cell. We never let the
    //     AI's HTML through, but a line break is harmless, so this ONE tag is allowed back
    //     in (matched only in its escaped form, so nothing else can sneak through).
    s = s.replace(/&lt;br\s*\/?&gt;/gi, "<br>");

    // 3. Links. Only http, https and mailto addresses are turned into links.
    s = s.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g,
      function (_, text, url) {
        return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + text + "</a>";
      }
    );

    // 4. Bold and italic.
    s = s.replace(/\*\*([^*\n]+?)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g, "$1<em>$2</em>");

    // 5. Put the code spans back (escaped).
    s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) {
      return "<code>" + escapeHtml(codes[Number(i)]) + "</code>";
    });
    return s;
  }

  // ---------- code blocks (``` fenced ```) ----------
  function codeBlock(code, lang) {
    const label = lang ? escapeHtml(lang) : "text";
    return (
      '<div class="codeblock">' +
      '<div class="codeblock-bar"><span class="codeblock-lang">' + label + "</span>" +
      '<button type="button" class="copy-code" aria-label="Copy code">Copy</button></div>' +
      "<pre><code>" + escapeHtml(code) + "</code></pre></div>"
    );
  }

  // ---------- helpers that recognise what kind of line we are looking at ----------
  const FENCE_OPEN = /^\s*```\s*([\w+#.-]*)\s*$/;
  const FENCE_CLOSE = /^\s*```\s*$/;
  const HEADING = /^(#{1,4})\s+(.*)$/;
  const HR = /^\s*([-*_])\s*(?:\1\s*){2,}$/;
  const QUOTE = /^\s*>\s?/;
  const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
  const TABLE_DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

  function isBlank(line) {
    return /^\s*$/.test(line);
  }

  function startsTable(lines, i) {
    return lines[i].indexOf("|") !== -1 && i + 1 < lines.length && TABLE_DIVIDER.test(lines[i + 1]);
  }

  // Does line i begin something other than a plain paragraph?
  function startsBlock(lines, i) {
    const line = lines[i];
    return (
      FENCE_OPEN.test(line) ||
      HEADING.test(line) ||
      HR.test(line) ||
      QUOTE.test(line) ||
      LIST_ITEM.test(line) ||
      startsTable(lines, i)
    );
  }

  // ---------- lists (with one or more levels of nesting) ----------
  function renderList(lines, start) {
    const stack = []; // the lists we are currently inside: { indent, tag }
    let html = "";
    let i = start;

    while (i < lines.length) {
      // A blank line inside a list is allowed if another list item follows it.
      if (isBlank(lines[i])) {
        let j = i;
        while (j < lines.length && isBlank(lines[j])) j++;
        if (j < lines.length && LIST_ITEM.test(lines[j])) {
          i = j;
          continue;
        }
        break;
      }

      const m = lines[i].match(LIST_ITEM);
      if (!m) break;

      const indent = m[1].replace(/\t/g, "    ").length;
      const ordered = /\d/.test(m[2]);
      const tag = ordered ? "ol" : "ul";

      // Close lists that are indented deeper than this item.
      while (stack.length && indent < stack[stack.length - 1].indent) {
        html += "</li></" + stack.pop().tag + ">";
      }

      if (stack.length && indent === stack[stack.length - 1].indent) {
        html += "</li><li>"; // another item at the same level
      } else {
        // First item, or a deeper (nested) list.
        const startNumber = ordered ? parseInt(m[2], 10) : 1;
        const startAttr = ordered && startNumber !== 1 ? ' start="' + startNumber + '"' : "";
        html += "<" + tag + startAttr + "><li>";
        stack.push({ indent: indent, tag: tag });
      }

      html += renderInline(m[3]);
      i++;
    }

    while (stack.length) html += "</li></" + stack.pop().tag + ">";
    return [html, i];
  }

  // ---------- tables ----------
  function splitRow(line) {
    let t = line.trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.trim(); });
  }

  function renderTable(lines, start) {
    const head = splitRow(lines[start]);
    let i = start + 2; // skip the header row and the ---|--- divider row
    let html = '<div class="table-wrap"><table><thead><tr>';
    head.forEach(function (c) { html += "<th>" + renderInline(c) + "</th>"; });
    html += "</tr></thead><tbody>";
    while (i < lines.length && !isBlank(lines[i]) && lines[i].indexOf("|") !== -1) {
      html += "<tr>";
      splitRow(lines[i]).forEach(function (c) { html += "<td>" + renderInline(c) + "</td>"; });
      html += "</tr>";
      i++;
    }
    html += "</tbody></table></div>";
    return [html, i];
  }

  // ---------- the main function: goes through the text line by line ----------
  function renderMarkdown(source) {
    const lines = String(source).replace(/\r\n?/g, "\n").replace(/\u0000/g, "").split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (isBlank(line)) { i++; continue; }

      // Code block. If the closing ``` has not arrived yet (the answer is still streaming),
      // everything so far is shown as code, and it completes itself once the fence arrives.
      const fence = line.match(FENCE_OPEN);
      if (fence) {
        const indent = line.match(/^\s*/)[0].length;
        const buf = [];
        i++;
        while (i < lines.length && !FENCE_CLOSE.test(lines[i])) {
          const lead = lines[i].match(/^\s*/)[0].length;
          buf.push(lines[i].slice(Math.min(indent, lead)));
          i++;
        }
        i++; // skip the closing fence
        out.push(codeBlock(buf.join("\n"), fence[1]));
        continue;
      }

      const h = line.match(HEADING);
      if (h) {
        // Chat answers need modest headings: "#" and "##" both become <h3> (the biggest we
        // use), "###" becomes <h4>, and "####" becomes <h5>.
        const level = h[1].length <= 2 ? 3 : h[1].length + 1;
        out.push("<h" + level + ">" + renderInline(h[2]) + "</h" + level + ">");
        i++;
        continue;
      }

      if (HR.test(line)) { out.push("<hr>"); i++; continue; }

      if (QUOTE.test(line)) {
        const buf = [];
        while (i < lines.length && QUOTE.test(lines[i])) {
          buf.push(lines[i].replace(QUOTE, ""));
          i++;
        }
        out.push("<blockquote>" + renderMarkdown(buf.join("\n")) + "</blockquote>");
        continue;
      }

      if (startsTable(lines, i)) {
        const r = renderTable(lines, i);
        out.push(r[0]);
        i = r[1];
        continue;
      }

      if (LIST_ITEM.test(line)) {
        const r = renderList(lines, i);
        out.push(r[0]);
        i = r[1];
        continue;
      }

      // Otherwise it is a paragraph: keep reading until a blank line or another block starts.
      const buf = [line];
      i++;
      while (i < lines.length && !isBlank(lines[i]) && !startsBlock(lines, i)) {
        buf.push(lines[i]);
        i++;
      }
      out.push("<p>" + buf.map(renderInline).join("<br>") + "</p>");
    }

    return out.join("\n");
  }

  global.renderMarkdown = renderMarkdown;

  // This lets us test the file with Node.js too; browsers simply skip this part.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { renderMarkdown: renderMarkdown, escapeHtml: escapeHtml };
  }
})(typeof window !== "undefined" ? window : globalThis);