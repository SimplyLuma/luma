# SPDX-License-Identifier: Apache-2.0
"""Transactional Notes replication at the existing NotesStore ownership boundary.

No transport, permission changes, background loop or personal-store discovery.
The daemon supplies an authorized store and calls these bounded operations.
"""
import hashlib
import json
import uuid

FIELDS={
    'folder':('id','name','favorite','sort_order','expanded'),
    'note':('id','title','body','runs_json','created_at','modified_at','folder_id','favorite','sort_order','deleted_at'),
}
TABLES={'folder':'folders','note':'notes'}
MAX_VALUE=131072
MAX_PAGE=524288
MAX_ITEMS=64
MAX_VECTOR=16
MAX_LOG=100000


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def uid(value):
    if not isinstance(value,str) or str(uuid.UUID(value))!=value:raise ValueError('invalid Notes identity')
    return value


def dominates(left,right):
    return all(left.get(key,0)>=value for key,value in right.items())


class NotesSync:
    def __init__(self,store,*,authorized=lambda:False):
        self.store,self.db,self.authorized=store,store.connection,authorized
        self._allowed()
        self._install()
        self.origin=self.db.execute("SELECT value FROM luma_notes_sync_meta WHERE key='origin'").fetchone()[0]

    def _install(self):
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS luma_notes_sync_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS luma_notes_sync_changes(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,uid TEXT NOT NULL,
            deleted INTEGER NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS luma_notes_sync_ops(
            position INTEGER PRIMARY KEY AUTOINCREMENT,origin TEXT NOT NULL,sequence INTEGER NOT NULL,
            kind TEXT NOT NULL,uid TEXT NOT NULL,digest TEXT NOT NULL,body TEXT NOT NULL,
            UNIQUE(origin,sequence));
          CREATE TABLE IF NOT EXISTS luma_notes_sync_frontier(
            kind TEXT NOT NULL,uid TEXT NOT NULL,position INTEGER NOT NULL,
            PRIMARY KEY(kind,uid,position));
          CREATE TABLE IF NOT EXISTS luma_notes_sync_cursors(peer TEXT PRIMARY KEY,position INTEGER NOT NULL);
        ''')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO luma_notes_sync_meta VALUES('origin',?)",(str(uuid.uuid4()),))
            self.db.execute("INSERT OR IGNORE INTO luma_notes_sync_meta VALUES('applying','0')")
            self.db.execute("INSERT OR IGNORE INTO luma_notes_sync_meta VALUES('captured','0')")
            self.db.execute("INSERT OR IGNORE INTO luma_notes_sync_meta VALUES('schema','1')")
            if self.db.execute("SELECT value FROM luma_notes_sync_meta WHERE key='schema'").fetchone()[0]!='1':
                raise ValueError('unsupported Notes synchronization schema')
        # SQLite executes triggers for every native connection, even while the
        # account daemon is stopped. Trigger writes share the native transaction.
        for kind,fields in FIELDS.items():
            table=TABLES[kind]
            for action in ('INSERT','UPDATE','DELETE'):
                row='OLD' if action=='DELETE' else 'NEW'
                value=','.join("'%s',%s.%s"%(field,row,field) for field in fields)
                deleted='1' if action=='DELETE' else f'{row}.deleted_at IS NOT NULL' if kind=='note' else '0'
                self.db.executescript(f'''
                  CREATE TRIGGER IF NOT EXISTS luma_notes_sync_{kind}_{action.lower()}
                  AFTER {action} ON {table}
                  WHEN (SELECT value FROM luma_notes_sync_meta WHERE key='applying')='0'
                  BEGIN
                    INSERT INTO luma_notes_sync_changes(kind,uid,deleted,payload)
                    VALUES('{kind}',{row}.id,{deleted},json_object({value}));
                  END;
                ''')
        with self.db:
            if not self.db.execute("SELECT 1 FROM luma_notes_sync_meta WHERE key='seeded'").fetchone():
                for kind,fields in FIELDS.items():
                    for row in self.db.execute('SELECT * FROM '+TABLES[kind]).fetchall():
                        data={field:row[field] for field in fields}
                        self.db.execute('INSERT INTO luma_notes_sync_changes(kind,uid,deleted,payload) VALUES(?,?,?,?)',
                            (kind,data['id'],bool(data.get('deleted_at')),encoded(data)))
                self.db.execute("INSERT INTO luma_notes_sync_meta VALUES('seeded','1')")

    def _allowed(self):
        if not self.authorized():raise PermissionError('Notes synchronization unavailable')

    def _heads(self,kind,key):
        return [(row[0],json.loads(row[1])) for row in self.db.execute('''
          SELECT o.position,o.body FROM luma_notes_sync_frontier f JOIN luma_notes_sync_ops o
          ON o.position=f.position WHERE f.kind=? AND f.uid=?''',(kind,key))]

    def _validate(self,operation):
        if (not isinstance(operation,dict) or set(operation)!={'origin','sequence','kind','uid','clock','deleted','value'}
                or operation['kind'] not in FIELDS or type(operation['sequence']) is not int
                or not 0<operation['sequence']<2**53 or type(operation['deleted']) is not bool):
            raise ValueError('invalid Notes operation')
        uid(operation['origin']);uid(operation['uid'])
        clock=operation['clock']
        if (not isinstance(clock,dict) or not 1<=len(clock)<=MAX_VECTOR
                or clock.get(operation['origin'])!=operation['sequence']):raise ValueError('invalid Notes version')
        for origin,value in clock.items():
            uid(origin)
            if type(value) is not int or not 0<value<2**53:raise ValueError('invalid Notes version')
        value=operation['value'];kind=operation['kind']
        if not isinstance(value,dict) or set(value)!=set(FIELDS[kind]) or value['id']!=operation['uid']:
            raise ValueError('invalid Notes value')
        if len(encoded(value).encode())>MAX_VALUE:raise ValueError('note exceeds synchronization size limit')
        for field in ('favorite','sort_order'):
            if type(value[field]) is not int:raise ValueError('invalid Notes ordering')
        if value['favorite'] not in (0,1) or abs(value['sort_order'])>2**53:raise ValueError('invalid Notes ordering')
        if kind=='folder':
            if not isinstance(value['name'],str) or not value['name'].strip() or value['expanded'] not in (0,1):
                raise ValueError('invalid folder')
        else:
            for field in ('title','body','runs_json','created_at','modified_at'):
                if not isinstance(value[field],str):raise ValueError('invalid note')
            if value['folder_id'] is not None:uid(value['folder_id'])
            if value['deleted_at'] is not None and not isinstance(value['deleted_at'],str):raise ValueError('invalid note deletion')
            from prairie_apps.notes_backend import _validate_runs
            runs=json.loads(value['runs_json'])
            if not isinstance(runs,list):raise ValueError('invalid rich text')
            _validate_runs(value['body'],tuple(runs))
        return operation

    def _record(self,operation):
        self._validate(operation)
        raw=encoded(operation);digest=hashlib.sha256(raw.encode()).hexdigest()
        old=self.db.execute('SELECT digest FROM luma_notes_sync_ops WHERE origin=? AND sequence=?',
                            (operation['origin'],operation['sequence'])).fetchone()
        if old:
            if old[0]!=digest:raise ValueError('Notes operation identity reused')
            return False
        if self.db.execute('SELECT count(*) FROM luma_notes_sync_ops').fetchone()[0]>=MAX_LOG:
            raise ValueError('Notes synchronization journal capacity reached')
        if operation['origin']==self.origin: # Local capture is separately admitted below.
            captured=int(self.db.execute("SELECT value FROM luma_notes_sync_meta WHERE key='captured'").fetchone()[0])
            if operation['sequence']<=captured:raise ValueError('unrecognized local Notes operation')
        heads=self._heads(operation['kind'],operation['uid'])
        position=self.db.execute('INSERT INTO luma_notes_sync_ops(origin,sequence,kind,uid,digest,body) VALUES(?,?,?,?,?,?)',
            (operation['origin'],operation['sequence'],operation['kind'],operation['uid'],digest,raw)).lastrowid
        if any(dominates(head['clock'],operation['clock']) for _,head in heads):return True
        for previous,head in heads:
            if dominates(operation['clock'],head['clock']):
                self.db.execute('DELETE FROM luma_notes_sync_frontier WHERE position=?',(previous,))
        if len(self._heads(operation['kind'],operation['uid']))>=MAX_VECTOR:raise ValueError('too many concurrent Notes versions')
        self.db.execute('INSERT INTO luma_notes_sync_frontier VALUES(?,?,?)',(operation['kind'],operation['uid'],position))
        return True

    def _capture(self):
        captured=int(self.db.execute("SELECT value FROM luma_notes_sync_meta WHERE key='captured'").fetchone()[0])
        rows=self.db.execute('''SELECT seq,kind,uid,deleted,payload FROM luma_notes_sync_changes
          WHERE seq IN (SELECT max(seq) FROM luma_notes_sync_changes WHERE seq>? GROUP BY kind,uid)
          ORDER BY CASE kind WHEN 'folder' THEN 0 ELSE 1 END,seq''',(captured,)).fetchall()
        for seq,kind,key,deleted,raw in rows:
            heads=self._heads(kind,key);clock={}
            for _,head in heads:
                for origin,version in head['clock'].items():clock[origin]=max(clock.get(origin,0),version)
            value=json.loads(raw)
            if heads and all(head['value']==value and head['deleted']==bool(deleted) for _,head in heads):continue
            clock[self.origin]=seq
            self._record(dict(origin=self.origin,sequence=seq,kind=kind,uid=key,clock=clock,deleted=bool(deleted),value=value))
        if rows:self.db.execute("UPDATE luma_notes_sync_meta SET value=? WHERE key='captured'",(str(max(row[0] for row in rows)),))

    def page(self,after=0,*,watermark=None):
        self._allowed()
        if type(after) is not int or after<0:raise ValueError('invalid Notes cursor')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._capture()
            latest=self.db.execute('SELECT coalesce(max(position),0) FROM luma_notes_sync_ops').fetchone()[0]
            if watermark is None:watermark=latest
            if type(watermark) is not int or not after<=watermark<=latest:raise ValueError('invalid Notes watermark')
            items=[];size=0;cursor=after
            for position,raw in self.db.execute('SELECT position,body FROM luma_notes_sync_ops WHERE position>? AND position<=? ORDER BY position LIMIT ?',
                                             (after,watermark,MAX_ITEMS)):
                size+=len(raw.encode())
                if size>MAX_PAGE:break
                items.append(json.loads(raw));cursor=position
            self._allowed();self.db.commit()
            return dict(store=self.origin,after=after,cursor=cursor,watermark=watermark,items=items)
        except BaseException:self.db.rollback();raise

    def _write(self,kind,value,deleted):
        fields=FIELDS[kind];value=dict(value)
        if kind=='folder' and deleted:
            self.db.execute('DELETE FROM folders WHERE id=?',(value['id'],));return
        if kind=='note':
            if deleted and value['deleted_at'] is None:value['deleted_at']=value['modified_at']
            folder=value['folder_id']
            if folder and not self.db.execute('SELECT 1 FROM folders WHERE id=?',(folder,)).fetchone():value['folder_id']=None
        columns=','.join(fields);updates=','.join(field+'=excluded.'+field for field in fields if field!='id')
        self.db.execute(f'INSERT INTO {TABLES[kind]}({columns}) VALUES({",".join("?" for _ in fields)}) ON CONFLICT(id) DO UPDATE SET {updates}',
                        tuple(value[field] for field in fields))

    def _materialize(self,kind,key):
        heads=[head for _,head in self._heads(kind,key)]
        if not heads:return
        # Deletions win the primary slot, but a concurrent edit survives in a
        # visible conflict copy. Timestamps never choose the primary version.
        heads.sort(key=lambda head:(not head['deleted'],hashlib.sha256(encoded(head).encode()).hexdigest()))
        primary=heads[0];self._write(kind,primary['value'],primary['deleted'])
        seen={encoded(primary['value'])}
        for head in heads[1:]:
            raw=encoded(head['value'])
            if head['deleted'] or raw in seen:continue
            seen.add(raw)
            copy_key=str(uuid.uuid5(uuid.UUID(key),hashlib.sha256(encoded(head).encode()).hexdigest()))
            if self._heads(kind,copy_key) or self.db.execute('SELECT 1 FROM '+TABLES[kind]+' WHERE id=?',(copy_key,)).fetchone():continue
            value=dict(head['value'],id=copy_key)
            if kind=='note':value.update(title='Conflicting copy: '+value['title'],deleted_at=None)
            else:value['name']='Conflicting copy: '+value['name']
            self.db.execute("UPDATE luma_notes_sync_meta SET value='0' WHERE key='applying'")
            try:self._write(kind,value,False)
            finally:self.db.execute("UPDATE luma_notes_sync_meta SET value='1' WHERE key='applying'")

    def apply(self,peer,page):
        self._allowed();uid(peer)
        if (not isinstance(page,dict) or set(page)!={'store','after','cursor','watermark','items'}
                or uid(page['store'])!=peer or not isinstance(page['items'],list) or len(page['items'])>MAX_ITEMS
                or any(type(page[key]) is not int or page[key]<0 for key in ('after','cursor','watermark'))
                or not page['after']<=page['cursor']<=page['watermark']
                or len(encoded(page).encode())>MAX_PAGE+4096):raise ValueError('invalid Notes page')
        if bool(page['items'])!=(page['cursor']>page['after']):raise ValueError('invalid Notes page progress')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._capture()
            previous=self.db.execute('SELECT position FROM luma_notes_sync_cursors WHERE peer=?',(peer,)).fetchone()
            previous=previous[0] if previous else 0
            if page['after']>previous:raise ValueError('Notes cursor gap')
            touched=set()
            for operation in page['items']:
                self._validate(operation)
                if page['cursor']<=previous and not self.db.execute(
                    'SELECT 1 FROM luma_notes_sync_ops WHERE origin=? AND sequence=?',
                    (operation['origin'],operation['sequence'])).fetchone():raise ValueError('changed replayed Notes page')
                if operation['origin']==self.origin and not self.db.execute(
                    'SELECT 1 FROM luma_notes_sync_ops WHERE origin=? AND sequence=?',(self.origin,operation['sequence'])).fetchone():
                    raise ValueError('remote impersonation of local Notes store')
                if self._record(operation):touched.add((operation['kind'],operation['uid']))
            for kind,key in touched:
                if kind=='note':
                    for _,head in self._heads(kind,key):
                        folder=head['value']['folder_id']
                        if folder and not self._heads('folder',folder) and not self.db.execute('SELECT 1 FROM folders WHERE id=?',(folder,)).fetchone():
                            raise ValueError('unknown Notes folder reference')
            self.db.execute("UPDATE luma_notes_sync_meta SET value='1' WHERE key='applying'")
            for kind,key in sorted(touched,key=lambda item:(item[0]!='folder',item[1])):self._materialize(kind,key)
            self.db.execute("UPDATE luma_notes_sync_meta SET value='0' WHERE key='applying'")
            self._capture()
            self.db.execute('INSERT INTO luma_notes_sync_cursors VALUES(?,?) ON CONFLICT(peer) DO UPDATE SET position=max(position,excluded.position)',
                            (peer,page['cursor']))
            self._allowed();self.db.commit()
            return dict(applied=True,cursor=max(previous,page['cursor']))
        except BaseException:self.db.rollback();raise
