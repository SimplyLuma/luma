// A uniform gradient has no visual ramp. Only classify simple captured CSS
// gradients; anything layered with another image stays a true gradient.
function splitLayers(value) {
  const parts = [];
  let depth = 0, start = 0;
  for (let i = 0; i < value.length; i++) {
    if (value[i] === '(') depth++;
    else if (value[i] === ')') depth--;
    else if (value[i] === ',' && depth === 0) { parts.push(value.slice(start, i).trim()); start = i + 1; }
    if (depth < 0) return [];
  }
  if (depth !== 0) return [];
  parts.push(value.slice(start).trim());
  return parts;
}

function cssColor(value) {
  const match = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/.exec(value);
  return match && [Number(match[1]), Number(match[2]), Number(match[3]), match[4] === undefined ? 1 : Number(match[4])];
}

function same(a, b) {
  return a && b && a.length === b.length && a.every((value, i) => Math.abs(value - b[i]) < 0.0001);
}

function isFlatCssGradient(image) {
  const layers = splitLayers(image || '').filter(layer => layer !== 'none');
  if (layers.length !== 1) return false;
  const gradient = /^(?:linear|radial)-gradient\((.*)\)$/.exec(layers[0]);
  if (!gradient) return false;
  const stops = splitLayers(gradient[1]);
  return stops.length >= 2 && stops.every(stop => same(cssColor(stop), cssColor(stops[0])));
}

function isFlatGtkGradient(gradient) {
  const stops = gradient && gradient.stops;
  return Array.isArray(stops) && stops.length >= 2 && stops.every(stop => same(stop[1], stops[0][1]));
}

function equivalentFlatGradient(specImage, gtkGradient, specSample, gtkSample, distance, tolerance) {
  if (!specSample || !gtkSample || distance(specSample, gtkSample) > tolerance) return false;
  return gtkGradient ? isFlatGtkGradient(gtkGradient) : isFlatCssGradient(specImage);
}

module.exports = { isFlatCssGradient, isFlatGtkGradient, equivalentFlatGradient };
