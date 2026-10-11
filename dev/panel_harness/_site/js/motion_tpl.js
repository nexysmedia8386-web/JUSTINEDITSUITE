/* Justin EDIT Suite — motion graphics templates (pure Canvas 2D, no Node: previewable in any browser).
   Every template is a function of time: draw(ctx, t, p, W, H) paints frame t (seconds) of a clip lasting p.dur on a
   TRANSPARENT W×H canvas — so the same code drives the live preview and the ProRes 4444 render (js/motion.js), and a
   frame never depends on the previous one (render can start anywhere, no drift).
   Sizes use u = shortSide / 1080 × p.scale, so a 16:9 and a 9:16 sequence get graphics of the same physical size.
   p: template fields + dur, scale, primary (box colour), accent, ink (text colour), _img (loaded logo, optional). */
(function () {
  "use strict";
  var FONT = '"Be Vietnam Pro","Segoe UI","Helvetica Neue",Arial,sans-serif';

  // ------------------------------------------------------------------ helpers
  function clamp(x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
  function seg(t, a, b) { return clamp((t - a) / (b - a)); }
  function eOut(x) { x = clamp(x); return 1 - Math.pow(1 - x, 3); }
  function eIn(x) { x = clamp(x); return x * x * x; }
  function eInOut(x) { x = clamp(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function eBack(x) { x = clamp(x); var c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); }
  function font(w, px) { return w + " " + Math.max(1, Math.round(px)) + "px " + FONT; }
  function rgba(hex, a) {
    var h = String(hex || "#000").replace("#", "");
    if (h.length === 3) h = h.split("").map(function (c) { return c + c; }).join("");
    var n = parseInt(h.slice(0, 6), 16) || 0;
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + (a == null ? 1 : a) + ")";
  }
  function rr(ctx, x, y, w, h, r) {                  // rounded rect path
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
  }
  /* largest font size ≤ px (and ≥ 55 % of it) whose text fits maxW */
  function fit(ctx, text, weight, px, maxW) {
    var s = px;
    ctx.font = font(weight, s);
    while (s > px * 0.55 && ctx.measureText(text).width > maxW) { s *= 0.94; ctx.font = font(weight, s); }
    return s;
  }
  function spaced(ctx, text, x, y, track) {          // letter-spaced text (canvas has no letter-spacing everywhere)
    var cx = x;
    for (var i = 0; i < text.length; i++) { ctx.fillText(text[i], cx, y); cx += ctx.measureText(text[i]).width + track; }
    return cx - x - track;
  }
  function spacedW(ctx, text, track) { var w = 0; for (var i = 0; i < text.length; i++) w += ctx.measureText(text[i]).width + track; return Math.max(0, w - track); }
  function U(p, W, H) { return Math.min(W, H) / 1080 * (+p.scale || 1); }
  function margins(W, H, u) { return { x: Math.max(60 * u, 0.05 * W), y: Math.max(60 * u, 0.06 * H) }; }
  function shadow(ctx, u, on) {
    if (on) { ctx.shadowColor = "rgba(0,0,0,.35)"; ctx.shadowBlur = 18 * u; ctx.shadowOffsetY = 4 * u; }
    else { ctx.shadowColor = "transparent"; ctx.shadowBlur = 0; ctx.shadowOffsetY = 0; }
  }

  // ------------------------------------------------------------------ 1. lower third: name + role
  function lowerThird(ctx, t, p, W, H) {
    var u = U(p, W, H), M = margins(W, H, u), dur = p.dur, vert = H > W;
    var name = String(p.name || ""), role = String(p.role || ""), padX = 30 * u, padY = 20 * u, bar = 10 * u;
    var nS = fit(ctx, name, 800, 56 * u, W - 2 * M.x - 2 * padX - bar);
    ctx.font = font(800, nS); var nW = ctx.measureText(name).width;
    var rS = fit(ctx, role, 500, 30 * u, W - 2 * M.x - 2 * padX - bar);
    ctx.font = font(500, rS); var rW = role ? ctx.measureText(role).width : 0;
    var bw = Math.max(nW, rW) + 2 * padX, bh = padY * 2 + nS * 1.12 + (role ? 8 * u + rS * 1.25 : 0);
    var x0 = p.pos === "bc" ? (W - bw - bar) / 2 : M.x;
    var y0 = p.pos === "tl" ? M.y + 20 * u : (vert ? H * 0.72 - bh : H - M.y - bh - 30 * u);
    var tOut = dur - 0.5;
    var barH = bh * (t < tOut + 0.3 ? eOut(seg(t, 0, 0.3)) : 1 - eIn(seg(t, tOut + 0.3, dur)));
    var wIn = eOut(seg(t, 0.12, 0.62)) * (1 - eIn(seg(t, tOut, tOut + 0.35)));
    var tA = clamp(seg(t, 0.25, 0.6)) * (1 - seg(t, tOut, tOut + 0.18));
    // box (revealed from the bar to the right)
    if (wIn > 0.001) {
      ctx.save();
      ctx.beginPath(); ctx.rect(x0 + bar, y0 - 4 * u, bw * wIn, bh + 8 * u); ctx.clip();
      shadow(ctx, u, true);
      ctx.fillStyle = rgba(p.primary, 0.92); rr(ctx, x0 + bar - 8 * u, y0, bw + 8 * u, bh, 8 * u); ctx.fill();
      shadow(ctx, u, false);
      ctx.textBaseline = "alphabetic";
      ctx.globalAlpha = tA;
      ctx.font = font(800, nS); ctx.fillStyle = p.ink || "#fff";
      ctx.fillText(name, x0 + bar + padX, y0 + padY + nS * 0.92 + (1 - eOut(seg(t, 0.25, 0.65))) * nS * 0.6);
      if (role) {
        var rA = clamp(seg(t, 0.38, 0.75)) * (1 - seg(t, tOut, tOut + 0.18));
        ctx.globalAlpha = rA; ctx.font = font(500, rS); ctx.fillStyle = p.accent;
        ctx.fillText(role, x0 + bar + padX, y0 + padY + nS * 1.12 + 8 * u + rS * 0.95 + (1 - eOut(seg(t, 0.38, 0.78))) * rS * 0.8);
      }
      ctx.restore();
    }
    if (barH > 0.5) { ctx.fillStyle = p.accent; ctx.fillRect(x0, y0 + (bh - barH) / 2, bar, barH); }
  }

  // ------------------------------------------------------------------ 2. logo pop (+ caption)
  var shineCv = null;
  function logoPop(ctx, t, p, W, H) {
    var u = U(p, W, H), M = margins(W, H, u), dur = p.dur, corner = p.pos === "tr" || p.pos === "br";
    var S = (corner ? 170 : 300) * u, cap = String(p.caption || "");
    var cx = corner ? W - M.x - S / 2 : W / 2;
    var cy = p.pos === "tr" ? M.y + S / 2 : p.pos === "br" ? H - M.y - S / 2 - (cap ? 60 * u : 0) : H / 2 - (cap ? 40 * u : 0);
    var tOut = dur - 0.4, out = eIn(seg(t, tOut, dur));
    // ring burst
    var rk = seg(t, 0, 0.6);
    if (rk > 0 && rk < 1) {
      ctx.save(); ctx.globalAlpha = (1 - rk) * 0.9; ctx.strokeStyle = p.accent; ctx.lineWidth = Math.max(1, 12 * u * (1 - rk));
      ctx.beginPath(); ctx.arc(cx, cy, S * (0.25 + 0.55 * eOut(rk)), 0, Math.PI * 2); ctx.stroke(); ctx.restore();
    }
    var sc = eBack(seg(t, 0.08, 0.55)) * (1 + 0.015 * Math.sin(Math.max(0, t - 0.55) * Math.PI * 0.8)) * (1 - 0.4 * out);
    if (sc > 0.001) {
      ctx.save(); ctx.globalAlpha = 1 - out; ctx.translate(cx, cy); ctx.scale(sc, sc);
      var img = p._img;
      if (img && (img.naturalWidth || img.width)) {
        var iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height, k = Math.min(S / iw, S / ih), dw = iw * k, dh = ih * k;
        // shine: a light band sweeps across the logo only (drawn on its own canvas so the caption stays untouched)
        var sh = seg(t, 0.65, 1.25);
        if (sh > 0 && sh < 1 && typeof document !== "undefined") {
          shineCv = shineCv || document.createElement("canvas");
          if (shineCv.width !== Math.ceil(dw) || shineCv.height !== Math.ceil(dh)) { shineCv.width = Math.ceil(dw); shineCv.height = Math.ceil(dh); }
          var sc2 = shineCv.getContext("2d");
          sc2.globalCompositeOperation = "source-over"; sc2.clearRect(0, 0, shineCv.width, shineCv.height);
          sc2.drawImage(img, 0, 0, dw, dh);
          sc2.globalCompositeOperation = "source-atop";
          var bx = -dw * 0.6 + dw * 2.2 * eInOut(sh), g = sc2.createLinearGradient(bx - dw * 0.25, 0, bx + dw * 0.25, dh * 0.4);
          g.addColorStop(0, "rgba(255,255,255,0)"); g.addColorStop(0.5, "rgba(255,255,255,.55)"); g.addColorStop(1, "rgba(255,255,255,0)");
          sc2.fillStyle = g; sc2.fillRect(0, 0, dw, dh);
          ctx.drawImage(shineCv, -dw / 2, -dh / 2, dw, dh);
        } else ctx.drawImage(img, -dw / 2, -dh / 2, dw, dh);
      } else {                                          // no logo file: a clean monogram badge
        ctx.fillStyle = p.primary; ctx.beginPath(); ctx.arc(0, 0, S * 0.42, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = p.accent; ctx.lineWidth = 8 * u; ctx.stroke();
        var mono = (cap || "LOGO").split(/\s+/).filter(Boolean).slice(0, 2).map(function (w) { return w[0]; }).join("").toUpperCase();
        ctx.fillStyle = p.ink || "#fff"; ctx.font = font(900, S * 0.32); ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(mono, 0, S * 0.02);
      }
      ctx.restore();
    }
    if (cap) {
      var ca = clamp(seg(t, 0.35, 0.75)) * (1 - out);
      if (ca > 0) {
        ctx.save(); ctx.globalAlpha = ca; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
        var cS = fit(ctx, cap.toUpperCase(), 800, (corner ? 26 : 38) * u, corner ? 320 * u : W - 2 * M.x);
        ctx.font = font(800, cS); ctx.fillStyle = p.ink || "#fff"; shadow(ctx, u, true);
        var tr = cS * 0.18, cw = spacedW(ctx, cap.toUpperCase(), tr);
        spaced(ctx, cap.toUpperCase(), cx - cw / 2, cy + S / 2 + 20 * u + cS + (1 - eOut(seg(t, 0.35, 0.75))) * 24 * u, tr);
        ctx.restore();
      }
    }
  }

  // ------------------------------------------------------------------ 3. number that counts up
  function fmtNum(v, dec) {
    var s = Math.abs(v).toFixed(dec), parts = s.split("."), int = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return (v < 0 ? "−" : "") + int + (dec > 0 ? "," + parts[1] : "");
  }
  function numDecimals(p) { var m = String(p.value).replace(",", ".").match(/\.(\d+)/); return m ? Math.min(3, m[1].length) : 0; }
  function counter(ctx, t, p, W, H) {
    var u = U(p, W, H), M = margins(W, H, u), dur = p.dur, val = parseFloat(String(p.value).replace(/\s/g, "").replace(",", ".")) || 0;
    var dec = numDecimals(p), pre = String(p.prefix || ""), suf = String(p.suffix || ""), lab = String(p.label || "");
    var nS = 150 * u, sS = nS * 0.5, lS = 36 * u;
    ctx.font = font(900, nS);
    var finalTxt = fmtNum(val, dec), nW = ctx.measureText(finalTxt).width;
    ctx.font = font(800, sS); var pW = pre ? ctx.measureText(pre).width + 10 * u : 0, xW = suf ? ctx.measureText(suf).width + 10 * u : 0;
    var total = pW + nW + xW, k = Math.min(1, (W - 2 * M.x) / total);
    if (k < 1) { nS *= k; sS *= k; nW *= k; pW *= k; xW *= k; total *= k; }
    var left = p.pos === "bl" ? M.x : (W - total) / 2, base = p.pos === "bl" ? H - M.y - (lab ? lS * 1.6 : 0) - 40 * u : H / 2 + nS * 0.3;
    var tOut = dur - 0.4, out = eIn(seg(t, tOut, dur)), a = clamp(seg(t, 0, 0.35)) * (1 - out);
    var dy = (1 - eOut(seg(t, 0, 0.35))) * 30 * u - out * 20 * u;
    var pop = t > 1.35 && t < 1.65 ? 1 + 0.06 * Math.sin(seg(t, 1.35, 1.65) * Math.PI) : 1;
    var cur = val * eOut(seg(t, 0.15, 1.35));
    ctx.save(); ctx.globalAlpha = a; shadow(ctx, u, true); ctx.textBaseline = "alphabetic";
    var mid = left + total / 2;
    ctx.translate(mid, base + dy); ctx.scale(pop, pop); ctx.translate(-mid, -(base + dy));
    if (pre) { ctx.font = font(800, sS); ctx.fillStyle = p.accent; ctx.textAlign = "left"; ctx.fillText(pre, left, base + dy); }
    ctx.font = font(900, nS); ctx.fillStyle = p.ink || "#fff"; ctx.textAlign = "right";
    ctx.fillText(fmtNum(cur, dec), left + pW + nW, base + dy);                  // right-aligned: the suffix never jitters
    if (suf) { ctx.font = font(800, sS); ctx.fillStyle = p.accent; ctx.textAlign = "left"; ctx.fillText(suf, left + pW + nW + 10 * u, base + dy); }
    shadow(ctx, u, false);
    var ul = eOut(seg(t, 0.2, 0.7)) * total * 0.55, ux = p.pos === "bl" ? left : mid - ul / 2;
    ctx.fillStyle = p.accent; ctx.fillRect(ux, base + dy + nS * 0.18, ul, 8 * u);
    if (lab) {
      ctx.globalAlpha = clamp(seg(t, 0.45, 0.85)) * (1 - out);
      var lS2 = fit(ctx, lab, 500, lS, W - 2 * M.x);
      ctx.font = font(500, lS2); ctx.fillStyle = p.ink || "#fff"; ctx.textAlign = p.pos === "bl" ? "left" : "center"; shadow(ctx, u, true);
      ctx.fillText(lab, p.pos === "bl" ? left : mid, base + dy + nS * 0.18 + 8 * u + lS2 * 1.5);
    }
    ctx.restore();
  }

  // ------------------------------------------------------------------ 4. callout: point at something in the frame
  function callout(ctx, t, p, W, H) {
    var u = U(p, W, H), dur = p.dur, txt = String(p.text || "");
    var px = W * clamp((+p.x || 50) / 100), py = H * clamp((+p.y || 50) / 100);
    var left = px > W * 0.6, down = py < H * 0.28, sx = left ? -1 : 1, sy = down ? 1 : -1;
    var ex = px + sx * 90 * u, ey = py + sy * 90 * u, hx = ex + sx * 70 * u;
    var tOut = dur - 0.4, out = eIn(seg(t, tOut, dur));
    ctx.save(); ctx.globalAlpha = 1 - out;
    // pulse rings (every 1.2 s) + dot
    for (var k = 0; k < 3; k++) {
      var ph = ((t - 0.2 - k * 1.2) / 1.2);
      if (ph > 0 && ph < 1 && t < tOut) {
        ctx.save(); ctx.globalAlpha = (1 - ph) * 0.8 * (1 - out); ctx.strokeStyle = p.accent; ctx.lineWidth = 4 * u;
        ctx.beginPath(); ctx.arc(px, py, 10 * u + 40 * u * eOut(ph), 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      }
    }
    var dr = 11 * u * eBack(seg(t, 0, 0.25));
    if (dr > 0) {
      ctx.fillStyle = p.accent; ctx.beginPath(); ctx.arc(px, py, dr, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = "#fff"; ctx.lineWidth = 3 * u; ctx.stroke();
    }
    // leader line: diagonal then horizontal, drawn progressively
    var L1 = Math.hypot(ex - px, ey - py), L2 = Math.abs(hx - ex), lp = eOut(seg(t, 0.2, 0.6)) * (L1 + L2);
    if (lp > 0) {
      ctx.strokeStyle = p.accent; ctx.lineWidth = 4 * u; ctx.lineCap = "round"; ctx.beginPath(); ctx.moveTo(px, py);
      if (lp <= L1) ctx.lineTo(px + (ex - px) * lp / L1, py + (ey - py) * lp / L1);
      else { ctx.lineTo(ex, ey); ctx.lineTo(ex + sx * (lp - L1), ey); }
      ctx.stroke();
    }
    // label box: expands, then the text types in
    var bS = 34 * u, pad = 18 * u;
    var maxW = left ? hx - 40 * u : W - hx - 40 * u;
    bS = fit(ctx, txt, 700, bS, Math.max(120 * u, maxW - 2 * pad));
    ctx.font = font(700, bS);
    var tw = ctx.measureText(txt).width, bw = tw + 2 * pad, bh = bS * 1.5 + pad;
    var bx = left ? hx - bw : hx, by = ey - bh / 2, grow = eOut(seg(t, 0.55, 0.85));
    if (grow > 0) {
      ctx.save(); shadow(ctx, u, true); ctx.fillStyle = rgba(p.primary, 0.92);
      var gw = bw * grow; rr(ctx, left ? bx + bw - gw : bx, by, gw, bh, 8 * u); ctx.fill(); shadow(ctx, u, false);
      var n = Math.floor(txt.length * seg(t, 0.62, 0.62 + Math.min(0.7, 0.035 * txt.length)));
      if (n > 0) {
        ctx.beginPath(); ctx.rect(left ? bx + bw - gw : bx, by, gw, bh); ctx.clip();
        ctx.fillStyle = p.ink || "#fff"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
        ctx.fillText(txt.slice(0, n), bx + pad, by + bh / 2 + bS * 0.04);
      }
      ctx.restore();
    }
    ctx.restore();
  }

  // ------------------------------------------------------------------ 5. chapter title card
  function titleCard(ctx, t, p, W, H) {
    var u = U(p, W, H), M = margins(W, H, u), dur = p.dur, kick = String(p.kicker || "").toUpperCase(), title = String(p.title || "");
    var tOut = dur - 0.55;
    if (p.dim !== false && p.dim !== "0" && p.dim !== "off") {
      var da = 0.35 * clamp(seg(t, 0, 0.3)) * (1 - seg(t, tOut + 0.2, dur));
      if (da > 0) { ctx.fillStyle = "rgba(0,0,0," + da + ")"; ctx.fillRect(0, 0, W, H); }
    }
    var tS = fit(ctx, title, 800, 76 * u, W - 2 * M.x), kS = 28 * u;
    var bh = (kick ? kS * 1.9 : 0) + tS * 1.35 + 60 * u, by = H / 2 - bh / 2;
    var inW = eOut(seg(t, 0, 0.45)), outX = eIn(seg(t, tOut, dur));
    var x0 = W * outX, x1 = W * inW;
    if (x1 - x0 > 0.5) {
      ctx.save();
      ctx.beginPath(); ctx.rect(x0, by - 6 * u, x1 - x0, bh + 12 * u); ctx.clip();
      ctx.fillStyle = rgba(p.primary, 0.95); ctx.fillRect(0, by, W, bh);
      ctx.fillStyle = p.accent; ctx.fillRect(0, by, W * eOut(seg(t, 0.15, 0.6)), 6 * u);
      var ta = 1 - seg(t, tOut, tOut + 0.2);
      ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
      if (kick) {
        ctx.globalAlpha = clamp(seg(t, 0.25, 0.6)) * ta; ctx.font = font(700, kS); ctx.fillStyle = p.accent;
        var tr = kS * 0.25, kw = spacedW(ctx, kick, tr); ctx.textAlign = "left";
        spaced(ctx, kick, W / 2 - kw / 2 - (1 - eOut(seg(t, 0.25, 0.6))) * 60 * u, by + 30 * u + kS, tr);
        ctx.textAlign = "center";
      }
      var ty = by + 30 * u + (kick ? kS * 1.9 : 0) + tS, rise = 1 - eOut(seg(t, 0.35, 0.8));
      ctx.globalAlpha = ta;
      ctx.save(); ctx.beginPath(); ctx.rect(0, ty - tS * 1.05, W, tS * 1.35); ctx.clip();       // the title rises out of a mask
      ctx.font = font(800, tS); ctx.fillStyle = p.ink || "#fff"; ctx.fillText(title, W / 2, ty + rise * tS * 1.2);
      ctx.restore();
      ctx.restore();
    }
  }

  // ------------------------------------------------------------------ 6. subscribe button + cursor click + bell
  function bell(ctx, cx, cy, s) {
    ctx.beginPath();
    ctx.moveTo(cx - 0.42 * s, cy + 0.28 * s);
    ctx.quadraticCurveTo(cx - 0.34 * s, cy + 0.16 * s, cx - 0.33 * s, cy - 0.04 * s);
    ctx.bezierCurveTo(cx - 0.33 * s, cy - 0.42 * s, cx + 0.33 * s, cy - 0.42 * s, cx + 0.33 * s, cy - 0.04 * s);
    ctx.quadraticCurveTo(cx + 0.34 * s, cy + 0.16 * s, cx + 0.42 * s, cy + 0.28 * s);
    ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy + 0.38 * s, 0.1 * s, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(cx, cy - 0.4 * s, 0.06 * s, 0, Math.PI * 2); ctx.fill();
  }
  function cursor(ctx, x, y, s) {
    var P = [[0, 0], [0, 1], [0.27, 0.76], [0.45, 1.12], [0.58, 1.06], [0.41, 0.7], [0.75, 0.7]];
    ctx.beginPath(); P.forEach(function (q, i) { ctx[i ? "lineTo" : "moveTo"](x + q[0] * s, y + q[1] * s); }); ctx.closePath();
    ctx.fillStyle = "#fff"; ctx.fill(); ctx.lineJoin = "round"; ctx.lineWidth = Math.max(1, s * 0.06); ctx.strokeStyle = "#111"; ctx.stroke();
  }
  function subscribe(ctx, t, p, W, H) {
    var u = U(p, W, H), M = margins(W, H, u), dur = p.dur, a = String(p.text || "ĐĂNG KÝ").toUpperCase(), b = String(p.done || "ĐÃ ĐĂNG KÝ").toUpperCase() + " ✓";
    var sub = String(p.sub || ""), fS = 40 * u, bh = 96 * u;
    ctx.font = font(800, fS);
    var bw = Math.max(ctx.measureText(a).width, ctx.measureText(b).width) + 88 * u, bellS = 70 * u, gap = 24 * u, gw = bw + gap + bellS;
    var gx = p.pos === "br" ? W - M.x - gw : (W - gw) / 2;
    var gy = p.pos === "c" ? H / 2 - bh / 2 : H - M.y - bh - (sub ? 60 * u : 0) - (H > W ? H * 0.12 : 20 * u);
    var tOut = dur - 0.35, out = eIn(seg(t, tOut, dur)), clicked = t >= 1.25;
    var press = t > 1.15 && t < 1.35 ? 1 - 0.08 * Math.sin(seg(t, 1.15, 1.35) * Math.PI) : 1;
    var sc = eBack(seg(t, 0, 0.4)) * press * (1 - 0.2 * out);
    var bcx = gx + bw / 2, bcy = gy + bh / 2;
    ctx.save(); ctx.globalAlpha = 1 - out;
    if (sc > 0.001) {
      ctx.save(); ctx.translate(bcx, bcy); ctx.scale(sc, sc); shadow(ctx, u, true);
      ctx.fillStyle = clicked ? "#3a3a42" : (p.btn || "#e5322d"); rr(ctx, -bw / 2, -bh / 2, bw, bh, bh / 2); ctx.fill(); shadow(ctx, u, false);
      ctx.fillStyle = "#fff"; ctx.font = font(800, fS); ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(clicked ? b : a, 0, fS * 0.05);
      ctx.restore();
    }
    var bp = eBack(seg(t, 1.35, 1.6));
    if (bp > 0.001) {
      var wig = t > 1.6 ? Math.sin((t - 1.6) * 22) * 0.35 * Math.exp(-(t - 1.6) * 2.2) : 0;
      ctx.save(); ctx.translate(gx + bw + gap + bellS / 2, bcy); ctx.scale(bp * (1 - 0.2 * out), bp * (1 - 0.2 * out));
      ctx.fillStyle = "rgba(255,255,255,.14)"; ctx.beginPath(); ctx.arc(0, 0, bellS / 2, 0, Math.PI * 2); ctx.fill();
      ctx.translate(0, -bellS * 0.18); ctx.rotate(wig); ctx.translate(0, bellS * 0.18);
      ctx.fillStyle = "#fff"; bell(ctx, 0, 0, bellS * 0.62);
      ctx.restore();
    }
    if (sub) {
      ctx.globalAlpha = clamp(seg(t, 0.3, 0.6)) * (1 - out);
      var sS = fit(ctx, sub, 500, 30 * u, W - 2 * M.x);
      ctx.font = font(500, sS); ctx.fillStyle = p.ink || "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; shadow(ctx, u, true);
      ctx.fillText(sub, gx + gw / 2, gy + bh + 18 * u + sS);
      shadow(ctx, u, false);
    }
    // cursor glides in, clicks, leaves
    var cm = eInOut(seg(t, 0.5, 1.1)), ca = clamp(seg(t, 0.45, 0.6)) * (1 - seg(t, 1.6, 1.9));
    if (ca > 0) {
      ctx.globalAlpha = ca * (1 - out);
      var sx0 = bcx + bw * 0.5 + 200 * u, sy0 = bcy + 170 * u, tx = bcx + bw * 0.18, ty = bcy + 6 * u;
      cursor(ctx, sx0 + (tx - sx0) * cm, sy0 + (ty - sy0) * cm, 46 * u * (t > 1.15 && t < 1.3 ? 0.9 : 1));
    }
    ctx.restore();
  }

  // ------------------------------------------------------------------ registry
  var TEMPLATES = {
    lower_third: { name: "Tên + chức danh", icon: "▭", dur: 5, draw: lowerThird, fields: [
      { k: "name", label: "Tên", def: "Nguyễn Văn An" }, { k: "role", label: "Chức danh / dòng phụ", def: "Kiến trúc sư · Home Flow" },
      { k: "pos", label: "Vị trí", type: "select", def: "bl", opts: [["bl", "Dưới trái"], ["bc", "Dưới giữa"], ["tl", "Trên trái"]] }] },
    logo_pop: { name: "Logo bật lên", icon: "◎", dur: 3.5, draw: logoPop, fields: [
      { k: "logo", label: "File logo (PNG nền trong)", type: "file", def: "" }, { k: "caption", label: "Chữ dưới logo", def: "HOME FLOW" },
      { k: "pos", label: "Vị trí", type: "select", def: "c", opts: [["c", "Giữa"], ["tr", "Góc trên phải"], ["br", "Góc dưới phải"]] }] },
    counter: { name: "Số liệu đếm lên", icon: "123", dur: 4, draw: counter, fields: [
      { k: "value", label: "Con số", def: "1250000" }, { k: "prefix", label: "Trước số", def: "" }, { k: "suffix", label: "Sau số", def: " lượt xem" },
      { k: "label", label: "Chú thích", def: "chỉ trong tháng đầu tiên" },
      { k: "pos", label: "Vị trí", type: "select", def: "c", opts: [["c", "Giữa"], ["bl", "Dưới trái"]] }] },
    callout: { name: "Chỉ vào chi tiết", icon: "↗", dur: 4, draw: callout, fields: [
      { k: "text", label: "Chữ", def: "Công tắc cảm ứng" }, { k: "x", label: "Điểm chỉ: ngang (%)", type: "num", def: 42 },
      { k: "y", label: "Điểm chỉ: dọc (%)", type: "num", def: 58 }] },
    title_card: { name: "Tiêu đề chương", icon: "▬", dur: 3, draw: titleCard, fields: [
      { k: "kicker", label: "Dòng nhỏ", def: "Phần 1" }, { k: "title", label: "Tiêu đề", def: "Hệ thống ánh sáng" },
      { k: "dim", label: "Làm tối hình nền", type: "select", def: "on", opts: [["on", "Có"], ["off", "Không"]] }] },
    subscribe: { name: "Nút Đăng ký", icon: "▶", dur: 4, draw: subscribe, fields: [
      { k: "text", label: "Chữ trên nút", def: "Đăng ký" }, { k: "done", label: "Sau khi bấm", def: "Đã đăng ký" },
      { k: "sub", label: "Dòng dưới", def: "để xem tập tiếp theo" }, { k: "btn", label: "Màu nút", type: "color", def: "#e5322d" },
      { k: "pos", label: "Vị trí", type: "select", def: "bc", opts: [["bc", "Dưới giữa"], ["c", "Giữa"], ["br", "Dưới phải"]] }] }
  };
  var BRAND = { primary: "#15161b", accent: "#ffc83d", ink: "#ffffff" };
  function params(id, over) {
    var T = TEMPLATES[id], p = { dur: T.dur, scale: 1 };
    Object.keys(BRAND).forEach(function (k) { p[k] = BRAND[k]; });
    T.fields.forEach(function (f) { p[f.k] = f.def; });
    Object.keys(over || {}).forEach(function (k) { if (over[k] !== undefined && over[k] !== null && over[k] !== "") p[k] = over[k]; });
    p.dur = Math.max(1.2, +p.dur || T.dur); p.scale = Math.max(0.3, +p.scale || 1);
    if (p.dim === "off") p.dim = false;
    return p;
  }
  /* one frame: transparent background, nothing else touched (save/restore around the template) */
  function draw(ctx, id, t, p, W, H) {
    var T = TEMPLATES[id]; if (!T) return;
    ctx.save(); ctx.clearRect(0, 0, W, H);
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic"; ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    try { T.draw(ctx, Math.max(0, Math.min(t, p.dur - 1e-3)), p, W, H); } finally { ctx.restore(); }
  }
  var api = { TEMPLATES: TEMPLATES, BRAND: BRAND, params: params, draw: draw, FONT: FONT, _fmtNum: fmtNum };
  if (typeof window !== "undefined") window.JESMotionTpl = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();
