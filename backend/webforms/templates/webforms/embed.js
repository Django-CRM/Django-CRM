{% comment %}
Rendered as application/javascript, not HTML. Builds the form inside the host
page's own DOM so it inherits the site's styling.

The config is inlined here at render time rather than fetched, because a
<script> tag is not subject to CORS. That keeps the cross-origin surface down
to the single submit route.

There is exactly one interpolation, `config_json`, serialised by
`WebFormEmbedJsView.config_json`. Do not add a second. This output is
JavaScript, so Django's HTML autoescaping is the wrong escaper and would not
protect a string literal, and per-value `escapejs` mangles every UUID into
`-`-separated noise. One JSON blob has one escaping rule to get right.

NEVER put `form.captcha_secret` into that payload. Only `captcha_site_key` is
public, and there is a test that says so.
{% endcomment %}(function () {
  var CONFIG = {{ config_json|safe }};

  // Prefer an explicit container so the site owner controls placement. Fall
  // back to wherever the script tag itself sits, which is what happens when
  // someone pastes the snippet and nothing else.
  var script = document.currentScript;
  var mount = document.getElementById(CONFIG.mountId);
  if (!mount) {
    mount = document.createElement("div");
    if (script && script.parentNode) script.parentNode.insertBefore(mount, script);
    else document.body.appendChild(mount);
  }

  var form = document.createElement("form");
  form.setAttribute("novalidate", "novalidate");

  var inputs = {};
  var errorNodes = {};

  CONFIG.fields.forEach(function (field) {
    var row = document.createElement("div");
    row.style.marginBottom = "16px";

    var label = document.createElement("label");
    label.textContent = field.label + (field.required ? " *" : "");
    label.style.display = "block";
    label.style.marginBottom = "4px";

    var input = document.createElement(field.multiline ? "textarea" : "input");
    if (!field.multiline) input.type = field.email ? "email" : "text";
    input.name = field.name;
    if (field.placeholder) input.placeholder = field.placeholder;
    input.style.width = "100%";
    // Without this, `width: 100%` plus padding and border overflows the
    // container and the host page scrolls sideways on a phone. The host page's
    // own reset cannot be relied on, since we do not control it, and the
    // iframe embed only avoids this because it ships its own `* { box-sizing }`.
    input.style.boxSizing = "border-box";
    input.style.maxWidth = "100%";
    input.style.minHeight = field.multiline ? "96px" : "44px";
    input.style.padding = "10px 12px";

    var error = document.createElement("div");
    error.style.color = "#b91c1c";
    error.style.fontSize = "14px";

    label.appendChild(input);
    row.appendChild(label);
    row.appendChild(error);
    form.appendChild(row);

    inputs[field.name] = input;
    errorNodes[field.name] = error;
  });

  // Honeypot. Positioned off-screen rather than display:none, so it still
  // looks fillable to a bot that skips hidden inputs.
  var trap = document.createElement("input");
  trap.type = "text";
  trap.name = CONFIG.honeypot;
  trap.tabIndex = -1;
  trap.setAttribute("autocomplete", "off");
  trap.setAttribute("aria-hidden", "true");
  trap.style.position = "absolute";
  trap.style.left = "-9999px";
  form.appendChild(trap);

  if (CONFIG.captchaSiteKey) {
    var widget = document.createElement("div");
    widget.className = "cf-turnstile";
    widget.setAttribute("data-sitekey", CONFIG.captchaSiteKey);
    form.appendChild(widget);

    var api = document.createElement("script");
    api.src = "https://challenges.cloudflare.com/turnstile/v0/api.js";
    api.async = true;
    api.defer = true;
    document.head.appendChild(api);
  }

  // Refusals that name no input land here: the captcha, the throttle, an
  // origin that is not on the list. Each answers `{"detail": "..."}`, and none
  // of those keys match a field, so without this they rendered nowhere at all
  // and the visitor got a re-enabled button and no explanation.
  var general = document.createElement("div");
  general.setAttribute("role", "alert");
  general.hidden = true;
  general.style.color = "#b91c1c";
  general.style.fontSize = "14px";
  general.style.margin = "0 0 12px";
  form.appendChild(general);

  var button = document.createElement("button");
  button.type = "submit";
  button.textContent = CONFIG.buttonLabel;
  button.style.boxSizing = "border-box";
  button.style.maxWidth = "100%";
  button.style.minHeight = "44px";
  button.style.padding = "12px 16px";
  form.appendChild(button);

  var done = document.createElement("div");
  done.hidden = true;

  mount.appendChild(form);
  mount.appendChild(done);

  function showGeneral(text) {
    general.textContent = text;
    general.hidden = false;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    button.disabled = true;
    Object.keys(errorNodes).forEach(function (key) {
      errorNodes[key].textContent = "";
    });
    general.textContent = "";
    general.hidden = true;

    var payload = {};
    new FormData(form).forEach(function (value, key) { payload[key] = value; });

    fetch(CONFIG.submitUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then(function (res) {
        return res.json().then(function (body) { return [res, body]; });
      })
      .then(function (pair) {
        var res = pair[0], body = pair[1];
        if (res.status === 200) {
          if (body.mode === "redirect") {
            // No frame here, so a plain navigation is correct.
            window.location = body.redirect_url;
            return;
          }
          form.hidden = true;
          // textContent, never innerHTML: `body.message` is org-authored copy
          // rendering on someone else's domain.
          done.textContent = body.message || "Thanks.";
          done.hidden = false;
          return;
        }
        if (!body || typeof body !== "object") {
          showGeneral("Sorry, that could not be sent. Please try again.");
          button.disabled = false;
          return;
        }
        var leftover = [];
        Object.keys(body).forEach(function (key) {
          var text = [].concat(body[key]).join(" ");
          if (errorNodes[key]) errorNodes[key].textContent = text;
          else leftover.push(text);
        });
        if (leftover.length) showGeneral(leftover.join(" "));
        button.disabled = false;
      })
      .catch(function () {
        // Offline, DNS, a CORS refusal, or a response that is not JSON.
        showGeneral("Could not reach the server. Please check your connection and try again.");
        button.disabled = false;
      });
  });
})();
