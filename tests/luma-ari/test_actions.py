# SPDX-License-Identifier: Apache-2.0
"""Real workspace changes and broker/installer boundary contracts."""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ari import policy, router
from ari.agent import Agent, Turn, _tool_text
from ari.mcp import Tool
from ari.store import Store
from ari.tools import applications, workspace


class Workspace(unittest.TestCase):
    def test_tool_results_preserve_list_and_scalar_data_for_the_next_model_call(self):
        import json
        for data in ([{'application': 'opaque', 'name': 'Weather'}], ['src/main.py'], 'source text', 7):
            text = _tool_text({'ok': True, 'summary': 'Read the application.', 'data': data, 'untrusted': True})
            self.assertIn('[Data from outside Ari.', text)
            self.assertEqual(json.loads(text.split('Details: ', 1)[1]), data)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'Projects' / 'Ari'
        self.patch = patch.object(workspace, 'root', return_value=self.root)
        self.patch.start()
        (self.root / 'test').mkdir(parents=True)

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_create_read_replace_and_conflict_preserve_files(self):
        args = dict(project='test', path='src/main.py', content='print("Hello")\n')
        self.assertTrue(workspace.write_project_file(args)['ok'])
        before = workspace.read_project_file(args)['data']
        self.assertEqual(before['content'], args['content'])
        self.assertFalse(workspace.write_project_file({**args, 'content':'bad'})['ok'])
        replacement = {**args, 'content':'print("Updated")\n', 'expected_sha256':before['sha256']}
        self.assertTrue(workspace.write_project_file({**replacement, 'preview':True})['ok'])
        self.assertEqual((self.root/'test/src/main.py').read_text(), args['content'])
        self.assertTrue(workspace.write_project_file(replacement)['ok'])
        self.assertFalse(workspace.write_project_file(replacement)['ok'])
        self.assertIn('src/main.py', workspace.list_project({'project':'test'})['data'])

    def test_traversal_symlinks_and_hardlinked_replacement_refused(self):
        for path in ('../../other', '/etc/passwd', 'src/../../other'):
            with self.assertRaises(ValueError):
                workspace.file_path(self.root/'test', path)
        for project in ('../x', '/tmp/x', '.', ''):
            with self.assertRaises(ValueError):
                workspace.project_path(project)
        (self.root/'test/link').symlink_to(self.root.parent)
        with self.assertRaises(ValueError):
            workspace.file_path(self.root/'test', 'link/secret')
        source = self.root/'source'; source.write_text('original')
        import os
        os.link(source, self.root/'test/shared')
        with self.assertRaises(ValueError):
            workspace.write_project_file(dict(project='test', path='shared', content='changed',
                expected_sha256=hashlib.sha256(b'original').hexdigest()))
        self.assertEqual(source.read_text(), 'original')

    def test_build_command_has_no_host_home_bus_network_or_inherited_env(self):
        args = workspace.sandbox_command(self.root/'test', ['python3','src/main.py'])
        self.assertIn('--unshare-all', args)
        self.assertIn('--clearenv', args)
        self.assertIn('--cap-drop', args)
        self.assertNotIn('--share-net', args)
        self.assertNotIn('/run', args)
        self.assertEqual(args[-3:], ['--','python3','src/main.py'])
        with self.assertRaises(ValueError):
            workspace.sandbox_command(self.root/'test', 'touch /tmp/not-an-array')

    def test_build_failure_is_not_a_successful_action(self):
        process = Mock()
        process.pid = 12345
        process.wait.return_value = 7
        with patch.object(workspace.subprocess,'Popen',return_value=process), patch.object(workspace.os,'killpg'):
            result = workspace.run_project(dict(project='test',command=['false']))
        self.assertFalse(result['ok'])
        self.assertEqual(result['data']['exit_code'], 7)

    def test_cancelled_workspace_server_can_be_started_again(self):
        import threading
        import time
        from ari.mcp import Client, ToolError
        code = "from ari.mcp import serve;import time;serve('workspace',{'slow':('wait',{},lambda _: (time.sleep(10),{'ok':True})[1]),'ping':('ping',{},lambda _:{'ok':True,'summary':'pong'})})"
        client = Client('workspace',[sys.executable,'-c',code])
        stop = threading.Event()
        timer = threading.Timer(.15,stop.set)
        try:
            client.start(); timer.start(); started=time.monotonic()
            with self.assertRaisesRegex(ToolError,'Stopped'):
                client.call('slow',{},stop=stop)
            self.assertLess(time.monotonic()-started,3)
            client.start()
            self.assertEqual(client.call('ping',{})['summary'],'pong')
        finally:
            timer.cancel();client.stop()


