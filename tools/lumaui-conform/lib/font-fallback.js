// Compare a probed Chromium fallback face with Pango's rendered face. A CSS
// font-family value alone is only the requested stack, not the actual glyph.
const familyNames = value => String(value || '').toLowerCase().split(',').map(x => x.trim());
const sameFamily = (a, b) => familyNames(a).some(x => familyNames(b).includes(x));

function fontFallback(requested, requestedWeight, gtk, gtkWeight, platformFonts) {
  const faces = Array.isArray(platformFonts) ? platformFonts.filter(f => f.glyphCount > 0) : [];
  const matched = faces.find(f => sameFamily(f.familyName, gtk));
  const familyMatch = faces.length ? !!matched : sameFamily(requested, gtk);
  // A probed fallback that supplies only a Regular face cannot render CSS 500.
  // Require that exact face on both sides; ordinary typography keeps exact weight.
  const fallbackRegularWeight = !!matched && !matched.isCustomFont &&
    (/(?:-|\s)Regular$/i.test(matched.postScriptName || '') || matched.postScriptName === 'LiberationSans') &&
    requestedWeight === 500 && gtkWeight === 400;
  return { familyMatch, fallbackRegularWeight,
    expectedFamily: faces.length ? faces.map(f => f.familyName).join(' + ') : requested };
}

module.exports = { fontFallback };
