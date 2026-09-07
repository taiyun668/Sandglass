(function () {
  "use strict";
  if (window.__sandglassNativeShell) return;
  window.__sandglassNativeShell = true;

  var sequence = 0;
  var pending = Object.create(null);
  var apiPending = Object.create(null);
  var API_DEADLINE_MS = 20000;
  var send = function (message) {
    window.chrome.webview.postMessage(String(message));
  };
  var request = function (action) {
    return new Promise(function (resolve) {
      var id = String(++sequence);
      var timer = setTimeout(function () {
        if (!pending[id]) return;
        delete pending[id];
        resolve(false);
      }, API_DEADLINE_MS);
      pending[id] = function (value) {
        clearTimeout(timer);
        resolve(value);
      };
      send("request\t" + id + "\t" + action);
    });
  };
  window.__sandglassResolve = function (id, value) {
    var resolve = pending[String(id)];
    if (!resolve) return;
    delete pending[String(id)];
    resolve(value);
  };
  window.__sandglassApiResolve = function (id, status, body) {
    var entry = apiPending[String(id)];
    if (!entry) return;
    delete apiPending[String(id)];
    if (entry.abort) entry.abort();
    entry.resolve(new Response(String(body), {
      status: Number(status),
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }
    }));
  };
  var originalFetch = window.fetch.bind(window);
  window.fetch = function (input, options) {
    var raw = typeof input === "string" ? input : input.url;
    var url = new URL(raw, window.location.href);
    if (url.origin !== window.location.origin || url.pathname.indexOf("/api/") !== 0) {
      return originalFetch(input, options);
    }
    return new Promise(function (resolve, reject) {
      var id = String(++sequence);
      var method = String((options && options.method) || (input && input.method) || "GET").toUpperCase();
      var body = options && options.body != null ? String(options.body) : "";
      var signal = options && options.signal;
      if (signal && signal.aborted) {
        reject(new DOMException("Aborted", "AbortError"));
        return;
      }
      var timer = setTimeout(function () {
        var entry = apiPending[id];
        if (!entry) return;
        delete apiPending[id];
        if (entry.abort) entry.abort();
        resolve(new Response("{\"error\":\"undelivered\"}", {
          status: 599,
          headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" }
        }));
      }, API_DEADLINE_MS);
      var onAbort = function () {
        clearTimeout(timer);
        delete apiPending[id];
        reject(new DOMException("Aborted", "AbortError"));
      };
      if (signal) signal.addEventListener("abort", onAbort, { once: true });
      apiPending[id] = {
        resolve: function (response) {
          clearTimeout(timer);
          resolve(response);
        },
        abort: signal ? function () { signal.removeEventListener("abort", onAbort); } : null
      };
      send("api\t" + id + "\t" + method + "\t" +
        encodeURIComponent(url.pathname + url.search) + "\t" + encodeURIComponent(body));
    });
  };
  window.pywebview = {
    api: {
      autostart_status: function () { return request("autostart_status"); },
      enable_autostart: function () { return request("enable_autostart"); },
      set_locale: function (locale) { send("locale\t" + locale); },
      minimize_panel: function () { send("minimize"); },
      close_panel: function () { send("close"); },
      fit_panel: function (height) { send("fit\t" + Math.round(height)); },
      begin_drag: function () { send("drag"); }
    }
  };

  var install = function () {
    if (document.getElementById("shell-bar")) return;
    var css = document.createElement("style");
    css.textContent =
      "html,body{min-height:0!important;background:transparent!important;" +
      "overflow-y:auto!important;scrollbar-width:none!important;-ms-overflow-style:none!important}" +
      "html::-webkit-scrollbar,body::-webkit-scrollbar{width:0!important;height:0!important;display:none!important}" +
      "html.overview-mode,html.overview-mode body{overflow-y:hidden!important}" +
      ".popover{width:100%!important;margin:0!important;padding-top:0!important;" +
      "border-radius:0!important;box-shadow:none!important}" +
      "html.overview-mode .popover{max-height:calc(100vh - 32px)!important}" +
      ".skip{display:none!important}" +
      ".app-menu-shell{position:fixed!important;top:0!important;left:6px!important;" +
      "height:32px!important;display:flex!important;align-items:center!important;z-index:10000!important}" +
      ".app-menu-panel{top:32px!important}" +
      "#shell-bar{position:fixed;top:0;left:0;right:0;height:32px;display:flex;" +
      "align-items:center;justify-content:flex-end;z-index:9999;padding-right:6px;background:var(--card)}" +
      ".shell-window-button{width:26px;height:26px;border:0;background:transparent;" +
      "color:currentColor;opacity:.45;font-size:17px;line-height:1;cursor:pointer;border-radius:7px}" +
      ".shell-window-button:hover{opacity:1;background:rgba(127,127,127,.18)}" +
      "#shell-minimize{font-size:19px;padding-bottom:7px}body{padding-top:32px}";
    document.head.appendChild(css);

    var bar = document.createElement("div");
    bar.id = "shell-bar";
    bar.innerHTML =
      '<button id="shell-minimize" class="shell-window-button" aria-label="缩回悬浮球" title="缩回悬浮球">−</button>' +
      '<button id="shell-close" class="shell-window-button" aria-label="缩回托盘" title="缩回托盘">×</button>';
    document.body.appendChild(bar);

    var labels = {
      "zh-CN": ["缩回悬浮球", "缩回托盘"], "zh-TW": ["縮回懸浮球", "縮回系統匣"],
      "en-US": ["Minimize to floating orb", "Close to system tray"],
      "es-ES": ["Minimizar a la esfera", "Cerrar a la bandeja"],
      "fr-FR": ["Réduire vers l’orbe", "Fermer dans la zone de notification"],
      "de-DE": ["Zur Kugel minimieren", "In die Taskleiste schließen"],
      "pt-BR": ["Minimizar para a esfera", "Fechar para a bandeja"],
      "ru-RU": ["Свернуть в сферу", "Закрыть в область уведомлений"],
      "ja-JP": ["フローティングオーブに戻す", "システムトレイに格納"],
      "ko-KR": ["플로팅 오브로 최소화", "시스템 트레이로 닫기"]
    };
    var syncLocale = function () {
      var locale = document.documentElement.lang || "zh-CN";
      var copy = labels[locale] || labels["en-US"];
      var minimize = document.getElementById("shell-minimize");
      var close = document.getElementById("shell-close");
      minimize.setAttribute("aria-label", copy[0]); minimize.title = copy[0];
      close.setAttribute("aria-label", copy[1]); close.title = copy[1];
      send("locale\t" + locale);
    };
    new MutationObserver(syncLocale).observe(document.documentElement, {
      attributes: true, attributeFilter: ["lang"]
    });
    syncLocale();

    document.getElementById("shell-minimize").onclick = function () { send("minimize"); };
    document.getElementById("shell-close").onclick = function () { send("close"); };
    var hot = 'button,a,input,select,textarea,label,[role="button"],[onclick],[tabindex]';
    document.addEventListener("mousedown", function (event) {
      if (event.button !== 0) return;
      if (event.target.closest && event.target.closest(hot)) return;
      event.preventDefault();
      send("drag");
    });

    var card = document.querySelector(".popover");
    if (card) {
      var last = 0;
      var tell = function () {
        var cardHeight = card.getBoundingClientRect().height;
        var height = Math.ceil(cardHeight) + 32;
        if (document.documentElement.classList.contains("overview-mode")) {
          var accounts = card.querySelector(".overview-cards");
          if (accounts) {
            var rows = accounts.querySelectorAll(".ov");
            var wanted = accounts.scrollHeight;
            if (rows.length > 5) {
              var box = accounts.getBoundingClientRect();
              var fifth = rows[4].getBoundingClientRect();
              wanted = Math.ceil(fifth.bottom - box.top + accounts.scrollTop);
            }
            height = Math.ceil(Math.max(0, cardHeight - accounts.clientHeight) + wanted) + 32;
          }
          height = Math.max(height, 560);
        }
        if (Math.abs(height - last) < 2) return;
        last = height;
        send("fit\t" + height);
      };
      new ResizeObserver(tell).observe(card);
      new MutationObserver(function () { setTimeout(tell, 0); }).observe(card, {
        childList: true, subtree: true
      });
      setTimeout(tell, 60);
    }
    window.dispatchEvent(new Event("pywebviewready"));
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", install, { once: true });
  } else {
    install();
  }
}());
