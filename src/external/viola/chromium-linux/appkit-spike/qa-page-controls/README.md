# Page control fixture

The optional fixture uses the real React and Radix packages pinned in
package.json and pnpm-lock.yaml. This is isolated QA, not browser UI.

Install with pnpm, bundle fixture.jsx using esbuild with --bundle --format=iife,
then set VIOLA_QA_REACT_BUNDLE to the absolute bundle path on the Linux test host
when running headless_qa.py --qualify-controls. Do not ship the bundle.

VIOLA_QA_PAGE_CONTROLS_ONLY=1 runs only the page-control fixture on the private
compositor for diagnosis; omit it for the complete native qualification.
