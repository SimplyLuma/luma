const fs = require('node:fs');
const path = require('node:path');

function displayTrackingEm(app, toolsDir = path.resolve(__dirname, '..'), env = process.env) {
  if (app !== 'calc') return null;
  const roots = [env.LUMAUI_CONFORM_KIT, env.LUMAUI_CONFORM_REPO,
    path.resolve(toolsDir, '../..')].filter(Boolean);
  for (const root of roots) {
    const file = path.join(root, 'config/shared/design-tokens.json');
    if (fs.existsSync(file)) {
      const value = JSON.parse(fs.readFileSync(file, 'utf8')).lumaui?.type_scale?.display?.tracking_em;
      if (Number.isFinite(value)) return value;
      throw new Error(`Calculator display tracking token missing in ${file}`);
    }
  }
  throw new Error(`Calculator display tracking tokens absent from ${roots.join(', ')}`);
}

// Chromium includes the display role's trailing letter spacing in each text
// span's advance. Pango puts that spacing only between glyphs. Normalize the
// captured advance boxes; the rendered glyphs and comparison tolerances stay
// untouched.
function isCalculatorDisplayWidget(app, widget, widgets) {
  if (app !== 'calc' || widget.offscreen || widget.ellipsized ||
      !widget.classes?.includes('lumaui-t-display') || !widget.text) return false;
  return widgets.some(w => w.id === widget.parent && w.classes?.includes('calc-expression'));
}

function calculatorDisplayTextBox(app, widget, widgets, box, trackingEm) {
  if (!isCalculatorDisplayWidget(app, widget, widgets) ||
      !Number.isFinite(widget.font?.size) || !Number.isFinite(trackingEm)) return box;
  const parent = widgets.find(w => w.id === widget.parent);
  const peers = widgets.filter(w => w.parent === parent.id && !w.offscreen &&
    w.classes?.includes('lumaui-t-display') && w.text);
  const toRight = peers.filter(w => w.box[0] >= widget.box[0] - 0.1).length;
  if (!toRight || box.w + trackingEm * widget.font.size <= 0) return box;
  const trailing = trackingEm * widget.font.size;
  return { ...box, x: box.x - toRight * trailing, w: box.w + trailing };
}

// The v70 text box uses the CSS line box top; GTK's text operation uses
// baseline minus Pango ascent. Their tops differ by about five pixels even
// when the painted glyph baselines agree. Only this matched display role has
// a reliable baseline on both sides; a real vertical move still changes it.
function calculatorDisplayPosition(app, spec, gtk, widgets) {
  if (!isCalculatorDisplayWidget(app, gtk.w, widgets) ||
      !Number.isFinite(spec.baseline) || !Number.isFinite(gtk.baseline) ||
      spec.font?.size !== gtk.font?.size || spec.font?.weight !== gtk.font?.weight)
    return { specY: spec.box.y, gtkY: gtk.box.y, baseline: false };
  return { specY: spec.baseline, gtkY: gtk.baseline, baseline: true };
}

module.exports = { calculatorDisplayTextBox, displayTrackingEm, calculatorDisplayPosition };
