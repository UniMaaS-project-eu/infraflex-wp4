#!/bin/sh
# Runs inside the 'aas-ui' (eclipsebasyx/aas-gui) container to patch
# /usr/src/app/dist (and /usr/share/nginx/html if present):
# 1. Rewrites all localhost / IP references on ports 8081-8085 in localStorage
#    (including JSON strings where the port is followed by '"'), sessionStorage,
#    outgoing Request/fetch/XHR calls, AND incoming Response.json()/text() payloads.
# 2. Ensures all descriptor endpoint hrefs match the active infrastructure origin
#    so BaSyx's "Untrusted endpoint blocked" check always passes.

set -e

TARGET_HOST="${1:-localhost}"

cat > /tmp/basyx-host-fix.js <<'EOF'
/* basyx-host-fix-v5 */
(function () {
  if (window.__BASYX_HOST_FIX_V5__) return;
  window.__BASYX_HOST_FIX_V5__ = true;

  var host = window.location.hostname;
  if (!host) return;

  // Match any localhost, 127.0.0.1, IPv4, or hostname on ports 8081-8085
  // Using \b after the port so it matches inside JSON strings ("http://localhost:8081") as well as URLs
  var re = /http:\/\/(localhost|127\.0\.0\.1|[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|[a-zA-Z0-9.-]+):(808[1-5])\b/g;

  function rw(s) {
    return typeof s === "string" ? s.replace(re, "http://" + host + ":$2") : s;
  }

  function syncStorage(store) {
    try {
      for (var i = 0; i < store.length; i++) {
        var k = store.key(i);
        var v = store.getItem(k);
        if (v && v.indexOf(":808") !== -1) {
          var nv = rw(v);
          if (nv !== v) store.setItem(k, nv);
        }
      }
    } catch (e) {}
  }

  // 1. Rewrite existing localStorage & sessionStorage immediately
  syncStorage(localStorage);
  syncStorage(sessionStorage);

  // Also intercept Storage.prototype.getItem / setItem so any JSON infrastructure config
  // read or written by Vue always has normalized origins
  try {
    var origGetItem = Storage.prototype.getItem;
    Storage.prototype.getItem = function (key) {
      var val = origGetItem.call(this, key);
      if (typeof val === "string" && val.indexOf(":808") !== -1) {
        return rw(val);
      }
      return val;
    };
    var origSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, val) {
      if (typeof val === "string" && val.indexOf(":808") !== -1) {
        val = rw(val);
      }
      return origSetItem.call(this, key, val);
    };
  } catch (e) {}

  // 2. Intercept Response.prototype.json and Response.prototype.text so descriptor
  //    endpoints ("href": "http://<EXTERNAL_HOST>:8081/...") returned by the registries
  //    always match the browser's active hostname origin.
  if (typeof Response !== "undefined" && Response.prototype) {
    var origText = Response.prototype.text;
    if (typeof origText === "function") {
      Response.prototype.text = function () {
        return origText.call(this).then(function (txt) {
          return typeof txt === "string" && txt.indexOf(":808") !== -1 ? rw(txt) : txt;
        });
      };
    }
    var origJson = Response.prototype.json;
    if (typeof origJson === "function" && typeof origText === "function") {
      Response.prototype.json = function () {
        return origText.call(this).then(function (txt) {
          if (typeof txt === "string" && txt.indexOf(":808") !== -1) {
            txt = rw(txt);
          }
          return JSON.parse(txt);
        });
      };
    }
  }

  // 3. Intercept window.Request
  var OrigReq = window.Request;
  if (typeof OrigReq === "function") {
    function PatchedReq(input, init) {
      if (typeof input === "string") {
        input = rw(input);
      } else if (typeof URL !== "undefined" && input instanceof URL) {
        input = new URL(rw(input.href));
      } else if (input && typeof input.url === "string") {
        var nu = rw(input.url);
        if (nu !== input.url) input = new OrigReq(nu, input);
      }
      return new OrigReq(input, init);
    }
    PatchedReq.prototype = OrigReq.prototype;
    window.Request = PatchedReq;
  }

  // 4. Intercept window.fetch
  if (typeof window.fetch === "function") {
    var origFetch = window.fetch;
    window.fetch = function (input, init) {
      if (typeof input === "string") {
        input = rw(input);
      } else if (typeof URL !== "undefined" && input instanceof URL) {
        input = new URL(rw(input.href));
      } else if (input && typeof input.url === "string") {
        var nu = rw(input.url);
        if (nu !== input.url && typeof OrigReq === "function") {
          input = new OrigReq(nu, input);
        }
      }
      return origFetch.call(this, input, init);
    };
  }

  // 5. Intercept XMLHttpRequest.prototype.open
  if (typeof XMLHttpRequest !== "undefined" && XMLHttpRequest.prototype.open) {
    var origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url) {
      if (typeof url === "string") {
        arguments[1] = rw(url);
      }
      return origOpen.apply(this, arguments);
    };
  }

  console.log("[basyx-host-fix-v5] Active for host:", host);
})();
EOF

PATCHED_ANY=0
for WEB_ROOT in /usr/src/app/dist /usr/src/app/dist-bff /usr/share/nginx/html /app/dist; do
  if [ ! -d "$WEB_ROOT" ]; then
    continue
  fi

  echo "[patch_aas_ui] Patching directory: ${WEB_ROOT}"
  PATCHED_ANY=1

  find "$WEB_ROOT" -type f \( -name "*.gz" -o -name "*.br" \) -delete 2>/dev/null || true

  # 1. Copy /basyx-host-fix.js into the web root
  cp /tmp/basyx-host-fix.js "${WEB_ROOT}/basyx-host-fix.js"
  chmod 644 "${WEB_ROOT}/basyx-host-fix.js"

  # 2. Prepend basyx-host-fix.js v5 to assets/useAuth-*.js and assets/index-*.js
  if [ -d "${WEB_ROOT}/assets" ]; then
    for jsf in "${WEB_ROOT}"/assets/useAuth-*.js "${WEB_ROOT}"/assets/index-*.js; do
      if [ -f "$jsf" ] && ! grep -q "basyx-host-fix-v5" "$jsf" 2>/dev/null; then
        cat /tmp/basyx-host-fix.js "$jsf" > "${jsf}.tmp" && mv "${jsf}.tmp" "$jsf"
        chmod 644 "$jsf"
        echo "[patch_aas_ui] Prepended host interceptor v5 to ${jsf}"
      fi
    done
  fi

  # 3. Reference /basyx-host-fix.js?v=5 in index.html
  if [ -f "${WEB_ROOT}/index.html" ]; then
    sed -i 's|<script id="basyx-host-fix">.*</script>||g' "${WEB_ROOT}/index.html" || true
    sed -i 's|/basyx-host-fix.js?v=[0-9]*|/basyx-host-fix.js?v=5|g' "${WEB_ROOT}/index.html" || true
    if ! grep -q "basyx-host-fix.js" "${WEB_ROOT}/index.html"; then
      sed -i 's|<head>|<head><script src="/basyx-host-fix.js?v=5"></script>|' "${WEB_ROOT}/index.html"
    fi
    chmod 644 "${WEB_ROOT}/index.html"
    echo "[patch_aas_ui] Updated ${WEB_ROOT}/index.html"
  fi
done

if [ "$PATCHED_ANY" -eq 0 ]; then
  echo "[patch_aas_ui] ERROR: No web root directories found inside aas-ui."
  exit 1
fi

echo "[patch_aas_ui] AAS UI patch v5 complete."
