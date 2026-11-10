/*
  app/static/app.js

  PURPOSE: The behaviour of the page. It:
    - loads the mode buttons and example questions from our server (/modes)
    - sends your message to /chat/stream and shows the answer word by word
    - formats the answer (using markdown.js) and adds Copy buttons
    - lets you stop a reply, retry after an error, and start a new chat
    - checks every 15 seconds whether the AI model is reachable (/health)
    - remembers your colour and mode choice in this browser

  Everything is wrapped in one function so our variables don't leak into the page.
*/
(function () {
  "use strict";

  // ----------------------------------------------------------------
  // Settings and shared state
  // ----------------------------------------------------------------
  const SVG_NS = "http://www.w3.org/2000/svg";
  const MAX_CHARS = 8000;          // must match the limit in app/api/schemas.py
  const MAX_HISTORY = 40;          // the server accepts 50 messages; we stay below that
  const HEALTH_EVERY_MS = 15000;   // how often to re-check the model
  const THEMES = ["cyan", "green", "violet", "amber"];
  const DEFAULT_HINT = "Enter sends. Shift+Enter adds a line.";

  const state = {
    modes: [],         // loaded from /modes
    mode: "learn",     // the selected mode id
    messages: [],      // the conversation we send to the server: [{role, content}, ...]
    busy: false,       // true while an answer is being written
    controller: null,  // lets us cancel the request (the Stop button)
  };

  // Shortcuts to the page elements (their ids are in index.html)
  const byId = (id) => document.getElementById(id);
  const dom = {
    app: byId("app"), thread: byId("thread"), threadInner: byId("threadInner"),
    composer: byId("composer"), input: byId("input"), send: byId("send"),
    modes: byId("modes"), modeHint: byId("modeHint"), suggestions: byId("suggestions"),
    status: byId("status"), statusText: byId("statusText"), hint: byId("hint"),
    newChat: byId("newChat"), themes: byId("themes"), announce: byId("announce"),
  };

  // ----------------------------------------------------------------
  // Small helpers
  // ----------------------------------------------------------------

  // Create an element: make("div", "msg bot", "optional text")
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Create an icon from the sprite in index.html: icon("send")
  function icon(name) {
    const id = "i-" + name;
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", "#" + (document.getElementById(id) ? id : "i-dot"));
    svg.appendChild(use);
    return svg;
  }

  // Browser storage can be blocked (private windows, strict settings), so every access
  // is wrapped in try/catch and the page works fine without it.
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* ignore */ } },
  };

  // Tell screen readers what is happening ("CyberGPT is replying").
  function announce(text) { dom.announce.textContent = text; }

  // Show a short message in the hint spot under the message box, then restore it.
  let hintTimer = null;
  function flashHint(message) {
    dom.hint.textContent = message;
    dom.hint.dataset.tone = "warn";
    clearTimeout(hintTimer);
    hintTimer = setTimeout(() => {
      dom.hint.textContent = DEFAULT_HINT;
      delete dom.hint.dataset.tone;
    }, 6000);
  }

  // Copy text to the clipboard. Returns true if it worked.
  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      try { // older fallback for pages where the modern clipboard is not allowed
        const box = document.createElement("textarea");
        box.value = text;
        box.style.cssText = "position:fixed;opacity:0";
        document.body.appendChild(box);
        box.select();
        const ok = document.execCommand("copy");
        box.remove();
        return ok;
      } catch (e2) { return false; }
    }
  }

  // Briefly change a button's label to "Copied".
  function flashCopied(button, copiedLabel, normalLabel, ok) {
    const labelNode = button.querySelector("span") || button;
    labelNode.textContent = ok ? copiedLabel : "Copy failed";
    button.dataset.copied = ok ? "true" : "false";
    setTimeout(() => { labelNode.textContent = normalLabel; delete button.dataset.copied; }, 1600);
  }

  // ----------------------------------------------------------------
  // Colour theme
  // ----------------------------------------------------------------
  function applyTheme(name) {
    if (THEMES.indexOf(name) === -1) name = "cyan";
    document.documentElement.setAttribute("data-theme", name);
    dom.themes.querySelectorAll(".swatch").forEach((b) => {
      b.setAttribute("aria-pressed", String(b.dataset.themeChoice === name));
    });
    store.set("cybergpt-theme", name);
  }

  dom.themes.addEventListener("click", (e) => {
    const button = e.target.closest(".swatch");
    if (button) applyTheme(button.dataset.themeChoice);
  });

  // ----------------------------------------------------------------
  // Model status light (top right)
  // ----------------------------------------------------------------
  function setStatus(kind, text, detail) {
    dom.status.dataset.state = kind;
    if (dom.statusText.textContent !== text) dom.statusText.textContent = text;
    dom.status.title = detail || "";
  }

  async function checkHealth() {
    try {
      const res = await fetch("/health", { cache: "no-store" });
      const data = await res.json();
      const llm = data.llm;
      if (llm.provider === "fake") {
        setStatus("demo", "Demo mode: no model", "CyberGPT is using the built-in fake AI. Set CYBERGPT_LLM_PROVIDER=openai_compat in .env to use a real model.");
      } else if (llm.ok) {
        setStatus("ok", llm.model, "Model online");
      } else {
        setStatus("degraded", "Model unavailable", llm.detail);
      }
    } catch (e) {
      setStatus("offline", "Server offline", "Start it with: uvicorn app.main:app --reload");
    }
  }

  // ----------------------------------------------------------------
  // Modes and example questions
  // ----------------------------------------------------------------
  function currentMode() {
    return state.modes.find((m) => m.id === state.mode) || state.modes[0];
  }

  function renderModes() {
    dom.modes.textContent = "";
    state.modes.forEach((m) => {
      const chip = make("button", "chip");
      chip.type = "button";
      chip.setAttribute("role", "radio");
      chip.dataset.mode = m.id;
      chip.appendChild(icon(m.id));
      chip.appendChild(make("span", "", m.label));
      dom.modes.appendChild(chip);
    });
  }

  function renderSuggestions() {
    dom.suggestions.textContent = "";
    (currentMode().suggestions || []).forEach((text) => {
      const li = make("li");
      const button = make("button", "", text);
      button.type = "button";
      button.addEventListener("click", () => sendMessage(text));
      li.appendChild(button);
      dom.suggestions.appendChild(li);
    });
  }

  function setMode(id, moveFocus) {
    if (!state.modes.some((m) => m.id === id)) id = state.modes[0].id;
    state.mode = id;
    store.set("cybergpt-mode", id);

    dom.modes.querySelectorAll(".chip").forEach((chip) => {
      const active = chip.dataset.mode === id;
      chip.setAttribute("aria-checked", String(active));
      chip.tabIndex = active ? 0 : -1; // only the selected one is a Tab stop; arrows move between them
      if (active && moveFocus) chip.focus();
    });

    const mode = currentMode();
    dom.input.placeholder = mode.placeholder;
    dom.modeHint.textContent = mode.tagline;
    renderSuggestions();
  }

  dom.modes.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (chip) setMode(chip.dataset.mode);
  });

  // Arrow keys move between the mode buttons, like a standard radio group.
  dom.modes.addEventListener("keydown", (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    const i = state.modes.findIndex((m) => m.id === state.mode);
    setMode(state.modes[(i + step + state.modes.length) % state.modes.length].id, true);
  });

  async function loadModes() {
    try {
      const res = await fetch("/modes");
      if (!res.ok) throw new Error("bad status");
      state.modes = await res.json();
    } catch (e) {
      dom.modeHint.textContent = "Can't reach the CyberGPT server. Start it with: uvicorn app.main:app --reload";
      return;
    }
    renderModes();
    setMode(store.get("cybergpt-mode") || state.modes[0].id);
  }

  // ----------------------------------------------------------------
  // The message box
  // ----------------------------------------------------------------

  // Grow the box as you type (up to a limit), and enable Send only when there is text.
  function refreshComposer() {
    dom.input.style.height = "auto";
    dom.input.style.height = Math.min(dom.input.scrollHeight, 220) + "px";
    dom.send.disabled = !state.busy && dom.input.value.trim() === "";
  }

  function setBusy(busy) {
    state.busy = busy;
    dom.composer.dataset.busy = String(busy);
    dom.send.dataset.mode = busy ? "stop" : "send";
    dom.send.setAttribute("aria-label", busy ? "Stop reply" : "Send message");
    dom.send.replaceChildren(icon(busy ? "stop" : "send"));
    refreshComposer();
  }

  dom.input.addEventListener("input", refreshComposer);

  dom.input.addEventListener("keydown", (e) => {
    // Enter sends; Shift+Enter makes a new line. (isComposing = the user is still
    // choosing characters with an input method, so we must not send yet.)
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (!state.busy) sendMessage(dom.input.value);
    }
  });

  dom.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (state.busy) stopReply();
    else sendMessage(dom.input.value);
  });

  // Escape also stops a reply.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.busy) stopReply();
  });

  function stopReply() {
    if (state.controller) state.controller.abort();
  }

  // ----------------------------------------------------------------
  // Showing messages
  // ----------------------------------------------------------------
  function setScreen(name) { dom.app.dataset.state = name; }

  function isPinnedToBottom() {
    const t = dom.thread;
    return t.scrollHeight - t.scrollTop - t.clientHeight < 90;
  }
  function scrollToBottom() { dom.thread.scrollTop = dom.thread.scrollHeight; }

  function addUserMessage(text) {
    const root = make("article", "msg user");
    root.appendChild(make("div", "bubble", text)); // textContent, so it can never run as HTML
    dom.threadInner.appendChild(root);
  }

  function addBotMessage() {
    const root = make("article", "msg bot");

    const avatar = make("div", "avatar");
    avatar.appendChild(icon("mark"));

    const body = make("div", "body");
    const meta = make("div", "meta");
    meta.appendChild(make("span", "who", "CyberGPT"));
    meta.appendChild(make("span", "mode-tag", currentMode().label));

    // Three bouncing dots until the first word arrives (fixed markup, no user data).
    const prose = make("div", "prose streaming");
    prose.innerHTML = '<span class="typing" role="img" aria-label="CyberGPT is thinking"><i></i><i></i><i></i></span>';

    const actions = make("div", "actions");
    body.append(meta, prose, actions);
    root.append(avatar, body);
    dom.threadInner.appendChild(root);
    return { root, body, prose, actions };
  }

  // Re-draws the answer at most once per screen refresh, however fast the words arrive.
  function makePainter(bot) {
    let queued = false;
    let latest = "";
    function paint() {
      if (!queued) return; // cancelled
      queued = false;
      const pinned = isPinnedToBottom();
      bot.prose.innerHTML = window.renderMarkdown(latest);
      if (pinned) scrollToBottom();
    }
    return {
      schedule(text) { latest = text; if (!queued) { queued = true; requestAnimationFrame(paint); } },
      cancel() { queued = false; },
    };
  }

  function makeAction(label, iconName, onClick) {
    const button = make("button", "act");
    button.type = "button";
    button.appendChild(icon(iconName));
    button.appendChild(make("span", "", label));
    button.addEventListener("click", () => onClick(button));
    return button;
  }

  function addReplyActions(bot, text) {
    bot.actions.appendChild(
      makeAction("Copy", "copy", async (button) => {
        flashCopied(button, "Copied", "Copy", await copyText(text));
      })
    );
  }

  function showError(bot, message) {
    const box = make("div", "error-note");
    box.setAttribute("role", "alert");
    box.appendChild(make("span", "", message));
    box.appendChild(
      makeAction("Retry", "retry", () => {
        if (state.busy) return;
        bot.root.remove();
        getReply();
      })
    );
    bot.body.appendChild(box);
  }

  // Copy buttons inside code blocks. One listener on the whole conversation handles them all,
  // including blocks that did not exist yet when the page loaded.
  dom.threadInner.addEventListener("click", async (e) => {
    const button = e.target.closest(".copy-code");
    if (!button) return;
    const code = button.closest(".codeblock").querySelector("code").textContent;
    flashCopied(button, "Copied", "Copy", await copyText(code));
  });

  // ----------------------------------------------------------------
  // Talking to the server
  // ----------------------------------------------------------------

  // kind: "model" = the AI reported a problem; "http" = the server refused; "network" = no connection
  class ApiError extends Error {
    constructor(message, kind) { super(message); this.kind = kind; }
  }

  function requestBody() {
    let history = state.messages.slice(-MAX_HISTORY);
    while (history.length && history[0].role !== "user") history.shift(); // must start with a user message
    return JSON.stringify({ messages: history, mode: state.mode });
  }

  async function readHttpError(res) {
    try {
      const data = await res.json();
      if (typeof data.detail === "string") return data.detail;
      if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
    } catch (e) { /* not JSON */ }
    return "The server replied with HTTP " + res.status + ".";
  }

  // Reads the streamed answer. `onToken` is called for every piece of text.
  async function streamChat(signal, onToken) {
    let res;
    try {
      res = await fetch("/chat/stream", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: requestBody(), signal: signal,
      });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError("Can't reach the CyberGPT server. Is it running?", "network");
    }
    if (!res.ok) throw new ApiError(await readHttpError(res), "http");
    if (!res.body) throw new ApiError("This browser can't stream replies.", "network");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finished = false;

    while (true) {
      let chunk;
      try {
        chunk = await reader.read();
      } catch (e) {
        if (e.name === "AbortError") throw e;
        throw new ApiError("The connection was interrupted.", "network");
      }
      if (chunk.done) break;

      buffer += decoder.decode(chunk.value, { stream: true });

      // Messages from the server are separated by a blank line (see sse_event in routes.py).
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, end).trim();
        buffer = buffer.slice(end + 2);
        if (block.indexOf("data:") !== 0) continue;

        let event;
        try { event = JSON.parse(block.slice(5)); } catch (e) { continue; }

        if (event.type === "token") onToken(event.text);
        else if (event.type === "error") throw new ApiError(event.message, "model");
        else if (event.type === "done") finished = true;
      }
    }
    if (!finished) throw new ApiError("The reply was cut off before it finished.", "network");
  }

  // The non-streaming route. Used only as a fallback when streaming is blocked
  // (some proxies and tunnels do not allow streamed replies).
  async function plainChat(signal) {
    let res;
    try {
      res = await fetch("/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: requestBody(), signal: signal,
      });
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw new ApiError("Can't reach the CyberGPT server. Is it running?", "network");
    }
    if (!res.ok) throw new ApiError(await readHttpError(res), "http");
    return (await res.json()).reply;
  }

  // ----------------------------------------------------------------
  // The full "ask and answer" flow
  // ----------------------------------------------------------------
  async function sendMessage(raw) {
    const text = raw.trim();
    if (!text || state.busy) return;

    if (text.length > MAX_CHARS) {
      flashHint("That message is " + text.length.toLocaleString() + " characters. The limit is 8,000. Shorten it or send it in parts.");
      return;
    }

    state.messages.push({ role: "user", content: text });
    addUserMessage(text);
    dom.input.value = "";
    refreshComposer();
    setScreen("chat");
    scrollToBottom();
    await getReply();
  }

  async function getReply() {
    const bot = addBotMessage();
    const painter = makePainter(bot);
    setBusy(true);
    announce("CyberGPT is replying");
    scrollToBottom();

    const controller = new AbortController();
    state.controller = controller;

    let text = "";
    let outcome = "done"; // "done", "stopped" or "error"
    let errorMessage = "";

    try {
      try {
        await streamChat(controller.signal, (piece) => {
          text += piece;
          painter.schedule(text);
        });
      } catch (err) {
        // If streaming itself was blocked and nothing arrived yet, quietly try the normal route.
        const streamingBlocked = err instanceof ApiError && err.kind !== "model" && text === "";
        if (!streamingBlocked) throw err;
        text = await plainChat(controller.signal);
      }
    } catch (err) {
      if (err && err.name === "AbortError") {
        outcome = "stopped";
      } else {
        outcome = "error";
        errorMessage = err instanceof ApiError ? err.message : "Something went wrong in the browser.";
      }
    }

    // ---- wrap up ----
    // Remember whether the reader was following the bottom, so we can keep following
    // after adding the error message or the buttons below the answer.
    const wasPinned = isPinnedToBottom();
    painter.cancel();
    state.controller = null;
    bot.prose.classList.remove("streaming");
    bot.prose.innerHTML = text ? window.renderMarkdown(text) : "";
    if (!text) bot.prose.remove();

    if (outcome === "error") {
      showError(bot, text ? "The reply stopped early: " + errorMessage : errorMessage);
      checkHealth(); // the status light may now be out of date
    } else if (text) {
      state.messages.push({ role: "assistant", content: text });
      // The note goes BEFORE the buttons row, so it sits right under the text.
      if (outcome === "stopped") bot.body.insertBefore(make("p", "note", "Reply stopped."), bot.actions);
      addReplyActions(bot, text);
    } else {
      bot.root.remove(); // stopped before anything arrived: leave no empty message behind
    }

    if (wasPinned) scrollToBottom();

    setBusy(false);
    announce(outcome === "error" ? "CyberGPT could not reply" : "Reply complete");
    dom.input.focus();
  }

  // ----------------------------------------------------------------
  // New chat
  // ----------------------------------------------------------------
  dom.newChat.addEventListener("click", () => {
    stopReply();
    state.messages = [];
    dom.threadInner.textContent = "";
    setScreen("home");
    dom.input.focus();
  });

  // ----------------------------------------------------------------
  // Start-up
  // ----------------------------------------------------------------
  applyTheme(store.get("cybergpt-theme") || "cyan");
  refreshComposer();
  loadModes();
  checkHealth();

  // Re-check the model regularly, but not while the tab is hidden.
  setInterval(() => { if (!document.hidden) checkHealth(); }, HEALTH_EVERY_MS);
  window.addEventListener("online", checkHealth);

  dom.input.focus();
})();