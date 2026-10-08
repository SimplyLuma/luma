# SPDX-License-Identifier: Apache-2.0
"""Actual WebKit book gesture/selection contract (DOM input integration).

This verifies the application's real pagination, CFI persistence, selection
bridge and saved highlight. It complements the compositor's native touchscreen
gate; dispatching TouchEvent here does not claim physical input acceptance.
"""
from tests import runtime_reader_pages as runner
from tests.fixtures import BODY

TOUCH = """(async () => {
 const doc=document.getElementById('frame').contentDocument;
 const target=doc.body;
 const send=(type,x,y,down=true)=>{
   // WebKitGTK exposes Touch but forbids its JavaScript construction. Supply
   // the documented list shape to real book event listeners; compositor input
   // is independently exercised by the native touchscreen acceptance gate.
   const touch={identifier:7,target,clientX:x,clientY:y};
   const event=new Event(type,{bubbles:true,cancelable:true});
   Object.defineProperties(event,{touches:{value:down?[touch]:[]},
     targetTouches:{value:down?[touch]:[]},changedTouches:{value:[touch]}});
   target.dispatchEvent(event);
 };
 const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
 window.leafTestTouch={send,delay,doc};
 return true;
})()"""

def script(app, messages):
    yield "until", lambda: app.window is not None and len(app.library.books()) == 1
    app.window.set_size_request(1160, 760)
    yield "until", lambda: app.window.get_width() >= 1100
    book = app.library.books()[0]
    reader = app.window.reader
    app.open_book(book.id)
    yield "until", lambda: bool(reader.position)
    reader.run(f"leaf.goToChapter({BODY}, null)")
    yield "until", lambda: reader.position.get("spine") == BODY
    original = reader.position["cfi"]
    result = yield "js", "({touch: typeof Touch, event: typeof TouchEvent})"
    print("WebKit touch constructors", result, flush=True)
    result = yield "js", TOUCH
    assert result is True, f"touch setup failed: {result}"
    result = yield "js", "(async()=>{try{const{send,delay}=window.leafTestTouch;send('touchstart',600,200);send('touchmove',420,202);send('touchend',400,202,false);await delay(350);return true}catch(e){return {error:e.message,stack:e.stack}}})()"
    assert result is True, f"touch dispatch failed: {result}"
    assert reader.position["cfi"] != original, f"left swipe did not advance: {reader.position}"
    forward = reader.position["cfi"]
    result = yield "js", "(async()=>{const{send,delay}=leafTestTouch;send('touchstart',400,200);send('touchmove',580,203);send('touchend',600,203,false);await delay(350);return true})()"
    assert reader.position["cfi"] == original, "right swipe did not return to the same saved CFI"
    result = yield "js", "(async()=>{const{send,delay}=leafTestTouch;send('touchstart',400,200);send('touchmove',406,380);send('touchend',406,400,false);await delay(200);return true})()"
    assert reader.position["cfi"] == original, "vertical movement turned a page"
    selected = yield "js", """(async()=>{
      const{doc,send,delay}=leafTestTouch;
      const walk=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT),r=doc.createRange();
      let node,rect;
      while(node=walk.nextNode()){
        if(!node.textContent.trim())continue;
        r.setStart(node,0);r.setEnd(node,Math.min(node.length,8));
        rect=r.getBoundingClientRect();if(rect.width>0&&rect.left>=0&&rect.right<doc.defaultView.innerWidth&&rect.top>=0)break;
      }
      if(!node)throw Error('no visible text in test EPUB');
      send('touchstart',rect.left+rect.width/2,rect.top+rect.height/2);
      await delay(650);send('touchend',rect.left+rect.width/2,rect.top+rect.height/2,false);
      await delay(100);return doc.getSelection().toString();
    })()"""
    assert selected and selected.strip(), "touch hold did not select actual text"
    yield "until", lambda: bool(reader.selection)
    assert reader.selection["selected"] == selected, reader.selection
    assert reader.selection["range"].startswith("epubcfi("), reader.selection
    assert reader.selection_bubble.get_visible(), "selection has no native action bubble"
    # A gesture moving an existing selection must never turn the spread.
    result = yield "js", "(async()=>{const{send,delay}=leafTestTouch;send('touchstart',400,200);send('touchmove',600,200);send('touchend',600,200,false);await delay(150);return true})()"
    assert reader.position["cfi"] == original, "selection gesture turned the page"
    reader._open_highlight_picker()
    reader._apply_colour("y")
    assert len(app.library.highlights(book.id)) == 1, "selected text was not actually persisted"
    assert app.library.highlights(book.id)[0].range == reader.selection["range"]
    yield "js", "leaf.clearSelection()"
    result = yield "js", "(async()=>{const{send,delay}=leafTestTouch;send('touchstart',400,200);send('touchmove',600,200);send('touchcancel',600,200,false);await delay(150);return true})()"
    assert reader.position["cfi"] == original, "cancelled gesture turned the page"
    print(f"LEAF WEBKIT TOUCH INTEGRATION PASS: forward={forward}, selected={selected!r}; actual saved highlight")

if __name__ == "__main__":
    runner.script = script
    try:
        raise SystemExit(runner.main())
    finally:
        runner.home.cleanup()
