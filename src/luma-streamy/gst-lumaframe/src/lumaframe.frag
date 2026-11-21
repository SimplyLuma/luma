// SPDX-License-Identifier: MPL-2.0
// lumaframe: one composited layer in one pass. Mirrors src/geometry.rs
// (Layout::shade); keep the two in step. GLSL ES 1.00 / GLSL 1.20.
// Canvas pixels, origin top-left; v_texcoord spans the whole canvas.

varying vec2 v_texcoord;
uniform sampler2D tex;

uniform vec2 u_canvas;        // canvas size: box plus the margins
uniform float u_aa;           // canvas pixels per output pixel
uniform vec4 u_rect;          // content rect: centre.xy, half size.zw
uniform float u_radius;
uniform float u_stroke;       // stroke width
uniform float u_stroke_in;    // stroke inner edge, inside the box edge
uniform float u_stroke_out;   // stroke outer edge, outside the box edge
uniform vec3 u_outer;         // outer edge of box and stroke: half size.xy, radius.z
uniform vec4 u_stroke_color;  // straight alpha
uniform vec4 u_crop;          // source origin.xy, extent.zw (0..1)
uniform vec4 u_shadow_color;  // straight alpha
uniform vec2 u_shadow_offset;
uniform float u_sigma;
uniform float u_sigma_min;    // below this, the shadow is a plain AA edge
uniform float u_opacity;
uniform vec2 u_footprint;     // one output pixel in texture coordinates
uniform float u_taps;         // samples per axis over the footprint, 1..4

#define SHADOW_SLICES 6
#define SHADOW_EXTENT 4.0

float rrect_sdf(vec2 p, vec2 c, vec2 h, float r) {
  vec2 q = abs(p - c) - h + vec2(r);
  return length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - r;
}

float coverage(float d) {
  return clamp(0.5 - d / u_aa, 0.0, 1.0);
}

// erf, Abramowitz and Stegun 7.1.27, two at a time.
vec2 erf2(vec2 x) {
  vec2 a = abs(x);
  vec2 t = 1.0 + a * (0.278393 + a * (0.230389 + a * (0.000972 + a * 0.078108)));
  vec2 t2 = t * t;
  return sign(x) * (1.0 - 1.0 / (t2 * t2));
}

// Gaussian-blurred rounded rect at p, relative to the rect centre: exact
// across x (an erf difference), sliced down y with each slice weighted by its
// exact Gaussian mass.
float rrect_shadow(vec2 p, vec2 h, float r, float sigma) {
  float k = 0.70710678 / sigma;
  float lo = max(p.y - h.y, -SHADOW_EXTENT * sigma);
  float hi = min(p.y + h.y, SHADOW_EXTENT * sigma);
  if (hi <= lo)
    return 0.0;
  float dt = (hi - lo) / float(SHADOW_SLICES);
  float sum = 0.0;
  float t0 = lo;
  for (int i = 0; i < SHADOW_SLICES; i++) {
    float t1 = t0 + dt;
    vec2 e = erf2(vec2(t1, t0) * k);
    float mass = 0.5 * (e.x - e.y);
    float y = p.y - 0.5 * (t0 + t1);
    float dy = max(abs(y) - (h.y - r), 0.0);
    float w = h.x - r + sqrt(max(r * r - dy * dy, 0.0));
    vec2 x = erf2(vec2(p.x + w, p.x - w) * k);
    sum += mass * 0.5 * (x.x - x.y);
    t0 = t1;
  }
  return sum;
}

void main() {
  vec2 p = v_texcoord * u_canvas;
  vec2 c = u_rect.xy;
  vec2 h = u_rect.zw;

  // The stroke's outer edge is the box grown by u_stroke_out with concentric
  // corners: its distance is d - u_stroke_out.
  float d = rrect_sdf(p, c, h, u_radius);
  float inside = coverage(d);
  float outer = coverage(d - u_stroke_out);

  vec2 uv = clamp((p - (c - h)) / (2.0 * h), 0.0, 1.0);
  vec2 st = u_crop.xy + uv * u_crop.zw;
  // Premultiplied colour of the part of the pixel inside the box: a box
  // filter over the pixel's footprint when the source is much larger.
  vec4 acc = vec4(0.0);
  for (int i = 0; i < 4; i++) {
    if (float(i) >= u_taps)
      break;
    for (int j = 0; j < 4; j++) {
      if (float(j) >= u_taps)
        break;
      vec2 o = (vec2(float(i), float(j)) + 0.5) / u_taps - 0.5;
      vec4 t = texture2D(tex, st + o * u_footprint);
      acc += vec4(t.rgb * t.a, t.a);
    }
  }
  acc /= u_taps * u_taps;
  float a = acc.a;
  vec3 rgb = acc.rgb;

  if (u_stroke > 0.0 && inside > 0.0) {
    // The share of the box's covered area under the stroke, laid over the
    // source.
    float band = max(inside - coverage(d + u_stroke_in), 0.0);
    float sa = u_stroke_color.a * band / inside;
    rgb = u_stroke_color.rgb * sa + rgb * (1.0 - sa);
    a = sa + a * (1.0 - sa);
  }
  a *= inside;
  rgb *= inside;

  if (u_stroke > 0.0) {
    // The stroke beyond the box edge, over nothing.
    float sa = u_stroke_color.a * max(outer - inside, 0.0);
    rgb += u_stroke_color.rgb * sa;
    a += sa;
  }

  if (u_shadow_color.a > 0.0) {
    vec2 q = p - u_shadow_offset;
    float s;
    if (u_sigma < u_sigma_min)
      s = coverage(rrect_sdf(q, c, u_outer.xy, u_outer.z));
    else
      s = rrect_shadow(q - c, u_outer.xy, u_outer.z, u_sigma);
    // Outside the box and stroke only, as CSS box-shadow.
    s *= u_shadow_color.a * (1.0 - outer);
    rgb += u_shadow_color.rgb * s;
    a += s;
  }

  a *= u_opacity;
  gl_FragColor = a > 0.0 ? vec4(rgb * (u_opacity / a), a) : vec4(0.0);
}
