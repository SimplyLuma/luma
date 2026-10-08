// Match an approved difference to one app, state, group, subject and property.
// A per-state cap prevents a narrowly approved repeated glyph from hiding new rows.
const { shortcutIssueMatches } = require('./linux-shortcut-equivalence');

function matchesDeviation(deviation, issue, state, app, used = 0, shortcutPairs = null) {
  if (deviation.apps && !deviation.apps.includes(app)) return false;
  if (deviation.states && !deviation.states.includes(state)) return false;
  if (deviation.state_pattern && !new RegExp(deviation.state_pattern).test(state)) return false;
  if (deviation.group && ![].concat(deviation.group).includes(issue.group)) return false;
  if (deviation.subject && !new RegExp(deviation.subject, 'i').test(issue.subject)) return false;
  if (deviation.prop && !new RegExp(deviation.prop, 'i').test(issue.prop)) return false;
  // What the spec and GTK show, when the approval is about a value pair (a system face for another).
  if (deviation.spec_value && !new RegExp(deviation.spec_value, 'i').test(String(issue.spec ?? ''))) return false;
  if (deviation.gtk_value && !new RegExp(deviation.gtk_value, 'i').test(String(issue.gtk ?? ''))) return false;
  if (deviation.max_delta != null && issue.mag != null && issue.mag > deviation.max_delta) return false;
  // A share of the spec's own size (a text run's width), and the face GTK drew it in.
  if (deviation.max_ratio != null && (issue.ratio == null || issue.ratio > deviation.max_ratio)) return false;
  if (deviation.gtk_family && !new RegExp(deviation.gtk_family, 'i').test(String(issue.family ?? ''))) return false;
  if (deviation.requires && [].concat(deviation.requires).some(flag => !issue[flag])) return false;
  if (deviation.max_count != null && used >= deviation.max_count) return false;
  if (deviation.requires_shortcut_pair && !shortcutIssueMatches(issue, shortcutPairs)) return false;
  return true;
}

module.exports = { matchesDeviation };