class Applications(unittest.TestCase):
    def tearDown(self):
        applications._reviews.clear()
        applications._software_reviews.clear()

    def test_broker_action_reports_provider_failure_and_exact_parameters(self):
        client = Mock()
        client.invoke_action.return_value = {'completed': True, 'result': {'ok':False,'summary':'Draft was not saved'}}
        args = dict(publication_id='surface-1',object_id='note-2',action_id='save',parameter={'text':'Draft'})
        with patch.object(applications,'broker',return_value=client):
            result = applications.app_action(args)
        self.assertFalse(result['ok'])
        self.assertEqual(result['summary'],'Draft was not saved')
        client.invoke_action.assert_called_once_with('surface-1','note-2','save',{'text':'Draft'})

    def test_broker_owns_access_scope_confirmation(self):
        client = Mock()
        client.request_access.return_value = {'granted':True}
        with patch.object(applications,'broker',return_value=client):
            self.assertTrue(applications.request_app_access({'application_id':'org.test.App'})['ok'])
            self.assertFalse(applications.request_app_access({'application_id':'org.test.App','scopes':['root']})['ok'])
        client.request_access.assert_called_once_with('org.test.App',['observe-public','invoke-low-risk'],duration=3600)

    def test_install_requires_owned_review_and_waits_for_workflow(self):
        self.assertFalse(applications.install_package({'review_id':'invented'})['ok'])
        import time
        report = SimpleNamespace(title='Example', path=Path('/tmp/example.flatpak'), identity='Publisher',
            permissions=('network',),warnings=('Warning',),requires_system_change=False,requires_restart=False)
        applications._reviews['review'] = (time.monotonic(), report)
        install = Mock(return_value={'name':'Example','application_id':'org.test.Example'})
        with patch.dict(sys.modules,{'luma_installer.workflow':SimpleNamespace(install=install)}):
            self.assertIn('network', applications.install_package({'review_id':'review','preview':True})['detail'])
            install.assert_not_called()
            install.side_effect = RuntimeError('The package changed after review')
            with self.assertRaisesRegex(RuntimeError,'changed'):
                applications.install_package({'review_id':'review'})
            install.side_effect = None
            self.assertTrue(applications.install_package({'review_id':'review'})['ok'])
            self.assertFalse(applications.install_package({'review_id':'review'})['ok'])

    def test_removal_retains_data_and_checks_essential_application(self):
        remove, protected = Mock(), Mock()
        modules = {'luma_installer.workflow':SimpleNamespace(resolve_record=lambda _: {'name':'Example'},remove=remove),
                   'luma_installer.removal':SimpleNamespace(refuse_protected=protected)}
        with patch.dict(sys.modules,modules):
            self.assertTrue(applications.remove_application({'application_id':'org.test.App','preview':True})['ok'])
            remove.assert_not_called()
            self.assertTrue(applications.remove_application({'application_id':'org.test.App'})['ok'])
            remove.assert_called_once_with({'name':'Example'},keep_data=True)
            protected.side_effect = RuntimeError('essential')
            with self.assertRaisesRegex(RuntimeError,'essential'):
                applications.remove_application({'application_id':'org.gnome.Shell'})

    def test_catalogue_install_rechecks_and_verifies_the_reviewed_source(self):
        import time
        source = SimpleNamespace(app_id='org.test.Example',commit='reviewed',remote='flathub')
        entry = SimpleNamespace(name='Example',publisher='Publisher')
        installation = Mock()
        actual = Mock()
        actual.get_commit.return_value = 'reviewed'; actual.get_origin.return_value = 'flathub'
        flatpak = SimpleNamespace(installed_ref=Mock(side_effect=[None,actual]),transaction=Mock())
        applications._software_reviews['catalogue-review'] = (time.monotonic(),entry,installation,source,
            [{'title':'Uses the internet','detail':'Can send and receive data'}])
        with patch.dict(sys.modules,{'luma_installer':SimpleNamespace(depot_flatpak=flatpak), 'luma_installer.depot_flatpak':flatpak}):
            preview = applications.install_software({'review_id':'catalogue-review','preview':True})
            self.assertIn('Uses the internet',preview['detail'])
            flatpak.transaction.assert_not_called()
            self.assertTrue(applications.install_software({'review_id':'catalogue-review'})['ok'])
        flatpak.transaction.return_value.run.assert_called_once_with(None)
        installation.drop_caches.assert_called_once_with(None)
        self.assertNotIn('catalogue-review',applications._software_reviews)

    def test_catalogue_install_cannot_report_a_different_commit_as_success(self):
        import time
        source = SimpleNamespace(app_id='org.test.Example',commit='reviewed',remote='flathub')
        actual = Mock(); actual.get_commit.return_value='substituted'
        flatpak = SimpleNamespace(installed_ref=Mock(side_effect=[None,actual]),transaction=Mock())
        applications._software_reviews['review'] = (time.monotonic(),SimpleNamespace(name='Example'),Mock(),source,[])
        with patch.dict(sys.modules,{'luma_installer':SimpleNamespace(depot_flatpak=flatpak), 'luma_installer.depot_flatpak':flatpak}):
            self.assertFalse(applications.install_software({'review_id':'review'})['ok'])

    def test_system_removal_uses_catalogued_helper_and_reports_only_verified_staging(self):
        source=SimpleNamespace(package='luma-example',desktop_id='org.test.Example.desktop',removable=True)
        entry=SimpleNamespace(id='example',identifier='org.test.Example',name='Example',luma_system=source)
        state=Mock(return_value=SimpleNamespace(state=lambda _:'removal-pending'))
        modules={'luma_installer.depot_catalog':SimpleNamespace(local_catalog=lambda:SimpleNamespace(applications=[entry])),
                 'luma_installer.removal':SimpleNamespace(is_essential=lambda **_:False),
                 'luma_installer.depot_system_apps':SimpleNamespace(SystemApps=SimpleNamespace(read=state))}
        with patch.dict(sys.modules,modules), patch.object(applications.subprocess,'run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertIn('Restart',applications.system_application_change({'application_id':'example','preview':True},'override-remove')['detail'])
            run.assert_not_called()
            result=applications.system_application_change({'application_id':'example'},'override-remove')
            self.assertIn('staged',result['summary'])
            self.assertNotIn('Uninstalled',result['summary'])
            self.assertEqual(run.call_args.args[0],['/usr/bin/pkexec','/usr/libexec/luma-installer-system','override-remove','luma-example'])
            state.return_value=SimpleNamespace(state=lambda _:'installed')
            self.assertFalse(applications.system_application_change({'application_id':'example'},'override-remove')['ok'])

    def test_system_removal_does_not_invoke_privilege_for_unknown_or_protected_app(self):
        modules={'luma_installer.depot_catalog':SimpleNamespace(local_catalog=lambda:SimpleNamespace(applications=[])),
                 'luma_installer.removal':SimpleNamespace(is_essential=lambda **_:True)}
        with patch.dict(sys.modules,modules), patch.object(applications.subprocess,'run') as run:
            self.assertFalse(applications.system_application_change({'application_id':'org.gnome.Shell'},'override-remove')['ok'])
            run.assert_not_called()


class ActionPolicy(unittest.TestCase):
    def test_application_adapter_gets_managed_identity_without_grant_flags(self):
        from ari.service import tool_commands
        commands=tool_commands(Path('/usr/example'),managed_app='org.projectluma.Ari',token='aabbcc')
        self.assertEqual(commands['applications'][:4],['/usr/bin/systemd-run','--user','--scope','--quiet'])
        self.assertIn('--unit=app-gnome-org.projectluma.Ari-aabbcc.scope',commands['applications'])
        self.assertEqual(commands['workspace'],[sys.executable,'/usr/example/workspace.py'])
        self.assertNotIn('--scope',tool_commands(Path('/usr/example'))['applications'])

    def test_every_exposed_action_has_an_enabled_permission(self):
        engine = policy.Policy()
        for name in applications.TOOLS.keys() | workspace.TOOLS.keys():
            self.assertIn(name,policy.TOOL_TIERS)
            self.assertIn(engine.tier(name),policy.ENABLED_TIERS)
        engine.enabled.discard(policy.CHANGE_SYSTEM)
        self.assertFalse(engine.check('install_package',calls_this_turn=0,after_untrusted=False).allowed)
        self.assertTrue(engine.needs_approval('install_package'))
        self.assertTrue(engine.needs_approval('run_project'))
        self.assertFalse(engine.needs_approval('app_action'))
        self.assertFalse(engine.check('run_shell',calls_this_turn=0,after_untrusted=False).allowed)

    def test_project_request_routes_complete_development_toolset(self):
        tools = [Tool(name,desc,schema,'workspace') for name,(desc,schema,_) in workspace.TOOLS.items()]
        chosen = router.select('Build me a calculator application',tools,10)
        self.assertEqual({tool.name for tool in chosen},set(workspace.TOOLS))

    def test_in_app_work_offers_discovery_access_and_actuation_together(self):
        entries={**applications.TOOLS,**workspace.TOOLS}
        tools=[Tool(name,desc,schema,'test') for name,(desc,schema,_) in entries.items()]
        tools += [Tool(name,'App',{},'desktop') for name in ('find_app','open_app')]
        chosen={tool.name for tool in router.select('Add a shopping list in Notes',tools,6)}
        self.assertTrue({'find_app','open_app','request_app_access','app_surfaces','app_action'} <= chosen)
        from ari.intents import match
        self.assertIsNone(match('Open Notes and add a shopping list'))

    def test_successful_project_action_has_one_persisted_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'ari.db')
            tools = SimpleNamespace(available=[],by_name={},call=Mock(return_value={'ok':True,'summary':'Created Calculator.'}))
            engine = policy.Policy(); engine.approval_mode='never'
            agent = Agent(store,tools,engine,model_provider=None)
            conversation = store.new_conversation()
            events = []
            turn = Turn(conversation,'Create a calculator app',events.append)
            with patch('ari.agent.audit.record'):
                self.assertTrue(agent.run_tool(turn,'create_project',{'name':'Calculator'})['ok'])
            self.assertEqual(len(turn.steps),1)
            self.assertEqual(len([event for event in events if event['type']=='step']),1)
            store.db.close()

    def test_project_permission_is_scoped_to_one_project_and_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'ari.db')
            tool=Tool('write_project_file','Write source',{'properties':{'preview':{'type':'boolean'}}},'workspace')
            tools=SimpleNamespace(available=[tool],by_name={'write_project_file':(None,tool)},
                call=Mock(return_value={'ok':True,'summary':'Updated source','preview':'Edit source'}))
            agent=Agent(store,tools,policy.Policy(),model_provider=None)
            events=[]
            def receive(event):
                events.append(event)
                if event['type']=='approval': agent.approve(event['approval'],True)
            turn=Turn(store.new_conversation(),'Build a calculator app',receive)
            with patch('ari.agent.audit.record'):
                for project in ('calculator','calculator','another'):
                    self.assertTrue(agent.run_tool(turn,'write_project_file',{'project':project,'path':'main.py','content':'code'})['ok'])
            self.assertEqual(len([event for event in events if event['type']=='approval']),2)
            self.assertEqual(turn.project_approvals,{'calculator','another'})
            store.db.close()

    def test_permission_revoked_while_approval_is_open_prevents_the_change(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'ari.db')
            tool=Tool('create_project','Create app',{'properties':{'preview':{'type':'boolean'}}},'workspace')
            tools=SimpleNamespace(available=[tool],by_name={'create_project':(None,tool)},
                call=Mock(return_value={'ok':True,'preview':'Create app','summary':'Created app'}))
            agent=Agent(store,tools,policy.Policy(),model_provider=None)
            def receive(event):
                if event['type']=='approval':
                    agent.policy.enabled.discard(policy.CHANGE_FILES)
                    agent.approve(event['approval'],True)
            turn=Turn(store.new_conversation(),'Create an app',receive)
            with patch('ari.agent.audit.record'):
                result=agent.run_tool(turn,'create_project',{'project':'app','name':'App'})
            self.assertFalse(result['ok'])
            self.assertEqual(len(turn.steps),0)
            self.assertEqual(tools.call.call_count,1)
            self.assertTrue(tools.call.call_args.args[1]['preview'])
            store.db.close()

    def test_application_request_keeps_the_full_action_bundle(self):
        names=('now','weather','find_app','open_app','request_app_access','app_surfaces','app_action','calculate','wikipedia')
        tools=[Tool(name,name,{'type':'object'},'applications') for name in names]
        chosen={tool.name for tool in router.select('Add Chicago to the weather app',tools,6)}
        self.assertTrue({'find_app','open_app','request_app_access','app_surfaces','app_action'} <= chosen)

    def test_followup_history_contains_receipts_without_duplicate_chat_bubbles(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'ari.db')
            tool=Tool('open_app','Open app',{'type':'object'},'desktop')
            tools=SimpleNamespace(available=[tool],by_name={'open_app':(None,tool)},
                                  call=Mock(return_value={'ok':True,'summary':'Opened Weather.'}))
            provider=SimpleNamespace(model='fixture',hosted_search=True,chat=Mock(return_value=iter([{'type':'text','text':'Next step'}])))
            agent=Agent(store,tools,policy.Policy(),model_provider=lambda:(provider,'Fixture','through your ChatGPT plan','date','large'))
            identity=store.new_conversation()
            with patch('ari.agent.audit.record'), patch('ari.agent._current_settings',return_value=''):
                agent.answer(Turn(identity,'Open my Weather app',lambda event:None))
                self.assertEqual(store.messages(identity)[-1]['content'],'')
                agent.answer(Turn(identity,'Add Chicago to the weather app',lambda event:None))
            submitted=provider.chat.call_args.args[0]
            self.assertIn({'role':'assistant','content':'Opened Weather.'},submitted)
            store.db.close()

    def test_accessibility_cannot_retarget_an_unnamed_application_even_without_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'ari.db')
            tool=Tool('act_in_application','Operate app',{'properties':{'preview':{'type':'boolean'}}},'accessibility')
            tools=SimpleNamespace(available=[tool],by_name={'act_in_application':(None,tool)},
                 call=Mock(return_value={'ok':True,'requested_name':'browser','preview':'Activate a browser control'}))
            p=policy.Policy();p.approval_mode='never'
            agent=Agent(store,tools,p,model_provider=None)
            turn=Turn(store.new_conversation(),'Add Chicago to the Weather app',lambda event:None)
            turn.read_untrusted=True
            with patch('ari.agent.audit.record'):
                result=agent.run_tool(turn,'act_in_application',{'application':'opaque','target':'opaque','operation':'action','action':'click'})
            self.assertFalse(result['ok'])
            self.assertEqual(tools.call.call_count,1)
            self.assertTrue(tools.call.call_args.args[1]['preview'])
            store.db.close()
