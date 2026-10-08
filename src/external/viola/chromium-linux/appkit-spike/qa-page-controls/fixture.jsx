import React from 'react';
import {createRoot} from 'react-dom/client';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import * as Popover from '@radix-ui/react-popover';
window._radix={dropdown:0,item:0,popover:0};
document.body.innerHTML='<div id="react-root"></div>';
window._radixRoot=createRoot(document.getElementById('react-root'));
window._radixRoot.render(<>
  <Dropdown.Root onOpenChange={open=>{if(open)window._radix.dropdown++}}>
    <Dropdown.Trigger style={{position:'absolute',left:20,top:100,width:160,height:40}}>Dropdown</Dropdown.Trigger>
    <Dropdown.Portal><Dropdown.Content style={{background:'white',padding:10}}>
      <Dropdown.Item onSelect={()=>window._radix.item++}>Choose item</Dropdown.Item>
    </Dropdown.Content></Dropdown.Portal>
  </Dropdown.Root>
  <Popover.Root onOpenChange={open=>{if(open)window._radix.popover++}}>
    <Popover.Trigger style={{position:'absolute',right:20,top:20,width:120,height:40}}>Notifications</Popover.Trigger>
    <Popover.Portal><Popover.Content style={{background:'white',padding:10}}>Notification content</Popover.Content></Popover.Portal>
  </Popover.Root>
</>);
