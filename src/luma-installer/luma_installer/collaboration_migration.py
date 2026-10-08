# SPDX-License-Identifier: Apache-2.0
"""Export one app's collaboration state from an owned SQLite snapshot.

The migration owner supplies already checked paths and commits this new staged
file alongside the corresponding native profile. Enrollment files are never
inputs. The existing database and any destination remain untouched on failure.
"""
import json
import os
from pathlib import Path
import sqlite3

KINDS = {'org.projectluma.Notes': ('note',), 'org.projectluma.Tasks': ('task', 'list')}
TABLES = {
 'documents': ('id','hub','device','kind','local_id','revision','role','base'),
 'snapshots': ('id','snapshot'),
 'invitations': ('id','hub','device','kind','owner'),
 'recipients': ('hub','device','account','person','uses','last_used'),
}
SCHEMA = (
 'CREATE TABLE documents(id TEXT PRIMARY KEY,hub TEXT NOT NULL,device TEXT NOT NULL,kind TEXT NOT NULL,local_id TEXT NOT NULL,revision INTEGER NOT NULL,role TEXT NOT NULL,base TEXT NOT NULL)',
 'CREATE TABLE snapshots(id TEXT PRIMARY KEY,snapshot TEXT NOT NULL)',
 'CREATE TABLE invitations(id TEXT PRIMARY KEY,hub TEXT NOT NULL,device TEXT NOT NULL,kind TEXT NOT NULL,owner TEXT NOT NULL)',
 'CREATE TABLE recipients(hub TEXT NOT NULL,device TEXT NOT NULL,account TEXT NOT NULL,person TEXT NOT NULL,uses INTEGER NOT NULL,last_used REAL NOT NULL,PRIMARY KEY(hub,device,account))',
)

def export_collaboration_cache(source, destination, app_id, *, cancelled=lambda: False):
    if app_id not in KINDS: raise ValueError('This app has no collaboration-cache migration.')
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.is_symlink(): raise ValueError('Collaboration snapshot must be a regular file.')
    original = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    output = None; created = False
    try:
        original.execute('PRAGMA query_only=ON'); original.execute('BEGIN')
        for table, columns in TABLES.items():
            entry = original.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()
            actual = tuple(row[1] for row in original.execute('PRAGMA table_info('+table+')'))
            if entry != ('table',) or actual != columns: raise ValueError('Unsupported collaboration cache schema.')
        fd = os.open(destination, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        os.close(fd); created = True
        output = sqlite3.connect(destination); output.execute('PRAGMA synchronous=FULL')
        for statement in SCHEMA: output.execute(statement)
        placeholders = ','.join('?' for _ in KINDS[app_id]); counts = {}
        with output:
            documents = original.execute('SELECT * FROM documents WHERE kind IN ('+placeholders+')',KINDS[app_id]).fetchall()
            for row in documents:
                if cancelled(): raise ValueError('Collaboration migration cancelled.')
                output.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',row)
                snapshot = original.execute('SELECT snapshot FROM snapshots WHERE id=?',(row[0],)).fetchone()
                if snapshot:
                    value = json.loads(snapshot[0])
                    if value.get('kind') != row[3]: raise ValueError('Collaboration snapshot has a different document kind.')
                    output.execute('INSERT INTO snapshots VALUES(?,?)',(row[0],snapshot[0]))
            invitations = original.execute('SELECT * FROM invitations WHERE kind IN ('+placeholders+')',KINDS[app_id]).fetchall()
            output.executemany('INSERT INTO invitations VALUES(?,?,?,?,?)',invitations)
            groups = {}; recipients = 0
            for row in original.execute('SELECT * FROM recipients ORDER BY uses DESC,last_used DESC LIMIT 1024'):
                if cancelled(): raise ValueError('Collaboration migration cancelled.')
                group = row[:2]
                if groups.get(group,0) >= 8 or recipients >= 128: continue
                person = json.loads(row[3]); clean = {key:person.get(key) for key in ('account','handle','display_name','hue')}
                output.execute('INSERT INTO recipients VALUES(?,?,?,?,?,?)',row[:3]+(json.dumps(clean),)+row[4:])
                groups[group] = groups.get(group,0)+1; recipients += 1
            if cancelled(): raise ValueError('Collaboration migration cancelled.')
            counts = {'documents':len(documents),'invitations':len(invitations),'recipients':recipients}
        if output.execute('PRAGMA quick_check').fetchone()[0] != 'ok': raise ValueError('Collaboration export failed validation.')
        output.close(); output = None
        with destination.open('rb') as stream: os.fsync(stream.fileno())
        return counts
    except BaseException:
        if output is not None: output.close(); output = None
        if created: destination.unlink(missing_ok=True)
        raise
    finally:
        if output is not None: output.close()
        original.close()
