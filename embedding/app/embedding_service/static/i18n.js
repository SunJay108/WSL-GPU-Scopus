"use strict";

/*
 * Language switch (English / 简体中文) for the pages of this app.
 *
 * The texts are in i18n/en.js and i18n/zh.js, one entry per key; a key that
 * zh.js leaves out shows in English.  Pages use them through
 *   - HTML: data-i18n="key" sets the text, data-i18n-html="key" the text with
 *     markup (only texts from these files), and
 *     data-i18n-attr="placeholder:key; title:key" attributes;
 *   - JS: I18N.t("key", {n: 3}) fills in {n}.  Numbers get thousands
 *     separators, and "key_one" is used instead of "key" when n is 1.
 * Buttons with data-lang-switch switch the language, which this browser
 * remembers.  Text that a page builds in JS is rebuilt in I18N.onChange().
 *
 * Only page texts are translated: messages from the server, questions and
 * answers stay as they are.  Same file in the search and embedding apps.
 */
window.I18N = (() => {
  const TEXTS = window.I18N_TEXTS || {};
  const LANGS = {
    en: { html: "en", locale: undefined, name: "English", switchTo: "Switch to English" },
    zh: { html: "zh-CN", locale: "zh-CN", name: "中文", switchTo: "切换到中文" },
  };
  const STORE_KEY = "ui.lang";
  const listeners = [];

  function saved() {
    let value = null;
    try { value = localStorage.getItem(STORE_KEY); } catch (_) { /* storage blocked */ }
    return Object.prototype.hasOwnProperty.call(LANGS, value) ? value : "en";
  }

  let lang = saved();

  function pick(key, vars) {
    const one = vars && Number(vars.n) === 1;
    for (const table of [TEXTS[lang], TEXTS.en]) {
      if (!table) continue;
      if (one && `${key}_one` in table) return table[`${key}_one`];
      if (key in table) return table[key];
    }
    return null;
  }

  const format = (v) => (typeof v === "number" ? v.toLocaleString(LANGS[lang].locale) : String(v == null ? "" : v));

  /** Text of `key` in the current language; a missing key shows as itself. */
  function t(key, vars) {
    const text = pick(key, vars);
    if (text == null) return key;
    return vars ? text.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? format(vars[name]) : m)) : text;
  }

  const has = (key) => pick(key) != null;

  function apply(root = document) {
    for (const node of root.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
    for (const node of root.querySelectorAll("[data-i18n-html]")) node.innerHTML = t(node.dataset.i18nHtml);
    for (const node of root.querySelectorAll("[data-i18n-attr]")) {
      for (const pair of node.dataset.i18nAttr.split(";")) {
        const [attr, key] = pair.split(":").map((s) => s.trim());
        if (attr && key) node.setAttribute(attr, t(key));
      }
    }
    const other = LANGS[lang === "en" ? "zh" : "en"];
    for (const btn of root.querySelectorAll("[data-lang-switch]")) {
      btn.textContent = other.name;
      btn.lang = other.html;
      btn.title = other.switchTo;
    }
  }

  function setLang(next) {
    if (!Object.prototype.hasOwnProperty.call(LANGS, next) || next === lang) return;
    lang = next;
    try { localStorage.setItem(STORE_KEY, lang); } catch (_) { /* storage blocked */ }
    document.documentElement.lang = LANGS[lang].html;
    apply();
    for (const fn of listeners) {
      try { fn(lang); } catch (err) { console.error(err); }
    }
  }

  document.addEventListener("click", (e) => {
    if (!e.target.closest("[data-lang-switch]")) return;
    e.preventDefault();
    setLang(lang === "en" ? "zh" : "en");
  });
  // Switched in another tab of this app.
  window.addEventListener("storage", (e) => { if (e.key === STORE_KEY) setLang(saved()); });

  document.documentElement.lang = LANGS[lang].html;
  apply();

  return {
    t,
    has,
    apply,
    setLang,
    onChange: (fn) => { listeners.push(fn); },
    get lang() { return lang; },
    /** Locale for dates: the browser's own in English, zh-CN in Chinese. */
    locale: () => LANGS[lang].locale,
  };
})();
