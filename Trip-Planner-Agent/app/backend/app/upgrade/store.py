import json, sqlite3, os
from pathlib import Path
from uuid import uuid4
from .models import utcnow

class VersionConflict(Exception): pass

class Store:
    def __init__(self,path=None):
        self.path=str(path or os.getenv('TRIP_DB_PATH',Path(__file__).resolve().parents[2]/'data/trips.db'))
        Path(self.path).parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS plans(id TEXT,version INTEGER,data TEXT NOT NULL,created_at TEXT,PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,plan_id TEXT,status TEXT,data TEXT NOT NULL);
            ''')
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row;return db
    def get(self,id,version=None):
        with self.connect() as db:
            row=db.execute('SELECT data FROM plans WHERE id=? AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1',(id,version,version)).fetchone()
        if not row: raise KeyError(id)
        return json.loads(row['data'])
    def save(self,plan,expected=None):
        id=plan.get('id') or str(uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT MAX(version) FROM plans WHERE id=?',(id,)).fetchone()[0] or 0
            if expected is not None and expected!=current: raise VersionConflict('行程已被修改，请刷新后再提交')
            plan={**plan,'id':id,'version':current+1,'updated_at':utcnow()}
            db.execute('INSERT INTO plans VALUES(?,?,?,?)',(id,current+1,json.dumps(plan,ensure_ascii=False),utcnow()))
        return plan
    def list(self):
        with self.connect() as db:
            rows=db.execute('SELECT p.data FROM plans p JOIN (SELECT id,MAX(version) version FROM plans GROUP BY id) v ON p.id=v.id AND p.version=v.version ORDER BY p.created_at DESC LIMIT 30').fetchall()
        return [json.loads(r['data']) for r in rows]
    def history(self,id):
        with self.connect() as db: rows=db.execute('SELECT data FROM plans WHERE id=? ORDER BY version',(id,)).fetchall()
        return [json.loads(r['data']) for r in rows]
    def save_run(self,run):
        with self.connect() as db: db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?)',(run['id'],run.get('plan_id'),run['status'],json.dumps(run,ensure_ascii=False)))
    def recover_interrupted(self):
        with self.connect() as db:
            rows=db.execute("SELECT id,data FROM runs WHERE status='running'").fetchall()
            for row in rows:
                run=json.loads(row['data']);run['status']='interrupted';run['error']={'code':'INTERRUPTED','message':'服务曾中断，可从已保存的查询结果继续'}
                db.execute('UPDATE runs SET status=?,data=? WHERE id=?',('interrupted',json.dumps(run,ensure_ascii=False),row['id']))
    def get_run(self,id):
        with self.connect() as db:r=db.execute('SELECT data FROM runs WHERE id=?',(id,)).fetchone()
        if not r:raise KeyError(id)
        return json.loads(r['data'])
