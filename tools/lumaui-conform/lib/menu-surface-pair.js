// A GTK popover's root box includes its shadow and arrow. The direct contents
// child is the painted menu surface that corresponds to a DOM role=menu box.
function menuSurfacePairs(specComps, widgets, pairedSpec = new Set(), pairedGtk = new Set()) {
  const byId = new Map(widgets.map(w => [w.id, w]));
  const contents = widgets.filter(w => w.css === 'contents' && !w.offscreen &&
    byId.get(w.parent)?.css === 'popover' && byId.get(w.parent)?.role === 'menu');
  const used = new Set(pairedGtk);
  const pairs = [];
  for (const spec of specComps.filter(s => s.role === 'menu' && !pairedSpec.has(s.el))) {
    const candidates = contents.filter(w => !used.has(w));
    if (!candidates.length) break;
    const distance = w => {
      const b = w.visible || w.box, s = spec.box;
      return Math.abs(b[0] - s.x) + Math.abs(b[1] - s.y) +
        Math.abs(b[2] - s.w) + Math.abs(b[3] - s.h);
    };
    const gtk = candidates.reduce((best, w) => distance(w) < distance(best) ? w : best);
    pairs.push([spec, gtk]);
    used.add(gtk);
  }
  return pairs;
}

module.exports = { menuSurfacePairs };
