"""Panel harness — run the real panel in a normal browser, without Premiere.

Copies the INSTALLED panel (%APPDATA%/Adobe/CEP/extensions/com.justin.editsuite) into _site/ next to shim.js (a stand-in
for CEP + Node with an in-memory disk and a fake sequence) and writes _site/panel.html with cache-busting ?v= on every
script, so a reload always runs the current code. Start-up errors are collected in window.__errs.

    python dev/panel_harness/build.py          → then serve _site (launch config "panel-harness", port 8766)
    open http://localhost:8766/panel.html      → JSON.stringify(window.__errs) must be "[]"

Why it exists: v2.4.3 shipped with a start-up crash (bindInputs read the removed brief_* inputs) that syntax checks and
the static id audit could not see — only running the panel's init shows it. Run this before every package build.
"""
import os
import re
import shutil
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "_site")
EXT = os.path.join(os.environ.get("APPDATA", ""), "Adobe", "CEP", "extensions", "com.justin.editsuite")
ENGINE = os.path.normpath(os.path.join(HERE, "..", "..", "engine"))


def main():
    if not os.path.isfile(os.path.join(EXT, "index.html")):
        raise SystemExit("không thấy panel đã cài: " + EXT)
    os.makedirs(SITE, exist_ok=True)
    for d in ("js", "css", "jsx", "knowledge"):
        if os.path.isdir(os.path.join(EXT, d)):
            shutil.copytree(os.path.join(EXT, d), os.path.join(SITE, d), dirs_exist_ok=True)
    shutil.copy2(os.path.join(HERE, "shim.js"), os.path.join(SITE, "shim.js"))
    shutil.copy2(os.path.join(ENGINE, "nxcore", "stylebook_params.json"), os.path.join(SITE, "stylebook_params.json"))
    v = str(int(time.time()))
    h = open(os.path.join(EXT, "index.html"), encoding="utf-8").read()
    h = re.sub(r'((?:src|href)="(?:js|css)/[^"?]+)"', lambda m: m.group(1) + "?v=" + v + '"', h)
    h = h.replace("<head>", '<head><script src="shim.js?v=' + v + '"></script>', 1)
    open(os.path.join(SITE, "panel.html"), "w", encoding="utf-8").write(h)
    print("panel harness ready: " + os.path.join(SITE, "panel.html") + "  (v=" + v + ")")


if __name__ == "__main__":
    main()
