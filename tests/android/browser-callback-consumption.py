#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compile Jelly's actual navigation decision with explicit Android substitutes.

Only callback consumption is qualified. This never authenticates an account,
uses a live callback, changes the source tree, or substitutes for Android intent
resolution and native window activation checks.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PREFIX = '''
open class WebView
class WebViewExt : WebView() {
 var isIncognito = false
 var lastLoadedUrl: String? = "https://fixture.invalid/start"
 var requestHeaders = emptyMap<String, String>()
 var followed = 0
 fun followUrl(url: String) { check(url.isNotEmpty()); followed++ }
}
class WebResourceRequest(val url: String, val isRedirect: Boolean = false,
 val isForMainFrame: Boolean = true, private val gesture: Boolean = false) {
 fun hasGesture() = gesture
}
object TextUtils { fun equals(a: String?, b: String?) = a == b }
open class WebViewClient {
 open fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = false
}
class Client(private val handled: Boolean) : WebViewClient() {
 var attempts = 0
 var delivered = 0
 private fun startActivityForUrl(view: WebView, url: String): Boolean {
  check(view is WebViewExt && url.isNotEmpty())
  attempts++
  if (handled) delivered++
  return handled
 }
'''
TEST = '''
}
fun main() {
 val callback = "luma-fixture://callback?code=SYNTHETIC"
 // A real handler already accepted this top-level redirect. Consuming it is
 // mandatory even without a user gesture; WebView must not load it again.
 for (headers in listOf(false, true)) {
  val view = WebViewExt()
  if (headers) view.requestHeaders = mapOf("X-Fixture" to "synthetic")
  val client = Client(true)
  val consumed = client.shouldOverrideUrlLoading(view, WebResourceRequest(callback, true))
  if (!consumed || client.delivered != 1 || view.followed != 0) kotlin.system.exitProcess(42)
 }
 for (scenario in 0..5) {
  val view = WebViewExt()
  var request = WebResourceRequest(callback)
  var handled = true
  var consumed = true
  var attempts = 1
  var followed = 0
  when (scenario) {
   0 -> { view.isIncognito = true; consumed = false; attempts = 0 }
   1 -> { request = WebResourceRequest(callback, isForMainFrame = false); consumed = false; attempts = 0 }
   2 -> { view.lastLoadedUrl = callback; consumed = false; attempts = 0 }
   3 -> { view.lastLoadedUrl = callback; request = WebResourceRequest(callback, gesture = true) }
   4 -> { handled = false; consumed = false; request = WebResourceRequest("https://fixture.invalid/page") }
   5 -> { handled = false; view.requestHeaders = mapOf("X-Fixture" to "synthetic"); followed = 1 }
  }
  val client = Client(handled)
  check(client.shouldOverrideUrlLoading(view, request) == consumed)
  check(client.attempts == attempts && client.delivered == if (handled) attempts else 0)
  check(view.followed == followed)
 }
 println("PASS production callback-consumption decision: handled redirects, headers, incognito, subframes, duplicates, gestures and normal web fallback; Android dependencies are substitutes")
}
'''


def method(source):
    start = source.index('    override fun shouldOverrideUrlLoading(')
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--kotlinc', required=True)
    parser.add_argument('--java', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='luma-browser-callback-') as directory:
        root = Path(directory)
        relative = Path('app/src/main/java/org/lineageos/jelly/webview/WebClient.kt')
        copied = root/relative
        copied.parent.mkdir(parents=True)
        shutil.copyfile(args.source, copied)
        original = copied.read_text()
        subprocess.run(['git', 'apply', str(args.patch.resolve())], cwd=root, check=True)
        patched = copied.read_text()
        assert patched != original
        environment = dict(os.environ, JAVA_HOME=str(Path(args.java).resolve().parents[1]),
                           JAVA_OPTS='-Xms32m -Xmx256m')
        for body, expected in ((method(patched), 0), (method(original), 42)):
            unit, jar = root/'Decision.kt', root/'decision.jar'
            unit.write_text(PREFIX+body+TEST)
            subprocess.run([args.kotlinc, str(unit), '-include-runtime', '-d', str(jar)],
                           env=environment, check=True, timeout=90)
            result = subprocess.run([args.java, '-Xmx128m', '-jar', str(jar)], timeout=15)
            assert result.returncode == expected, ('callback consumption control', result.returncode, expected)
        print('PASS unchanged production control rejected with exact exit42; no live authorization data used')


if __name__ == '__main__':
    main()
