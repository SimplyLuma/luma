// The Calculator reference uses macOS shortcut labels. Accept the Linux keycaps
// only when both visible pieces form the corresponding menu-row shortcut.
const nearBox = (a, b) => a && b && ['x', 'y', 'w', 'h'].every(k => Math.abs(a[k] - b[k]) <= 1);
const middleY = b => b.y + b.h / 2;

function calculatorShortcutPairs(app, state, specTexts, gtkTexts) {
  const pairs = new Map();
  if (app !== 'calc' || state !== 'menu') return pairs;
  for (const digit of ['1', '2']) {
    const spec = specTexts.find(t => !t.offscreen && t.text === `⌘${digit}`);
    if (!spec) continue;
    const pair = gtkTexts.filter(t => !t.offscreen && t.text === 'Ctrl').flatMap(ctrl =>
      gtkTexts.filter(t => !t.offscreen && t.text === digit).map(key => ({ ctrl, key })))
      .find(({ ctrl, key }) => {
        const gap = key.box.x - (ctrl.box.x + ctrl.box.w);
        return gap >= 0 && gap <= 12 &&
          Math.abs(middleY(ctrl.box) - middleY(key.box)) <= 8 &&
          Math.abs(middleY(spec.box) - middleY(ctrl.box)) <= 24 &&
          Math.abs(ctrl.box.x - spec.box.x) <= 60;
      });
    if (pair) pairs.set(digit, { spec: spec.box, ctrl: pair.ctrl.box, key: pair.key.box });
  }
  return pairs;
}

function shortcutIssueMatches(issue, pairs) {
  if (!pairs || issue.group !== 'menu') return false;
  for (const [digit, pair] of pairs) {
    if (issue.prop === 'missing' && issue.subject === `text "⌘${digit}"` && nearBox(issue.box, pair.spec)) return true;
    if (issue.prop === 'extra' && issue.subject === 'text "Ctrl"' && nearBox(issue.box, pair.ctrl)) return true;
    if (issue.prop === 'extra' && issue.subject === `text "${digit}"` && nearBox(issue.box, pair.key)) return true;
  }
  return false;
}

function acceptedShortcutLabels(issues) {
  return new Set(issues.filter(issue => issue.accepted === 'calc-linux-shortcut-spec')
    .map(issue => issue.subject.match(/^text "(⌘[12])"$/)?.[1]).filter(Boolean));
}

module.exports = { calculatorShortcutPairs, shortcutIssueMatches, acceptedShortcutLabels };
